#!/usr/bin/env python3
"""Render the editable Mermaid workflow as a self-contained presentation SVG.

Uses only the Python standard library; no external fonts, scripts or images.
Copyright (c) 2026 PKU-Gaolab, Ming-Ao Lu. SPDX-License-Identifier: MIT
"""
import argparse
from html import escape
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "docs" / "images"
NODES = {"inputs", "run", "pulldown", "scan", "window_scan", "pae_scan",
         "prepare_msa", "predict", "results"}
NODE_PATTERN = r'(\w+)\(?\["([^"]+)"\]\)?'
EDGES = {("inputs", "run"), ("inputs", "pulldown"), ("inputs", "scan"),
         ("scan", "window_scan"), ("scan", "pae_scan"), ("run", "prepare_msa"),
         ("pulldown", "prepare_msa"), ("window_scan", "prepare_msa"),
         ("pae_scan", "prepare_msa"), ("prepare_msa", "predict"), ("predict", "results")}


def build(language, check=False):
    source = (IMAGES / f"workflow-{language}.mmd").read_text(encoding="utf-8")
    labels = {key: value.split("<br/>") for key, value in
              re.findall(NODE_PATTERN, source)}
    edges = set(re.findall(r'(\w+)\s*-->\s*(\w+)', re.sub(NODE_PATTERN, r'\1', source)))
    if set(labels) != NODES or edges != EDGES:
        raise ValueError("Workflow graph changed; update the presentation layout too")
    title = re.search(r"accTitle: (.+)", source).group(1)
    description = re.search(r"accDescr: (.+)", source).group(1)
    zh = language == "zh-CN"
    svg = [f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="848" viewBox="0 0 1200 848" role="img" aria-labelledby="title description">
<title id="title">{escape(title)}</title>
<desc id="description">{escape(description)}</desc>
<defs>
  <linearGradient id="canvas" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#fbfbfd"/><stop offset="1" stop-color="#f0f2f7"/>
  </linearGradient>
  <linearGradient id="scan-card" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#f5fbfa"/>
  </linearGradient>
  <filter id="shadow" x="-15%" y="-20%" width="130%" height="150%">
    <feDropShadow dx="0" dy="10" stdDeviation="13" flood-color="#1c2748" flood-opacity="0.055"/>
  </filter>
  <marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
    <path d="M1 1 L6 4 L1 7" fill="none" stroke="#a3a9b5" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
  </marker>
</defs>
<style>
  text {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif; }}
  .wire {{ fill:none;stroke:#c1c6d0;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round; }}
</style>
<rect x="1" y="1" width="1198" height="846" rx="36" fill="url(#canvas)" stroke="#e5e7ee"/>
''']

    def text(x, y, value, size=22, fill="#1d1d1f", weight=400, anchor="start", spacing=None):
        tracking = f' letter-spacing="{spacing}"' if spacing is not None else ""
        svg.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{tracking}>{escape(value)}</text>')

    def rect(x, y, width, height, radius=24, fill="#ffffff", stroke="#e9ebf0", shadow=False):
        effect = ' filter="url(#shadow)"' if shadow else ""
        svg.append(f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="{radius}" fill="{fill}" stroke="{stroke}"{effect}/>')

    def wire(data, arrow=False):
        marker = ' marker-end="url(#arrow)"' if arrow else ""
        svg.append(f'<path class="wire" d="{data}"{marker}/>')

    def wrapped(value, width, size):
        # Approximate system-font widths, keeping words intact in English.
        tokens = list(value) if zh else value.split(" ")
        lines, line = [], ""
        for token in tokens:
            candidate = line + ("" if zh or not line else " ") + token
            units = sum(1 if ord(c) > 255 else 0.53 for c in candidate)
            if line and units * size > width:
                lines.append(line)
                line = token
            else:
                line = candidate
        return lines + [line]

    text(64, 59, "AF3 CONSOLE", 17, "#777c88", 600, spacing=2.5)
    text(1136, 59, "模式总览" if zh else "WORKFLOW OVERVIEW", 16, "#8b909c", 500, "end", 1)
    rect(368, 100, 464, 66, 33, shadow=True)
    text(600, 141, labels["inputs"][0], 25, weight=550, anchor="middle")
    wire("M600 166 V198")
    wire("M232 224 V214 Q232 198 248 198 H952 Q968 198 968 214 V224", True)
    wire("M232 214 V224", True)
    wire("M600 198 V224", True)

    accents = {"run": "#007aff", "pulldown": "#a44ed6", "scan": "#139b91"}
    for key, x in (("run", 64), ("pulldown", 432), ("scan", 800)):
        svg.append(f'<g id="{key}">')
        rect(x, 232, 336, 300, 28, fill="url(#scan-card)" if key == "scan" else "#ffffff", shadow=True)
        accent = accents[key]
        svg.append(f'<rect x="{x+28}" y="260" width="28" height="4" rx="2" fill="{accent}"/>')
        text(x+28, 315, labels[key][0], 42, weight=650)
        size = 21 if key == "scan" else 23
        for row, line in enumerate(wrapped(" ".join(labels[key][1:]), 282, size)):
            text(x+28, 353+row*31, line, size, "#777c87")
        # Restrained geometric symbols distinguish the three choices.
        cx, cy = x+281, 282
        if key == "run":
            svg.append(f'<g fill="none" stroke="{accent}" stroke-width="2"><path d="M{cx-9} {cy+6} L{cx+9} {cy-6}"/><circle cx="{cx-12}" cy="{cy+8}" r="6"/><circle cx="{cx+12}" cy="{cy-8}" r="6"/></g>')
        elif key == "pulldown":
            for dx in (-8, 8):
                for dy in (-8, 8):
                    svg.append(f'<rect x="{cx+dx-4}" y="{cy+dy-4}" width="8" height="8" rx="2.5" fill="{accent}" opacity="0.8"/>')
        else:
            svg.append(f'<g fill="none" stroke="{accent}" stroke-width="2.5" stroke-linecap="round"><path d="M{cx-13} {cy-8} H{cx+13} M{cx-13} {cy+8} H{cx+13}"/><path d="M{cx-5} {cy-13} V{cy-3} M{cx+5} {cy+3} V{cy+13}"/></g>')
        svg.append('</g>')

    # Scan's alternatives live inside the Scan card, not as a fourth mode.
    for key, y in (("window_scan", 379), ("pae_scan", 450)):
        svg.append(f'<g id="{key}">')
        rect(824, y, 288, 58, 15, "#f2f7f7", "#e6eeee")
        text(842, y+25, labels[key][0], 21, "#246f6a", 550)
        text(842, y+46, " ".join(labels[key][1:]), 17, "#788988")
        svg.append('</g>')

    wire("M232 532 V556 Q232 578 254 578 H946 Q968 578 968 556 V532")
    wire("M600 532 V610", True)
    rect(64, 620, 1072, 174, 28, shadow=True)
    text(600, 653, "共用预测流程" if zh else "SHARED PREDICTION WORKFLOW", 16, "#9095a1", 500, "middle", 1.2)
    for key, x in (("prepare_msa", 238), ("predict", 600), ("results", 962)):
        svg.append(f'<g id="{key}">')
        text(x, 708, labels[key][0], 27, "#007aff" if key == "prepare_msa" else "#1d1d1f", 600, "middle")
        for row, line in enumerate(wrapped(" ".join(labels[key][1:]), 295, 19)):
            text(x, 741+row*24, line, 19, "#818692", anchor="middle")
        svg.append('</g>')
    wire("M400 710 H440", True)
    wire("M762 710 H802", True)
    svg.append('</svg>')
    target = IMAGES / f"workflow-{language}.svg"
    content = "\n".join(svg) + "\n"
    if check:
        if not target.is_file() or target.read_text(encoding="utf-8") != content:
            raise ValueError("Workflow SVG is stale; run packaging/build_workflow_diagram.py")
    else:
        target.write_text(content, encoding="utf-8", newline="\n")
    print(target.relative_to(ROOT).as_posix())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify generated SVGs without writing files")
    args = parser.parse_args()
    for language in ("en", "zh-CN"):
        build(language, args.check)
