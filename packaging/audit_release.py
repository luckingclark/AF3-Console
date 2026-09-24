#!/usr/bin/env python3
"""Audit public text and decoded GUI sources without importing the application."""
import argparse
import ast
import base64
import hashlib
import json
from pathlib import Path
import re
import sys
import zlib

ROOT = Path(__file__).resolve().parents[1]
ACCESSION = re.compile(r"(?<![A-Za-z0-9])(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})(?=$|[^A-Za-z0-9]|x[0-9])")
ALLOWED_IDS = {"P12345", "Q12345"}
TEXT_SUFFIXES = {".py", ".md", ".mmd", ".svg", ".txt", ".json", ".yml", ".yaml", ".cff", ".sh", ".html"}
SECRET_PATTERNS = [re.compile(x) for x in (
    r"gh[pousr]_[A-Za-z0-9]{30,}", r"github_pat_[A-Za-z0-9_]{40,}",
    r"AKIA[0-9A-Z]{16}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"sk-[A-Za-z0-9]{32,}")]
SITE_PATTERNS = [re.compile(x, re.I) for x in (
    r"/work" r"/home/", r"/opt" r"/ohpc/", r"/ssd" r"cache/", r"[A-Z]:[/\\]Users[/\\]",
    r"[A-Z]:[/\\]Projects[/\\]", r"\b[A-Z0-9]+_(?:HUMAN|MOUSE|RAT)\b")]


def manifest(root=ROOT):
    data = json.loads((root / "packaging/release_files.json").read_text(encoding="utf-8"))
    for group in ("source", "runtime"):
        paths = data[group]
        if len(paths) != len(set(paths)):
            raise ValueError("Duplicate release entry: " + group)
        for relative in paths:
            p = Path(relative)
            if p.is_absolute() or ".." in p.parts or relative.replace("\\", "/") != relative:
                raise ValueError("Unsafe release entry: " + relative)
    return data


def decoded_gui(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ("_SOURCES", "SOURCE_HASHES"):
                    values[target.id] = ast.literal_eval(node.value)
    if set(values) != {"_SOURCES", "SOURCE_HASHES"}:
        raise ValueError("GUI payload or hashes missing")
    decoded = {name + ".py": zlib.decompress(base64.b64decode(value)).decode("utf-8")
               for name, value in values["_SOURCES"].items()}
    if set(decoded) != set(values["SOURCE_HASHES"]):
        raise ValueError("GUI source/hash names differ")
    for name, text in decoded.items():
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != values["SOURCE_HASHES"][name]:
            raise ValueError("GUI embedded hash mismatch: " + name)
    return decoded


def text_for_scan(relative, text):
    # The only opaque source asset is the embedded cat PNG. Random image bytes
    # are not prose or protein identifiers; its metadata is inspected separately.
    if Path(relative).name == "af3_ui_brand.py":
        tree = ast.parse(text)
        lines = text.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) > 1000:
                for i in range(node.lineno - 1, node.end_lineno):
                    lines[i] = ""
        return "\n".join(lines)
    return text


def wrapper_text(path):
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    for node in ast.parse(text).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in
                ("_SOURCES", "SOURCE_HASHES") for t in node.targets):
            for i in range(node.lineno - 1, node.end_lineno):
                lines[i] = ""
    return "\n".join(lines)


def read_public_text(path):
    data = path.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # Preserve exact upstream notice files and hashes; some old font
        # notices predate UTF-8. The fallback is for inspection, not rewriting.
        if path.parent.name == "LICENSES":
            return data.decode("latin-1")
        raise


def audit_text(relative, text, forbidden=()):
    issues = []
    for number, line in enumerate(text_for_scan(relative, text).splitlines(), 1):
        if any(m.group() not in ALLOWED_IDS for m in ACCESSION.finditer(line)):
            issues.append(f"{relative}:{number}: non-placeholder accession")
        if any(p.search(line) for p in SECRET_PATTERNS):
            issues.append(f"{relative}:{number}: possible credential")
        if any(p.search(line) for p in SITE_PATTERNS):
            issues.append(f"{relative}:{number}: site/research identifier")
        if any(term and term.casefold() in line.casefold() for term in forbidden):
            issues.append(f"{relative}:{number}: private denylist match")
    return issues


def audit(root=ROOT, files=None, forbidden=()):
    root = Path(root).resolve()
    files = files if files is not None else manifest(root)["source"]
    issues = []
    for relative in files:
        issues.extend(audit_text(relative + "::filename", relative, forbidden))
        path = root / relative
        if not path.is_file():
            issues.append(relative + ": missing release file")
            continue
        if path.is_symlink() or root not in path.resolve().parents:
            issues.append(relative + ": symlink or external release file")
            continue
        if relative == "af3_gui":
            try:
                issues.extend(audit_text(relative + "::wrapper", wrapper_text(path), forbidden))
                for name, text in decoded_gui(path).items():
                    issues.extend(audit_text(relative + "::module-name", name, forbidden))
                    issues.extend(audit_text("af3_gui::" + name, text, forbidden))
                    source = root / name
                    if source.exists() and source.read_text(encoding="utf-8") != text:
                        issues.append(name + ": generated GUI is stale")
            except (ValueError, SyntaxError, zlib.error) as exc:
                issues.append("af3_gui: " + str(exc))
        elif path.suffix in TEXT_SUFFIXES or relative in {"LICENSE", ".gitignore", ".gitattributes"}:
            issues.extend(audit_text(relative, read_public_text(path), forbidden))
    return issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--private-denylist", type=Path,
                        help="Optional local JSON string list; never include it in a release")
    args = parser.parse_args()
    terms = json.loads(args.private_denylist.read_text(encoding="utf-8")) if args.private_denylist else []
    issues = audit(args.root, forbidden=terms)
    if issues:
        print("\n".join(issues), file=sys.stderr)
        return 1
    print("Public release audit passed: allowed files, text, decoded GUI, and source hashes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
