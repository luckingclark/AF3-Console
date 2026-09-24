#!/usr/bin/env python3
"""Build deterministic source/runtime ZIPs from a reviewed allowlist, never live config."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys
import zipfile

sys.dont_write_bytecode = True
from audit_release import ROOT, audit, manifest


def version(root):
    for node in ast.parse((root / "af3_runtime.py").read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "VERSION" for t in node.targets):
            value = ast.literal_eval(node.value)
            if not isinstance(value, str) or not value or any(c not in "0123456789.-abcdefghijklmnopqrstuvwxyz" for c in value):
                raise ValueError("Invalid version")
            return value
    raise ValueError("VERSION missing")


def write_zip(path, members):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(members.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o100755 if name.endswith("/af3_gui") or name.endswith(".sh") else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)


def build(root, output):
    root = root.resolve()
    entries = manifest(root)
    issues = audit(root)
    if issues:
        raise ValueError("Release audit failed:\n" + "\n".join(issues))
    if not set(entries["runtime"]).issubset(entries["source"]):
        raise ValueError("Runtime entries must be included in source distribution")
    output.mkdir(parents=True, exist_ok=True)
    release = version(root)
    sums = []
    for kind in ("source", "runtime"):
        prefix = f"af3-console-{release}-{kind}"
        data = {name: (root / name).read_bytes() for name in entries[kind]}
        hashes = {name: hashlib.sha256(value).hexdigest() for name, value in sorted(data.items())}
        metadata = {"project": "AF3 Console", "version": release, "kind": kind, "files": hashes}
        raw = (json.dumps(metadata, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        members = {prefix + "/" + name: value for name, value in data.items()}
        members[prefix + "/manifest.json"] = raw
        archive = output / (prefix + ".zip")
        write_zip(archive, members)
        report = output / (prefix + ".manifest.json")
        report.write_bytes(raw)
        for path in (archive, report):
            sums.append(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name)
        print(archive.name, len(hashes), "files", archive.stat().st_size, "bytes")
    (output / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    build(args.root, args.output or args.root / "dist")


if __name__ == "__main__":
    main()
