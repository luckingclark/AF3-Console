"""Conservative, add-only replication of complete AlphaFold MSA products.

Scanning is read-only. A plan must be explicitly executed by its caller. Existing
peer products are never merged, replaced or removed; a different whole directory
is a conflict, even if only one companion file differs. No rsync is required.
"""
from contextlib import ExitStack
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import stat
import sys
import tempfile

import af3_runtime as R


class SyncConflict(ValueError):
    """A peer or source changed, so adding this product is unsafe."""


class InvalidProduct(ValueError):
    """An incomplete or nonportable MSA product cannot be synchronized."""


_REFERENCE_KEYS = {"unpairedMsaPath", "pairedMsaPath", "mmcifPath", "userCCDPath"}
_CHUNK = 1024 * 1024


def _linked(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _no_links(path):
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if _linked(part):
            raise ValueError("Symlinks and junctions are not synchronized: " + str(part))
    return path


def _roots(values):
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 3:
        raise ValueError("Configure between one and three independent MSA directories")
    roots = []
    for value in values:
        if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
            raise ValueError("An MSA directory path is empty")
        root = _no_links(Path(value).expanduser()).resolve()
        for other in roots:
            if root == other or root in other.parents or other in root.parents:
                raise ValueError("MSA directories must be distinct and must not contain each other")
            if root.exists() and other.exists() and os.path.samefile(root, other):
                raise ValueError("Two MSA directories refer to the same location")
        roots.append(root)
    return roots


def _issue(path, reason, **extra):
    return dict(path=str(path), reason=str(reason), **extra)


def _stamp(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise InvalidProduct("Only regular files can be synchronized: " + str(path))
    return [info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_dev, info.st_ino]


def _hash_file(path):
    _no_links(path)
    before = _stamp(path)
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK), b""):
            digest.update(chunk)
    if _stamp(path) != before:
        raise SyncConflict("Source changed while being scanned; scan again: " + str(path))
    return {"size": before[0], "sha256": digest.hexdigest(), "stamp": before}


def _tree(unit, kind):
    """List every companion, rejecting links and special files anywhere inside."""
    _no_links(unit)
    if kind == "file":
        _stamp(unit)
        return [], [(unit.name, unit)]
    if not unit.is_dir():
        raise InvalidProduct("MSA output directory is missing: " + str(unit))
    directories, files = [], []
    def inaccessible(exc):
        raise exc
    for directory, names, filenames in os.walk(unit, followlinks=False, onerror=inaccessible):
        base = Path(directory)
        for name in sorted(names):
            child = base / name
            if _linked(child):
                raise InvalidProduct("A companion is a symlink or junction: " + str(child))
            directories.append(child.relative_to(unit).as_posix())
        for name in sorted(filenames):
            child = base / name
            if _linked(child):
                raise InvalidProduct("A companion is a symlink: " + str(child))
            _stamp(child)
            files.append((child.relative_to(unit).as_posix(), child))
    return sorted(directories), sorted(files)


def _references(value):
    if isinstance(value, list):
        for child in value:
            yield from _references(child)
    elif isinstance(value, dict):
        for key, child in value.items():
            if key in _REFERENCE_KEYS:
                yield key, child
            else:
                yield from _references(child)


def _validate_json(path, unit, kind):
    try:
        with path.open(encoding="utf-8") as stream:
            data = json.load(stream)
    except (ValueError, OSError) as exc:
        raise InvalidProduct("Cannot read complete JSON: " + str(path)) from exc
    for key, reference in _references(data):
        if not isinstance(reference, str) or not reference:
            raise InvalidProduct(key + " must reference an existing companion file")
        asset = Path(reference)
        if kind == "file":
            raise InvalidProduct("Flat JSON with companion paths is not copied alone; place the JSON and relative companions in one output directory")
        if asset.is_absolute() or PureWindowsPath(reference).drive or reference.startswith("~"):
            raise InvalidProduct("External or absolute " + key + " is not portable; use an inline value or a relative companion inside this output directory")
        target = _no_links(path.parent / asset).resolve()
        if unit.resolve() not in target.parents or not target.is_file():
            raise InvalidProduct(key + " must remain inside the complete output directory")
    del data  # Large inline MSAs need not coexist with the runtime schema cache.
    if not R.validate_msa(path):
        details = getattr(R, "msa_validation_errors", lambda value: [])(path)
        raise InvalidProduct("Incomplete MSA JSON: " + str(path) + ("; " + "; ".join(map(str, details[:3])) if details else ""))


def _records(root, unit, kind, data_paths):
    records, lock_names = [], []
    for path in data_paths:
        name = path.name[:-len("_data.json")]
        try:
            record_path = Path(R.msa_record_path(root, name))
        except ValueError:
            continue  # Unmanaged, arbitrary job names can still be portable.
        _no_links(record_path)
        native = kind == "directory" and unit.name == name and path.parent == unit
        if native or kind == "file":
            lock_names.append(name)
        if not os.path.lexists(record_path):
            continue
        record = R.read_json(record_path)
        if not isinstance(record, dict) or record.get("status") != "complete":
            raise InvalidProduct("MSA has a pending, failed or unreadable management record: " + name)
        if not native:
            # Complete records only describe their canonical native product.
            # Copying them with timestamp/flat files would pin a missing path.
            continue
        if not record.get("fingerprint") or record.get("name") != name or not R.msa_ready(root, name):
            raise InvalidProduct("MSA does not match its completed management record: " + name)
        records.append(dict(name=name, path=str(record_path),
                            identity={key: record.get(key) for key in ("name", "fingerprint", "input_signature", "status")},
                            **_hash_file(record_path)))
    return records, sorted(set(lock_names))


def _inspect(root, unit):
    if unit.parent != root or unit.name.startswith("."):
        raise InvalidProduct("Only direct, visible output units can be synchronized")
    kind = "directory" if unit.is_dir() else "file"
    directories, files = _tree(unit, kind)
    data_paths = [path for _, path in files if path.name.endswith("_data.json")]
    if not data_paths:
        raise InvalidProduct("Output unit contains no *_data.json")
    if kind == "file" and not unit.name.endswith("_data.json"):
        raise InvalidProduct("Only *_data.json can be synchronized as a flat file")
    for path in data_paths:
        _validate_json(path, unit, kind)
    records, lock_names = _records(root, unit, kind, data_paths)
    manifest = {"kind": kind, "directories": directories,
                "files": [dict(path=relative, **_hash_file(path)) for relative, path in files],
                "records": records, "lock_names": lock_names}
    identity = R.digest({"kind": kind, "directories": directories,
                         "files": [[item["path"], item["size"], item["sha256"]] for item in manifest["files"]],
                         "records": [record["identity"] for record in records]}, 64)
    _source_unchanged(root, unit, manifest)
    return {"source": str(unit), "source_root": str(root), "relative": unit.name,
            "files": len(files), "bytes": sum(item["size"] for item in manifest["files"]),
            "identity": identity, "manifest": manifest}


def _source_unchanged(root, unit, manifest):
    directories, files = _tree(unit, manifest["kind"])
    actual = {relative: _stamp(path) for relative, path in files}
    expected = {item["path"]: item["stamp"] for item in manifest["files"]}
    if directories != manifest["directories"] or actual != expected:
        raise SyncConflict("Source changed since the scan; scan again: " + str(unit))
    # New/removed state records also change whether an unmanaged file is reusable.
    expected_records = {record["name"]: record for record in manifest["records"]}
    for name in manifest["lock_names"]:
        record_path = Path(R.msa_record_path(root, name))
        _no_links(record_path)
        record = expected_records.get(name)
        if record is not None:
            if _stamp(record_path) != record["stamp"]:
                raise SyncConflict("MSA management record changed; scan again: " + name)
        elif os.path.lexists(record_path):
            # Flat legacy files can coexist with a complete native record, but
            # must not be synchronized under its different management identity.
            raise SyncConflict("An MSA management record appeared; scan again: " + name)


def _inventory(root, plan):
    units = {}
    try:
        _no_links(root)
        with os.scandir(root) as entries:
            candidates = sorted(entries, key=lambda entry: entry.name)
    except (OSError, ValueError) as exc:
        plan["errors"].append(_issue(root, "Cannot scan MSA directory; check that it exists and is readable: " + str(exc)))
        return None
    for entry in candidates:
        if entry.name.startswith("."):
            continue
        path = root / entry.name
        try:
            if entry.is_symlink() or _linked(path):
                plan["invalid"].append(_issue(path, "Symlinks and junctions are never synchronized"))
                continue
            if entry.is_dir(follow_symlinks=False):
                with os.scandir(path) as children:
                    if not any(child.name.endswith("_data.json") for child in children):
                        continue
            elif not entry.name.endswith("_data.json"):
                continue
            units[entry.name] = _inspect(root, path)
        except (OSError, ValueError) as exc:
            plan["invalid"].append(_issue(path, exc))
    return units


def _destination_clear(root, unit, manifest):
    _no_links(root)
    _no_links(unit)
    if os.path.lexists(unit):
        raise SyncConflict("Destination already exists; existing files are never merged or overwritten: " + str(unit))
    for name in manifest["lock_names"]:
        record_path = Path(R.msa_record_path(root, name))
        _no_links(record_path)
        if os.path.lexists(record_path):
            raise SyncConflict("Destination MSA name already has a management record: " + str(record_path))


def scan_pools(roots):
    """Read-only, bidirectional union plan for one to three independent pools.

    Different copies of the same relative unit are conflicts across the entire
    plan: an empty third pool never receives an arbitrarily selected variant.
    Missing/unreadable roots are reported and excluded, not created by scanning.
    """
    plan = {"roots": [], "copies": [], "conflicts": [], "invalid": [], "errors": []}
    try:
        normalized = _roots(roots)
    except (OSError, ValueError) as exc:
        plan["errors"].append(_issue("", exc))
        return plan
    plan["roots"] = [str(root) for root in normalized]
    inventories = {root: _inventory(root, plan) for root in normalized}
    relatives = sorted({relative for inventory in inventories.values() if inventory for relative in inventory})
    for relative in relatives:
        sources = [inventory[relative] for inventory in inventories.values() if inventory and relative in inventory]
        if len({source["identity"] for source in sources}) != 1:
            plan["conflicts"].append(_issue(relative, "Different complete products have the same relative name; choose a distinct directory name manually", sources=[source["source"] for source in sources]))
            continue
        source = sources[0]
        for root, inventory in inventories.items():
            if inventory is None or relative in inventory:
                continue
            destination = root / relative
            try:
                _destination_clear(root, destination, source["manifest"])
            except (OSError, ValueError) as exc:
                plan["conflicts"].append(_issue(destination, exc, source=source["source"]))
                continue
            plan["copies"].append(dict(source, destination=str(destination), destination_root=str(root)))
    return plan


def _copy_verified(source, target, expected):
    _no_links(source)
    if _stamp(source) != expected["stamp"]:
        raise SyncConflict("Source changed before copying; scan again: " + str(source))
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    count = 0
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(source, flags), "rb") as incoming, target.open("xb") as outgoing:
        for chunk in iter(lambda: incoming.read(_CHUNK), b""):
            outgoing.write(chunk)
            digest.update(chunk)
            count += len(chunk)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    if count != expected["size"] or digest.hexdigest() != expected["sha256"] or _stamp(source) != expected["stamp"]:
        raise SyncConflict("Source changed while copying; no MSA product was published: " + str(source))
    os.utime(target, ns=(expected["stamp"][1], expected["stamp"][1]))


def _rename_directory_exclusive(source, destination):
    """True after an atomic no-replace rename, False when unavailable."""
    if os.name == "nt":
        os.rename(source, destination)  # Windows always fails if target exists.
        return True
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, "renameat2", None)
        if rename is not None:
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            rename.restype = ctypes.c_int
            if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1) == 0:
                return True
            code = ctypes.get_errno()
            if code not in (errno.ENOSYS, errno.EINVAL, errno.ENOTSUP):
                raise OSError(code, os.strerror(code), str(destination))
    return False


def _publish_directory(stage, destination, manifest):
    if _rename_directory_exclusive(stage, destination):
        return
    data_files = [item for item in manifest["files"] if item["path"].endswith("_data.json")]
    if len(data_files) != 1:
        raise OSError("This filesystem cannot safely publish a directory containing several data JSON files atomically")
    # Old Linux kernels can lack renameat2. Exclusively claim the whole unit,
    # add companions, and publish its only ready JSON atomically as the last step.
    # If publication fails, never remove this now-visible directory or its files.
    destination.mkdir(exist_ok=False)
    for directory in manifest["directories"]:
        _no_links(destination)
        (destination / directory).mkdir(exist_ok=False)
    ordered = sorted(manifest["files"], key=lambda item: item["path"].endswith("_data.json"))
    for item in ordered:
        target = destination / item["path"]
        _no_links(target)
        os.link(stage / item["path"], target)  # Atomic and exclusive; no copy fallback.


def _own_staging_cleanup(stage, staging_root):
    """Only remove the private mkdtemp tree allocated by this invocation."""
    resolved_root, resolved_stage = staging_root.resolve(), stage.resolve()
    if (resolved_stage.parent != resolved_root or not stage.name.startswith("copy-")
            or _linked(stage) or _linked(staging_root)):
        return
    if stage.exists():
        shutil.rmtree(stage)


def _execute_copy(item, roots):
    source_root = _no_links(Path(item["source_root"])).resolve()
    destination_root = _no_links(Path(item["destination_root"])).resolve()
    if source_root not in roots or destination_root not in roots or source_root == destination_root:
        raise ValueError("Copy roots do not match the confirmed sync plan")
    relative = item["relative"]
    if not isinstance(relative, str) or not relative or relative.startswith(".") or Path(relative).name != relative or "/" in relative or "\\" in relative:
        raise ValueError("Sync units must be direct, visible children of their pools")
    source, destination = source_root / relative, destination_root / relative
    if str(source) != item["source"] or str(destination) != item["destination"]:
        raise ValueError("Copy paths do not match the confirmed relative name")
    manifest = item["manifest"]
    # Manifest filenames are data from a prior scan, but validate them again
    # before using them to create paths in a private staging directory.
    for relative_path in manifest["directories"] + [entry["path"] for entry in manifest["files"]]:
        candidate = Path(relative_path)
        if (not candidate.parts or candidate.is_absolute() or PureWindowsPath(relative_path).drive
                or ".." in candidate.parts or "\\" in relative_path):
            raise ValueError("Unsafe companion path in sync plan")
    for record in manifest["records"]:
        expected_path = Path(R.msa_record_path(source_root, record["name"]))
        if str(expected_path) != record["path"] or record["name"] not in manifest["lock_names"]:
            raise ValueError("Unsafe management record path in sync plan")
    _source_unchanged(source_root, source, manifest)
    _destination_clear(destination_root, destination, manifest)
    staging_root = _no_links(destination_root / ".af3_msa_sync")
    staging_root.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="copy-", dir=staging_root))
    created_records = []
    try:
        staged_unit = stage / relative
        if manifest["kind"] == "directory":
            staged_unit.mkdir()
            for directory in manifest["directories"]:
                (staged_unit / directory).mkdir()
        for entry in manifest["files"]:
            origin = source / entry["path"] if manifest["kind"] == "directory" else source
            target = staged_unit / entry["path"] if manifest["kind"] == "directory" else staged_unit
            _copy_verified(origin, target, entry)
        for record in manifest["records"]:
            _copy_verified(Path(record["path"]), stage / ".msa_records" / (record["name"] + ".json"), record)
        _source_unchanged(source_root, source, manifest)
        # Relative companions now resolve in staging, proving the copy is portable.
        for entry in manifest["files"]:
            if entry["path"].endswith("_data.json"):
                data_path = staged_unit / entry["path"] if manifest["kind"] == "directory" else staged_unit
                _validate_json(data_path, staged_unit, manifest["kind"])
        with ExitStack() as locks:
            for name in manifest["lock_names"]:
                lock_path = _no_links(destination_root / ".msa_records" / (name + ".lock"))
                locks.enter_context(R.file_lock(lock_path, timeout=10))
            _destination_clear(destination_root, destination, manifest)
            _source_unchanged(source_root, source, manifest)
            for record in manifest["records"]:
                target = Path(R.msa_record_path(destination_root, record["name"]))
                _no_links(target)
                target.parent.mkdir(exist_ok=True)
                os.link(stage / ".msa_records" / target.name, target)
                created_records.append((target, _stamp(target)))
            if manifest["kind"] == "directory":
                _publish_directory(staged_unit, destination, manifest)
            else:
                _no_links(destination)
                os.link(staged_unit, destination)
            created_records.clear()
    finally:
        # A failed publication may leave only records created by this call.
        # Never unlink a peer record, or a record changed since we created it.
        for record, stamp in created_records:
            try:
                if not _linked(record) and _stamp(record) == stamp:
                    record.unlink()
            except OSError:
                pass
        _own_staging_cleanup(stage, staging_root)


def execute_plan(plan):
    """Execute confirmed additions; recheck every source and destination first."""
    result = {"copied": 0, "skipped": 0, "conflicts": list(plan.get("conflicts", [])), "errors": list(plan.get("errors", []))}
    try:
        roots = _roots(plan.get("roots", []))
    except (OSError, ValueError) as exc:
        result["errors"].append(_issue("", exc))
        return result
    for item in plan.get("copies", []):
        try:
            _execute_copy(item, roots)
            result["copied"] += 1
        except (SyncConflict, FileExistsError) as exc:
            result["skipped"] += 1
            result["conflicts"].append(_issue(item.get("destination", ""), exc, source=item.get("source", "")))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result["skipped"] += 1
            result["errors"].append(_issue(item.get("destination", ""), "Copy was not completed; source and existing peer files were preserved: " + str(exc)))
    return result


def replicate_product(primary, name, destinations):
    """Add just one completed primary product to its configured backup pools."""
    result = {"copied": 0, "skipped": 0, "conflicts": [], "errors": []}
    try:
        roots = _roots([primary, *destinations])
        root = roots[0]
        path = Path(R.msa_data_path(root, name))
        if not R.msa_ready(root, name):
            raise InvalidProduct("MSA is not complete; no backup was published: " + name)
        unit = path if path.parent == root else path.parent
        source = _inspect(root, unit)
        plan = dict(roots=[str(value) for value in roots], copies=[], conflicts=[], errors=[])
        for destination_root in roots[1:]:
            destination = destination_root / unit.name
            if os.path.lexists(destination):
                try:
                    if _inspect(destination_root, destination)["identity"] == source["identity"]:
                        result["skipped"] += 1
                        continue
                except (OSError, ValueError):
                    pass
                plan["conflicts"].append(_issue(destination, "Existing backup differs; no files were overwritten", source=str(unit)))
                continue
            plan["copies"].append(dict(source, destination=str(destination), destination_root=str(destination_root)))
        execution = execute_plan(plan)
        execution["skipped"] += result["skipped"]
        return execution
    except (OSError, ValueError) as exc:
        result["errors"].append(_issue(primary, exc))
        return result
