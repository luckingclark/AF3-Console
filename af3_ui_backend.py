#!/usr/bin/env python3
# ==============================================================================
# af3_ui_backend.py - AF3 UI 的「无 UI 依赖」共享后端
# ==============================================================================
# 桌面版发布时由 merge_gui.py 与其他 UI 模块合并成 af3_gui。
# 这里只放拼命令、调用 af3.py、读取产物和解析表达式的逻辑，
# 不导入 PySide6，便于 CLI 与 GUI 共享行为和独立测试。
#
# 所有函数都是纯函数或只触文件系统/子进程;缓存(如有)由前端各自负责。
# ==============================================================================

import contextlib
import copy
import uuid
import threading
import af3_runtime as R
import af3_pae as PAE
import csv
import glob
import html as _html_mod
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request

# ----------------------------------------------------------------------------
# af3 模块(同目录)——只读其常量与纯解析函数
# ----------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    import af3  # noqa: E402
except Exception as _e:  # 导入失败时降级:仅子进程可用,本地预览不可用
    af3 = None
    AF3_IMPORT_ERR = str(_e)
else:
    AF3_IMPORT_ERR = ""

HOST_OUTPUT = getattr(af3, "HOST_OUTPUT", os.path.join(_HERE, "output"))
HOST_MSA_DATA = getattr(af3, "HOST_MSA_DATA", os.path.join(_HERE, "msa_data"))
HOST_SPECS = getattr(af3, "HOST_SPECS", os.path.join(HOST_OUTPUT, ".specs"))
# 部署根目录(标题栏显示用)与 GUI 下载默认目录(集中好找,SFTP 面板直达)
HOST_BASE = getattr(af3, "HOST_BASE", "") or os.path.dirname(HOST_OUTPUT.rstrip("/\\"))
DOWNLOADS_DIR = os.path.join(HOST_BASE, "downloads")
HOST_INFER_DATA = getattr(af3, "HOST_INFER_DATA", HOST_BASE + "/infer_data")

AF3_PY = os.path.join(_HERE, "af3.py")
PYTHON = sys.executable or "python3"
UI_TMP_DIR = os.path.join(getattr(af3,"HOST_CACHE",HOST_BASE), "ui_inputs")


# ============================================================================
# 子进程:调 af3.py(UTF-8 收发,跨 locale 稳健)
# ============================================================================

def cmdline_str(args):
    """命令列表 -> 可读 shell 串(展示用)。"""
    return " ".join(shlex.quote(str(a)) for a in [PYTHON, AF3_PY] + list(args))


def run_af3(args, timeout=900):
    """执行 af3.py <args>。返回 (returncode, stdout, stderr)。
    af3.py 输出含中文:强制 UTF-8 收发,避免登录节点非 UTF-8 locale / Windows GBK 崩溃。"""
    cmd = [PYTHON, AF3_PY] + [str(a) for a in args]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=timeout, cwd=_HERE, env=env)
        return r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", f"命令超时(>{timeout}s):{cmdline_str(args)}"
    except FileNotFoundError as e:
        return 127, "", f"无法执行 Python 解释器 '{PYTHON}':{e}"
    except Exception as e:
        return 126, "", f"执行失败:{e}"


def format_run_result(rc, out, err, ok_msg="提交成功"):
    """纯函数:把执行结果整理成展示消息。
    返回 dict(ok, code, blocks=[(kind,text)]),kind ∈ {out, err, ok, bad};
    前端按 kind 自行渲染(st.success / st.error / messagebox / insert tag …)。"""
    blocks = []
    if out.strip():
        blocks.append(("out", out.strip()))
    if err.strip():
        blocks.append(("err", err.strip()[:4000]))
    if rc == 0:
        blocks.append(("ok", ok_msg))
    else:
        blocks.append(("bad", f"af3.py 退出码 {rc}(上方输出含错误原因与提示)"))
    return {"ok": rc == 0, "code": rc, "blocks": blocks}


# ============================================================================
# 文件 / 文本小工具
# ============================================================================

def ui_tmp_path(fname):
    folder = os.path.join(UI_TMP_DIR, uuid.uuid4().hex)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, os.path.basename(fname))


def write_tmp_lines(fname, lines):
    path = ui_tmp_path(fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def split_lines(text):
    """文本框 -> 非空、非注释行。"""
    return [l.strip() for l in (text or "").splitlines()
            if l.strip() and not l.strip().startswith("#")]


def sanitize_tag(s, maxlen=80):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", (s or ""))[:maxlen]


# ============================================================================
# 表达式解析(本地预览,不联网除非 fetch=True)
# ============================================================================

def capture_af3_parse(expr, fetch=False):
    """解析单个表达式;捕获 af3.die() 的输出。返回 (entities, error)。"""
    if af3 is None:
        return None, f"af3.py 未能导入(无法本地预览):{AF3_IMPORT_ERR}"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            return af3.parse_expression(expr, fetch=fetch), None
        except SystemExit:
            return None, buf.getvalue().strip() or "表达式解析失败"
        except Exception as e:
            return None, f"解析异常:{e}"


def entity_summary(ent):
    """entity dict -> 人类可读片段(供即时预览)。"""
    t = ent["type"]
    if t in ("protein", "rna", "dna"):
        if ent.get("uniprot"):
            src = f"{ent['uniprot']} (UniProt,提交时抓序列)"
        elif ent.get("sequence"):
            src = f"{t}:{ent['sequence'][:12]}…({len(ent['sequence'])} 字符)"
        else:
            src = t
    elif t == "ligand":
        src = "ligand:" + (",".join(ent["ccd"]) if ent.get("ccd")
                           else (ent.get("smiles") or "")[:24])
    else:
        src = t
    extra = []
    if ent.get("copies", 1) > 1:
        extra.append(f"x{ent['copies']}")
    if ent.get("trunc"):
        extra.append(f"trunc={ent['trunc']}")
    if ent.get("mut"):
        extra.append(f"mut={ent['mut']}")
    if ent.get("modifications"):
        extra.append(f"mod x{len(ent['modifications'])}")
    return src + ("(" + ",".join(extra) + ")" if extra else "")


def entity_to_expr(ent):
    """entity dict -> 表达式串(重提用)。完整往返:拷贝数 xN、配体/DNA/RNA、
    :mod=/:msa=/:pairedmsa=/:tpl=/:desc= 都还原。
    UniProt 保留 :trunc=/:mut=(MSA key 语义不变);裸序列直接用(可能已截短的)
    序列,不再附 trunc。"""
    t = ent.get("type")
    copies = ent.get("copies", 1)
    suffix = f"x{copies}" if copies > 1 else ""
    opts = []

    if t == "protein":
        if ent.get("uniprot"):
            src = ent["uniprot"]
            if ent.get("trunc"):
                opts.append("trunc=" + ent["trunc"])
            if ent.get("mut"):
                opts.append("mut=" + ent["mut"])
        elif ent.get("sequence"):
            src = "p:" + ent["sequence"]
        else:
            return None
    elif t == "rna":
        if ent.get("sequence"):
            src = "r:" + ent["sequence"]
        else:
            return None
    elif t == "dna":
        if ent.get("sequence"):
            src = "d:" + ent["sequence"]
        else:
            return None
    elif t == "ligand":
        if ent.get("ccd"):
            src = "l:" + ",".join(ent["ccd"])
        elif ent.get("smiles"):
            src = "l:" + ent["smiles"]   # l: 前缀防 SMILES 中 ':' 歧义
        else:
            return None
    else:
        return None

    # 修饰:protein 用 ptmType/ptmPosition,rna/dna 用 modificationType/basePosition
    mods = ent.get("modifications") or []
    if mods:
        items = []
        for m in mods:
            ccd = m.get("ptmType") or m.get("modificationType")
            pos = m.get("ptmPosition") or m.get("basePosition")
            if ccd and pos:
                items.append(f"{ccd}@{pos}")
        if items:
            opts.append("mod=" + ",".join(items))
    if ent.get("msa_path"):
        opts.append("msa=" + ent["msa_path"])
    if ent.get("paired_msa") is not None:
        opts.append("pairedmsa=" + ent["paired_msa"])   # 空串 = 显式空 paired MSA
    elif ent.get("paired_msa_path"):
        opts.append("pairedmsa=" + ent["paired_msa_path"])
    if ent.get("template"):
        tpl = ent["template"]
        q = ",".join(str(i) for i in tpl.get("queryIndices", []))
        ti = ",".join(str(i) for i in tpl.get("templateIndices", []))
        opts.append(f"tpl={tpl.get('mmcifPath', '')}:{q}:{ti}")
    if ent.get("name"):
        opts.append("name=" + ent["name"])
    if ent.get("desc"):
        opts.append("desc=" + ent["desc"])
    return (src + suffix + ((":" + ":".join(opts)) if opts else ""))


# ============================================================================
# 命令拼装(表单值 -> af3.py 参数列表)
# ============================================================================

def append_common_args(args, opts, include_bonds=True):
    if opts.get("num_seeds", 0) > 0:
        args += ["--num-seeds", str(opts["num_seeds"])]
    if opts.get("seeds"):
        args += ["--seeds", opts["seeds"]]
    if opts.get("partition"):
        args += ["--partition", opts["partition"]]
    if opts.get("msa_partition"):
        args += ["--msa-partition", opts["msa_partition"]]
    if opts.get("max_conc", 0) > 0:
        args += ["--max-concurrent", str(opts["max_conc"])]
    if opts.get("template_free"):
        args.append("--template-free")
    if opts.get("msa_free"):
        args.append("--msa-free")
    if include_bonds and opts.get("bonds"):
        args += ["--bonds", opts["bonds"]]
    if include_bonds and opts.get("user_ccd"):
        args += ["--user-ccd", opts["user_ccd"]]
    if opts.get("force"):
        args.append("--force")
    return args


def build_run_args(lines, mode="end2end", json_path=None, name="", opts=None,
                   dry_run=False, user_tag="", pae_params=None):
    """mode: 'end2end' | 'msa_only' | 'infer_only' | 'pae'。返回 (args, error)。"""
    opts = opts or {}
    if mode == "pae":
        # 单蛋白 PAE domain 分段(af3.py pae):缺 confidences 自动提交单体预测
        if json_path is not None:
            return None, "pae 模式不使用 --json"
        if not lines:
            return None, "请先填写任务表达式"
        if len(lines) != 1:
            return None, "pae 模式只接受单个蛋白(一行表达式)"
        pp = pae_params or {}
        args = ["pae", lines[0],
                "--pae-cutoff", str(pp.get("pae_cutoff", 5)),
                "--pae-resolution", str(pp.get("pae_resolution", 0.5)),
                "--pae-min-domain", str(pp.get("pae_min_domain", 10)),
                "--pae-domains-per-window",
                str(pp.get("pae_domains_per_window", 1)),
                "--pae-min-frag", str(pp.get("pae_min_frag", 250))]
        pmf = pp.get("pae_max_frag") or 0
        if pmf:
            args += ["--pae-max-frag", str(pmf)]
        args = append_common_args(args, opts, include_bonds=False)
        if dry_run:
            args.append("--dry-run")
        return args, None
    args = ["run"]
    if mode == "msa_only":
        args.append("--msa-only")
    elif mode == "infer_only":
        args.append("--infer-only")
    if json_path is not None:
        if not str(json_path).strip():
            return None, "请填写 AF3 JSON 文件路径"
        args += ["--json", str(json_path).strip()]
    else:
        if not lines:
            return None, "请先填写任务表达式"
        if len(lines) == 1:
            target = lines[0]
        else:
            # 多行 -> 任务文件;默认名:有用户标签用 ui_<tag>,否则 af3_run_时间戳
            # (每次都是新目录;想续跑复用请显式命名)
            tag = name.strip() or ("ui_" + user_tag if user_tag
                                   else "af3_run_" + time.strftime("%Y%m%d-%H-%M"))
            target = write_tmp_lines(f"{sanitize_tag(tag)}.txt", lines)
        args.append(target)
    if name.strip() and (json_path is not None or len(lines or []) == 1):
        args += ["--name", name.strip()]
    args = append_common_args(args, opts, include_bonds=True)
    if dry_run:
        args.append("--dry-run")
    return args, None


def build_screen_args(stype, a_lines, b_lines, name="", topk=0, self_pairs=False,
                      scan_params=None, opts=None, dry_run=False, user_tag=""):
    """stype: 'pulldown' | 'scan'。scan_params: dict(win,overlap,min_frag,threshold)。
    返回 (args, error)。"""
    opts = opts or {}
    if not a_lines or not b_lines:
        return None, "A、B 两组都需要至少一个蛋白"
    tag = name.strip() or ("ui_" + user_tag if user_tag
                           else "af3_screen_" + time.strftime("%Y%m%d-%H-%M"))
    tag = sanitize_tag(tag)
    a_arg = a_lines[0] if len(a_lines) == 1 else write_tmp_lines(f"{tag}_A.txt", a_lines)
    b_arg = b_lines[0] if len(b_lines) == 1 else write_tmp_lines(f"{tag}_B.txt", b_lines)
    args = [stype, a_arg, b_arg]
    if name.strip():
        args += ["--name", tag]
    if topk and int(topk) > 0:
        args += ["--topk", str(int(topk))]
    if self_pairs:
        args.append("--self")
    if stype == "scan" and scan_params:
        smode = scan_params.get("mode", "win")
        args += ["--mode", smode,
                 "--split-threshold", str(scan_params.get("threshold", 500))]
        if smode == "pae":
            args += ["--pae-cutoff", str(scan_params.get("pae_cutoff", 5)),
                     "--pae-resolution",
                     str(scan_params.get("pae_resolution", 0.5)),
                     "--pae-min-domain",
                     str(scan_params.get("pae_min_domain", 10)),
                     "--pae-domains-per-window",
                     str(scan_params.get("pae_domains_per_window", 1)),
                     "--pae-min-frag",
                     str(scan_params.get("pae_min_frag", 250))]
            pmf = scan_params.get("pae_max_frag") or 0
            if pmf:
                args += ["--pae-max-frag", str(pmf)]
        else:
            args += ["--win", str(scan_params.get("win", 300)),
                     "--overlap", str(scan_params.get("overlap", 100)),
                     "--min-frag", str(scan_params.get("min_frag", 100))]
        if opts.get("shared_msa"):
            args.append("--shared-msa")
    args = append_common_args(args, opts, include_bonds=False)
    if dry_run:
        args.append("--dry-run")
    return args, None


# ============================================================================
# 默认批次名建议(#3:带上 UniProt id,Dashboard 一眼认出是什么任务)
# ============================================================================

def _first_id(lines):
    """清单里第一个像 UniProt accession 的行首 token(去掉 +: 之后的内容与 xN 拷贝数)。"""
    for ln in lines or []:
        tok = re.split(r"[+:x]", (ln or "").strip(), maxsplit=1)[0].strip()
        if _ACC_LABEL_RE.match(tok):
            return tok
    return ""


def suggest_screen_name(a_lines, b_lines):
    """pulldown/scan 默认批次名:'P12345x13_vs_Q12345x4'(A首id×A数 vs B首id×B数);
    单侧只有一个时不带数量:'P12345_vs_Q12345'。取不到 id 返回 ''(回退 af3_screen_时间戳)。"""
    a0, b0 = _first_id(a_lines), _first_id(b_lines)
    if not (a0 and b0):
        return ""
    sa = a0 + (f"x{len(a_lines)}" if len(a_lines) > 1 else "")
    sb = b0 + (f"x{len(b_lines)}" if len(b_lines) > 1 else "")
    return f"{sa}_vs_{sb}"


def suggest_run_name(lines):
    """run 默认任务名:第一个表达式的第一个 id,多行加 _nN:'P12345_n3'。
    取不到返回 ''(回退 af3_run)。"""
    a0 = _first_id(lines)
    if not a0:
        return ""
    return a0 + (f"_n{len(lines)}" if len(lines) > 1 else "")


def spec_side_expressions(spec):
    """spec -> (a_exprs, b_exprs, from_input)。
    优先用 spec["input"](提交时记录的初始输入行);旧 spec 没有该字段时,
    回退为从 pairs 反推(scan 反推出的是切片后的片段侧,仅为近似)。"""
    inp = spec.get("input") or {}
    a_exprs = [e for e in (inp.get("a") or []) if e]
    b_exprs = [e for e in (inp.get("b") or []) if e]
    if a_exprs and b_exprs:
        return a_exprs, b_exprs, True
    pairs = spec.get("pairs") or []
    seen_a, seen_b, a_exprs, b_exprs = set(), set(), [], []
    for p in pairs:
        for side, seen, lst in (("a", seen_a, a_exprs), ("b", seen_b, b_exprs)):
            s = p.get(side) or {}
            # 新格式:侧 = {"entities":[...]};旧格式:a/b 直接是实体 dict
            ents = s.get("entities") if isinstance(s, dict) else None
            if ents is None:
                ents = [s] if (s.get("type") or s.get("uniprot") or s.get("sequence")) else []
            ex = "+".join(filter(None, (entity_to_expr(e) for e in ents))) or None
            key = ex or id(s)
            if key in seen:
                continue
            seen.add(key)
            if ex:
                lst.append(ex)
    return a_exprs, b_exprs, False


def spec_prefill(spec):
    """spec -> (a_lines, b_lines, info) 供"填入提交页编辑"预填。
    info: {"type", "name", "from_input", "scan_params"};无法重建时 a_lines 为 None。"""
    t = spec.get("type")
    name = spec.get("name") or spec.get("tag") or "af3_rerun"
    if t == "run":
        exprs = spec.get("expressions") or \
            [j.get("expression") for j in spec.get("jobs", []) if j.get("expression")]
        exprs = [e for e in exprs if e]
        if not exprs:
            return None, None, {"error": "spec 中没有可重提的表达式"}
        return exprs, None, {"type": "run", "name": name, "from_input": True,
                             "scan_params": {}}
    if t in ("pulldown", "scan"):
        a_exprs, b_exprs, from_input = spec_side_expressions(spec)
        if not a_exprs or not b_exprs:
            return None, None, {"error": "无法从 spec 重建 A/B 组表达式"}
        return a_exprs, b_exprs, {"type": t, "name": name,
                                  "from_input": from_input,
                                  "scan_params": spec.get("scan_params") or {}}
    return None, None, {"error": f"该类型({t})不支持填入编辑"}


def rebuild_args_from_spec(spec):
    """由 spec.json 重建重提命令。返回 (args, note)。"""
    t = spec.get("type")
    if t == "run":
        exprs = spec.get("expressions") or \
            [j.get("expression") for j in spec.get("jobs", []) if j.get("expression")]
        exprs = [e for e in exprs if e]
        if not exprs:
            return None, "spec 中没有可重提的表达式"
        name = spec.get("name") or "af3_rerun"
        target = exprs[0] if len(exprs) == 1 else write_tmp_lines(f"{name}.txt", exprs)
        args = ["run", target, "--force"]
        jobs = spec.get("jobs") or []
        if jobs and jobs[0].get("template_free"):
            args.append("--template-free")
        if spec.get("partition"):
            args += ["--partition", str(spec["partition"])]
        if spec.get("max_concurrent"):
            args += ["--max-concurrent", str(spec["max_concurrent"])]
        return args, (f"重提 {len(exprs)} 个任务(--force;已存在的输出会被跳过,除非 force 生效)")
    if t == "raw_json":
        src = spec.get("json_source")
        if not src or not os.path.isfile(src):
            return None, f"原始 JSON 不存在:{src}"
        return ["run", "--json", src, "--force"], "重提手写 JSON"
    if t in ("pulldown", "scan"):
        a_exprs, b_exprs, from_input = spec_side_expressions(spec)
        if not a_exprs or not b_exprs:
            return None, "无法从 spec 重建 A/B 组表达式"
        name = spec.get("name") or spec.get("tag") or "af3_rerun"
        fa = write_tmp_lines(f"{name}_A.txt", a_exprs)
        fb = write_tmp_lines(f"{name}_B.txt", b_exprs)
        args = [t, fa, fb, "--name", name, "--force"]
        if spec.get("num_seeds"):
            args += ["--num-seeds", str(spec["num_seeds"])]
        if spec.get("topk"):
            args += ["--topk", str(spec["topk"])]
        if spec.get("self"):
            args.append("--self")
        if spec.get("template_free"):
            args.append("--template-free")
        if t == "scan":
            if spec.get("shared_msa"):
                args.append("--shared-msa")
            sp = spec.get("scan_params") or {}
            for flag, key in (("--win", "win"), ("--overlap", "overlap"),
                              ("--min-frag", "min_frag"),
                              ("--split-threshold", "split_threshold")):
                if sp.get(key):
                    args += [flag, str(sp[key])]
        if spec.get("partition"):
            args += ["--partition", str(spec["partition"])]
        if spec.get("max_concurrent"):
            args += ["--max-concurrent", str(spec["max_concurrent"])]
        if from_input:
            note = (f"由初始输入重建 A={len(a_exprs)}、B={len(b_exprs)} 行;"
                    f"--force 复用同名批次,已完成的 pair 会自动跳过")
        else:
            note = (f"旧批次无初始输入记录,由处理后 pairs 重建 A={len(a_exprs)}、"
                    f"B={len(b_exprs)} 行"
                    + ("(scan 为片段级近似,建议核对后再重提)" if t == "scan" else "")
                    + ";--force 复用同名批次,已完成的 pair 会自动跳过")
        return args, note
    return None, f"未知 spec 类型:{t}"


# ============================================================================
# 实时预览:resolve / pairs / scan 切片
# ============================================================================

def resolve_msa_keys(entities, msa_dir=None):
    """给每个 entity 设 _msa_key(只查文件是否存在,快)。返回 entities(原地)。"""
    msa_dir = msa_dir or HOST_MSA_DATA
    for e in entities:
        if af3 is None:
            continue
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                af3.resolve_msa_key(e, msa_dir)
            except SystemExit:
                e["_msa_key"] = af3.base_msa_key(e)
    return entities


def set_base_msa_keys(entities):
    """轻量:_msa_key = base_msa_key(纯计算,不查文件、不读盘)。
    供"每次按键就刷新"的实时预览用——完整 resolve_msa_key 会读 *_data.json,
    在网络盘上每敲一键卡 ~1s。pair 计数只需 key 去重,无需读盘。"""
    if af3 is None:
        return entities
    for e in entities:
        try:
            e["_msa_key"] = af3.base_msa_key(e)
        except Exception:
            pass
    return entities


def build_pairs_count(a_ents, b_ents, self_pairs=False):
    """返回 pair 列表(af3.build_pairs);af3 不可用返回 None。
    元素可以是实体 dict(=单实体侧)或实体列表(=复合侧,#2)。"""
    if af3 is None:
        return None
    try:
        return af3.build_pairs(_as_sides(a_ents), _as_sides(b_ents), self_pairs)
    except Exception:
        return None


def _as_sides(items):
    """[ent|list, ...] -> af3 的 side 字典列表(标签用 af3._side_label)。"""
    sides = []
    for el in items or []:
        ents = list(el) if isinstance(el, (list, tuple)) else [el]
        sides.append({"entities": ents,
                      "label": af3._side_label(ents)})
    return sides


def scan_fragment_preview(items, win, overlap, min_frag, threshold):
    """items: [(group, entity), ...]。
    返回 (rows, frag_entities):rows=[{group,label,frag,length}];frag_entities=切片后实体列表。
    只切未修饰蛋白;DNA/RNA/配体/带 PTM 的蛋白整段保留(与 af3.py variants 一致)。"""
    rows, frag_ents = [], []
    for grp, e in items:
        label = e.get("uniprot") or e.get("name") or \
            ("seq_" + (af3.md5_short(e["sequence"]) if af3 and e.get("sequence") else "?"))
        seq = e.get("sequence") or ""
        cut = (af3 is not None and e.get("type") == "protein" and seq)
        frags = af3.fragment_windows(len(seq), win, overlap, min_frag, threshold) \
            if cut else ([(1, len(seq))] if seq else [])
        for (s, en) in frags:
            rows.append({"group": grp, "label": label,
                         "frag": f"{s}-{en}", "length": en - s + 1})
        if not frags:      # 配体/DNA(无序列):也显示一行,知道它整体参与
            rows.append({"group": grp, "label": label,
                         "frag": "full", "length": "-"})
        fe = af3.make_fragments(e, _NS(win=win, overlap=overlap, min_frag=min_frag,
                                       split_threshold=threshold)) \
            if cut else [e]
        frag_ents.extend(fe)
    return rows, frag_ents


# --- scan 实时组合数的片段计数(#5:长度已知就能离线算,不联网) ---
_SEQ_LEN_CACHE = {}


def _seq_len(e):
    """实体序列长度:已有序列直接用;UniProt 查 af3 的磁盘缓存(.seq,不联网)。
    结果按 accession 记忆,避免热路径反复读网络盘。取不到返回 None。"""
    seq = e.get("sequence")
    if seq:
        return len(seq)
    acc = e.get("uniprot")
    if not acc or af3 is None:
        return None
    if acc in _SEQ_LEN_CACHE:
        return _SEQ_LEN_CACHE[acc]
    n = None
    try:
        p = os.path.join(getattr(af3, "UNIPROT_CACHE", ""), f"{acc}.seq")
        if os.path.isfile(p):
            with open(p) as f:
                n = len(f.read().strip()) or None
    except Exception:
        n = None
    _SEQ_LEN_CACHE[acc] = n
    return n


def fragment_totals(a_ents, b_ents, win, overlap, min_frag, threshold):
    """scan 的片段总数(供实时组合数):返回 (fa, fb, all_known)。
    all_known=False 表示有序列长度未知的蛋白(没抓过缓存),此时计数不含它们。"""
    def frags_of(ents):
        total, known = 0, True
        for e in ents:
            n = _seq_len(e)
            if n is None:
                known = False
                continue
            total += len(af3.fragment_windows(n, win, overlap, min_frag, threshold)) \
                if af3 is not None else 1
        return total, known
    fa, ka = frags_of(a_ents)
    fb, kb = frags_of(b_ents)
    return fa, fb, ka and kb


def fragment_side_variants(a_sides, b_sides, win, overlap, min_frag, threshold):
    """(#2 复合侧)scan 组合数:每侧变体数 = 侧内各蛋白片段数的乘积
    (带 PTM 的蛋白也切,位号随片段改写;DNA/RNA/配体不切,按 1 计)。
    返回 (va, vb, all_known)。"""
    def variants_of(sides):
        total, known = 0, True
        for ents in sides:
            v = 1
            for e in ents:
                if e.get("type") != "protein":
                    continue
                n = _seq_len(e)
                if n is None:
                    known = False
                    continue
                v *= len(af3.fragment_windows(n, win, overlap, min_frag, threshold)) \
                    if af3 is not None else 1
            total += v
        return total, known
    va, ka = variants_of(a_sides)
    vb, kb = variants_of(b_sides)
    return va, vb, ka and kb


def scan_fragment_preview_pae(items, pae_params):
    """scan --mode pae 的切片预览:按 PAE domain window 切。
    items: [(group, entity), ...];pae_params: {threshold, pae_cutoff,
    resolution, min_domain, domains_per_window, min_window, max_span}。
    缺全长 confidences.json 的蛋白给 'need PAE' 行(提交时走两阶段自动补算)。"""
    rows, frag_ents = [], []
    th = pae_params.get("threshold", 500)
    # auto(None/0)= 跟随 split-threshold,与 af3.py make_pae_fragments 语义一致
    max_span = pae_params.get("max_span") or th
    cache = {}
    for grp, e in items:
        label = e.get("uniprot") or e.get("name") or \
            ("seq_" + (af3.md5_short(e["sequence"]) if af3 and e.get("sequence") else "?"))
        seq = e.get("sequence") or ""
        if (af3 is None or e.get("type") != "protein" or not seq
                or len(seq) <= th):
            rows.append({"group": grp, "label": label,
                         "frag": "full" if seq else "-",
                         "length": len(seq) if seq else "-"})
            frag_ents.append(e)
            continue
        key = e.get("_msa_key") or af3.base_msa_key(e)
        ppath, pchain = af3._pae_split_path_opt(e.get("pae_path"))
        conf = ppath or af3.find_confidences_json(key, af3.HOST_OUTPUT)
        if not conf:
            rows.append({"group": grp, "label": label,
                         "frag": "need PAE",
                         "length": "auto 2-stage at submit"})
            frag_ents.append(e)
            continue
        ckey = (conf, pchain)
        if ckey not in cache:
            cache[ckey] = af3._pae_read_confidences(conf, chain=pchain)
        pae, res_ids = cache[ckey]
        if len(res_ids) != len(seq):
            rows.append({"group": grp, "label": label,
                         "frag": "PAE mismatch",
                         "length": f"{len(res_ids)} vs {len(seq)}"})
            frag_ents.append(e)
            continue
        windows, domains, notes = af3._pae_fragment_windows(
            pae, res_ids,
            pae_cutoff=pae_params.get("pae_cutoff", 5),
            resolution=pae_params.get("resolution", 0.5),
            min_domain=pae_params.get("min_domain", 10),
            domains_per_window=pae_params.get("domains_per_window", 1),
            min_window=pae_params.get("min_window", 250),
            max_span=max_span)
        if len(windows) <= 1:
            rows.append({"group": grp, "label": label,
                         "frag": "full", "length": len(seq)})
            frag_ents.append(e)
            continue
        for w in windows:
            rows.append({"group": grp, "label": label,
                         "frag": f"{w['start']}-{w['end']}",
                         "length": w["end"] - w["start"] + 1,
                         "domains": "+".join(w["domains"])})
        for nt in notes:
            rows.append({"group": grp, "label": "  note",
                         "frag": "-", "length": nt})
        fents = af3.make_pae_fragments(
            e, _NS(mode="pae", split_threshold=th,
                   pae_cutoff=pae_params.get("pae_cutoff", 5),
                   pae_resolution=pae_params.get("resolution", 0.5),
                   pae_min_domain=pae_params.get("min_domain", 10),
                   pae_domains_per_window=pae_params.get("domains_per_window", 1),
                   pae_min_frag=pae_params.get("min_window", 250),
                   pae_max_frag=pae_params.get("max_span"),
                   _pae_cache={key: ((pae, res_ids), conf)},
                   _pae_report={}))
        frag_ents.extend(fents)
    return rows, frag_ents


class _NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def screen_metrics(a_ents, b_ents, self_pairs=False, num_seeds=0, is_scan=False):
    pairs=build_pairs_count(a_ents,b_ents,self_pairs)
    if pairs is None: return None
    seeds=num_seeds or (4 if is_scan else 1)
    return dict(a=len(a_ents),b=len(b_ents),pairs=len(pairs),seeds=seeds,models=len(pairs)*seeds*5)


def preview_screen_plan(a_sides,b_sides,mode,scan_params,opts,self_pairs):
    # Same planner as submission. Unknown UniProt lengths require the explicit online preview.
    sides = copy.deepcopy([a_sides,b_sides])
    for group in sides:
        for ents in group:
            for ent in ents:
                if ent.get("type") in ("protein","rna") and not ent.get("sequence"): return None
                ent["_msa_free"] = bool(opts.get("msa_free"))
    raw=[]
    for group in sides:
        raw.append([dict(entities=es,label=af3._side_label(es),expr="+".join(entity_to_expr(e) for e in es)) for es in group])
    if mode=="scan":
        if scan_params.get("mode")=="pae": return None
        args=_NS(mode="win", win=scan_params["win"],overlap=scan_params["overlap"],min_frag=scan_params["min_frag"],
                 split_threshold=scan_params["threshold"],shared_msa=bool(opts.get("shared_msa")),msa_dir=HOST_MSA_DATA)
        setattr(args,"self",self_pairs)
        pairs=af3.build_scan_plan(args,raw[0],raw[1])["pairs"]
    else: pairs=af3.build_pairs(raw[0],raw[1],self_pairs)
    n = len(af3.parse_seeds(opts["seeds"])) if opts.get("seeds") and opts["seeds"]!="random" else int(opts.get("num_seeds") or (4 if mode=="scan" else 1))
    return dict(a=len(a_sides),b=len(b_sides),pairs=len(pairs),seeds=n,models=len(pairs)*n*5)


# ============================================================================
# 产物读取(spec / csv / cif)
# ============================================================================

def guess_type(dirname):
    for prefix, t in (("pulldown_", "pulldown"), ("scan_", "scan"),
                      ("batch_rank_", "rank"), ("batch_", "run-batch")):
        if dirname.startswith(prefix):
            return t
    return "run"


def _dir_mtime(d):
    try:
        return os.path.getmtime(d)
    except OSError:
        return 0.0


def _csv_data_rows(path):
    try:
        with open(path,encoding="utf8",newline="") as stream:
            return sum(1 for row in csv.DictReader(stream) if row.get("status", "ok") == "ok" and row.get("iptm"))
    except (OSError,ValueError): return 0


def _harvest_run_monomers(spec, scanned_dir):
    """run 类型批次里已完成的"单个全长蛋白"infer 顺手收进 infer_data 池
    (供 af3.py pae / scan --mode pae 复用)。判定与 af3._maybe_harvest_pair_to_pool
    一致:恰好 1 个蛋白实体、copies==1、无 trunc。run 任务没有 watcher,
    完成时刻没有 af3.py 进程在跑,因此在每次扫描时惰性收割——池里已有
    confidences 时 _harvest_to_infer_pool 直接返回,代价仅一次 isfile。"""
    harvest = getattr(af3, "_harvest_to_infer_pool", None)
    base_key = getattr(af3, "base_msa_key", None)
    if not (harvest and base_key and HOST_INFER_DATA):
        return
    jobs = spec.get("jobs")
    if not isinstance(jobs, list):
        return
    out_base = spec.get("batch_dir") or spec.get("output_dir") \
        or os.path.dirname(scanned_dir)
    for job in jobs:
        if not isinstance(job, dict):
            continue
        ents = job.get("entities") or []
        if len(ents) != 1:
            continue
        e = ents[0]
        if not isinstance(e, dict) or e.get("type") != "protein":
            continue
        if e.get("copies", 1) != 1 or e.get("trunc"):
            continue
        key = e.get("_msa_key") or base_key(e)
        name = job.get("name")
        if not key or not name:
            continue
        rdir = os.path.join(out_base, name)
        if not glob.glob(os.path.join(rdir, "*_summary_confidences.json")):
            continue
        try:
            harvest(key, rdir)
        except Exception:
            pass


def _harvest_nospec_monomer(d, base):
    """无 spec.json 的单任务目录(--infer-only 直提 / MSA 已就绪直提不写 spec):
    读顶层 summary_confidences 判断是否单链(单体)结果,是则以目录名为 key
    收进 infer_data 池。只读小文件,每目录至多一次(入池后 harvest 直接返回)。"""
    harvest = getattr(af3, "_harvest_to_infer_pool", None)
    if not (harvest and HOST_INFER_DATA):
        return
    hits = glob.glob(os.path.join(d, "*_summary_confidences.json"))
    if len(hits) != 1:
        return
    try:
        with open(hits[0], encoding="utf-8") as f:
            summ = json.load(f)
    except (OSError, ValueError):
        return
    n_chains = len(summ.get("chain_iptm") or summ.get("chain_ptm") or [])
    if n_chains != 1:
        return
    try:
        harvest(base, d)
    except Exception:
        pass


def scan_batches(host_output=None, host_specs=None):
    """扫描 output/ 下含 spec.json 或 ranking.csv 的批次。轻量,不查 pair 结果。
    pairs_done:有 ranking.csv 时 = 其数据行数(完成 pair 数),否则 None(前端显示 ?)。"""
    host_output = host_output or HOST_OUTPUT
    host_specs = host_specs or HOST_SPECS
    batches = {}
    if os.path.isdir(host_output):
        for d in sorted(glob.glob(os.path.join(host_output, "*"))):
            if not os.path.isdir(d):
                continue
            base = os.path.basename(d)
            if base.startswith("."):
                continue
            spec_path = os.path.join(d, "spec.json")
            info = {"name": base, "dir": d, "spec": None,
                    "type": guess_type(base), "status": "",
                    "pairs_done": None, "pairs_total": None,
                    "mtime": _dir_mtime(d),
                    "has_ranking": os.path.isfile(os.path.join(d, "ranking.csv"))}
            if os.path.isfile(spec_path):
                try:
                    spec = R.spec_summary(spec_path)
                    info["spec"] = spec_path
                    info["display_name"] = spec.get("name") or base
                    info["type"] = spec.get("type") or info["type"]
                    info["status"] = spec.get("status") or ""
                    info["msa_jids"] = spec.get("msa_jids", [])
                    info["infer_jids"] = spec.get("infer_jids", [])
                    info["controller_jid"] = spec.get("controller_jid") or spec.get("watcher_jid")
                    info["updated_at"] = spec.get("updated_at", 0)
                    info["scheduler_error"] = spec.get("scheduler_error")
                    if info['status'].startswith('msa_'):
                        runtime_path=os.path.join(d,'.runtime','af3_runtime.py')
                        try:
                            # A small immutable text file, never execute the old snapshot.
                            with open(runtime_path,encoding='utf8') as runtime_file:
                                if any('squeue' in line and '"--me"' in line for line in runtime_file):
                                    info['legacy_controller']=True
                        except OSError: pass
                    pairs = spec.get("pairs")
                    if isinstance(pairs, list):
                        info["pairs_total"] = len(pairs)
                        # 进行中的批次(ranking.csv 还没生成):按结果目录
                        # 统计已完成 pair 数,Dashboard 显示 465/591 而非 ?/591
                        if spec.get("version",1)<2 and not info["has_ranking"]:
                            done = 0
                            for p in pairs:
                                pn = p.get("name") if isinstance(p, dict) else None
                                if pn and os.path.isfile(os.path.join(
                                        d, pn, f"{pn}_summary_confidences.json")):
                                    done += 1
                            info["pairs_done"] = done
                    if spec.get("version", 1) >= 2:
                        info["pairs_total"] = spec.get('task_count', len(spec.get("pairs", spec.get("jobs", []))))
                        info["pairs_done"] = spec.get("counts", {}).get("succeeded", 0)
                        info["revision"] = spec.get("revision", 0)
                        info["error"] = spec.get("error") or spec.get("scheduler_error")
                except (OSError, ValueError):
                    pass
            elif not info["has_ranking"]:
                # 单任务目录(run/pae 单体等):无 spec/ranking,但有输入或结果文件
                # 也收录进 Dashboard(原来只展示批量任务)
                if glob.glob(os.path.join(d, "*_summary_confidences.json")):
                    info["status"] = "done"
                    # Completion harvesting is owned by the controller.
                elif glob.glob(os.path.join(d, "*_data.json")):
                    info["status"] = "submitted"
                else:
                    continue
            batches[d] = info
            if info['has_ranking'] and info['pairs_done'] is None:
                info['pairs_done'] = _csv_data_rows(os.path.join(d, 'ranking.csv'))
    # infer_data 池:单体全长 infer 产物(scan --mode pae / af3.py pae 复用)。
    # 类型并入 run(不单独列 monomer 选项卡)。
    if HOST_INFER_DATA and os.path.isdir(HOST_INFER_DATA):
        for d in sorted(glob.glob(os.path.join(HOST_INFER_DATA, "*"))):
            if not os.path.isdir(d) or d in batches:
                continue
            base = os.path.basename(d)
            if base.startswith("."):
                continue
            has_result = bool(glob.glob(os.path.join(
                d, "*_summary_confidences.json")))
            has_input = bool(glob.glob(os.path.join(d, "*_data.json")))
            if not has_result and not has_input:
                continue
            spec_path = os.path.join(d, "spec.json")
            info = {"name": base, "dir": d,
                    "spec": spec_path if os.path.isfile(spec_path) else None,
                    "type": "run",
                    "status": "done" if has_result else "submitted",
                    "pairs_done": None, "pairs_total": None,
                    "mtime": _dir_mtime(d), "has_ranking": False}
            if info["spec"]:
                try:
                    with open(spec_path) as f:
                        spec = json.load(f)
                    info["status"] = spec.get("status") or info["status"]
                    info["display_name"] = spec.get("name") or base
                except (OSError, ValueError):
                    pass
            batches[d] = info
    # Standalone PAE has a work identity before its prediction directory exists.
    for sp in glob.glob(os.path.join(HOST_INFER_DATA, '.work', '*', 'spec.json')):
        spec = R.spec_summary(sp)
        if not spec: continue
        d = os.path.dirname(sp)
        name = spec.get('name', os.path.basename(d))
        result_dir = spec.get('result_dir') or os.path.join(spec.get('output_dir', HOST_INFER_DATA), name)
        # This association follows the exact planned output identity.
        batches.pop(result_dir, None)
        batches[d] = dict(name=name, display_name=spec.get('label') or name, dir=d,
            result_dir=result_dir, spec=sp, type='pae', status=spec.get('status','preparing'),
            pairs_done=spec.get('counts',{}).get('succeeded',0), pairs_total=1,
            controller_jid=spec.get('controller_jid'), msa_jids=spec.get('msa_jids',[]),
            infer_jids=spec.get('infer_jids',[]), mtime=spec.get('updated_at',_dir_mtime(d)),
            has_ranking=False, error=spec.get('error'), scheduler_error=spec.get('scheduler_error'))
    if os.path.isdir(host_specs):
        for sp in glob.glob(os.path.join(host_specs, "*.spec.json")):
            try:
                with open(sp) as f:
                    spec = json.load(f)
                outdir = spec.get("outdir") or os.path.dirname(sp)
                if outdir in batches:
                    continue
                rk = os.path.isfile(os.path.join(outdir, "ranking.csv"))
                pdone = _csv_data_rows(
                    os.path.join(outdir, "ranking.csv")) if rk else None
                spairs = spec.get("pairs")
                if pdone is None and isinstance(spairs, list):
                    pdone = sum(
                        1 for p in spairs
                        if isinstance(p, dict) and p.get("name")
                        and os.path.isfile(os.path.join(
                            outdir, p["name"],
                            f"{p['name']}_summary_confidences.json")))
                batches[outdir] = {
                    "name": spec.get("name", os.path.basename(sp)),
                    "dir": outdir, "spec": sp,
                    "type": spec.get("type") or "", "status": spec.get("status") or "",
                    "pairs_done": pdone,
                    "pairs_total": len(spairs) if isinstance(spairs, list) else None,
                    "mtime": _dir_mtime(sp),
                    "has_ranking": rk}
            except (OSError, ValueError):
                continue
    for info in batches.values():
        enrich_batch(info)
        if info.get('legacy_controller'): info['status']='legacy_controller'
        if info.get("scheduler_error") and info.get("status") not in ("done", "failed", "partial_failed"):
            info["status"] = "scheduler_unavailable"
    return sorted(_collapse_legacy_pool_copies(list(batches.values())), key=lambda r: r["mtime"], reverse=True)


def _collapse_legacy_pool_copies(batches):
    """Hide proven legacy copies only. Explicit batch plans retain their identity."""
    pools = {b["internal_name"]: b for b in batches if b["source"] == "infer_pool"
             and b["status"] == "done" and not b.get("spec")}
    visible = []
    for batch in batches:
        pool = pools.get(batch["internal_name"])
        if pool and pool is not batch and not batch.get("spec") and not batch.get("has_ranking"):
            # Harvested copies share exactly the same best model and summary.
            # Never merge unrelated calculations based on their display names.
            def files(folder, suffix):
                return sorted(glob.glob(os.path.join(folder, "*" + suffix)))
            model_a, model_b = files(batch["dir"], "_model.cif"), files(pool["dir"], "_model.cif")
            summary_a, summary_b = files(batch["dir"], "_summary_confidences.json"), files(pool["dir"], "_summary_confidences.json")
            same = False
            if len(model_a) == len(model_b) == len(summary_a) == len(summary_b) == 1:
                try:
                    same = all(R.file_digest(a) == R.file_digest(b) for a,b in [(model_a[0],model_b[0]),(summary_a[0],summary_b[0])])
                except OSError: pass
            elif not model_a and not summary_a and len(model_b) == len(summary_b) == 1:
                # Legacy MSA-only directory: match the actual monomer input,
                # rather than mistaking all equal display names for one task.
                data_a,data_b=files(batch['dir'],'_data.json'),files(pool['dir'],'_data.json')
                if len(data_a)==len(data_b)==1:
                    try:
                        inputs=[af3.read_data_json(p).get('sequences',[]) for p in (data_a[0],data_b[0])]
                        if all(len(entries)==1 and 'protein' in entries[0] for entries in inputs):
                            proteins=[entries[0]['protein'] for entries in inputs]
                            same=bool(proteins[0].get('sequence')) and all(
                                proteins[0].get(k,[])==proteins[1].get(k,[]) for k in ('sequence','modifications'))
                            same=same and all(not isinstance(p.get('id'),list) or len(p['id'])==1 for p in proteins)
                    except (OSError,ValueError,TypeError): pass
            if same:
                pool.setdefault("pool_aliases", []).append(batch["dir"])
                continue
        visible.append(batch)
    return visible


def apply_queue_status(batches, queue):
    """Overlay live Slurm stages without changing saved plans or claiming success."""
    for batch in batches:
        status = batch.get("status", "")
        if not batch.get("spec") or status in ("done", "failed", "partial_failed"): continue
        def states(ids):
            return [value[1] for jid,value in queue.items() if any(R.job_id_matches(jid, expected) for expected in ids)]
        msa = states(batch.get("msa_jids", [])); infer = states(batch.get("infer_jids", []))
        controller = states([batch["controller_jid"]]) if batch.get("controller_jid") else []
        if batch.get("scheduler_error"):
            batch["status"] = "scheduler_unavailable"
        elif batch.get('legacy_controller'):
            batch['status']='legacy_controller'
        elif infer:
            batch["status"] = "infer_running" if "RUNNING" in infer else "infer_queued"
        elif msa:
            batch["status"] = "msa_running" if any(s in ("RUNNING", "COMPLETING") for s in msa) else "msa_queued"
        elif controller and status.startswith("msa_"):
            batch["status"] = "msa_checking"
        elif not controller and batch.get("controller_jid") and time.time()-batch.get("updated_at",0) > 120:
            batch["status"] = "controller_stopped"
    return batches


def batch_id(b):
    """UI identity is independent of display language, name and queue status."""
    return os.path.normcase(os.path.abspath(os.path.normpath(b["dir"])))


def enrich_batch(b):
    b["id"] = batch_id(b)
    b["internal_name"] = os.path.basename(os.path.normpath(b["dir"]))
    if not b.get("display_name"):
        spec = R.read_json(b.get("spec"), {}) if b.get("spec") else {}
        b["display_name"] = spec.get("name") or b.get("name") or b["internal_name"]
    try:
        in_pool = HOST_INFER_DATA and os.path.commonpath(
            [os.path.abspath(b["dir"]), os.path.abspath(HOST_INFER_DATA)]) == os.path.abspath(HOST_INFER_DATA)
    except ValueError:
        in_pool = False
    b["source"] = "infer_pool" if in_pool else "output"
    # Stable short UI discriminator, even for legacy folders without a hash.
    b["short_id"] = R.digest(b["id"], 8)
    return b


def batch_display_name(b):
    return b.get("display_name") or b.get("name") or os.path.basename(b["dir"])


def batch_label(b):
    label = batch_display_name(b)
    if b["type"]:
        label += f"  [{b['type']}]"
    if b["status"]:
        label += f"  ({b['status']})"
    return label


def fmt_mtime(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def safe_mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0.0


def parse_csv_df(text, pandas):
    """dtype=str + keep_default_na=False:蛋白名 'NA' 不变 NaN。"""
    if pandas is None:
        return None
    return pandas.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)


def parse_matrix_df(text, pandas):
    """iptm_matrix.csv -> 数值 DataFrame(index=第一列)。"""
    if pandas is None:
        return None
    df = pandas.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, index_col=0)
    return df.apply(lambda col: pandas.to_numeric(col, errors="coerce"))


def build_heatmap_fig(text, pandas, px, zmin=0, zmax=1, cmap="viridis"):
    """iptm_matrix.csv 文本 -> plotly imshow figure;缺依赖返回 None。"""
    if px is None or pandas is None:
        return None
    mat = parse_matrix_df(text, pandas)
    fig = px.imshow(mat, color_continuous_scale=cmap, zmin=zmin, zmax=zmax,
                    aspect="auto", labels=dict(color="ipTM"), text_auto=".2f")
    fig.update_xaxes(side="top")
    return fig


def list_model_cifs(outdir, cap=300):
    """结果目录里的 *_model.cif -> [(label, path)]。不进 logs/scripts/input。"""
    cifs = []
    if not os.path.isdir(outdir):
        return cifs
    for root, dirs, files in os.walk(outdir):
        dirs[:] = [d for d in dirs if d not in ("logs", "scripts", "input")]
        rel = os.path.relpath(root, outdir)
        for fn in sorted(files):
            if fn.endswith("_model.cif"):
                label = fn if rel == "." else f"{rel}/{fn}"
                cifs.append((label, os.path.join(root, fn)))
        if len(cifs) > cap:
            break
    return cifs


def _top_level_cifs(folder):
    """pair 文件夹里**不在** seed-* 子目录下的 *_model.cif(顶层=最优模型)。"""
    out = []
    if not os.path.isdir(folder):
        return out
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith("seed-")
                   and d not in ("logs", "scripts", "input")]
        for fn in sorted(files):
            if fn.endswith("_model.cif"):
                out.append(os.path.join(root, fn))
    return out


def list_ranked_cifs(outdir, cap=500):
    """结构下载下拉:有 ranking.csv 时按 rank 排序、每对一条
    (只取 pair 文件夹顶层 cif,不进 seed-*);没有则退回遍历(同样排除 seed-*)。
    返回 [dict(label, path, a, b, rank, iptm)] —— a/b 供前端做蛋白名标注。"""
    manifest = R.read_json(os.path.join(outdir, 'results_index.json'))
    if isinstance(manifest, dict) and manifest.get('version') == 1:
        return manifest.get('entries',[])[:cap]
    ranking = os.path.join(outdir, "ranking.csv")
    if os.path.isfile(ranking):
        try:
            rows = list(csv.DictReader(io.StringIO(read_text(ranking))))
        except Exception:
            rows = []
        items = []
        for row in rows:  # ranking.csv 本身按 rank 顺序写
            folder = (row.get("folder") or "").strip()
            if not folder:
                continue
            cifs = _top_level_cifs(os.path.join(outdir, folder))
            if not cifs:
                continue  # 该对还没出模型
            a, b = (row.get("name_a") or "").strip(), (row.get("name_b") or "").strip()
            pair = (a + "+" + b) if (a or b) else folder
            rank = (row.get("rank") or "").strip() or "?"
            iptm = (row.get("iptm") or "").strip()
            try:
                iptm_s = f" · ipTM {float(iptm):.2f}"
            except (TypeError, ValueError):
                iptm_s = ""
            items.append({"label": f"rank {rank} · {pair}{iptm_s}", "path": cifs[0],
                          "a": a, "b": b, "rank": rank, "iptm": iptm})
            if len(items) >= cap:
                break
        if items:
            return items
    out = []
    if os.path.isdir(outdir):
        for root, dirs, files in os.walk(outdir):
            dirs[:] = [d for d in dirs if not d.startswith("seed-")
                       and d not in ("logs", "scripts", "input")]
            rel = os.path.relpath(root, outdir)
            for fn in sorted(files):
                if fn.endswith("_model.cif"):
                    out.append({"label": (fn if rel == "." else f"{rel}/{fn}"),
                                "path": os.path.join(root, fn),
                                "a": "", "b": "", "rank": "", "iptm": ""})
            if len(out) > cap:
                break
    return out


def matrix_rank_lookup(ranking_path):
    """ranking.csv -> {(name_a, name_b): {"rank","iptm"}}(正反两键都挂),
    供热图悬停按 (行标签, 列标签) 查 rank。文件不存在/解析失败返回 {}。"""
    if not ranking_path or not os.path.isfile(ranking_path):
        return {}
    try:
        rows = list(csv.DictReader(io.StringIO(read_text(ranking_path))))
    except Exception:
        return {}
    out = {}
    for r in rows:
        a, b = (r.get("name_a") or "").strip(), (r.get("name_b") or "").strip()
        if not a or not b:
            continue
        info = {"rank": (r.get("rank") or "").strip(),
                "iptm": (r.get("iptm") or "").strip()}
        out[(a, b)] = info
        out.setdefault((b, a), info)
    return out


def result_paths(outdir):
    """一个批次目录里各类产物路径(存在与否由前端 isfile 判断)。"""
    return {k: os.path.join(outdir, v) for k, v in {
        "ranking": "ranking.csv", "matrix": "iptm_matrix.csv",
        "profile": "iptm_profile.csv", "hits": "scan_hits.csv",
        "prot_matrix": "iptm_matrix_protein.csv", "report": "report.md"}.items()}


# ============================================================================
# UniProt entry 名标注(仅 UI 显示用;不改 af3.py 生成的任何文件)
# ============================================================================

_NAME_CACHE_FILE = os.path.join(os.path.expanduser("~"),
                                ".cache", "af3_gui", "uniprot_names.json")
_NAME_CACHE = None
_NAME_MISS = set()   # 本进程内抓不到的 acc(不写盘,下次启动再试)

_ACC = r"[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2}"
_ACC_RE = re.compile(r"\b(" + _ACC + r")\b")
# 片段标签后缀:af3.py 实际产出 '_t2201-2500'(带 t),兼容 '_1-300'(不带)
_ACC_LABEL_RE = re.compile(r"^(" + _ACC + r")(_t?\d+-\d+)?$")
# 自由文本里的 accession token(可带 _tN-M 片段后缀、xN 拷贝数后缀)。前后都不
# 允许单词字符:目录名 rank_001_P12345_t1-416_vs_Q12345_... 里的 accession 两侧
# 是 '_'/字母,不会命中 -> 报告正文标注时不会污染 folder 路径。
_ACC_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])((" + _ACC + r")"
                           r"(?:_t?\d+-\d+)?(?:x\d+)?)(?![A-Za-z0-9_])")


def split_acc_label(text):
    """'Q12345_1-300' -> ('Q12345','_1-300');'P12345' -> ('P12345','');
    不是 accession 标签 -> (None, None)。"""
    m = _ACC_LABEL_RE.match((text or "").strip())
    return (m.group(1), m.group(2) or "") if m else (None, None)


def extract_accessions(text):
    """文本里所有像 UniProt accession 的 token(去重、保序)。"""
    seen, out = set(), []
    for m in _ACC_RE.finditer(text or ""):
        a = m.group(1)
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


def _load_name_cache():
    global _NAME_CACHE
    if _NAME_CACHE is None:
        try:
            with open(_NAME_CACHE_FILE, encoding="utf-8") as f:
                _NAME_CACHE = json.load(f)
        except Exception:
            _NAME_CACHE = {}
    return _NAME_CACHE


def _fetch_entry_name(acc, timeout=10):
    """rest.uniprot.org 的 fasta 头 '>sp|P12345|entry_name ...' -> 'entry_name'。
    与 af3.py 抓序列同一 endpoint 族,使用公开 API。失败返回 ''。"""
    try:
        url = f"https://rest.uniprot.org/uniprotkb/{acc}.fasta"
        req = urllib.request.Request(url, headers={"User-Agent": "af3-gui/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            head = resp.read(4096).decode("utf-8", "replace").split("\n", 1)[0]
        m = re.match(r"^>\w+\|[^|]+\|(\S+)", head)
        return m.group(1) if m else ""
    except Exception:
        return ""


def uniprot_entry_names(accs, online=True, timeout=10):
    """{acc: entry 名}。online=False 只查缓存(同步、零延迟,渲染热路径用);
    online=True 抓缺失项(放后台线程)。抓不到记进 _NAME_MISS,本进程不再重试。"""
    cache = _load_name_cache()
    out = {a: cache[a] for a in accs if cache.get(a)}
    if online:
        changed = False
        for a in accs:
            if a in out or a in _NAME_MISS:
                continue
            name = _fetch_entry_name(a, timeout=timeout)
            if name:
                cache[a] = name
                out[a] = name
                changed = True
            else:
                _NAME_MISS.add(a)
        if changed:
            try:
                os.makedirs(os.path.dirname(_NAME_CACHE_FILE), exist_ok=True)
                with open(_NAME_CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump(cache, f)
            except Exception:
                pass
    return out


def names_missing(accs):
    """缓存里没有、且本进程没失败过的 acc —— 需要后台抓取的清单。"""
    cache = _load_name_cache()
    return [a for a in accs if not cache.get(a) and a not in _NAME_MISS]


def with_entry_name(acc, names):
    """'P12345' -> 'P12345 · entry_name'(查不到就原样)。"""
    n = (names or {}).get(acc) or ""
    return f"{acc} · {n}" if n else acc


def batch_accessions(outdir):
    """批次结果里出现的 UniProt accession(ranking.csv 的 name_a/name_b +
    iptm_matrix.csv 的轴标签;片段标签取基底)。供 UI 标注用。"""
    accs, seen = [], set()

    def add(label):
        a, _ = split_acc_label(label)
        if a and a not in seen:
            seen.add(a)
            accs.append(a)

    rp = os.path.join(outdir, "ranking.csv")
    if os.path.isfile(rp):
        try:
            for r in csv.DictReader(io.StringIO(read_text(rp))):
                add(r.get("name_a"))
                add(r.get("name_b"))
        except Exception:
            pass
    mp = os.path.join(outdir, "iptm_matrix.csv")
    if os.path.isfile(mp):
        try:
            lines = read_text(mp).splitlines()
            for cell in (lines[0].split(",")[1:] if lines else []):
                add(cell)
            for line in lines[1:]:
                add(line.split(",", 1)[0])
        except Exception:
            pass
    return accs


# ============================================================================
# 批次结果 HTML 报告(#3:把 Results 页的内容整理成单个自包含文件,写到批次目录)
# ============================================================================

def _png_b64(fig):
    import base64
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, bbox_inches="tight")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _esc(s):
    return _html_mod.escape(str(s), quote=True)


def export_batch_html(outdir, names=None):
    with _PLOT_LOCK:
        return _export_batch_html(outdir,names)


def _export_batch_html(outdir, names=None):
    """生成 <outdir>/results_report.html(自包含:表格 + 图 base64 内嵌)。
    names: UniProt 标注 {acc: entry 名};传 None 时按批次里的 accession 自动抓取
    (缓存命中零延迟,缺失才联网)。返回 (path, error)。
    注意:前端启动时已先 import matplotlib(six.moves 早已就位),此处懒 import 安全。"""
    try:
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        import pandas as pd
        HAS = True
    except Exception:
        HAS = False
    if names is None:
        names = uniprot_entry_names(batch_accessions(outdir))
    P = result_paths(outdir)

    def anno(s):
        """自由文本里的 accession(含 _tN-M 片段 / xN 拷贝数后缀)后追加
        ' · ENTRY_NAME'。目录名内部的 accession(两侧为单词字符)不命中,
        folder 路径天然不受影响。"""
        s = str(s)
        if not names:
            return s

        def rep(m):
            nm = names.get(m.group(2))
            return f"{m.group(1)} · {nm}" if nm else m.group(1)
        return _ACC_TOKEN_RE.sub(rep, s)

    H = ['<html><head><meta charset="utf-8"><title>AF3 batch report</title>',
         "<style>body{font-family:sans-serif;margin:24px;color:#111}",
         "h1{font-size:20px} h2{font-size:16px;margin-top:28px;color:#333}",
         "table{border-collapse:collapse;font-size:13px}",
         "td,th{border:1px solid #ccc;padding:3px 8px;text-align:left}",
         "th{background:#eee;cursor:pointer;user-select:none;white-space:nowrap}",
         "th:hover{background:#dde} th .dir{color:#888;font-size:11px}",
         "td.warn{color:#b45309;font-weight:bold}",
         ".warn-inline{color:#b45309;font-weight:bold}",
         ".meta{color:#555;font-size:13px}",
         "pre{background:#f6f6f6;padding:10px;font-size:12px;overflow-x:auto}",
         "img{max-width:100%}</style>",
         # 表头点击排序:数值感知(parseFloat,'0.63 ⚠' 也能排),再点反向
         "<script>",
         "document.addEventListener('DOMContentLoaded',function(){",
         "document.querySelectorAll('table.sortable').forEach(function(tb){",
         "var ths=tb.querySelectorAll('th');",
         "ths.forEach(function(th,ci){th.addEventListener('click',function(){",
         "var asc=th.dataset.asc!=='1';",
         "ths.forEach(function(t){delete t.dataset.asc;",
         "var d=t.querySelector('.dir');if(d)d.textContent='';});",
         "th.dataset.asc=asc?'1':'0';",
         "var ind=document.createElement('span');ind.className='dir';",
         "ind.textContent=asc?' ▲':' ▼';th.appendChild(ind);",
         "var rows=Array.prototype.slice.call(tb.querySelectorAll('tr')).slice(1);",
         "rows.sort(function(a,b){",
         "var x=a.cells[ci]?a.cells[ci].textContent.trim():'';",
         "var y=b.cells[ci]?b.cells[ci].textContent.trim():'';",
         "var xn=x.replace(/\\s*⚠/,''),yn=y.replace(/\\s*⚠/,'');",
         "var nx=parseFloat(xn),ny=parseFloat(yn);",
         "var bx=(xn!==''&&!isNaN(nx)&&isFinite(xn)),by=(yn!==''&&!isNaN(ny)&&isFinite(yn));",
         "var cmp=(bx&&by)?(nx-ny):(bx?-1:(by?1:x.localeCompare(y)));",
         "return asc?cmp:-cmp;});",
         "rows.forEach(function(r){tb.appendChild(r);});});});});});",
         "</script></head><body>"]
    name = os.path.basename(outdir.rstrip("/\\"))
    H.append(f"<h1>AF3 batch report — {_esc(anno(name))}</h1>")
    H.append(f'<p class="meta">dir: {_esc(outdir)}<br>'   # 路径不标注(复制用)
             f'generated: {time.strftime("%Y-%m-%d %H:%M:%S")}</p>')

    # spec 参数摘要
    spec_path = os.path.join(outdir, "spec.json")
    if os.path.isfile(spec_path):
        try:
            spec = R.read_json(spec_path)
            keep = ("type", "name", "num_seeds", "topk", "self", "template_free",
                    "shared_msa", "partition", "max_concurrent", "status")
            kv = ", ".join(f"{k}={spec[k]}" for k in keep if spec.get(k) is not None)
            H.append(f'<p class="meta">spec: {_esc(kv)}</p>')
        except Exception:
            pass

    # 图:ipTM 矩阵 / 蛋白级矩阵。静态 PNG 没法悬停 -> rank 前 20 直接写进格子。
    # 片段级矩阵:按 (name_a,name_b) 查 ranking.csv(片段级 rank);
    # 蛋白级矩阵:按单元格自身 ipTM 在矩阵内重排 rank 并标注 rank+iptm 两行
    # (ranking.csv 的 top 20 常被 --self 的 A×A/B×B 自配对占满,那些组合不在
    #  A×B 蛋白矩阵里,直接查会整图只标出一两格)。
    rank_info = matrix_rank_lookup(P["ranking"]) if os.path.isfile(P["ranking"]) else {}
    rank_norm = {}
    for (ka, kb), info in rank_info.items():
        aa, _ = split_acc_label(ka)
        bb, _ = split_acc_label(kb)
        if aa and bb:
            rank_norm.setdefault((aa, bb), info)

    def _rank_of(rl, cl):
        info = rank_info.get((rl, cl))
        if info:
            return info
        aa, _ = split_acc_label(rl)
        bb, _ = split_acc_label(cl)
        return rank_norm.get((aa or "", bb or ""))

    def matrix_img(path, title, annotate_iptm=False):
        if not (HAS and os.path.isfile(path)):
            return
        try:
            mat = parse_matrix_df(read_text(path), pd)
            fig = Figure(figsize=(9, 5))
            FigureCanvasAgg(fig)
            ax = fig.add_subplot(111)
            vals = mat.values.astype(float)
            im = ax.imshow(vals, aspect="auto", cmap="viridis", vmin=0, vmax=1)
            ax.set_xticks(range(len(mat.columns)))
            ax.set_xticklabels([anno(str(c)) for c in mat.columns],
                               rotation=90, fontsize=6)
            ax.set_yticks(range(len(mat.index)))
            ax.set_yticklabels([anno(str(r)) for r in mat.index], fontsize=6)
            ax.set_title(title)
            fig.colorbar(im, ax=ax, label="ipTM")
            if annotate_iptm:
                # 蛋白级矩阵:rank 注解按「矩阵单元格自身的 ipTM 排名」标注 top 20。
                # 不能查 ranking.csv——那是片段级排名,且含 A×A/B×B 自配对
                # (不在本矩阵里),top 20 常被自配对占满,导致整图只标出一两格。
                order = sorted(
                    ((vals[ri][ci], ri, ci)
                     for ri in range(len(mat.index))
                     for ci in range(len(mat.columns))
                     if vals[ri][ci] == vals[ri][ci]),   # 去 NaN
                    key=lambda t: -t[0])
                cell_rank = {(ri, ci): i + 1
                             for i, (_v, ri, ci) in enumerate(order)}
                for (ri, ci), rk in cell_rank.items():
                    if rk > 20:
                        continue
                    v = vals[ri][ci]
                    fg = "white" if (v != v or v < 0.45) else "black"
                    ax.text(ci, ri, f"{rk:02d}\n{v:.2f}", ha="center", va="center",
                            fontsize=5, color=fg)
            else:
                # 片段级矩阵:按 (name_a,name_b) 查 ranking.csv 的片段级 rank
                for ri, rl in enumerate(mat.index):
                    for ci, cl in enumerate(mat.columns):
                        info = _rank_of(str(rl), str(cl))
                        if not info:
                            continue
                        try:
                            rk = int(info["rank"])
                        except (TypeError, ValueError):
                            continue
                        if rk > 20:
                            continue
                        v = vals[ri][ci]
                        fg = "white" if (v != v or v < 0.45) else "black"
                        ax.text(ci, ri, f"{rk:02d}", ha="center", va="center",
                                fontsize=4.5, color=fg)
            fig.tight_layout()
            H.append(f"<h2>{_esc(title)}</h2><img src='data:image/png;base64,{_png_b64(fig)}'>")
        except Exception as e:
            H.append(f"<p>{_esc(title)} plot failed: {_esc(e)}</p>")

    # 模块顺序:ipTM matrix -> 蛋白级 matrix -> report.md -> ipTM ranking
    matrix_img(P["matrix"], "ipTM matrix")
    matrix_img(P["prot_matrix"], "ipTM matrix (protein level)", annotate_iptm=True)

    # report.md 原文(标注 accession;folder 列的目录名内部不受影响)
    if os.path.isfile(P["report"]):
        H.append("<h2>report.md</h2><pre>" + _esc(anno(read_text(P["report"]))) + "</pre>")

    # ranking 表(最后;表头可点击排序,seed 波动单元格标 ⚠)
    if os.path.isfile(P["ranking"]):
        try:
            rows = list(csv.DictReader(io.StringIO(read_text(P["ranking"]))))
            if rows:
                cols = list(rows[0].keys())
                best_c = "iptm" if "iptm" in cols else (
                    "best_iptm" if "best_iptm" in cols else None)
                mean_c = "mean_iptm" if "mean_iptm" in cols else None
                H.append('<h2>ipTM ranking</h2><p class="meta">'
                         "点击表头排序(再点反向);"
                         + ('<span class="warn-inline">⚠</span> = seed 波动预警'
                            "(best − μ &gt; 0.05,疑似单 seed 偶然,建议加 seed 复测)"
                            if best_c and mean_c else "")
                         + "</p>")
                H.append('<table class="sortable"><tr>'
                         + "".join(f"<th>{_esc(c)}</th>" for c in cols) + "</tr>")
                for r in rows:
                    cells = []
                    for c in cols:
                        txt = anno(r[c]) if c in ("name_a", "name_b") else r[c]
                        cls = ""
                        if c == mean_c and best_c:
                            try:
                                if float(r[best_c]) - float(r[mean_c]) > 0.05:
                                    txt += " ⚠"
                                    cls = ' class="warn"'
                            except (TypeError, ValueError):
                                pass
                        cells.append(f"<td{cls}>{_esc(txt)}</td>")
                    H.append("<tr>" + "".join(cells) + "</tr>")
                H.append("</table>")
        except Exception as e:
            H.append(f"<p>ranking.csv read failed: {_esc(e)}</p>")

    H.append("</body></html>")
    path = os.path.join(outdir, "results_report.html")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(H))
    except Exception as e:
        return None, f"写 HTML 失败:{e}"
    return path, None


# ============================================================================
# PAE 图(#1):读 cif 同目录的 <name>_confidences.json 里的 pae 矩阵
# ============================================================================

def confidences_json_for_cif(cif_path):
    """X_model.cif -> 同目录 X_confidences.json(不是 _summary_ 那个)。不存在返回 None。"""
    d = os.path.dirname(cif_path)
    base = os.path.basename(cif_path)
    for suf in ("_model.cif", ".cif"):
        if base.endswith(suf):
            cand = os.path.join(d, base[:-len(suf)] + "_confidences.json")
            if os.path.isfile(cand):
                return cand
    return None


def input_json_for_cif(cif_path):
    """X_model.cif -> 同目录 X_data.json(infer 输入,含各链序列)。
    用于推 PAE 的链边界;不存在(老批次未归档)返回 None。"""
    base = os.path.basename(cif_path)
    if not base.endswith("_model.cif"):
        return None
    cand = os.path.join(os.path.dirname(cif_path),
                        base[:-len("_model.cif")] + "_data.json")
    return cand if os.path.isfile(cand) else None


def chains_from_input_json(json_path, entry=None):
    inp = R.read_json(json_path, {}) if json_path else {}
    conf = confidences_json_for_cif(entry["path"]) if entry and entry.get("path") else None
    if not conf and json_path and json_path.endswith("_data.json"):
        conf = json_path[:-10] + "_confidences.json"
    if not conf: return []
    try: _,meta = PAE.load(conf)
    except (OSError,ValueError): return []
    labels = []
    for side in ("a","b"):
        for text in (entry or {}).get(side,"").split("+"):
            if not text: continue
            m = re.match(r"^(.*)x(\d+)$", text)
            labels.extend([m.group(1)]*int(m.group(2)) if m else [text])
    return PAE.chains(meta,inp,labels)


def compute_pae_domains(path, chains, params):
    request = dict(path=path,chains=chains,params=params)
    proc = subprocess.run([PYTHON,os.path.join(_HERE,"af3_pae.py")], input=json.dumps(request),
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE,encoding="utf8",timeout=1800)
    if proc.returncode: raise RuntimeError(proc.stderr.strip())
    return json.loads(proc.stdout)


class AnalysisCancelled(Exception):
    pass


_PAE_PROCESS_LOCK=threading.Lock()
_PAE_CHILDREN_LOCK=threading.Lock()
_PAE_CHILDREN={}
_PAE_SHUTDOWN=threading.Event()


def stop_pae_processes(shutdown=False):
    with _PAE_CHILDREN_LOCK:
        if shutdown:_PAE_SHUTDOWN.set()
        for proc,event in list(_PAE_CHILDREN.items()):
            if shutdown or event.is_set():
                event.set()
                if proc.poll() is None:proc.kill()


def run_pae_request(request, cancelled, matrix=None):
    """At most one expensive analysis/render process; cancellation kills its work."""
    import tempfile
    while not _PAE_PROCESS_LOCK.acquire(timeout=.1):
        if cancelled.is_set(): raise AnalysisCancelled('Cancelled')
    try:
        if cancelled.is_set(): raise AnalysisCancelled('Cancelled')
        with tempfile.TemporaryDirectory(prefix='af3-pae-ui-') as tmp:
            request=dict(request)
            if matrix is not None:
                import numpy as np
                request['matrix']=os.path.join(tmp,'matrix.npy');np.save(request['matrix'],matrix,allow_pickle=False)
            request['preview']=os.path.join(tmp,'preview.npy')
            with _PAE_CHILDREN_LOCK:
                if cancelled.is_set() or _PAE_SHUTDOWN.is_set():raise AnalysisCancelled('Cancelled')
                proc=subprocess.Popen([PYTHON,'-B',os.path.join(_HERE,'af3_pae.py')],stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,encoding='utf8',
                    creationflags=(subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0))
                _PAE_CHILDREN[proc]=cancelled
            started=time.monotonic();payload=json.dumps(request)
            try:
                while True:
                    if cancelled.is_set(): raise AnalysisCancelled('Cancelled')
                    if time.monotonic()-started>1800: raise TimeoutError('PAE analysis timed out')
                    try:
                        out,err=proc.communicate(payload,timeout=.1);break
                    except subprocess.TimeoutExpired: payload=None
                if cancelled.is_set():raise AnalysisCancelled('Cancelled')
                if proc.returncode: raise RuntimeError(err.strip() or 'PAE process failed (possibly out of memory)')
                result=json.loads(out)
                if request.get('action')=='load':
                    import numpy as np
                    result['pae']=np.load(request['preview'],allow_pickle=False)
                return result
            finally:
                if proc.poll() is None: proc.kill()
                proc.communicate()
                with _PAE_CHILDREN_LOCK:_PAE_CHILDREN.pop(proc,None)
    finally: _PAE_PROCESS_LOCK.release()


def load_pae_json(path, max_n=1400):
    try:
        matrix, meta = PAE.load(path)
        ds = max(1,(len(matrix)+max_n-1)//max_n)
        preview = matrix[::ds,::ds].copy()
        return preview, ("preview step=%s; exact analysis uses full matrix" % ds if ds>1 else ""), ds
    except Exception as exc: return None,str(exc),1


# PAE domains 面板每次 Apply 都要不降采样的完整矩阵;大 json 在网络盘上
# 重读要几十秒。按 (路径, mtime) 缓存,最多保留 2 份(2000x2000 约百 MB)。


def load_pae_json_full(path):
    try:
        matrix, meta = PAE.load(path)
        return matrix, "read-only mapped matrix", 1
    except Exception as exc: return None,str(exc),1


def b_side_profile(ranking_path):
    """(#5)从 ranking.csv 计算 B 侧片段剖面(af3.py 的 iptm_profile.csv 只给 A 侧):
    每个 B 片段在所有 A 伙伴上的 best ipTM 与 best_partner。
    排序与 A 侧一致:蛋白按各自最优片段 ipTM 降序,蛋白内按 best_iptm 降序。
    返回 [{frag, best_iptm, best_partner, parent}](已排序)。"""
    if not os.path.isfile(ranking_path):
        return []
    try:
        rows = list(csv.DictReader(io.StringIO(read_text(ranking_path))))
    except Exception:
        return []
    best = {}
    for r in rows:
        b = (r.get("name_b") or "").strip()
        if not b:
            continue
        try:
            v = float((r.get("iptm") or "").strip())
        except (TypeError, ValueError):
            continue
        if b not in best or v > best[b][0]:
            best[b] = (v, (r.get("name_a") or "").strip())
    out = []
    for frag, (v, partner) in best.items():
        parent = frag.split("_t", 1)[0] if "_t" in frag else frag
        out.append({"frag": frag, "best_iptm": v, "best_partner": partner,
                    "parent": parent})
    parent_best = {}
    for r in out:
        parent_best[r["parent"]] = max(parent_best.get(r["parent"], -1), r["best_iptm"])
    out.sort(key=lambda r: (-parent_best[r["parent"]], r["parent"], -r["best_iptm"]))
    return out


# ============================================================================
# 批次清理(#1:跑错了/没用的结果,按类别删,省空间)
# ============================================================================

def fmt_bytes(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.1f} {u}" if u != "B" else f"{n} B"
        n /= 1024
    return f"{n} B"


def _du(path):
    total, count = 0, 0
    for root, dirs, files in os.walk(path):
        for fn in files:
            try:
                total += os.path.getsize(os.path.join(root, fn))
                count += 1
            except OSError:
                pass
    return total, count


def batch_summary(batch, language="zh"):
    spec = R.read_json(batch.get("spec") or "", {})
    lines = ["%s · %s · %s" % (batch_display_name(batch),batch.get("type",""),batch.get("status",spec.get("status",""))),
             "目录: " + batch.get("dir", ""), "最后更新: " + fmt_mtime(spec.get("updated_at",batch.get("mtime",0)))]
    if spec.get("counts"): lines.append("进度: " + "  ".join("%s=%s" % x for x in spec["counts"].items()))
    for key in ("error","scheduler_error","controller_jid","watcher_jid"):
        if spec.get(key): lines.append(key + ": " + str(spec[key]))
    failures = [(name,st) for name,st in spec.get("task_states",{}).items() if st[0] in ("failed","blocked","cancelled")]
    for name,st in failures[:30]: lines.append("%s: %s — %s" % (name,st[0],st[1]))
    if len(failures)>30: lines.append("其余失败项见 spec.json")
    lines.append("日志: " + os.path.join(batch.get("dir",""),"logs"))
    if batch.get('legacy_controller'):
        lines.append('Old snapshot uses squeue --me. Updating the installed program only fixes new submissions.' if language=='en'
                     else '旧任务快照仍使用 squeue --me；更新当前程序只会修复新提交的任务。')
    for alias in batch.get('pool_aliases',[]):
        lines.append(('Associated MSA / original copy: ' if language=='en' else '关联 MSA / 原始副本：')+alias)
    if language == "en":
        words = {"目录: ": "Directory: ", "最后更新: ": "Updated: ", "进度: ": "Progress: ",
                 "其余失败项见 spec.json": "See spec.json for remaining failures", "日志: ": "Logs: "}
        lines = [next((en + line[len(zh):] for zh, en in words.items() if line.startswith(zh)), line) for line in lines]
    return "\n".join(lines)


def prune_seed_targets(batch, iptm_lt=None, rank_gt=None, mode="all"):
    """低置信 pair 的 seed-*/ 瘦身清单(只列不动手):
    读批次 ranking.csv,选出满足阈值的 pair(iptm < iptm_lt / rank > rank_gt,
    mode="all" 两个条件同时满足,"any" 任一满足;rank 为空=未进 topk,视为最差),
    目标是这些 pair 目录下的 seed-* 子目录——顶层 model.cif / confidences /
    summary / data.json / ranking_scores.csv 全部保留。
    返回 {"paths","bytes","count","pairs","desc"};无 ranking.csv 或无命中返回 None。"""
    d = batch.get("dir")
    rk = os.path.join(d, "ranking.csv") if d else None
    if not (rk and os.path.isfile(rk)):
        return None
    out = {"paths": [], "bytes": 0, "count": 0, "pairs": 0}
    with open(rk, encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            folder = (row.get("folder") or "").strip()
            if not folder:
                continue
            if not R.contained(os.path.join(d, folder), d): continue
            try:
                iptm = float(row["iptm"]) if (row.get("iptm") or "").strip() else None
            except ValueError:
                iptm = None
            try:
                rank = int(row["rank"]) if (row.get("rank") or "").strip() else None
            except ValueError:
                rank = None
            conds = []
            if iptm_lt is not None:
                conds.append(iptm is not None and iptm < iptm_lt)
            if rank_gt is not None:
                conds.append(rank is None or rank > rank_gt)
            if not conds:
                continue
            hit = all(conds) if mode == "all" else any(conds)
            if not hit:
                continue
            seeds = [s for s in glob.glob(os.path.join(d, folder, "seed-*"))
                     if os.path.isdir(s) and R.contained(s, d)]
            if not seeds:
                continue
            out["pairs"] += 1
            for s in seeds:
                size, count = _du(s)
                out["paths"].append(s)
                out["bytes"] += size
                out["count"] += count
    if not out["paths"]:
        return None
    cond_txt = []
    if iptm_lt is not None:
        cond_txt.append(f"ipTM < {iptm_lt:g}")
    if rank_gt is not None:
        cond_txt.append(f"rank > {rank_gt}")
    out["desc"] = (f"seed-*/ subdirs of {out['pairs']} low-confidence pairs "
                   f"({' AND '.join(cond_txt) if mode == 'all' else ' OR '.join(cond_txt)}); "
                   "top-level model.cif / confidences / summary are kept")
    return out


def batch_msa_keys(batch):
    """批次 spec 里实体用到的 _msa_key 清单。
    兼容两种 spec:新格式 pair["a"]={"entities":[...]}(侧),旧格式 pair["a"]=实体。"""
    spec_path = batch.get("spec")
    keys = []
    if not (spec_path and os.path.isfile(spec_path)):
        return keys
    try:
        spec = R.read_json(spec_path)
    except Exception:
        return keys

    def add(k):
        if k and k not in keys:
            keys.append(k)

    for p in spec.get("pairs") or []:
        for side in ("a", "b"):
            s = p.get(side) or {}
            ents = s.get("entities") if isinstance(s, dict) else None
            if ents is None:
                ents = [s] if s.get("_msa_key") else []
            for e in ents:
                add(e.get("_msa_key"))
    if not keys:  # run 型:spec 里是 expressions,解析出实体再算 base key(不读盘)
        exprs = spec.get("expressions") or \
            [j.get("expression") for j in spec.get("jobs", []) if j.get("expression")]
        for expr in exprs or []:
            ents, err = capture_af3_parse(expr, fetch=False)
            for e in ents or []:
                set_base_msa_keys([e])
                add(e.get("_msa_key"))
    return keys


def batch_clean_targets(batch):
    """清理清单(只列不动手):{category: {"paths","bytes","count","desc"}}。
    分类:output = 整个批次目录(结果/日志/脚本/输入)。共享 MSA 池永不清理。"""
    out = {}
    d = batch.get("dir")
    if d and os.path.isdir(d):
        size, count = _du(d)
        out["output"] = {"paths": [d], "bytes": size, "count": count,
                         "desc": "entire batch dir (results/logs/scripts/inputs)"}
    return out


def _protect_msa_pools(target, batch):
    """Reject cleanup intersecting current or task-pinned MSA storage."""
    from pathlib import Path
    roots = list(msa_pool_paths())
    if not roots:
        raise ValueError("Cannot safely clean outputs until the MSA directory is configured")
    # A task retains its original shared directories after Setup is changed.
    directory = Path(batch.get("dir") or "")
    paths = (Path(batch.get("spec") or directory / "spec.json"),
             directory / ".runtime" / "config.json")
    for source in paths:
        if not source.exists():
            continue
        saved = R.read_json(source)
        if not isinstance(saved, dict):
            raise ValueError("Cannot verify protected MSA directories: " + str(source))
        configs = [saved]
        if isinstance(saved.get("config"), dict):
            configs.append(saved["config"])
        if saved.get("msa_dir"):
            roots.append(saved["msa_dir"])
        for cfg in configs:
            if cfg.get("HOST_MSA_DATA"):
                roots.append(cfg["HOST_MSA_DATA"])
            backups = cfg.get("MSA_BACKUP_DIRS", [])
            if not isinstance(backups, list):
                raise ValueError("Invalid protected MSA backup list: " + str(source))
            roots.extend(backups)
    target = os.path.normcase(os.path.realpath(target))
    for root in roots:
        if not isinstance(root, str) or not root.strip():
            raise ValueError("Invalid protected MSA directory")
        root = os.path.normcase(os.path.realpath(os.path.expanduser(root)))
        try:
            common = os.path.commonpath([target, root])
        except ValueError:  # Independent Windows drives.
            continue
        if common in (target, root):
            raise ValueError("MSA pool protected: cleanup cannot delete a pool, its contents, or a parent directory: " + root)


def clean_batch(batch, categories, prune=None):
    directory = os.path.realpath(batch.get('dir') or '')
    spec_path = batch.get('spec') or os.path.join(directory, 'spec.json')
    if os.path.realpath(os.path.dirname(spec_path)) != directory:
        return {}, '清理对象与任务计划目录不一致'
    if os.path.islink(batch.get('dir', '')):
        return {}, '不能删除链接指向的结果目录'
    try:
        _protect_msa_pools(directory, batch)
        with R.lifecycle_lock(directory, timeout=.2):
            # Check legacy controllers too; release the inner handle before deletion.
            with R.file_lock(os.path.join(directory, '.controller.lock'), timeout=.2): pass
            return _clean_batch_locked(batch, categories, prune)
    except (TimeoutError, OSError, ValueError) as exc: return {}, str(exc)


def _clean_batch_locked(batch, categories, prune=None):
    """执行清理。categories ⊆ {"output","msa","seeds","all"};prune=(iptm_lt, rank_gt,
    mode) 仅在含 "seeds" 时使用(按删除时的阈值重新计算,不依赖对话框预览)。
    返回 (已删 {类别: 字节}, error)。"""
    latest = R.read_json(batch.get("spec") or "",{})
    if latest: batch = dict(batch,status=latest.get("status"))
    if latest:
        try:
            active=R.queue_snapshot(force=True)
            ids = R.registered_job_ids(latest, batch['dir'])
            if any(R.job_id_matches(actual, expected) for actual in active for expected in ids):
                return {},"控制器或子任务仍在队列中，不能清理"
        except R.SchedulerUnavailable as exc:return {},str(exc)
    if batch.get("status") not in ("done","failed","partial_failed","cancelled"):
        return {}, "运行中的批次不能清理"
    if "msa" in categories:
        return {}, "共享 MSA 由独立缓存管理；请仅清理本批次输出或 seed 产物"
    targets = batch_clean_targets(batch)
    if prune is not None and ("seeds" in categories or "all" in categories):
        pt = prune_seed_targets(batch, *prune)
        if pt:
            targets["seeds"] = pt
    if "all" in categories:
        categories = list(targets)
    categories = sorted(categories, key=lambda c: c != "seeds")  # 先瘦身再整删
    done = {}
    # Recheck after potentially slow size/seed enumeration, under lifecycle lock.
    if latest:
        try:
            active = R.queue_snapshot(force=True)
            ids = R.registered_job_ids(R.read_json(batch.get('spec') or '', latest), batch['dir'])
            if any(R.job_id_matches(a, e) for a in active for e in ids):
                return {}, '检测到新提交的子任务，不能清理'
        except R.SchedulerUnavailable as exc: return {}, str(exc)
    for cat in categories:
        t = targets.get(cat)
        if not t:
            continue
        if any(not R.contained(p, batch['dir'], allow_root=(cat == 'output')) for p in t['paths']):
            return done, '清理路径超出选中任务目录'
        try:
            # Check again immediately before deletion, including symlink aliases
            # and a pool newly configured during the cleanup preview.
            for p in t["paths"]:
                _protect_msa_pools(p, batch)
            if cat == "output":
                shutil.rmtree(t["paths"][0])
            elif cat == "seeds":
                for p in t["paths"]:
                    shutil.rmtree(p)
            else:
                for p in t["paths"]:
                    os.remove(p)
            done[cat] = t["bytes"]
        except Exception as e:
            return done, f"{cat}: {e}"
    return done, None


# ============================================================================
# Setup 页:用户配置 JSON 的读取与白名单更新
# ============================================================================

# 可编辑白名单:key -> (类型, 说明)。派生量(HOST_OUTPUT 等)不直接改,
# 跟随 HOST_BASE;af3.py 子进程每次调用都重读文件,写完立即生效。
EDITABLE_CONFIG = {
    "HOST_BASE": ("str", "部署根目录(工作目录)"),
    "HOST_SIF": ("str", "AlphaFold 3 容器镜像(.sif)"),
    "HOST_DB_SOURCE": ("str", "AF3 数据库目录"),
    "CONTAINER_RUNTIME": ("str", "容器命令名或可执行文件绝对路径"),
    "CONTAINER_MODULE": ("str", "可选环境模块名"),
    "HOST_SSD_CACHE": ("str", "可选节点本地数据库缓存目录"),
    "MSA_PARTITION": ("str", "MSA(CPU) 分区"),
    "INF_PARTITION": ("str", "infer(GPU) 分区"),
    "INF_FALLBACK_PARTITION": ("str", "大 token / OOM 重投分区"),
    "AUX_PARTITION": ("str", "控制任务 CPU 分区，留空使用 MSA 分区"),
    "MSA_MAX_CONCURRENT": ("int", "MSA 默认并发上限"),
    "INF_MAX_CONCURRENT": ("int", "infer 默认并发上限"),
    "INF_BIG_TOKEN": ("int", "自动投大分区的 token 阈值"),
    "INF_MAX_TOKEN": ("int", "单任务 token 硬上限(超过直接拒交)"),
    "INF_BUCKETS": ("str", "infer 桶列表(须与预编译缓存一致,勿随意改)"),
    # 派生路径:默认跟随 HOST_BASE,但允许显式覆盖(写回后 af3.py 里变成绝对路径)
    "HOST_OUTPUT": ("str", "输出根目录(默认 HOST_BASE/output)"),
    "HOST_MSA_DATA": ("str", "MSA 产物池目录(默认 HOST_BASE/msa_data)"),
    "MSA_BACKUP_DIRS": ("paths", "最多两个只新增的 MSA 备份目录"),
    "HOST_MODELS": ("str", "AF3 模型权重目录(默认 HOST_BASE/models)"),
    "HOST_CACHE": ("str", "缓存目录(默认 HOST_BASE/cache)"),
    "HOST_JAX_CACHE": ("str", "JAX 编译缓存目录(默认 HOST_BASE/af3_buckets_cache)"),
}


def current_config():
    """Setup 页表单初值:可编辑键的当前值 + 派生/只读显示值。"""
    editable = {k: getattr(af3, k, "") for k in EDITABLE_CONFIG} if af3 else {}
    derived = {
        "HOST_OUTPUT": HOST_OUTPUT,
        "HOST_MSA_DATA": HOST_MSA_DATA,
        "HOST_MODELS": getattr(af3, "HOST_MODELS", "") if af3 else "",
        "HOST_CACHE": getattr(af3, "HOST_CACHE", "") if af3 else "",
        "HOST_JAX_CACHE": getattr(af3, "HOST_JAX_CACHE", "") if af3 else "",
        "AF3_PY": AF3_PY,
        "PYTHON": PYTHON,
    }
    return {"editable": editable, "derived": derived}


def update_af3_config(updates):
    if not updates: return 0, None, "没有改动"
    try:
        for key in updates:
            if key not in EDITABLE_CONFIG: raise ValueError("不允许修改: " + key)
        clean = R.normalize_config(updates)
        # Resolve derived paths and environment overrides before persisting, so
        # changing only HOST_BASE cannot accidentally nest the primary in a peer.
        candidate = {"__file__": af3.__file__}
        R.configure(candidate, af3._CONFIG_DEFAULTS, updates=clean, apply_assets=False)
        saved = R.save_config(clean)
        af3.reload_config()
        for key in ("HOST_BASE","HOST_OUTPUT","HOST_MSA_DATA","HOST_SPECS","HOST_INFER_DATA"):
            globals()[key] = getattr(af3,key)
        globals()["DOWNLOADS_DIR"] = os.path.join(af3.HOST_BASE,"downloads")
        globals()["UI_TMP_DIR"] = os.path.join(af3.HOST_CACHE,"ui_inputs")
        return len(clean), saved, None
    except Exception as exc: return 0, None, str(exc)


def msa_pool_paths():
    return R.msa_pool_paths(af3.config_snapshot())


def scan_msa_pools(paths=None):
    import af3_msa_sync
    return af3_msa_sync.scan_pools(msa_pool_paths() if paths is None else paths)


def sync_msa_pools(plan):
    import af3_msa_sync
    if plan.get("roots") != msa_pool_paths():
        raise ValueError("MSA directories changed; check them again before synchronization")
    return af3_msa_sync.execute_plan(plan)


def run_af3_stream(args,on_line,timeout=1800):
    cmd=[PYTHON,"-u",AF3_PY]+[str(a) for a in args]
    env=dict(os.environ,PYTHONIOENCODING="utf-8",PYTHONDONTWRITEBYTECODE="1")
    proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,encoding="utf8",errors="replace",cwd=_HERE,env=env)
    timer=threading.Timer(timeout,proc.kill);timer.daemon=True;timer.start()
    from collections import deque
    chunks=deque(maxlen=4000)
    try:
        for line in proc.stdout:
            chunks.append(line)
            on_line(line.rstrip("\n"))
        rc=proc.wait()
        return rc,"".join(chunks),""
    finally: timer.cancel()


_CSV_INDEX = {}
_CSV_VIEWS = {}
_CSV_LOCK = threading.Lock()


def csv_page(path,page=0,size=200,search='',sort_column=None,descending=False):
    from array import array
    version = R.file_version(path)
    with _CSV_LOCK:
        cached = _CSV_INDEX.get(version)
        if cached is None:
            with open(path,'rb') as stream:
                class Lines:
                    offset=0
                    def __iter__(self):return self
                    def __next__(self):
                        line=stream.readline()
                        if not line:raise StopIteration
                        encoding='utf-8-sig' if self.offset==0 else 'utf8'
                        self.offset+=len(line)
                        return line.decode(encoding)
                lines=Lines();reader = csv.reader(lines); columns=next(reader,[])
                offsets = array('Q')
                while True:
                    offset=lines.offset
                    try: next(reader)
                    except StopIteration: break
                    offsets.append(offset)
            if R.file_version(path) != version: raise ValueError('结果正在更新，请刷新')
            if len(_CSV_INDEX)>=8: _CSV_INDEX.pop(next(iter(_CSV_INDEX)))
            cached=(columns,offsets);_CSV_INDEX[version]=cached
    columns,offsets=cached
    with open(path,encoding='utf8',newline='') as stream:
        def read(offset):
            stream.seek(offset);return next(csv.reader(iter(stream.readline,'')))
        if search or sort_column is not None:
            view_key=(version,search,sort_column,descending)
            with _CSV_LOCK: selected=_CSV_VIEWS.get(view_key)
            if selected is None:
                rows=[(o,read(o)) for o in offsets]
                if search: rows=[(o,r) for o,r in rows if search.casefold() in ' '.join(r).casefold()]
                if sort_column is not None:
                    def key(item):
                        row=item[1];value=row[sort_column] if sort_column<len(row) else ''
                        try:return (0,float(value))
                        except ValueError:return (1,value.casefold())
                    rows.sort(key=key,reverse=descending)
                selected=array('Q',(o for o,r in rows))
                with _CSV_LOCK:
                    if len(_CSV_VIEWS)>=8:_CSV_VIEWS.pop(next(iter(_CSV_VIEWS)))
                    _CSV_VIEWS[view_key]=selected
            return columns,[read(o) for o in selected[page*size:(page+1)*size]],len(selected)
        result=[read(o) for o in offsets[page*size:(page+1)*size]]
    return columns,result,len(offsets)


def result_version(outdir):
    names = ('results_index.json','ranking.csv','iptm_matrix.csv','scan_hits.csv',
             'iptm_profile.csv','iptm_matrix_protein.csv')
    files = [os.path.join(outdir,n) for n in names]
    files += glob.glob(os.path.join(outdir,'*_model.cif'))
    files += glob.glob(os.path.join(outdir,'*_confidences.json'))
    return tuple(R.file_version(p) for p in sorted(files))


_PLOT_LOCK=threading.Lock()
def render_plot(path,kind):
    # Matplotlib Agg rendering is serialized; only PNG bytes cross to the GUI.
    with _PLOT_LOCK:
        import pandas as pandas
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        fig=Figure(figsize=(9,5),dpi=100);FigureCanvasAgg(fig);ax=fig.add_subplot(111)
        values=rows=cols=pos=None
        if kind=="matrix":
            df=parse_matrix_df(read_text(path),pandas)
            values=df.values.astype(float);rows=list(map(str,df.index));cols=list(map(str,df.columns))
            image=ax.imshow(values,aspect="auto",cmap="viridis",vmin=0,vmax=1)
            xs=list(range(0,len(cols),max(1,(len(cols)+39)//40)));ys=list(range(0,len(rows),max(1,(len(rows)+39)//40)))
            ax.set_xticks(xs);ax.set_xticklabels([cols[i] for i in xs],rotation=90,fontsize=7)
            ax.set_yticks(ys);ax.set_yticklabels([rows[i] for i in ys],fontsize=7)
            fig.colorbar(image,ax=ax,label="ipTM")
        else:
            df=parse_csv_df(read_text(path),pandas)
            if "best_iptm" in df:
                ax.bar(range(len(df)),pandas.to_numeric(df["best_iptm"],errors="coerce"));ax.set_ylim(0,1)
        fig.tight_layout();fig.canvas.draw();p=ax.get_position();pos=(p.x0,p.y0,p.width,p.height)
        buf=io.BytesIO();fig.savefig(buf,format="png",dpi=120)
        return buf.getvalue(),values,rows,cols,pos
