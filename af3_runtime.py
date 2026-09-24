"""Persistence and identity shared by the CLI, controllers and desktop UI.

Only stdlib imports here; importing the submission CLI never imports Qt/NumPy.
"""
import contextlib
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import OrderedDict
from pathlib import Path

VERSION = "0.1.0"
ASSET_ROOT = None
ACTIVE = {"PENDING", "RUNNING", "CONFIGURING", "COMPLETING", "SUSPENDED", "RESIZING", "REQUEUED"}
TERMINAL = {"succeeded", "failed", "cancelled", "blocked"}


class BusinessError(ValueError):
    """Invalid user input; CLI translates this to a nonzero exit code."""


def contained(path, root, allow_root=False):
    p, r = Path(path).resolve(), Path(root).resolve()
    return (allow_root and p == r) or r in p.parents


def registered_job_ids(spec, workdir):
    """Include historical attempts and exact OOM retry IDs, never job names."""
    ids = {str(spec[k]) for k in ('controller_jid', 'watcher_jid') if spec.get(k)}
    for key in ('msa_jids', 'infer_jids', 'pae_infer_jids'):
        ids.update(str(j) for j in spec.get(key, []) if j)
    roots = {str(Path(workdir).resolve())}
    names = [j['name'] for j in spec.get('jobs', [])]
    if spec.get('type') == 'raw_json': names.append(spec['name'])
    output = spec.get('batch_dir') or spec.get('output_dir')
    if output:
        for name in names:
            intent=read_json(Path(output)/'.tasks'/(name+'.submission.json'),{})
            if intent and not intent.get('job_id'):raise SchedulerUnavailable('提交结果未确认，不能清理')
            if intent.get('job_id'):ids.add(str(intent['job_id']))
            rec = read_json(task_path(output, name), {})
            if rec.get('job_id'): ids.add(str(rec['job_id']))
            ids.update(str(a['job_id']) for a in rec.get('attempts', []) if a.get('job_id'))
            folder = Path(output) / '.attempts' / name
            if folder.is_dir():
                for p in folder.glob('*.retry'):
                    ids.add(p.read_text().strip().split(';')[0])
    for root in roots:
        control=read_json(Path(root)/'.control_submission.json',{})
        if control and not control.get('job_id'):raise SchedulerUnavailable('控制器提交结果未确认，不能清理')
        if control.get('job_id'):ids.add(str(control['job_id']))
        for p in (Path(root)/'scripts').glob('*.submission.json'):
            rec=read_json(p,{})
            if rec and not rec.get('job_id'):raise SchedulerUnavailable('MSA 提交结果未确认: '+str(p))
            if rec.get('job_id'):ids.add(str(rec['job_id']))
            ids.update(str(a['job_id']) for a in rec.get('attempts',[]) if a.get('job_id'))
        # Legacy warmup arrays are recorded before submit_msa_stage returns.
        import re
        for p in (Path(root)/'logs').glob('**/*.tsv'):
            ids.update(re.findall(r'Job ID:\s*(\d+)',p.read_text(encoding='utf8')))
        for p in (Path(root) / '.tasks').glob('*.json'):
            rec = read_json(p, {})
            if p.name.endswith('.submission.json') and not rec.get('job_id'):
                raise SchedulerUnavailable('存在结果未确认的提交，不能清理: ' + str(p))
            if rec.get('job_id'): ids.add(str(rec['job_id']))
            ids.update(str(a['job_id']) for a in rec.get('attempts', []) if a.get('job_id'))
        for p in (Path(root) / '.attempts').glob('*/*.retry'):
            ids.add(p.read_text().strip().split(';')[0])
    return ids


def digest(value, length=20):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    separators=(",", ":")).encode()).hexdigest()[:length]


def read_json(path, default=None, resolve=True):
    try:
        with open(path, encoding="utf-8") as stream:
            value = json.load(stream)
        if resolve and isinstance(value, dict) and value.get('storage_version') == 3:
            base = Path(path).parent
            plan = _PLAN_CACHE.load(str(base / value['plan_file']))
            result = unpack_plan(plan)
            for key, filename in value.get('detail_files', {}).items():
                result[key] = unpack_plan(_PLAN_CACHE.load(str(base / filename)))
            result.update(value)
            return result
        return value
    except (OSError, ValueError):
        return default


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, str(path))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")


@contextlib.contextmanager
def file_lock(path, timeout=60):
    """OS lock: automatically released on crash; never unlink a live lock inode."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as stream:
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b"0"); stream.flush()
        end = time.monotonic() + timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (OSError, BlockingIOError):
                if time.monotonic() >= end:
                    raise TimeoutError("另一个进程正在更新: " + str(path))
                time.sleep(.1)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def update_json(path, **updates):
    with file_lock(str(path) + ".lock"):
        value = read_json(path, resolve=False)
        if not isinstance(value, dict):
            raise ValueError("状态文件缺失或损坏: " + str(path))
        if value.get('storage_version') == 3:
            details = dict(value.get('detail_files', {}))
            for key in ('pairs','jobs','task_states','fragments','raw_sides','pae_pending','pae_report','msa_entities'):
                if key not in updates: continue
                data = updates.pop(key)
                filename = 'state_' + key + '.json'
                atomic_json(Path(path).parent / filename, pack_plan(data))
                details[key] = filename
                if key in ('pairs','jobs'): value['task_count'] = len(data or [])
            value['detail_files'] = details
        value.update(updates)
        value["updated_at"] = time.time()
        value["revision"] = value.get("revision", 0) + 1
        atomic_json(path, value)
        return value


def lifecycle_lock(workdir, timeout=60):
    directory=Path(workdir).resolve()
    # Stable inode outside the removable task directory, including on Windows.
    return file_lock(directory.parent/'.af3_locks'/(digest(str(directory),32)+'.lock'),timeout=timeout)


@contextlib.contextmanager
def controller_lock(workdir, timeout=1):
    with lifecycle_lock(workdir,timeout):
        with file_lock(Path(workdir)/'.controller.lock',timeout):
            yield


def config_path():
    return os.path.abspath(os.path.expanduser(os.environ.get("AF3_CONFIG", "").strip() or
        os.path.join(os.path.expanduser("~"), ".config", "af3_console", "config.json")))


DERIVED = {"HOST_MODELS": "models", "HOST_MSA_DATA": "msa_data",
           "HOST_INFER_DATA": "infer_data", "HOST_OUTPUT": "output",
           "HOST_CACHE": "cache", "HOST_JAX_CACHE": "af3_buckets_cache"}

CONFIG_ENV_KEYS = ("AF3_BASE",)
INTEGER_CONFIG_KEYS = {
    "MSA_NTASKS_SINGLE", "MSA_NTASKS_BATCH", "MSA_BATCH_MIN_TASKS",
    "MSA_MAX_CONCURRENT", "MSA_SSD_FACTOR", "MSA_SSD_MIN_EFF",
    "INF_NTASKS", "INF_GPUS", "INF_MAX_CONCURRENT", "INF_BIG_TOKEN",
    "INF_UM_TOKEN", "INF_MAX_TOKEN", "WATCHER_POLL_SEC",
    "WATCHER_MAX_WAIT_SEC", "SEED_MIN", "SEED_MAX",
}


def normalize_config(values):
    """Validate a full or partial configuration without writing or probing paths.

    Empty paths stay empty so an unfinished Setup can be saved safely. Relative
    host paths are resolved against the current process directory; use absolute
    paths or ~ in portable user configurations.
    """
    clean = dict(values)
    for key, value in clean.items():
        if key == "MSA_BACKUP_DIRS":
            if not isinstance(value, list) or len(value) > 2:
                raise ValueError("MSA_BACKUP_DIRS must be a list of at most two backup directories")
            paths = []
            for item in value:
                item = normalize_config({"HOST_MSA_DATA": item})["HOST_MSA_DATA"]
                if item:
                    paths.append(item)
            value = paths
        elif key in INTEGER_CONFIG_KEYS:
            if isinstance(value, bool) or not isinstance(value, (int, str)):
                raise ValueError(key + " must be a positive integer")
            if isinstance(value, str) and not re.fullmatch(r"[0-9]+", value.strip()):
                raise ValueError(key + " must be a positive integer")
            value = int(value)
            if value <= 0:
                raise ValueError(key + " must be a positive integer")
        elif key.startswith(("HOST_", "CONTAINER_")) or key.endswith("_PARTITION") or key == "INF_BUCKETS":
            if not isinstance(value, str):
                raise ValueError(key + " must be a string")
            if any(char in value for char in ("\x00", "\r", "\n")):
                raise ValueError(key + " must not contain NUL or newlines")
            value = value.strip()
            if key.startswith("HOST_") and value:
                # These delimiters are interpreted by Singularity bind syntax.
                if ":" in value or "," in value:
                    # A Windows drive is useful for local previews and tests;
                    # actual Slurm submissions remain Linux-only.
                    if not (os.name == "nt" and re.match(r"^[A-Za-z]:[\\/]", value) and ":" not in value[2:] and "," not in value):
                        raise ValueError(key + " cannot contain ':' or ',' (container bind delimiters)")
                value = os.path.abspath(os.path.expanduser(value))
            if key.endswith("_PARTITION") and value and not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
                raise ValueError(key + " must be a single Slurm partition name")
            if key == "CONTAINER_MODULE" and value and not re.fullmatch(r"[A-Za-z0-9_.+/-]+", value):
                raise ValueError("CONTAINER_MODULE must be a module name, not a shell command")
            if key == "CONTAINER_RUNTIME" and value:
                if "/" in value or "\\" in value or value.startswith("~"):
                    value = os.path.abspath(os.path.expanduser(value))
                elif not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
                    raise ValueError("CONTAINER_RUNTIME must be an executable name or path")
            if key == "INF_BUCKETS" and value:
                if not re.fullmatch(r"[1-9][0-9]*(?:,[1-9][0-9]*)*", value):
                    raise ValueError("INF_BUCKETS must contain comma-separated positive integers")
                buckets = [int(item) for item in value.split(",")]
                if buckets != sorted(set(buckets)):
                    raise ValueError("INF_BUCKETS must be strictly increasing")
        clean[key] = value
    if "MSA_BACKUP_DIRS" in clean:
        _validate_msa_pool_roots(([clean["HOST_MSA_DATA"]] if clean.get("HOST_MSA_DATA") else []) + clean["MSA_BACKUP_DIRS"])
    return clean


def _validate_msa_pool_roots(paths):
    """Reject aliases and overlapping trees before any pool scan or write."""
    resolved = [os.path.normcase(os.path.realpath(path)) for path in paths]
    for index, left in enumerate(resolved):
        for right in resolved[index + 1:]:
            try:
                common = os.path.commonpath([left, right])
            except ValueError:  # Separate Windows drives.
                continue
            if common in (left, right):
                raise ValueError("MSA directories must be distinct, non-overlapping folders (including symlink aliases)")


def msa_pool_paths(config):
    values = normalize_config({"HOST_MSA_DATA": config.get("HOST_MSA_DATA", ""),
                               "MSA_BACKUP_DIRS": config.get("MSA_BACKUP_DIRS", [])})
    if not values["HOST_MSA_DATA"]:
        return []
    return [values["HOST_MSA_DATA"], *values["MSA_BACKUP_DIRS"]]


def configure(namespace, defaults=None, updates=None, apply_assets=True):
    """site defaults < user file < AF3_BASE; snapshot file is a complete config."""
    base_values = dict(defaults or {k: v for k, v in namespace.items()
                                   if k.isupper() and (isinstance(v, (str, int, float)) or k == "MSA_BACKUP_DIRS")})
    values = dict(base_values)
    site_path = Path(namespace["__file__"]).with_name("site_config.json")
    site = read_json(site_path, {} if not site_path.exists() else None)
    user = read_json(config_path(), {} if not os.path.exists(config_path()) else None)
    if not isinstance(site, dict) or not isinstance(user, dict):
        raise ValueError("配置必须是 JSON 对象")
    explicit = dict(site); explicit.update(user)
    if updates is not None:
        explicit.update(normalize_config(updates))
    if os.environ.get("AF3_BASE", "").strip() and not os.environ.get("AF3_SNAPSHOT"):
        explicit["HOST_BASE"] = os.environ["AF3_BASE"]
    explicit = normalize_config({k: v for k, v in explicit.items() if k in base_values})
    values = normalize_config(values)
    base = explicit.get("HOST_BASE", values["HOST_BASE"])
    values["HOST_BASE"] = base
    for key, suffix in DERIVED.items():
        values[key] = os.path.join(base, suffix) if base else ""
    values.update({k: v for k, v in explicit.items() if k in base_values})
    values["AUX_PARTITION"] = explicit.get("AUX_PARTITION") or values.get("MSA_PARTITION", "")
    values["UNIPROT_CACHE"] = os.path.join(values["HOST_CACHE"], "uniprot") if values["HOST_CACHE"] else ""
    values["HOST_LOGS"] = os.path.join(values["HOST_OUTPUT"], ".logs") if values["HOST_OUTPUT"] else ""
    values["HOST_SPECS"] = os.path.join(values["HOST_OUTPUT"], ".specs") if values["HOST_OUTPUT"] else ""
    msa_pool_paths(values)
    global ASSET_ROOT
    if apply_assets:
        ASSET_ROOT = os.path.join(values["HOST_CACHE"], "assets") if values["HOST_CACHE"] else ""
    namespace.update(values)
    return base_values


def save_config(updates):
    updates = normalize_config(updates)
    path = config_path()
    with file_lock(path + ".lock"):
        old = read_json(path, {})
        if not isinstance(old, dict):
            raise ValueError("Configuration must be a JSON object")
        old.update(updates)
        atomic_json(path, normalize_config(old))
    return path


_FILE_DIGESTS = OrderedDict()


def file_digest(path):
    p = Path(host_asset_path(path)).expanduser().resolve()
    st = p.stat(); key = (str(p), st.st_size, st.st_mtime_ns)
    if key not in _FILE_DIGESTS:
        h = hashlib.sha256()
        with p.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(block)
        _FILE_DIGESTS[key] = h.hexdigest()
        while len(_FILE_DIGESTS) > 256:
            _FILE_DIGESTS.popitem(last=False)
    return _FILE_DIGESTS[key]


def asset_identity(value):
    if isinstance(value, dict):
        return {k: (file_digest(v) if k.lower().endswith("path") and v else asset_identity(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [asset_identity(v) for v in value]
    return value


def entity_identity(ent):
    keys = ("type", "sequence", "copies", "modifications", "ccd", "smiles",
            "msa_path", "paired_msa", "paired_msa_path", "template", "_frag", "_shared_source")
    result = {k: ent.get(k) for k in keys if ent.get(k) is not None}
    if isinstance(result.get('_frag'), dict):
        result['_frag'] = {k:v for k,v in result['_frag'].items() if k != 'parent'}
    # User-facing labels/UniProt aliases are not scientific identity.
    if not ent.get("sequence") and ent.get("uniprot"):
        result["unresolved_uniprot"] = ent["uniprot"]
    return asset_identity(result)


class JsonCache:
    """Bounded decoded MSA cache. Callers must never mutate cached values."""
    def __init__(self, budget=96 * 1024 * 1024):
        self.budget = budget; self.entries = OrderedDict(); self.size = 0

    def load(self, path, transform=None, namespace=None):
        st = os.stat(path); key = (os.path.abspath(path), st.st_size, st.st_mtime_ns, namespace)
        if key in self.entries:
            self.entries.move_to_end(key); return self.entries[key][0]
        with open(path, encoding="utf-8") as stream:
            value = json.load(stream)
        if transform is not None: value=transform(value)
        # Externalized MSA entries contain only short references; bound by actual content.
        cost = len(json.dumps(value,ensure_ascii=False))*3 if transform is not None else st.st_size*3
        if cost <= self.budget:
            while self.entries and self.size + cost > self.budget:
                _, (_, old_cost) = self.entries.popitem(last=False); self.size -= old_cost
            self.entries[key] = (value, cost); self.size += cost
        return value


_PLAN_CACHE = JsonCache()
_MSA_SCHEMA_CACHE = JsonCache()


def pack_plan(value):
    entities = {}
    def walk(item):
        if isinstance(item, list): return [walk(x) for x in item]
        if not isinstance(item, dict): return item
        if 'type' in item and 'sequence' in item:
            key = digest(item, 32); entities.setdefault(key, item)
            return {'$entity': key}
        return {k: walk(v) for k, v in item.items()}
    result = walk(value)
    return {'_packed_plan': 1, 'entities': entities, 'value': result}


def unpack_plan(value):
    if not isinstance(value, dict) or value.get('_packed_plan') != 1: return value
    def walk(item):
        if isinstance(item, list): return [walk(x) for x in item]
        if not isinstance(item, dict): return item
        if set(item) == {'$entity'}:
            import copy
            return copy.deepcopy(value['entities'][item['$entity']])
        return {k: walk(v) for k,v in item.items()}
    return walk(value['value'])


def spec_summary(path):
    """Old plans remain readable; new list refreshes only open the small spec."""
    return read_json(path, {}, resolve=False)


def file_version(path):
    try:
        st = os.stat(path)
        return (os.path.abspath(path), st.st_size, st.st_mtime_ns, st.st_ino)
    except OSError: return (os.path.abspath(path), None)


def publish_result_index(root, names):
    entries = []
    for name in names:
        if not valid_result(root, name): continue
        folder = Path(root) / name
        summary = read_json(folder / (name + '_summary_confidences.json'), {})
        entries.append(dict(label=name, path=str(folder / (name + '_model.cif')),
                            confidence=str(folder / (name + '_confidences.json')),
                            a='', b='', rank='', iptm=summary.get('iptm','')))
    index = dict(version=1, entries=entries)
    target = Path(root) / 'results_index.json'
    if read_json(target) != index: atomic_json(target, index)


def find_specs(output, infer_data=None):
    paths = list(Path(output).glob('*/spec.json')) + list((Path(output)/'.specs').glob('*.spec.json'))
    if infer_data: paths += list((Path(infer_data)/'.work').glob('*/spec.json'))
    return sorted(set(str(p.resolve()) for p in paths))


def resolve_spec(selector, output, infer_data=None):
    path = Path(selector)
    if path.is_dir() and (path/'spec.json').is_file(): return str((path/'spec.json').resolve())
    if path.is_file() and path.suffix == '.json': return str(path.resolve())
    hits = []
    for p in find_specs(output, infer_data):
        s = spec_summary(p)
        if selector in (Path(p).parent.name, s.get('name'), s.get('label'), s.get('tag')): hits.append(p)
    if len(hits) == 1: return hits[0]
    if hits: raise BusinessError('名称对应多个任务，请指定目录或 spec:\n' + '\n'.join(hits))
    return None


def host_asset_path(path):
    if str(path).startswith("/root/af_assets/") and ASSET_ROOT:
        return os.path.join(ASSET_ROOT, os.path.basename(path))
    return path


def externalize_input(data, root):
    """One immutable asset per content digest; input JSON only contains references.

    Absolute host paths are rewritten to /root/af_assets by the submission layer.
    The whole asset directory is mounted once; no per-entity bind proliferation.
    """
    Path(root).mkdir(parents=True, exist_ok=True)
    def materialize(text, suffix):
        key = hashlib.sha256(text.encode("utf-8")).hexdigest()
        target = Path(root) / (key + suffix)
        if not target.exists():
            with file_lock(str(target) + ".lock"):
                if not target.exists(): atomic_text(target, text)
        return "/root/af_assets/" + target.name
    def walk(obj):
        if isinstance(obj, list): return [walk(v) for v in obj]
        if not isinstance(obj, dict): return obj
        out = {k: walk(v) for k, v in obj.items()}
        for inline, pathkey, suffix in (("unpairedMsa", "unpairedMsaPath", ".a3m"),
                                        ("pairedMsa", "pairedMsaPath", ".a3m"),
                                        ("mmcif", "mmcifPath", ".cif"),
                                        ("userCCD", "userCCDPath", ".cif")):
            if isinstance(out.get(inline), str) and out[inline]:
                out[pathkey] = materialize(out.pop(inline), suffix)
            elif out.get(pathkey) and not out[pathkey].startswith("/root/af_assets/"):
                out[pathkey] = materialize(_msa_read_asset(os.path.expanduser(out[pathkey])), suffix)
        return out
    return walk(data)


def pin_assets(value, root):
    """Freeze external MSA/template/CCD sources once per content, streaming copies."""
    if isinstance(value, list): return [pin_assets(v, root) for v in value]
    if not isinstance(value, dict): return value
    result = {}
    for key, item in value.items():
        if key in ("identity", "_parent_identity", "parent_identity"):
            # Scientific identities already replace paths with content digests;
            # only the corresponding live entity fields refer to actual files.
            import copy
            result[key] = copy.deepcopy(item)
        elif key in ("msa_path", "paired_msa_path", "mmcifPath", "user_ccd_path", "user_ccd") and item:
            source = Path(host_asset_path(item)).expanduser().resolve()
            expected = file_digest(source)
            target = Path(root) / (expected + source.suffix)
            with file_lock(str(target) + ".lock"):
                if not target.exists():
                    tmp = str(target) + ".tmp." + str(os.getpid())
                    shutil.copyfile(source, tmp)
                    if file_digest(tmp) != expected:
                        os.unlink(tmp); raise ValueError("复制期间输入文件变化: " + str(source))
                    os.replace(tmp, target)
            result[key] = str(target)
        else: result[key] = pin_assets(item, root)
    return result


def validate_msa(path, entity=None):
    """Whether every chain has usable, explicitly completed MSA input fields."""
    return not msa_validation_errors(path, entity)


def _validate_msa(path, entity=None):
    return validate_msa(path, entity)


# Keep compact validation summaries even when a decoded JSON exceeds the MSA
# cache's memory budget. External resource versions are checked on every call.
_MSA_VALIDATION_CACHE = OrderedDict()
_MSA_INSERTIONS = str.maketrans('', '', 'abcdefghijklmnopqrstuvwxyz')
_MSA_PROTEIN_EQUIVALENTS = str.maketrans({'B': 'D', 'Z': 'E', 'U': 'C', 'J': 'X', 'O': 'X'})


def _msa_validation_version(path):
    try:
        st = os.stat(path)
        return (st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino, st.st_mode)
    except OSError:
        return None


def _msa_read_asset(path):
    """Read AF3's plain/gzip/xz/zstd text references without engine imports."""
    import gzip
    import lzma
    with open(path, 'rb') as source:
        magic = source.read(6)
        source.seek(0)
        if magic[:2] == b'\x1f\x8b':
            with gzip.open(source, 'rt', encoding='utf-8') as stream:
                return stream.read()
        if magic == b'\xfd7zXZ\x00':
            try:
                with lzma.open(source, 'rt', encoding='utf-8') as stream:
                    return stream.read()
            except lzma.LZMAError as exc:
                raise ValueError('invalid xz resource') from exc
        if magic[:4] == b'\x28\xb5\x2f\xfd':
            try:
                import zstandard
            except ImportError as exc:
                raise ValueError('zstd resource needs zstandard installed, or an uncompressed file') from exc
            try:
                with zstandard.open(source, 'rt', encoding='utf-8') as stream:
                    return stream.read()
            except zstandard.ZstdError as exc:
                raise ValueError('invalid zstd resource') from exc
        return source.read().decode('utf-8')


def _msa_a3m_error(text, sequence, kind):
    """Check all alignment lengths without retaining a second full alignment."""
    if not text.strip():
        return None  # AF3 explicitly permits an empty/custom MSA.
    record_count, aligned_length = 0, 0
    query_parts = []
    for match in re.finditer(r'[^\r\n]+', text):
        line = match.group().strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('>'):
            if record_count and aligned_length != len(sequence):
                return 'A3M record %d has an aligned length different from sequence' % record_count
            record_count += 1
            aligned_length = 0
            continue
        if not record_count:
            return 'A3M must start with a FASTA header (>)'
        if not re.fullmatch(r'[A-Za-z-]+', line):
            return 'A3M record %d contains invalid alignment characters' % record_count
        aligned = line.translate(_MSA_INSERTIONS)
        aligned_length += len(aligned)
        if record_count == 1:
            query_parts.append(aligned)
    if not record_count:
        return 'A3M has no sequence records'
    if aligned_length != len(sequence):
        return 'A3M record %d has an aligned length different from sequence' % record_count
    query = ''.join(query_parts)
    if kind == 'protein':
        query = query.translate(_MSA_PROTEIN_EQUIVALENTS)
        expected = sequence.upper().translate(_MSA_PROTEIN_EQUIVALENTS)
    else:
        query = ''.join(letter if letter in 'ACGU-' else 'N' for letter in query)
        expected = ''.join(letter if letter in 'ACGU-' else 'N' for letter in sequence.upper())
    if query != expected:
        return 'A3M first sequence does not match the chain sequence'
    return None


def _msa_schema_summary(path):
    """Return errors, external dependencies and compact chain identities."""
    errors, references, identities, empty_fields = [], {}, {}, []
    data = _MSA_SCHEMA_CACHE.load(path, namespace=_msa_validation_version(path))
    if not isinstance(data, dict) or not isinstance(data.get('sequences'), list) or not data['sequences']:
        return ['sequences must be a nonempty array'], references, identities, empty_fields

    def source_text(body, field, label):
        pathkey = field + 'Path'
        if field not in body and pathkey not in body:
            errors.append(label + ': missing ' + field + ' or ' + pathkey)
            return None
        if field in body and pathkey in body:
            errors.append(label + ': use only one of ' + field + ' and ' + pathkey)
            return None
        if field in body:
            if not isinstance(body[field], str):
                errors.append(label + '.' + field + ': must be a string (empty is allowed for MSA)')
                return None
            return body[field]
        value = body[pathkey]
        if not isinstance(value, str) or not value.strip() or any(c in value for c in '\x00\r\n'):
            errors.append(label + '.' + pathkey + ': must be a nonempty file path')
            return None
        ref = Path(host_asset_path(value)).expanduser()
        if not ref.is_absolute():
            ref = Path(path).parent / ref
        ref = os.path.abspath(ref)
        references[ref] = _msa_validation_version(ref)
        try:
            result = _msa_read_asset(ref)
            if references[ref] != _msa_validation_version(ref):
                errors.append(label + '.' + pathkey + ': resource changed while validating; retry later')
                return None
            return result
        except (OSError, ValueError, EOFError) as exc:
            detail = 'cannot read/decode referenced resource'
            if isinstance(exc, ValueError) and str(exc).startswith('zstd resource'):
                detail = str(exc)
            errors.append(label + '.' + pathkey + ': ' + detail)
            return None

    seen_ids = set()
    for index, entry in enumerate(data['sequences']):
        label = 'sequences[%d]' % index
        if not isinstance(entry, dict) or len(entry) != 1:
            errors.append(label + ': must contain exactly one chain type')
            continue
        kind, body = next(iter(entry.items()))
        label += '.' + str(kind)
        if kind not in ('protein', 'rna', 'dna', 'ligand') or not isinstance(body, dict):
            errors.append(label + ': unsupported chain type or invalid body')
            continue
        ids = body.get('id')
        ids = [ids] if isinstance(ids, str) else ids
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or not re.fullmatch(r'[A-Z]+', i) for i in ids):
            errors.append(label + '.id: must be an uppercase chain ID or a nonempty array of chain IDs')
        else:
            if any(i in seen_ids for i in ids) or len(set(ids)) != len(ids):
                errors.append(label + '.id: duplicate chain ID')
            seen_ids.update(ids)
        chain_label = label + '[' + ','.join(i for i in (ids or []) if isinstance(i, str)) + ']'
        if kind == 'ligand':
            ccd, smiles = body.get('ccdCodes'), body.get('smiles')
            ccd_ok = isinstance(ccd, list) and ccd and all(isinstance(v, str) and v.strip() for v in ccd)
            smiles_ok = isinstance(smiles, str) and bool(smiles.strip())
            if not (bool(ccd_ok) ^ smiles_ok):
                errors.append(label + ': provide either nonempty ccdCodes or smiles')
            continue
        sequence = body.get('sequence')
        if not isinstance(sequence, str) or not re.fullmatch(r'[A-Za-z]+', sequence):
            errors.append(label + '.sequence: must be a nonempty string of residue letters')
            continue
        identities.setdefault(kind, []).append(sequence)
        if kind == 'dna':
            continue
        msa_fields = ['unpairedMsa']
        if kind == 'protein' or any(k in body for k in ('pairedMsa', 'pairedMsaPath')):
            msa_fields.append('pairedMsa')
        for field in msa_fields:
            text = source_text(body, field, label)
            if text is not None:
                error = _msa_a3m_error(text, sequence, kind)
                if error:
                    errors.append(label + '.' + field + ': ' + error)
                elif not text.strip() and (kind == 'protein' or field == 'unpairedMsa'):
                    empty_fields.append(chain_label + '.' + field)
        if kind != 'protein' and 'templates' not in body:
            continue
        templates = body.get('templates')
        if not isinstance(templates, list):
            errors.append(label + '.templates: must be an array (empty [] is allowed)')
            continue
        if kind == 'protein' and not templates:
            empty_fields.append(chain_label + '.templates')
        for template_index, template in enumerate(templates):
            template_label = label + '.templates[%d]' % template_index
            if not isinstance(template, dict):
                errors.append(template_label + ': must be an object')
                continue
            mmcif = source_text(template, 'mmcif', template_label)
            if mmcif is not None and (not mmcif.strip() or not re.search(r'(?m)^\s*data_\S+', mmcif)):
                errors.append(template_label + '.mmcif: must contain nonempty mmCIF text with a data_ block')
            query, target = template.get('queryIndices'), template.get('templateIndices')
            if not isinstance(query, list) or not isinstance(target, list):
                errors.append(template_label + ': queryIndices and templateIndices must be arrays')
                continue
            if len(query) != len(target):
                errors.append(template_label + ': queryIndices and templateIndices must have equal lengths')
            for field, values in (('queryIndices', query), ('templateIndices', target)):
                if any(type(v) is not int or v < 0 for v in values):
                    errors.append(template_label + '.' + field + ': indices must be nonnegative integers')
            if any(type(v) is int and v >= len(sequence) for v in query):
                errors.append(template_label + '.queryIndices: index outside chain sequence')
    return errors, references, identities, empty_fields


def msa_validation_errors(path, entity=None):
    """Explain unusable MSA inputs without modifying files or exposing sequences.

    Explicit empty MSA strings/template arrays are valid AF3 input. This checks
    schema, alignment/query consistency and referenced assets, not biological
    quality or the full structural validity of a template mmCIF.
    """
    try:
        path = os.path.abspath(os.fspath(path))
        version = _msa_validation_version(path)
        if version is None:
            return ['MSA JSON file is missing or unreadable']
        key = (path, version, str(ASSET_ROOT or ''))
        summary = _MSA_VALIDATION_CACHE.get(key)
        if summary is None or any(_msa_validation_version(ref) != saved for ref, saved in summary[1].items()):
            summary = _msa_schema_summary(path)
            if _msa_validation_version(path) != version:
                return ['MSA JSON changed while validating; retry after the writer finishes']
            # A changed reference must not become cached as a stable failure.
            if any(_msa_validation_version(ref) != saved for ref, saved in summary[1].items()):
                return list(summary[0]) or ['MSA referenced resource changed while validating; retry later']
            _MSA_VALIDATION_CACHE[key] = summary
            while len(_MSA_VALIDATION_CACHE) > 128:
                _MSA_VALIDATION_CACHE.popitem(last=False)
        else:
            _MSA_VALIDATION_CACHE.move_to_end(key)
        errors = list(summary[0])
        if entity is not None:
            if not isinstance(entity, dict) or not isinstance(entity.get('type'), str):
                errors.append('Requested MSA entity must specify a chain type')
            else:
                bodies = summary[2].get(entity['type'], [])
                if len(bodies) != 1:
                    errors.append('MSA JSON must contain exactly one chain entry of the requested type')
                elif bodies[0] != entity.get('sequence'):
                    errors.append('MSA JSON chain sequence does not match the requested entity')
        return errors
    except (OSError, ValueError, TypeError, KeyError, AttributeError, EOFError):
        return ['MSA JSON is unreadable, malformed, or contains invalid field types']


def msa_empty_fields(path, entity=None):
    """List explicit empty fields in valid inputs, for a reuse decision dialog.

    These fields are legal AF3 choices, not validation failures. Missing fields
    or invalid resources are reported by msa_validation_errors instead.
    """
    if msa_validation_errors(path, entity):
        return []
    path = os.path.abspath(os.fspath(path))
    key = (path, _msa_validation_version(path), str(ASSET_ROOT or ''))
    summary = _MSA_VALIDATION_CACHE.get(key)
    return list(summary[3]) if summary is not None else []


def msa_reuse_fingerprint(path):
    """Bind an empty-input approval to JSON and referenced resource contents."""
    errors = msa_validation_errors(path)
    if errors:
        raise ValueError('; '.join(errors))
    path = os.path.abspath(os.fspath(path))
    key = (path, _msa_validation_version(path), str(ASSET_ROOT or ''))
    summary = _MSA_VALIDATION_CACHE.get(key)
    if summary is None:
        raise ValueError('MSA JSON changed during approval; check again')
    return digest({'json': [file_digest(path), _msa_validation_version(path)], 'assets': {
        ref: [file_digest(ref), _msa_validation_version(ref)] for ref in summary[1]}}, 32)


def _msa_storage_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,199}", name):
        raise ValueError("Invalid MSA name: use at most 200 ASCII letters, digits, _, - or .")
    return name


def _msa_storage_path(root, name, *parts):
    """Build a cache path without allowing names or symlinks to escape its root."""
    _msa_storage_name(name)
    path = os.path.join(os.fspath(root), *parts)
    if not contained(path, root):
        raise ValueError("MSA path is outside the configured pool")
    return path


def msa_native_path(root, name):
    """The data pipeline's native output, kept with all its companion files."""
    name = _msa_storage_name(name)
    return _msa_storage_path(root, name, name, name + "_data.json")


def msa_record_path(root, name):
    name = _msa_storage_name(name)
    return _msa_storage_path(root, name, ".msa_records", name + ".json")


def _msa_timestamp_paths(root, name):
    """Native AF3 retry folders, newest timestamp first (never directory mtime)."""
    name = _msa_storage_name(name)
    pattern = re.compile(re.escape(name) + r"_(\d{8}_\d{6})\Z")
    candidates = []
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                match = pattern.fullmatch(entry.name)
                if not match or not entry.is_dir(follow_symlinks=False):
                    continue
                try:
                    path = _msa_storage_path(root, name, entry.name, name + "_data.json")
                except (OSError, ValueError):
                    continue
                candidates.append((match.group(1), path))
    except OSError:
        return []
    return [path for _, path in sorted(candidates, reverse=True)]


def msa_data_path(root, name):
    """Managed native path, otherwise latest valid AF3 timestamp/native/flat.

    A present but invalid canonical file still blocks a flat fallback. A managed
    record always pins its canonical file, including while running or failed.
    """
    native = msa_native_path(root, name)
    record = msa_record_path(root, name)
    if os.path.exists(record):
        return native
    for path in _msa_timestamp_paths(root, name):
        if validate_msa(path):
            return path
    if os.path.exists(native):
        return native
    flat = _msa_storage_path(root, name, name + "_data.json")
    return flat if os.path.exists(flat) else native


def msa_ready(root, name, entity=None):
    """A managed output becomes readable only after the pipeline reports success."""
    record_path = msa_record_path(root, name)
    path = msa_data_path(root, name)
    if os.path.exists(record_path):
        record = read_json(record_path)
        if not isinstance(record, dict) or record.get("status") != "complete":
            return False
        path = msa_native_path(root, name)
        if not _msa_matches_input(path, name, record.get("input_signature")):
            return False
    return validate_msa(path, entity)


def _msa_record_lock(root, name):
    name = _msa_storage_name(name)
    return file_lock(_msa_storage_path(root, name, ".msa_records", name + ".lock"), timeout=10)


def _required_msa_record(root, name):
    record = read_json(msa_record_path(root, name))
    if not isinstance(record, dict) or record.get("name") != name or not record.get("fingerprint"):
        raise ValueError("MSA name must be reserved before running: " + name)
    return record


def reserve_msa(root, name, fingerprint):
    """Claim a name once, without changing keys or replacing existing products."""
    if not isinstance(fingerprint, str) or not fingerprint:
        raise ValueError("MSA fingerprint must be a nonempty string")
    record_path = msa_record_path(root, name)
    with _msa_record_lock(root, name):
        if os.path.exists(record_path):
            record = _required_msa_record(root, name)
            if record["fingerprint"] != fingerprint:
                raise ValueError("MSA name is reserved for different inputs; rebuild the plan: " + name)
            return record
        native = msa_native_path(root, name)
        flat = _msa_storage_path(root, name, name + "_data.json")
        if os.path.exists(native) or os.path.exists(flat):
            raise ValueError("Existing MSA has no identity record; use a different planned name: " + name)
        now = time.time()
        record = dict(name=name, fingerprint=fingerprint, status="reserved", created_at=now, updated_at=now)
        atomic_json(record_path, record)
        return record


def _msa_input_signature(data, name):
    """Compare all polymer chains, accepting AF3's equivalent ID list encoding."""
    if not isinstance(data, dict) or data.get("name") != name:
        raise ValueError("MSA JSON name does not match its planned output: " + name)
    sequences = data.get("sequences")
    if not isinstance(sequences, list) or not sequences:
        raise ValueError("MSA JSON has no sequences")
    chains = []
    for entry in sequences:
        if not isinstance(entry, dict) or len(entry) != 1:
            raise ValueError("Invalid MSA sequence entry")
        kind, body = next(iter(entry.items()))
        if kind not in ("protein", "rna", "dna", "ligand") or not isinstance(body, dict):
            raise ValueError("Invalid MSA sequence type")
        ids = body.get("id")
        ids = [ids] if isinstance(ids, str) else ids
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
            raise ValueError("Invalid MSA chain IDs")
        if kind in ("protein", "rna", "dna"):
            sequence = body.get("sequence")
            if not isinstance(sequence, str) or not sequence:
                raise ValueError("Missing polymer sequence in MSA JSON")
            chains.extend((kind, chain, sequence) for chain in ids)
        else:
            # Ligands do not undergo alignment, but still belong to raw JSON jobs.
            chains.extend((kind, chain, "") for chain in ids)
    return digest({"name": name, "chains": sorted(chains)}, 64)


def _msa_matches_input(path, name, signature):
    if not validate_msa(path):
        return False
    try:
        return _msa_input_signature(_MSA_SCHEMA_CACHE.load(path), name) == signature
    except (OSError, ValueError, TypeError):
        return False


def msa_start(root, name, inputpath):
    """Return True to run AF3 or False to reuse a complete, matching output."""
    signature = _msa_input_signature(read_json(inputpath), name)
    with _msa_record_lock(root, name):
        record = _required_msa_record(root, name)
        if record.get("input_signature") not in (None, signature):
            raise ValueError("MSA input changed after this name was reserved: " + name)
        native = msa_native_path(root, name)
        if record.get("status") == "complete" and _msa_matches_input(native, name, signature):
            return False
        directory = Path(native).parent
        if os.path.lexists(directory) and (not directory.is_dir() or next(directory.iterdir(), None) is not None):
            raise ValueError("MSA output directory already contains data; refusing to overwrite it. "
                             "Retry in the GUI to create a new MSA directory: " + str(directory))
        record.update(status="running", input_signature=signature, updated_at=time.time())
        record.pop("error", None)
        record.pop("exit_code", None)
        atomic_json(msa_record_path(root, name), record)
        return True


def msa_finish(root, name, inputpath, exit_code):
    """Publish readiness, never move, rename or delete the AF3 output directory."""
    exit_code = int(exit_code)
    with _msa_record_lock(root, name):
        record = _required_msa_record(root, name)
        if record.get("status") != "running":
            raise ValueError("MSA output cannot be completed before its run starts: " + name)
        ready = False
        error = "AF3 data pipeline exited with status " + str(exit_code)
        if exit_code == 0:
            error = "AF3 output is missing, invalid or does not match the input"
            try:
                signature = _msa_input_signature(read_json(inputpath), name)
                ready = (record.get("input_signature") == signature and
                         _msa_matches_input(msa_native_path(root, name), name, signature))
            except ValueError as exc:
                error = str(exc)
        record.update(status="complete" if ready else "failed", exit_code=exit_code, updated_at=time.time())
        if ready:
            record.pop("error", None)
        else:
            record["error"] = error
        atomic_json(msa_record_path(root, name), record)
        return ready


def msa_replicate(root, name, destinations):
    """Back up one completed product, preserving primary readiness on copy failure."""
    if __name__ == "__main__":
        sys.modules.setdefault("af3_runtime", sys.modules[__name__])
    import af3_msa_sync
    if not isinstance(destinations, list) or len(destinations) > 2:
        raise ValueError("Expected up to two MSA backup directories")
    if not msa_ready(root, name):
        raise ValueError("Only a complete MSA product can be backed up: " + name)
    try:
        result = af3_msa_sync.replicate_product(root, name, destinations)
    except (OSError, ValueError, TimeoutError) as exc:
        result = dict(copied=0, skipped=0, conflicts=[], errors=[str(exc)])
    receipt = dict(result, name=name, destinations=destinations, updated_at=time.time(),
                   status="incomplete" if result["errors"] or result["conflicts"] else "complete")
    atomic_json(Path(root) / ".msa_backup_receipts" / (name + ".json"), receipt)
    return receipt


def snapshot(source, workdir, config):
    """Pin code and complete config before any cluster side effect."""
    target = Path(workdir) / ".runtime"
    target.mkdir(parents=True, exist_ok=True)
    source_root = Path(source).parent
    names = ["af3.py", "af3_runtime.py", "af3_msa_sync.py", "af3_pae.py",
             "af3_networkx_community.py", "af3_pae_domains.py",
             "LICENSE", "THIRD_PARTY_NOTICES.md"]
    licenses = source_root / "LICENSES"
    if not licenses.is_dir():
        raise ValueError("Installation is incomplete: LICENSES directory is missing")
    names.extend(str(p.relative_to(source_root)) for p in licenses.rglob("*") if p.is_file())
    for name in names:
        src = source_root / name; dst = target / name
        if not src.is_file():
            raise ValueError("Installation is incomplete: " + name)
        if dst.exists() and dst.read_bytes() != src.read_bytes():
            raise ValueError("该批次绑定旧版本；请使用新批次名，或从原计划重试")
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))
    cfg = target / "config.json"
    if not cfg.exists(): atomic_json(cfg, config)
    return str(target / "af3.py"), str(cfg)


def task_path(root, name):
    return str(Path(root) / ".tasks" / (name + ".json"))


def record_submission(root, name, job_id, input_path, tag):
    path = task_path(root, name)
    with file_lock(path + ".lock"):
        old = read_json(path, {})
        history = old.get("attempts", [])
        history.append({"job_id": str(job_id), "submitted_at": time.time()})
        record = dict(name=name, tag=tag, job_id=str(job_id), input=os.path.abspath(input_path),
                      status="submitted", submitted_at=time.time(), attempts=history)
        atomic_json(path, record)
    return record


def valid_result(root, name):
    folder = Path(root) / name
    if not folder.is_dir(): return False
    # Validate the actual job's best model and summary, not arbitrary leftover files.
    cif = folder / (name + "_model.cif")
    summary = read_json(folder / (name + "_summary_confidences.json"))
    if not cif.is_file() or not cif.stat().st_size or not isinstance(summary, dict): return False
    return any(isinstance(summary.get(k), (int, float)) and math.isfinite(summary[k])
               for k in ("ranking_score", "ptm", "iptm"))


class SchedulerUnavailable(RuntimeError):
    pass


_QUEUE_CACHE = None


def job_id_matches(actual, expected):
    """An array master owns its elements; an element never owns its siblings."""
    actual, expected = str(actual).split(";", 1)[0], str(expected).split(";", 1)[0]
    return actual == expected or ("_" not in expected and actual.split("_", 1)[0] == expected)


def permanent_queue_error(message):
    text = message.lower()
    return any(token in text for token in ("unrecognized option", "unrecognised option", "unknown option", "invalid option", "no such file or directory"))


def queue_snapshot(force=False):
    global _QUEUE_CACHE
    if not force and _QUEUE_CACHE and time.monotonic() - _QUEUE_CACHE[0] < 2:
        return _QUEUE_CACHE[1]
    try:
        # Numeric UID avoids environment-derived usernames; -u also works on
        # the older Slurm installed on our cluster (which has no --me).
        user = str(os.getuid()) if hasattr(os, "getuid") else __import__("getpass").getuser()
        r = subprocess.run(["squeue", "-u", user, "-r", "-h", "-o", "%i|%j|%T"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SchedulerUnavailable(str(exc))
    if r.returncode:
        raise SchedulerUnavailable(r.stderr.strip() or "squeue failed")
    jobs = {}
    for line in r.stdout.splitlines():
        if not line.strip(): continue
        cols = line.split("|", 2)
        if len(cols) != 3 or not cols[0].strip() or not cols[2].strip():
            raise SchedulerUnavailable("Unexpected squeue output: " + line[:200])
        jobs[cols[0].strip()] = (cols[1].strip(), cols[2].strip())
    _QUEUE_CACHE = (time.monotonic(), jobs)
    return jobs


def task_state(root, name, queue=None):
    rec = read_json(task_path(root, name), {})
    if not rec:
        return "succeeded" if valid_result(root, name) else "waiting", ""
    jid = rec["job_id"]
    # A retry file is written by the failed attempt before its shell exits.
    seen = set()
    submitted_at = rec.get("submitted_at", 0)
    while jid not in seen:
        seen.add(jid)
        retry = Path(root) / ".attempts" / name / (jid + ".retry")
        if not retry.exists(): break
        submitted_at = max(submitted_at, retry.stat().st_mtime)
        jid = retry.read_text().strip().split(";")[0]
    observation = Path(root) / '.attempts' / name / (jid + '.missing.json')
    if queue is not None and jid in queue:
        scheduler_state=queue[jid][1]
        if scheduler_state.startswith('CANCELLED'):return 'cancelled','Job %s: %s' % (jid,scheduler_state)
        if scheduler_state in ('TIMEOUT','FAILED','NODE_FAIL','OUT_OF_MEMORY','BOOT_FAIL','DEADLINE'):
            return 'failed','Job %s: %s' % (jid,scheduler_state)
        if observation.exists(): observation.unlink()
        return ("running" if queue[jid][1] in ('RUNNING', 'COMPLETING') else "queued"), jid
    receipt = Path(root) / ".attempts" / name / (jid + ".exit")
    if receipt.exists():
        code = receipt.read_text().strip()
        if code == "0" and valid_result(root, name): return "succeeded", jid
        return "failed", "Job %s exit=%s; logs/infer" % (jid, code)
    if queue is None:
        return "unknown", "调度状态暂不可用"
    # A long running job gets the same visibility grace as a short job.
    with file_lock(str(observation) + '.lock'):
        observed = read_json(observation)
        if observed is None:
            observed = {'first_missing_at': time.time()}
            atomic_json(observation, observed)
    if time.time() - observed['first_missing_at'] < 120: return "confirming", jid
    return "failed", "Job %s 已离开队列且无成功回执（取消、超时或异常终止）" % jid


def finish_attempt(root, name, jid, code):
    if code == 0 and not valid_result(root, name): code = 1
    atomic_text(Path(root) / ".attempts" / name / (jid + ".exit"), str(code) + "\n")
    return code


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("msa-check", "msa-start", "msa-finish", "msa-replicate"):
        command = sys.argv[1]
        try:
            arguments = sys.argv[2:]
            expected = {"msa-check": 2, "msa-start": 3, "msa-finish": 4, "msa-replicate": 3}[command]
            if len(arguments) == expected + 2 and arguments[-2] == "--assets":
                if not arguments[-1].strip():
                    raise ValueError("--assets requires a host asset directory")
                ASSET_ROOT = os.path.abspath(os.path.expanduser(arguments[-1]))
                arguments = arguments[:-2]
            if len(arguments) != expected:
                raise ValueError("Usage: af3_runtime.py " + command + " ROOT NAME" +
                                 (" INPUT" if command != "msa-check" else "") +
                                 (" STATUS" if command == "msa-finish" else "") +
                                 " [--assets HOST_ASSET_DIR]")
            if command == "msa-check":
                sys.exit(0 if msa_ready(arguments[0], arguments[1]) else 1)
            if command == "msa-start":
                sys.exit(0 if msa_start(arguments[0], arguments[1], arguments[2]) else 3)
            if command == "msa-replicate":
                result = msa_replicate(arguments[0], arguments[1], json.loads(arguments[2]))
                print(json.dumps(result, ensure_ascii=False))
                sys.exit(0 if result["status"] == "complete" else 1)
            sys.exit(0 if msa_finish(arguments[0], arguments[1], arguments[2], int(arguments[3])) else 1)
        except (OSError, ValueError, TimeoutError) as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)
    if len(sys.argv)>1 and sys.argv[1]=="validate-msa": sys.exit(0 if validate_msa(sys.argv[2]) else 1)
    if len(sys.argv)>1 and sys.argv[1] == "finish":
        sys.exit(finish_attempt(sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])))
