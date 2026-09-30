#!/usr/bin/env python3
# ==============================================================================
# af3.py - AlphaFold 3 统一提交脚本(MSA / Infer / Rank 一条龙)
# ==============================================================================
#
# 一个脚本统一 AF3 的两阶段流程:
#   af3.py run      <表达式|任务文件|--json F>   端到端:补 MSA -> 自动 Infer
#   af3.py run --msa-only   <同上>               只做 MSA(按序列去重复用)
#   af3.py run --infer-only <同上>               只做 Infer(要求 MSA 已就绪)
#   af3.py pulldown <A> <B>                      A x B 全组合筛选 + 排序 + ipTM 矩阵
#   af3.py scan     <A> <B>                      长蛋白切片段后 A x B 筛选(定位互作位点)
#                                                --mode win 固定滑窗(默认)
#                                                --mode pae 按 PAE domain 切(需全长单体
#                                                预测;缺则自动两阶段:先补全长 MSA+单体
#                                                infer(1 seed),watcher 接力切片筛选)
#   af3.py rank     <目录|清单文件>... [-o DIR]  按 ipTM 排序
#   af3.py pae      <蛋白> [--pae-*]             单蛋白 PAE domain 分段查看(不提交 scan;
#                                                缺 confidences 自动提交单体预测入 infer_data 池)
#   af3.py status   [--jobs 任务文件|spec.json]  进度 + 失败诊断
#   af3.py profile  <scan批次>                   重新生成 scan 全套报告(profile/hits/矩阵/report)
#   (msa / infer 两个旧命令保留为 run --msa-only / --infer-only 的兼容别名)
#
# 表达式语法(一行 = 一个预测任务,实体用 + 连接):
#   P12345                     UniProt 蛋白(自动抓序列)
#   P12345x6                   6 拷贝
#   P12345:trunc=1-300         截短
#   P12345:mut=K5A,R10L        点突变
#   P12345:mod=SEP@5,TPO@12    PTM(infer 阶段注入)
#   P12345:name=myprot         自定义任务名
#   p:<your_amino_acid_sequence>  裸蛋白序列；此处为格式说明，不可直接运行
#   d:ACGT... / r:ACGU...      DNA / RNA
#   l:ATP / l:ATPx2            CCD 配体(多拷贝)
#   l:NAG,FUC                  多 CCD 单配体(糖链)
#   l:CC(=O)Oc1ccccc1C(=O)O    SMILES(含小写自动识别,l: 可省)
#   P12345:msa=/path/to/your/unpaired.a3m        外部 unpaired MSA
#   P12345:tpl=/path/to/your/template.cif:0,1:0,1   手工模板
#   P12345:pae=/path/to/your/confidences.json       指定 PAE 来源 confidences.json(scan --mode pae)
#
# 兼容 Python >= 3.9。运行环境:SLURM 集群 + Singularity,提交机为登录节点。
# 配置与使用说明: README.md 和 docs/
#
# infer 一律挂 JAX 编译缓存(--buckets 桶表见 INF_BUCKETS;普通任务顺便把
# 编译产物持久化到 HOST_JAX_CACHE,同桶后续任务直接命中)。unified memory
# 按可配置 token 阈值分级；缓存按编译环境哈希。不同 GPU、内存和
# 软件版本的容量不同，请先验证资源设置。INF_MAX_TOKEN 是提交策略上限。
# ==============================================================================

import argparse
import copy
import csv
import shlex
import io
import af3_runtime as R
from af3_networkx_community import (_HeapElement, _MappedQueue,
                                    _greedy_modularity_gen, _greedy_modularity_communities)
from af3_pae_domains import _pae_domains_pure
import difflib
import getpass
import glob
import hashlib
import itertools
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

# ============================================================================
# CONFIG —— 按集群修改
# ============================================================================

# --- MSA(data pipeline,CPU;一律不设 --mem) ---
MSA_PARTITION       = ""     # Set your cluster's CPU partition in Setup/config.json.
MSA_NTASKS_SINGLE   = 16    # 非批量 MSA(一批次任务数 < MSA_BATCH_MIN_TASKS)每任务核数
MSA_NTASKS_BATCH    = 8     # 批量 MSA(>= MSA_BATCH_MIN_TASKS)每任务核数
MSA_BATCH_MIN_TASKS = 6     # 一批次 MSA 任务数少于此值即按非批量对待
MSA_MAX_CONCURRENT  = 12    # 批量 MSA 默认最大并发
# SSD 拷贝判据:仅当 (总任务数 * 6 / 最大并发) >= 15 才把数据库拷到节点 SSD
# 例:n=18, mc=6  -> 18 >= 15 拷;n=28, mc=12 -> 14 < 15 不拷
MSA_SSD_FACTOR      = 6
MSA_SSD_MIN_EFF     = 15

# --- Infer(GPU;一律 1 卡 + 12 核,不设 --mem) ---
INF_PARTITION          = ""     # Set your cluster's GPU partition.
INF_FALLBACK_PARTITION = ""     # Optional; empty disables automatic partition changes.
INF_NTASKS             = 12
INF_GPUS               = 1
INF_MAX_CONCURRENT     = 16
INF_BIG_TOKEN          = 3584       # Above this threshold use the configured fallback, if any.
INF_UM_TOKEN           = 5120       # Unified-memory threshold; validate for your GPU and RAM.
INF_MAX_TOKEN          = 7168       # Configurable submission limit; validate for your hardware.
# Inference bucket sizes; compilation caches are created by each installation.
# No precompiled research cache is bundled with the public release.
INF_BUCKETS = "256,512,768,1024,1280,1536,2048,2560,3072,3584,4096,4608,5120,5632,6144,6656,7168"

# --- controller / watcher job ---
AUX_PARTITION   = ""     # Empty follows the resolved MSA_PARTITION.
CONTROLLER_TIME = "02:00:00"
WATCHER_TIME    = "7-00:00:00"
WATCHER_POLL_SEC    = 60
WATCHER_MAX_WAIT_SEC = 7 * 24 * 3600

# --- 容器内路径 ---
CONTAINER_RUNTIME = "singularity"  # Executable name or absolute path.
CONTAINER_MODULE = ""              # Optional Environment Modules name.
CONTAINER_INPUT  = "/root/af_input"
CONTAINER_OUTPUT = "/root/af_output"
CONTAINER_MODELS = "/root/models"
CONTAINER_DB     = "/root/public_databases"
CONTAINER_JAX_CACHE = "/root/jax_cache"   # 编译缓存容器内挂载点

# --- 宿主机路径 ---
HOST_SIF        = ""     # /path/to/your/alphafold3.sif
# Setup saves a separate user configuration; AF3_BASE can override the workspace.
HOST_BASE       = os.path.join(os.path.expanduser("~"), "AF3")
HOST_MODELS     = HOST_BASE + "/models"
HOST_DB_SOURCE  = ""     # /path/to/your/alphafold3_databases
HOST_SSD_CACHE  = ""     # Optional node-local database cache; empty disables copies.
HOST_MSA_DATA   = HOST_BASE + "/msa_data"     # Native AF3 outputs: {key}/{key}_data.json.
MSA_BACKUP_DIRS = []                         # Up to two add-only copies of complete MSA products.
HOST_INFER_DATA = HOST_BASE + "/infer_data"   # 单体全长 infer 产物池 <key>/(scan --mode pae 复用)
HOST_OUTPUT     = HOST_BASE + "/output"       # 一次提交 = 一个自包含目录
HOST_CACHE      = HOST_BASE + "/cache"        # 纯缓存(可随便删)
UNIPROT_CACHE   = HOST_CACHE + "/uniprot"
HOST_JAX_CACHE  = HOST_BASE + "/af3_buckets_cache"      # JAX 编译缓存(预热桶,勿删)
# --- 旧布局路径(已废弃,仅供 status / profile 读取历史数据;不再写入) ---
HOST_LOGS       = HOST_OUTPUT + "/.logs"      # 旧:非批次日志根
HOST_SPECS      = HOST_OUTPUT + "/.specs"     # 旧:spec.json + watcher 状态

SEED_MIN = 1
SEED_MAX = 99999999

AF3_PY = os.path.abspath(__file__)
_CONFIG_DEFAULTS = R.configure(globals())
_MSA_FREE_POLICY = False
_DATA_CACHE = R.JsonCache(384 * 1024 * 1024)
_SEQUENCE_CACHE = {}
MAX_PLAN_TASKS = 100000

def reload_config():
    globals().get("_ENVIRONMENT_CACHE",{}).clear()
    R.configure(globals(), _CONFIG_DEFAULTS)

def config_snapshot():
    return {k: globals()[k] for k in _CONFIG_DEFAULTS if k != "AF3_PY"}


def _is_placeholder_setting(key, value):
    if not isinstance(value, str):
        return False
    if key.endswith("_PARTITION"):
        return bool(re.fullmatch(r"your_[A-Za-z0-9_]*partition", value, re.I))
    return bool(re.search(r"(?:^|/)path/to/your(?:/|$)", value.replace("\\", "/"), re.I))


def deployment_checks(config=None, require_msa=True, require_infer=True,
                      require_scheduler=True, require_aux=True):
    """Read-only deployment checks shared by Setup and actual submissions.

    The container executable is checked inside the job when a module is used;
    login-node PATH alone cannot establish what that module provides.
    """
    values = config_snapshot()
    if config is not None:
        values.update(config)
    try:
        values = R.normalize_config(values)
    except ValueError as exc:
        return [dict(key="configuration", label="Configuration", ok=False, message=str(exc))]
    checks = []

    def add(key, ok, message):
        checks.append(dict(key=key, label=key, ok=bool(ok), message=message))

    if require_scheduler:
        add("platform", sys.platform.startswith("linux"), "Cluster submissions require Linux with Slurm.")
        for command in ("sbatch", "squeue", "flock"):
            add(command, shutil.which(command), command + " must be available in PATH.")
    if not (require_msa or require_infer):
        return checks

    values["AUX_PARTITION"] = values.get("AUX_PARTITION") or values.get("MSA_PARTITION", "")
    used = {"HOST_BASE", "HOST_OUTPUT", "HOST_CACHE", "HOST_SIF", "HOST_DB_SOURCE",
            "HOST_MODELS", "CONTAINER_RUNTIME", "CONTAINER_MODULE"}
    if require_msa:
        used.update(("HOST_MSA_DATA", "HOST_SSD_CACHE", "MSA_PARTITION"))
    if require_infer:
        used.update(("HOST_JAX_CACHE", "HOST_INFER_DATA", "INF_PARTITION", "INF_FALLBACK_PARTITION"))
    if require_scheduler and require_aux:
        used.add("AUX_PARTITION")
    placeholders = {key for key in used if _is_placeholder_setting(key, values.get(key, ""))}
    for key in sorted(placeholders):
        add(key, False, "Replace the example placeholder for " + key + " with your actual cluster setting.")

    for key, is_file in (("HOST_SIF", True), ("HOST_DB_SOURCE", False), ("HOST_MODELS", False)):
        if key in placeholders:
            continue
        path = values.get(key, "")
        valid = bool(path) and (os.path.isfile(path) if is_file else os.path.isdir(path)) and os.access(path, os.R_OK)
        add(key, valid, key + (" must be a readable file." if is_file else " must be a readable directory."))
    writable = ["HOST_BASE", "HOST_OUTPUT", "HOST_CACHE"]
    if require_msa:
        writable.append("HOST_MSA_DATA")
    if require_infer:
        writable.append("HOST_JAX_CACHE")
    for key in writable:
        if key in placeholders:
            continue
        path = values.get(key, "")
        parent = Path(path) if path else None
        while parent is not None and not parent.exists() and parent != parent.parent:
            parent = parent.parent
        valid = parent is not None and parent.is_dir() and os.access(str(parent), os.W_OK | os.X_OK)
        add(key, valid, key + " must be a writable directory or have a writable parent.")
    if require_msa:
        for index, path in enumerate(values.get("MSA_BACKUP_DIRS", []), 1):
            key = "MSA_BACKUP_DIRS[" + str(index) + "]"
            placeholder = _is_placeholder_setting(key, path)
            valid = not placeholder and os.path.isdir(path) and os.access(path, os.R_OK | os.W_OK | os.X_OK)
            add(key, valid, key + " must be an existing readable/writable backup directory; create it explicitly and replace any placeholder.")
    for key, needed in (("MSA_PARTITION", require_msa), ("INF_PARTITION", require_infer)):
        if needed and key not in placeholders:
            add(key, values.get(key), "Set " + key + " to a partition on your cluster.")
    if require_scheduler and require_aux and "AUX_PARTITION" not in placeholders:
        add("AUX_PARTITION", values.get("AUX_PARTITION") or values.get("MSA_PARTITION"),
            "Set the controller partition (AUX_PARTITION, or MSA_PARTITION).")
    runtime = values.get("CONTAINER_RUNTIME", "")
    module = values.get("CONTAINER_MODULE", "")
    if placeholders.intersection(("CONTAINER_RUNTIME", "CONTAINER_MODULE")):
        return checks
    if module:
        add("CONTAINER_RUNTIME", bool(runtime), "The configured module and runtime are checked again inside each job.")
    else:
        add("CONTAINER_RUNTIME", bool(runtime) and shutil.which(runtime), "Container runtime must be available in PATH, or configure CONTAINER_MODULE.")
    return checks


def ensure_deployment(config=None, **stages):
    failures = [check for check in deployment_checks(config, **stages) if not check["ok"]]
    if failures:
        raise R.BusinessError("Deployment is incomplete; update Setup or your AF3_CONFIG file:\n" +
                              "\n".join("- " + item["message"] for item in failures))


def _submission_config(args):
    values = config_snapshot()
    for argument, key in (("partition", "INF_PARTITION"), ("msa_partition", "MSA_PARTITION"),
                          ("aux_partition", "AUX_PARTITION"), ("fallback_partition", "INF_FALLBACK_PARTITION"),
                          ("output_dir", "HOST_OUTPUT"), ("msa_dir", "HOST_MSA_DATA")):
        value = getattr(args, argument, None)
        if value or (argument == "fallback_partition" and value is not None):
            values[key] = value
    if not getattr(args, "aux_partition", None) and getattr(args, "msa_partition", None):
        values["AUX_PARTITION"] = args.msa_partition
    return values


def _container_setup_lines():
    """Generate the same quoted runtime preparation for MSA and inference."""
    values = R.normalize_config({"CONTAINER_RUNTIME": CONTAINER_RUNTIME, "CONTAINER_MODULE": CONTAINER_MODULE})
    runtime, module = values["CONTAINER_RUNTIME"], values["CONTAINER_MODULE"]
    lines = []
    if module:
        lines += ["type module >/dev/null 2>&1 || { echo 'Environment Modules is unavailable; initialize it before submission.' >&2; exit 127; }",
                  "module load " + shlex.quote(module) + " || exit $?"]
    lines += ["command -v " + shlex.quote(runtime) +
              " >/dev/null 2>&1 || { echo 'Configured container runtime is unavailable.' >&2; exit 127; }"]
    return lines

_ENVIRONMENT_CACHE = {}


def _environment_identity():
    key=(HOST_SIF,HOST_DB_SOURCE)
    if key in _ENVIRONMENT_CACHE:return dict(_ENVIRONMENT_CACHE[key])
    def stamp(p):
        try:
            st = os.stat(p); return [os.path.realpath(p), st.st_size, st.st_mtime_ns]
        except OSError: return [os.path.abspath(p), "unavailable"]
    value={"pipeline": stamp(HOST_SIF), "database": stamp(HOST_DB_SOURCE),
            "version": R.read_json(os.path.join(HOST_DB_SOURCE, "database_version.json"), None)}
    _ENVIRONMENT_CACHE[key]=value
    return dict(value)



def _prediction_environment():
    key=(HOST_SIF,HOST_DB_SOURCE,HOST_MODELS)
    if key in _ENVIRONMENT_CACHE:return dict(_ENVIRONMENT_CACHE[key])
    value=_environment_identity()
    weights=[]
    try:
        for item in sorted(Path(HOST_MODELS).iterdir()):
            if item.is_file():
                st=item.stat();weights.append([item.name,st.st_size,st.st_mtime_ns])
    except OSError:weights=["unavailable"]
    value["models"]=[os.path.realpath(HOST_MODELS),weights]
    _ENVIRONMENT_CACHE[key]=value
    return dict(value)



# ============================================================================
# 小工具
# ============================================================================

def note(msg):
    print(msg)


def warn(msg):
    print(f"  WARNING: {msg}")


def die(msg, hint=None):
    print(f"ERROR: {msg}")
    if hint:
        print(f"  提示: {hint}")
    raise R.BusinessError(msg + ("; " + hint if hint else ""))


def random_seed():
    return random.randint(SEED_MIN, SEED_MAX)


def parse_seeds(spec):
    """'12345' / '12345,67890' / 'random' / '' -> [seeds]"""
    if not spec or not spec.strip():
        return [random_seed()]
    seeds = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if part.lower() == "random":
            seeds.append(random_seed())
        else:
            try:
                s = int(part)
                if s < 0 or s > SEED_MAX:
                    die(f"Seed {s} 超出范围 (0-{SEED_MAX})")
                seeds.append(s)
            except ValueError:
                die(f"无效 seed '{part}',应为整数或 'random'")
    return seeds if seeds else [random_seed()]


def get_seeds(seeds_spec=None, num_seeds=None):
    """--num-seeds 优先(生成 N 个随机 seed),否则解析 --seeds。"""
    if num_seeds is not None and num_seeds > 0:
        return [random_seed() for _ in range(num_seeds)]
    return parse_seeds(seeds_spec)


def col_index_to_id(idx):
    """0-based index -> 表格 ID: 0=A, 1=B, ..., 25=Z, 26=AA, ..."""
    result = ""
    n = idx
    while True:
        n, r = divmod(n, 26)
        result = chr(ord("A") + r) + result
        if n == 0:
            break
        n -= 1
    return result


def id_to_col_index(id_str):
    result = 0
    for ch in id_str.upper():
        result = result * 26 + (ord(ch) - ord("A") + 1)
    return result - 1


def md5_short(s, n=8):
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:n]


def sanitize_name(name, maxlen=120):
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    return name[:maxlen]


def format_iptm_for_name(iptm):
    formatted = f"{iptm:.4f}".rstrip("0").rstrip(".")
    return formatted if formatted else "0"


# ============================================================================
# 批次/任务目录的自包含子结构(全脚本唯一的路径拼接规则)
#   <目录>/
#   ├── spec.json        这批是什么:参数、进度、job id
#   ├── logs/{msa,infer}/  + <tag>_controller_%j.out / <tag>_watcher_%j.out
#   ├── scripts/         实际提交的 slurm 脚本
#   ├── input/msa/       这批生成的 MSA 输入 json(可随时重新生成)
#   └── .submitted       watcher 崩溃恢复状态(screen 才有)
# ============================================================================

def batch_paths(root):
    """批次/任务目录的固定子结构路径。"""
    return {
        "logs": os.path.join(root, "logs"),
        "msa_logs": os.path.join(root, "logs", "msa"),
        "infer_logs": os.path.join(root, "logs", "infer"),
        "scripts": os.path.join(root, "scripts"),
        "msa_input": os.path.join(root, "input", "msa"),
        "msa_local": os.path.join(root, "msa_local"),
        "spec": os.path.join(root, "spec.json"),
        "submitted": os.path.join(root, ".submitted"),
    }


_SCAFFOLD_ENTRIES = {"logs", "scripts", "input", "spec.json", ".submitted", "msa_local"}


def _dir_pending_scaffolding(out_dir):
    """目录里只有我们自己的脚手架(logs/scripts/input/spec 等)与 infer 输入 json,
    AF3 还没往里写过结果 -> 视为"待算",可以继续提交。"""
    if not os.path.isdir(out_dir):
        return False
    try:
        entries = os.listdir(out_dir)
    except OSError:
        return False
    for e in entries:
        if e in _SCAFFOLD_ENTRIES or e.endswith("_data.json"):
            continue
        return False
    return True


# ============================================================================
# UniProt 抓取(带磁盘缓存)
# ============================================================================

def fetch_uniprot(uniprot_id):
    """抓取 UniProt 序列,磁盘缓存于 cache/uniprot/。"""
    os.makedirs(UNIPROT_CACHE, exist_ok=True)
    cache_file = os.path.join(UNIPROT_CACHE, f"{uniprot_id}.seq")
    if os.path.isfile(cache_file):
        with open(cache_file) as f:
            seq = f.read().strip()
        if seq:
            return seq
    url = f"https://rest.uniprot.org/uniprotkb/{uniprot_id}.fasta"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "af3.py/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            fasta = resp.read().decode("utf-8")
        seq = "".join(l for l in fasta.strip().split("\n")
                      if not l.startswith(">")).strip()
        if not seq:
            die(f"UniProt '{uniprot_id}' 返回空序列")
        with open(cache_file, "w") as f:
            f.write(seq + "\n")
        return seq
    except urllib.error.HTTPError as e:
        die(f"抓取 UniProt '{uniprot_id}' 失败:HTTP {e.code}",
            hint="若是配体请写成 l:ATP 形式;若是裸序列请检查是否拼写正确")
    except urllib.error.URLError as e:
        die(f"抓取 UniProt '{uniprot_id}' 网络错误:{e}")


# ============================================================================
# 序列操作
# ============================================================================

def apply_truncation(seq, trunc_str):
    if not trunc_str:
        return seq
    m = re.match(r"^(\d+)-(\d+)$", trunc_str)
    if not m:
        die(f"无效截短 '{trunc_str}',应形如 1-500")
    start, end = int(m.group(1)), int(m.group(2))
    if start < 1 or end > len(seq) or start > end:
        die(f"截短 {start}-{end} 超出序列长度 {len(seq)}")
    return seq[start - 1:end]


def apply_mutations(seq, mutations_str):
    if not mutations_str:
        return seq
    seq_list = list(seq)
    for mut in mutations_str.split(","):
        mut = mut.strip()
        if not mut:
            continue
        m = re.match(r"^([A-Za-z])(\d+)([A-Za-z])$", mut)
        if not m:
            die(f"无效突变 '{mut}',应形如 K5A")
        from_aa, pos, to_aa = m.group(1).upper(), int(m.group(2)), m.group(3).upper()
        if pos < 1 or pos > len(seq_list):
            die(f"突变 {mut}:位置 {pos} 超出序列长度 {len(seq_list)}")
        if seq_list[pos - 1] != from_aa:
            warn(f"{mut}:位置 {pos} 预期 {from_aa},实际 {seq_list[pos-1]},仍按要求突变")
        seq_list[pos - 1] = to_aa
    return "".join(seq_list)


def parse_modifications(mod_str, entity_type):
    """'CCD@POS,...' -> AF3 modifications 列表。"""
    if not mod_str:
        return []
    mods = []
    for item in mod_str.split(","):
        item = item.strip()
        if not item:
            continue
        m = re.match(r"^([A-Za-z0-9_-]+)@(\d+)$", item)
        if not m:
            die(f"无效修饰 '{item}',应形如 HY3@1")
        ccd_code, pos = m.group(1), int(m.group(2))
        if entity_type == "protein":
            mods.append({"ptmType": ccd_code, "ptmPosition": pos})
        else:
            mods.append({"modificationType": ccd_code, "basePosition": pos})
    return mods


def parse_template_spec(template_str):
    """'mmcif_path:query_indices:template_indices'(0-based,逗号分隔)。"""
    if not template_str:
        return None
    parts = template_str.split(":")
    if len(parts) != 3:
        die(f"无效模板 '{template_str}',应形如 'a.cif:0,1:0,1'")
    try:
        query_idx = [int(x.strip()) for x in parts[1].split(",") if x.strip()]
        template_idx = [int(x.strip()) for x in parts[2].split(",") if x.strip()]
    except ValueError:
        die("模板索引必须是逗号分隔的整数")
    if len(query_idx) != len(template_idx):
        die(f"queryIndices({len(query_idx)})与 templateIndices({len(template_idx)})长度不一致")
    return {"mmcifPath": parts[0].strip(),
            "queryIndices": query_idx, "templateIndices": template_idx}


def parse_bonds(bonds_str):
    """'CHAIN1:RES1:ATOM1->CHAIN2:RES2:ATOM2;...' -> bondedAtomPairs。"""
    if not bonds_str or not bonds_str.strip():
        return []
    pairs = []
    for bond in bonds_str.split(";"):
        bond = bond.strip()
        if not bond:
            continue
        m = re.match(r"^(\w+):(\d+):(\w+)->(\w+):(\d+):(\w+)$", bond)
        if not m:
            die(f"无效共价键 '{bond}',应形如 'A:145:SG->C:1:C04'")
        pairs.append([[m.group(1), int(m.group(2)), m.group(3)],
                      [m.group(4), int(m.group(5)), m.group(6)]])
    return pairs


# ============================================================================
# 表达式解析(§1)
# ============================================================================

OPTION_KEYS = ["trunc", "mut", "mod", "name", "msa", "pairedmsa", "tpl", "desc", "pae"]

_SMILES_CHARS = set("()[]=#@/\\+.-")


def _looks_like_smiles(s):
    if re.search(r"[a-z]", s):
        return True
    return any(c in _SMILES_CHARS for c in s)


def _is_uniprot_id(s):
    return bool(re.match(r"^[OPQ][0-9][A-Z0-9]{3}[0-9]$", s)) or \
           bool(re.match(r"^[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2}$", s))


def split_entities(expr):
    """按 '+' 切分实体,但忽略 []/() 内部的 '+'(SMILES 如 [NH+])。"""
    parts, depth, cur = [], 0, ""
    for ch in expr:
        if ch in "[(":
            depth += 1
        elif ch in "])":
            depth = max(0, depth - 1)
        if ch == "+" and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def _split_entity_segments(entity_str):
    """按 ':' 切分实体;不含 '=' 的片段并回前一段(兼容 tpl=a.cif:0,1:0,1)。"""
    raw = entity_str.split(":")
    segs = [raw[0]]
    for s in raw[1:]:
        if re.match(r"^[A-Za-z_]+=", s):
            segs.append(s)
        else:
            segs[-1] += ":" + s
    return segs


def _new_entity():
    return {
        "type": None, "source": "", "uniprot": "", "sequence": "",
        "trunc": "", "mut": "", "modifications": [], "copies": 1,
        "name": "", "ccd": None, "smiles": None,
        "msa_path": None, "paired_msa_path": None, "paired_msa": None,
        "template": None, "desc": "", "_msa_key": None, "pae_path": None,
    }


def parse_entity(seg, fetch=True):
    """解析单个实体表达式为 entity dict。"""
    ent = _new_entity()
    # --- 先剥类型前缀(否则 l:O=C=O 这类 SMILES 会被误切成选项) ---
    prefix = None
    rest = seg
    m = re.match(r"^([pdrlPDRL]):(.*)$", seg)
    if m:
        prefix = m.group(1).lower()
        rest = m.group(2)

    parts = _split_entity_segments(rest)
    head = parts[0].strip()

    # --- 选项 ---
    opts = {}
    for o in parts[1:]:
        if "=" not in o:
            die(f"实体 '{seg}' 中无法理解的片段 ':{o}'",
                hint="选项应形如 :trunc=1-300 :mut=K5A :mod=SEP@5 :name=x :msa=f.a3m :tpl=a.cif:0,1:0,1")
        k, v = o.split("=", 1)
        k = k.strip().lower()
        if k not in OPTION_KEYS:
            close = difflib.get_close_matches(k, OPTION_KEYS, n=1)
            hint = f"你是不是想写 ':{close[0]}=...'?" if close else \
                   f"可用选项: {', '.join(OPTION_KEYS)}"
            die(f"未知选项 ':{k}='(实体 '{seg}')", hint=hint)
        opts[k] = v.strip()

    src = head
    if not src:
        die(f"实体 '{seg}' 缺少来源(UniProt / 序列 / CCD / SMILES)")

    # --- 拷贝数 xN(先剥拷贝再判 SMILES;小写 x 不是合法 SMILES 原子,
    #     而 P12345x6 这类 UniProt+拷贝含小写 x,不能先判 SMILES) ---
    # 若整个串本身就是合法 UniProt accession，则整体按单体处理。
    # 拷贝修饰符追加在完整 accession 后，例如 Q12345x2。
    copies = 1
    if not _is_uniprot_id(src.upper()):
        m2 = re.match(r"^(.+?)[xX](\d+)$", src)
        if m2:
            src = m2.group(1)
            copies = int(m2.group(2))
            if copies < 1:
                die(f"拷贝数必须 >= 1(实体 '{seg}')")
    is_smi = _looks_like_smiles(src)

    ent["source"] = src
    ent["copies"] = copies

    # --- 类型判定 ---
    if prefix == "l" or (prefix is None and is_smi):
        ent["type"] = "ligand"
        if _looks_like_smiles(src):
            ent["smiles"] = src
        else:
            codes = [c.strip() for c in src.split(",") if c.strip()]
            if not codes or not all(re.match(r"^[A-Za-z0-9_]+$", c) for c in codes):
                die(f"无效配体 '{src}'", hint="CCD 代码形如 l:ATP 或 l:NAG,FUC;SMILES 直接写即可")
            ent["ccd"] = codes
    elif prefix == "d":
        ent["type"] = "dna"
        ent["sequence"] = src.upper()
        if not re.match(r"^[ACGTN]+$", ent["sequence"]):
            die(f"DNA 序列含非法字符:'{src}'", hint="DNA 仅允许 A/C/G/T/N")
    elif prefix == "r":
        ent["type"] = "rna"
        ent["sequence"] = src.upper()
        if not re.match(r"^[ACGUN]+$", ent["sequence"]):
            die(f"RNA 序列含非法字符:'{src}'", hint="RNA 仅允许 A/C/G/U/N")
    else:
        # protein
        ent["type"] = "protein"
        if prefix == "p" or prefix is None:
            if _is_uniprot_id(src.upper()):
                ent["uniprot"] = src.upper()
            elif re.match(r"^[A-Za-z]+$", src) and len(src) > 15:
                ent["sequence"] = src.upper()
            elif prefix == "p":
                ent["sequence"] = src.upper()
                if not re.match(r"^[A-Z]+$", ent["sequence"]):
                    die(f"蛋白序列含非法字符:'{src}'")
            else:
                # 短字符串:按 UniProt 处理(抓取失败会给出提示)
                ent["uniprot"] = src.upper()
        if ent["uniprot"] and not ent["sequence"]:
            if fetch:
                ent["sequence"] = fetch_uniprot(ent["uniprot"])
        if ent["sequence"]:
            ent["sequence"] = ent["sequence"].upper()
            if not re.match(r"^[A-Z]+$", ent["sequence"]):
                die(f"蛋白序列含非法字符:'{ent['sequence'][:40]}...'")

    # --- 应用选项 ---
    ent["name"] = opts.get("name", "")
    ent["desc"] = opts.get("desc", "")

    if ent["type"] == "protein":
        ent["trunc"] = opts.get("trunc", "")
        ent["mut"] = opts.get("mut", "")
        if ent["trunc"] or ent["mut"]:
            ent["sequence"] = apply_truncation(ent["sequence"], ent["trunc"])
            ent["sequence"] = apply_mutations(ent["sequence"], ent["mut"])
        if "msa" in opts:
            ent["msa_path"] = opts["msa"]
        if "pairedmsa" in opts:
            if opts["pairedmsa"] == "":
                ent["paired_msa"] = ""       # 显式空 paired MSA
            else:
                ent["paired_msa_path"] = opts["pairedmsa"]
        if "tpl" in opts:
            ent["template"] = parse_template_spec(opts["tpl"])
        if "pae" in opts:
            ent["pae_path"] = opts["pae"]
    elif ent["type"] == "rna":
        if "msa" in opts:
            ent["msa_path"] = opts["msa"]
    # 不允许的选项检查
    for bad in ("trunc", "mut", "tpl", "pairedmsa", "pae"):
        if bad in opts and ent["type"] != "protein":
            die(f"选项 ':{bad}=' 只适用于蛋白(实体 '{seg}')")
    if "msa" in opts and ent["type"] not in ("protein", "rna"):
        die(f"选项 ':msa=' 只适用于蛋白/RNA(实体 '{seg}')")

    if "mod" in opts:
        if ent["type"] == "ligand":
            die(f"配体不支持 ':mod='(实体 '{seg}')")
        ent["modifications"] = parse_modifications(opts["mod"], ent["type"])

    return ent


def parse_expression(expr, fetch=True):
    """解析一行表达式 -> entity dict 列表。"""
    entities = []
    for seg in split_entities(expr):
        entities.append(parse_entity(seg, fetch=fetch))
    if not entities:
        die(f"表达式为空:'{expr}'")
    return entities


# ============================================================================
# 批量输入文件(txt / tsv / csv,§1)
# ============================================================================

_HEADER_WORDS = {"id", "uniprot", "name", "entities", "expression"}


def read_task_file(path):
    """读取任务/列表文件,返回表达式列表(每行一个)。"""
    if not os.path.isfile(path):
        die(f"文件不存在:{path}")
    ext = Path(path).suffix.lower()
    expressions = []
    with open(path, "r", encoding="utf-8") as f:
        for ln, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if ext in (".tsv", ".csv"):
                sep = "\t" if ext == ".tsv" else ","
                first = line.split(sep)[0].strip()
                if ln == 1 and first.lower() in _HEADER_WORDS:
                    continue  # 表头
                line = first
            if line:
                expressions.append(line)
    return expressions


def resolve_input_expressions(input_arg):
    """位置参数 -> (expressions, batch_tag or None)。"""
    if os.path.isfile(input_arg):
        exprs = read_task_file(input_arg)
        if not exprs:
            die(f"任务文件为空:{input_arg}")
        return exprs, sanitize_name(Path(input_arg).stem, 60)
    return [input_arg], None


# ============================================================================
# MSA 命名与复用(§2)
# ============================================================================

def base_msa_key(ent):
    """按 §2 规则计算 MSA 任务名;DNA/配体返回 None(不做 MSA)。"""
    t = ent["type"]
    if t == "protein":
        if ent["uniprot"]:
            key = ent["uniprot"]
            if ent["trunc"]:
                key += "_t" + ent["trunc"]
            if ent["mut"]:
                key += "_m" + ent["mut"].replace(",", "_")
            return key
        if ent["name"]:
            key = ent["name"]
            if ent["trunc"]:
                key += "_t" + ent["trunc"]
            if ent["mut"]:
                key += "_m" + ent["mut"].replace(",", "_")
            return key
        return "seq_" + md5_short(ent["sequence"])
    if t == "rna":
        if ent["name"]:
            return ent["name"]
        return "rseq_" + md5_short(ent["sequence"])
    return None


def msa_data_path(key, msa_dir):
    """Native output location, with read compatibility for old flat products."""
    return R.msa_data_path(msa_dir, key)


def _sequence_in_data_json(path, entity_type):
    try:
        st = os.stat(path); key = (path, st.st_size, st.st_mtime_ns, entity_type)
        if key not in _SEQUENCE_CACHE:
            data = read_data_json(path)
            seq = next((entry[entity_type].get("sequence", "") for entry in data.get("sequences", [])
                        if entity_type in entry), None)
            if len(_SEQUENCE_CACHE) >= 4096: _SEQUENCE_CACHE.clear()
            _SEQUENCE_CACHE[key] = seq
        return _SEQUENCE_CACHE[key]
    except (OSError, ValueError, TypeError): return None


def check_msa_ready(key, ent, msa_dir):
    """双保险判定:文件存在 + 序列逐字符一致。返回 'ready' | 'missing' | 'conflict'。"""
    path = msa_data_path(key, msa_dir)
    if not os.path.isfile(path):
        return "missing"
    record = R.read_json(R.msa_record_path(msa_dir, key), {})
    if not isinstance(record, dict):
        return "conflict"
    if record and record.get("status") != "complete":
        return "missing"
    if record and ent.get("_msa_fingerprint") and record.get("fingerprint") != ent["_msa_fingerprint"]:
        return "conflict"
    if R.msa_ready(msa_dir, key, ent):
        return "ready"
    return "conflict"


def count_msa_products(msa_dir):
    """Count completed native and old flat products once; exclude hidden jobs."""
    if not os.path.isdir(msa_dir): return 0
    keys = set()
    for entry in os.scandir(msa_dir):
        if entry.name.startswith("."): continue
        try:
            if entry.is_symlink(): continue
            if entry.is_dir():
                keys.update(p.name[:-10] for p in Path(entry.path).glob("*_data.json") if p.is_file())
            elif entry.name.endswith("_data.json"): keys.add(entry.name[:-10])
        except OSError:
            continue
    count = 0
    for key in keys:
        try: count += R.msa_ready(msa_dir, key)
        except (OSError, ValueError): pass
    return count


def _synthesized_index_path(msa_dir):
    """shared-msa 切列合成的片段 data.json 索引(它们 templates 为空,
    被非 shared-msa 场景复用时需要提醒)。"""
    return os.path.join(msa_dir, ".synthesized")


def _mark_synthesized(key, msa_dir):
    try:
        existing = set()
        if os.path.isfile(_synthesized_index_path(msa_dir)):
            with open(_synthesized_index_path(msa_dir)) as f:
                existing = {l.strip() for l in f if l.strip()}
        if key not in existing:
            with open(_synthesized_index_path(msa_dir), "a") as f:
                f.write(key + "\n")
    except OSError:
        pass


def _is_synthesized(key, msa_dir):
    try:
        with open(_synthesized_index_path(msa_dir)) as f:
            return key in {l.strip() for l in f if l.strip()}
    except OSError:
        return False


_MSA_REVIEW_CONTEXT = None


def _begin_msa_review(args, remote=False):
    """One submission's decisions; approval never applies to future files."""
    global _MSA_REVIEW_CONTEXT
    if not isinstance(getattr(args, 'empty_msa_approved', None), dict):
        args.empty_msa_approved = {}
    _MSA_REVIEW_CONTEXT = dict(args=args, remote=remote, pending={}, replacements={},
                               nonce=R.digest([time.time_ns(), os.getpid(), random.random()], 16))
    return _MSA_REVIEW_CONTEXT


def _review_data_path(path, args, entity=None):
    """Return recompute, or collect/authorize an explicit empty-input choice."""
    fields = R.msa_empty_fields(path, entity)
    if not fields:
        return None
    context = _MSA_REVIEW_CONTEXT
    if context is None or context['args'] is not args:
        context = _begin_msa_review(args)
    path = os.path.abspath(path)
    stamp = R.msa_reuse_fingerprint(path)
    if args.empty_msa_approved.get(path) == stamp:
        return None
    if context['remote']:
        raise R.BusinessError('MSA 含合法空字段，需要确认后才能继续推理: ' + path +
                              ' (' + ', '.join(fields) + ')。请在 GUI 中重试，选择复用或重新 MSA；原文件保留。')
    policy = getattr(args, 'empty_msa_policy', 'review')
    if policy == 'reuse' and not getattr(args, 'dry_run', False):
        args.empty_msa_approved[path] = stamp
        return None
    if policy == 'recompute' and not getattr(args, 'dry_run', False):
        return 'recompute'
    context['pending'][path] = dict(path=path, fields=fields)
    return None


def _finish_msa_review(args):
    context = _MSA_REVIEW_CONTEXT
    if not context or context['args'] is not args or not context['pending']:
        return
    report = {'empty': list(context['pending'].values())}
    print('MSA_REUSE_REVIEW_JSON=' + json.dumps(report, ensure_ascii=False, separators=(',', ':')), flush=True)
    if getattr(args, 'dry_run', False):
        note('[DRY RUN] 上述缓存含合法空字段；正式提交时需选择复用、重新 MSA 或取消。')
        return
    print('MSA 缓存需要确认。使用 --empty-msa-policy reuse 或 recompute；也可取消。原文件会保留。', file=sys.stderr)
    raise SystemExit(42)


def _recompute_entity(ent, msa_dir):
    """Allocate one fresh MSA identity per old input within this submission."""
    context = _MSA_REVIEW_CONTEXT
    if context is None or context['remote']:
        raise R.BusinessError('重新 MSA 必须在交互提交阶段明确选择')
    original_key = ent.get('_msa_key')
    lookup = (os.path.abspath(msa_dir), original_key, ent.get('_msa_fingerprint'))
    for field in ('msa_path', 'paired_msa', 'paired_msa_path', 'template'):
        ent[field] = None
    ent.pop('_shared_source', None)
    ent.pop('_shared_parent_key', None)
    ent['_msa_free'] = False
    if lookup not in context['replacements']:
        base = sanitize_name(base_msa_key(ent) or original_key or 'msa', 80)
        nonce = R.digest([context['nonce'], lookup], 16)
        key = base + '__recalc_' + nonce
        fingerprint = R.digest({'recomputed': nonce, 'previous': lookup[2]})
        context['replacements'][lookup] = (key, fingerprint, nonce)
    key, fingerprint, nonce = context['replacements'][lookup]
    ent.update(_msa_key=key, _msa_fingerprint=fingerprint, _msa_recomputed=nonce,
               _msa_recompute_sequence=ent['sequence'], _msa_recompute_base_key=base_msa_key(ent))
    return key


def _msa_output_occupied(msa_dir, key):
    """Include damaged JSON and companion-only directories; never mutate them."""
    path = msa_data_path(key, msa_dir)
    if os.path.lexists(path):
        return True
    directory = Path(R.msa_native_path(msa_dir, key)).parent
    return os.path.lexists(directory) and (not directory.is_dir() or next(directory.iterdir(), None) is not None)


def _repair_entity(ent, msa_dir):
    """Move a failed cache's planned output to a fresh name, keeping its recipe."""
    context = _MSA_REVIEW_CONTEXT
    if context is None or context['remote']:
        raise R.BusinessError('MSA 原目录已有未完成或损坏的数据；请从 GUI 重试并创建新计划，原数据保留')
    lookup = ('repair', os.path.abspath(msa_dir), ent.get('_msa_key'), ent.get('_msa_fingerprint'))
    if lookup not in context['replacements']:
        nonce = R.digest([context['nonce'], lookup], 16)
        base = sanitize_name(base_msa_key(ent) or ent.get('_msa_key') or 'msa', 80)
        context['replacements'][lookup] = (base + '__repair_' + nonce,
            R.digest({'repair': nonce, 'previous': lookup[-1]}), nonce)
    key, fingerprint, nonce = context['replacements'][lookup]
    ent.update(_msa_key=key, _msa_fingerprint=fingerprint, _msa_recomputed=nonce,
               _msa_recompute_sequence=ent['sequence'], _msa_recompute_base_key=base_msa_key(ent))
    note('MSA 缓存未完成或损坏；保留原目录，改用新目录: ' + key)
    return key


def _review_entities(entities, args, local_dir=None):
    """Review pinned or freshly planned entities without changing pool files."""
    for ent in entities:
        key = ent.get('_msa_key')
        if not key:
            continue
        root = local_dir if local_dir and R.msa_ready(local_dir, key, ent) else args.msa_dir
        if check_msa_ready(key, ent, root) != 'ready':
            occupied_root = local_dir if local_dir and _msa_output_occupied(local_dir, key) else root
            if _msa_output_occupied(occupied_root, key):
                _repair_entity(ent, args.msa_dir)
            continue
        if _review_data_path(msa_data_path(key, root), args, ent) == 'recompute':
            _recompute_entity(ent, args.msa_dir)


def _clear_empty_raw_fields(data, path):
    """Remove only explicitly empty pipeline fields from an in-memory copy."""
    for field in R.msa_empty_fields(path):
        match = re.fullmatch(r'sequences\[(\d+)\]\.(protein|rna)\[[^]]*\]\.(\w+)', field)
        if not match:
            continue
        index, kind, name = match.groups()
        body = data['sequences'][int(index)][kind]
        body.pop(name, None)
        body.pop(name + 'Path', None)
    return data


def resolve_msa_key(ent, msa_dir):
    if ent["type"] not in ("protein", "rna"):
        ent["_msa_key"] = None
        return None, None
    if ent.get('_msa_recomputed') and ent.get('_msa_recompute_sequence') == ent.get('sequence') and ent.get('_msa_recompute_base_key') == base_msa_key(ent):
        return ent['_msa_key'], check_msa_ready(ent['_msa_key'], ent, msa_dir)
    for field in ('_msa_recomputed', '_msa_recompute_sequence', '_msa_recompute_base_key'):
        ent.pop(field, None)
    ent.setdefault("_msa_free", _MSA_FREE_POLICY)
    identity = {"type": ent["type"], "sequence": ent["sequence"],
                "msa_free": ent["_msa_free"], "shared_source": ent.get("_shared_source"), "environment": _environment_identity(),
                "inputs": R.asset_identity({k: ent.get(k) for k in
                    ("msa_path", "paired_msa", "paired_msa_path", "template")})}
    fingerprint = R.digest(identity)
    ent["_msa_fingerprint"] = fingerprint
    legacy_key = "msa_" + fingerprint
    # Existing hash products retain their names and locations. A precise old
    # identity match is reusable; a same-sequence scan across arbitrary caches
    # would ignore external inputs and database/container changes.
    if check_msa_ready(legacy_key, ent, msa_dir) == "ready":
        key = legacy_key
    elif ent.get("uniprot"):
        full_base = base_msa_key(ent)
        base = sanitize_name(full_base, 90)
        record = R.read_json(R.msa_record_path(msa_dir, base), {})
        occupied = record or os.path.exists(os.path.join(msa_dir, base)) or os.path.exists(msa_data_path(base, msa_dir))
        special = base != full_base or ent["_msa_free"] or ent.get("_shared_source") or any(
            ent.get(k) is not None for k in ("msa_path", "paired_msa", "paired_msa_path", "template"))
        # The readable name is used for a new ordinary accession. Provenance
        # records disambiguate later sequence/environment revisions. Ordinary
        # untracked AF3 products are compatible after schema/sequence checks;
        # their historical environment is unknown and must not be invented.
        legacy_ready = not record and R.msa_ready(msa_dir, base, ent)
        same_identity = isinstance(record, dict) and record.get("fingerprint") == fingerprint
        key = base if not special and (not occupied or same_identity or legacy_ready) else base + "__" + fingerprint
    else:
        # Anonymous sequences retain content-based reuse, including aliases.
        key = legacy_key
    ent["_msa_key"] = key
    if (_MSA_REVIEW_CONTEXT is not None and not _MSA_REVIEW_CONTEXT['remote'] and
            os.path.abspath(getattr(_MSA_REVIEW_CONTEXT['args'], 'msa_dir', msa_dir)) == os.path.abspath(msa_dir)):
        _review_entities([ent], _MSA_REVIEW_CONTEXT['args'])
        key = ent['_msa_key']
    return key, check_msa_ready(key, ent, msa_dir)


def entity_display_name(ent):
    """用于任务/目录命名的短名。"""
    t = ent["type"]
    if t in ("protein", "rna"):
        base = base_msa_key(ent) or "seq"
    elif t == "dna":
        base = ent["name"] or ("dna_" + md5_short(ent["sequence"], 6))
    else:  # ligand
        if ent["ccd"]:
            base = "_".join(ent["ccd"])
        else:
            base = "smi_" + md5_short(ent["smiles"], 6)
    if ent["copies"] > 1:
        base += f"_x{ent['copies']}"
    return base


def job_name_for_entities(entities, user_name=None):
    if user_name:
        return sanitize_name(user_name)
    return sanitize_name("_".join(entity_display_name(e) for e in entities))


# ============================================================================
# AF3 JSON 生成(MSA 输入 & infer 拼装)
# ============================================================================

def generate_msa_input_json(ent, key, msa_free=False):
    """单链 MSA 输入 JSON。modelSeeds 仅占位(infer 时无条件覆盖)。"""
    t = ent["type"]
    if t == "protein":
        body = {"id": "A", "sequence": ent["sequence"]}
        if msa_free:
            body["unpairedMsa"] = ""
            body["pairedMsa"] = ""
        else:
            if ent.get("msa_path"):
                body["unpairedMsaPath"] = ent["msa_path"]
            if ent.get("paired_msa") is not None:
                body["pairedMsa"] = ent["paired_msa"]
            elif ent.get("paired_msa_path"):
                body["pairedMsaPath"] = ent["paired_msa_path"]
            if ent.get("template"):
                body["templates"] = [ent["template"]]
        seqs = [{"protein": body}]
    elif t == "rna":
        body = {"id": "A", "sequence": ent["sequence"]}
        if msa_free:
            body["unpairedMsa"] = ""
        elif ent.get("msa_path"):
            body["unpairedMsaPath"] = ent["msa_path"]
        seqs = [{"rna": body}]
    else:
        die(f"实体类型 {t} 不需要 MSA")
    return {"name": key, "modelSeeds": [random_seed()], "sequences": seqs,
            "dialect": "alphafold3", "version": 4}


def read_data_json(filepath):
    if not os.path.isfile(filepath): return None
    def prepare(data):
        def resolve(value):
            if isinstance(value, list): return [resolve(item) for item in value]
            if not isinstance(value, dict): return value
            return {key: (os.path.abspath(os.path.join(os.path.dirname(filepath), item))
                          if key.lower().endswith("path") and isinstance(item, str) and item and not os.path.isabs(item)
                          else resolve(item)) for key, item in value.items()}
        return R.externalize_input(resolve(data), os.path.join(HOST_CACHE, "assets"))
    try: return _DATA_CACHE.load(filepath, transform=prepare, namespace=HOST_CACHE)
    except ValueError as exc: die("无法解析 MSA 数据 %s: %s" % (filepath, exc))


def extract_sequences(data):
    sequences = data.get("sequences", [])
    if not sequences:
        die(f"data.json 中没有 sequences(name='{data.get('name', '?')}')")
    result = []
    for entry in sequences:
        for et in ("protein", "rna", "dna", "ligand"):
            if et in entry:
                result.append((et, entry[et]))
                break
        else:
            die(f"未知实体类型:{list(entry.keys())}")
    return result


def get_entity_ids(edict):
    eid = edict.get("id", "A")
    return list(eid) if isinstance(eid, list) else [eid]


def set_entity_ids(edict, new_ids):
    edict["id"] = new_ids[0] if len(new_ids) == 1 else new_ids


def find_data_json(identifier, msa_dir, strict=True, local_dir=None):
    """Read complete native/legacy products, preferring batch-local fragments."""
    def explicit_ready(path):
        key = Path(path).name.removesuffix("_data.json")
        try:
            for folder in (Path(path).parent, Path(path).parent.parent):
                if (os.path.exists(R.msa_record_path(folder, key)) and
                        os.path.abspath(path) == os.path.abspath(R.msa_native_path(folder, key))):
                    return R.msa_ready(folder, key)
        except ValueError:
            return False
        return R.validate_msa(path)
    if os.path.isfile(identifier) and (os.path.isabs(identifier) or os.path.dirname(identifier)):
        if explicit_ready(identifier): return identifier
        if strict: die("MSA data JSON is invalid or unfinished: " + identifier)
        return None
    # An explicitly selected AF3 output directory can carry an automatically
    # appended timestamp, while its JSON retains the original job name.
    directories = [identifier] if os.path.isdir(identifier) else []
    if os.path.isdir(os.path.join(msa_dir, identifier)):
        directories.append(os.path.join(msa_dir, identifier))
    for directory in dict.fromkeys(directories):
        if Path(directory).name == identifier and not re.search(r"_[0-9]{8}_[0-9]{6}$", identifier):
            continue  # Plain identifiers use managed completion checks below.
        candidates = [str(path) for path in Path(directory).glob("*_data.json") if explicit_ready(str(path))]
        if len(candidates) == 1: return candidates[0]
        if len(candidates) > 1:
            if strict: die("MSA directory contains multiple data JSON files; select one explicitly: " + directory)
            return None
        if strict: die("No complete MSA data JSON in directory: " + directory)
        return None
    key = identifier.removesuffix("_data.json")
    for folder in (local_dir, msa_dir):
        if folder and R.msa_ready(folder, key):
            return msa_data_path(key, folder)
    if strict:
        die(f"找不到 '{identifier}' 的完整 MSA data.json(在 {msa_dir})",
            hint="先运行 af3.py msa 或 af3.py run")
    return None


def build_infer_json(job_name, entities, seeds, msa_dir, template_free=False,
                     bonds=None, user_ccd=None, user_ccd_path=None, local_msa_dir=None):
    """拼装 infer JSON:重排 chain id、扩 copies、注入 PTM/seeds/templates/bonds。
    local_msa_dir(#11)给出时优先从批次本地目录取 MSA 产物(shared-msa 切列)。"""
    seqs = []
    idx = 0
    for ent in entities:
        t = ent["type"]
        copies = ent.get("copies", 1)
        ids = [col_index_to_id(idx + j) for j in range(copies)]
        idx += copies
        if t in ("protein", "rna"):
            path = find_data_json(ent["_msa_key"], msa_dir, strict=True,
                                  local_dir=local_msa_dir)
            errors = R.msa_validation_errors(path, ent)
            if errors:
                raise R.BusinessError('MSA 数据不完整: ' + path + '\n' + '\n'.join(errors))
            if _MSA_REVIEW_CONTEXT is not None:
                _review_data_path(path, _MSA_REVIEW_CONTEXT['args'], ent)
                _finish_msa_review(_MSA_REVIEW_CONTEXT['args'])
            data = read_data_json(path)
            src = None
            for et, ed in extract_sequences(data):
                if et == t:
                    src = dict(ed)  # MSA strings are immutable; do not serialize them again
                    break
            if src is None:
                die(f"{path} 中没有 {t} 实体")
            set_entity_ids(src, ids)
            if ent.get("_msa_free"):
                for field in ("unpairedMsaPath", "pairedMsaPath"):
                    src.pop(field, None)
                src["unpairedMsa"] = ""
                if t == "protein": src["pairedMsa"] = ""
            # PTM 注入(infer 阶段)
            if ent.get("modifications"):
                src["modifications"] = ent["modifications"]
            if template_free:
                src["templates"] = []
            elif ent.get("template"):
                src["templates"] = [ent["template"]]
            if ent.get("desc"):
                src["description"] = ent["desc"]
            seqs.append({t: src})
        elif t == "dna":
            body = {"sequence": ent["sequence"]}
            set_entity_ids(body, ids)
            if ent.get("modifications"):
                body["modifications"] = ent["modifications"]
            if ent.get("desc"):
                body["description"] = ent["desc"]
            seqs.append({"dna": body})
        elif t == "ligand":
            body = {}
            set_entity_ids(body, ids)
            if ent["ccd"]:
                body["ccdCodes"] = list(ent["ccd"])
            else:
                body["smiles"] = ent["smiles"]
            if ent.get("desc"):
                body["description"] = ent["desc"]
            seqs.append({"ligand": body})
    out = {"name": job_name, "modelSeeds": list(seeds), "sequences": seqs,
           "dialect": "alphafold3", "version": 4}
    if bonds:
        out["bondedAtomPairs"] = bonds
    if user_ccd:
        out["userCCD"] = user_ccd
    elif user_ccd_path:
        out["userCCDPath"] = user_ccd_path
    return R.externalize_input(out, os.path.join(HOST_CACHE, "assets"))


# ============================================================================
# SLURM 基础
# ============================================================================

def _run(cmd, timeout=30, inp=None):
    return subprocess.run(cmd, input=inp, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, universal_newlines=True,
                          timeout=timeout)


def submit_job(script_text, script_save_path=None, dependency=None, deployment_config=None):
    """提交 sbatch,返回 job ID。dependency: 'afterok:123:456' 形式。"""
    is_msa = "--norun_inference" in script_text
    is_infer = "--norun_data_pipeline" in script_text
    values = config_snapshot()
    if deployment_config is not None:
        values.update(deployment_config)
    partition = re.search(r"^#SBATCH --partition=(.+)$", script_text, re.M)
    if partition:
        if _is_placeholder_setting("AUX_PARTITION", partition.group(1)):
            raise R.BusinessError("Replace the example partition placeholder before submitting.")
        if is_msa: values["MSA_PARTITION"] = partition.group(1)
        if is_infer: values["INF_PARTITION"] = partition.group(1)
    ensure_deployment(values, require_msa=is_msa, require_infer=is_infer, require_aux=False)
    if dependency:
        # 注入 #SBATCH --dependency 行(放在前部)
        lines = script_text.split("\n")
        insert_at = 1
        while insert_at < len(lines) and lines[insert_at].startswith("#SBATCH"):
            insert_at += 1
        lines.insert(insert_at, f"#SBATCH --dependency={dependency}")
        script_text = "\n".join(lines)
    if script_save_path:
        os.makedirs(os.path.dirname(script_save_path) or ".", exist_ok=True)
        with open(script_save_path, "w") as f:
            f.write(script_text)
        result = _run(["sbatch", script_save_path])
    else:
        result = _run(["sbatch"], inp=script_text)
    if result.returncode != 0:
        die(f"sbatch 失败:{result.stderr.strip()}")
    return result.stdout.strip().split()[-1]


def squeue_jobs(user=None):
    return list(R.queue_snapshot().values())


def count_infer_jobs(tag):
    # Compatibility API. Actual controllers use saved job IDs and one snapshot/round.
    return sum(1 for n, st in squeue_jobs() if n.startswith("afi_" + R.digest(tag, 12)))


def partition_nodes(partition):
    try:
        r = _run(["sinfo", "-p", partition, "-N", "-h", "-o", "%N"], timeout=15)
        if r.returncode != 0:
            return None
        nodes = sorted({l.strip() for l in r.stdout.splitlines() if l.strip()})
        return nodes or None
    except Exception:
        return None


def pin_to_nodes(keep_nodes, partition):
    """决定 pin 方式:('exclude', nodes) | ('nodelist', nodes) | (None, None)。"""
    if not keep_nodes:
        return None, None
    all_nodes = partition_nodes(partition)
    if all_nodes is None:
        return "nodelist", list(keep_nodes)
    excl = sorted(set(all_nodes) - set(keep_nodes))
    if not excl:
        return None, None
    return "exclude", excl


# ============================================================================
# MSA 提交(收编 af3_submit_msa.py:array + SSD two-phase + 每任务日志)
# ============================================================================

def _msa_slurm_lines(job_name, cores, host_in, host_out, array_spec, json_files,
                     use_ssd, batch_log_dir, node_list_file=None,
                     ssd_nodes=None, exclude_nodes=None,
                     partition=MSA_PARTITION):
    lines = ["#!/bin/bash"]
    lines.append(f"#SBATCH --job-name={job_name[:48]}")
    lines.append(f"#SBATCH --partition={partition or MSA_PARTITION}")
    lines.append("#SBATCH --nodes=1")
    lines.append(f"#SBATCH --ntasks-per-node={cores}")
    lines.append(f"#SBATCH --array={array_spec}")
    if batch_log_dir:
        lines.append("#SBATCH --output=/dev/null")
        lines.append("#SBATCH --error=/dev/null")
    else:
        lines.append("#SBATCH --output=%A_%a.out")
        lines.append("#SBATCH --error=%A_%a.err")
    lines.append("#SBATCH --get-user-env")

    # pin 到已缓存 DB 的节点:优先 --exclude(无数节点语义,某些站点的
    # sbatch wrapper 会把 --nodelist + --nodes=1 改成非法节点范围)
    if exclude_nodes:
        lines.append(f"#SBATCH --exclude={','.join(exclude_nodes)}")
    elif ssd_nodes:
        lines.append(f"#SBATCH --nodelist={','.join(ssd_nodes)}")

    lines.append("")

    files_str = " ".join(shlex.quote(f) for f in json_files)
    lines.append(f"JSON_FILES=({files_str})")
    lines.append('JSON_FN=${JSON_FILES[$SLURM_ARRAY_TASK_ID]}')
    lines.append('')
    lines.append('# 从 JSON 文件名推导任务名(如 P12345_input.json -> P12345)')
    lines.append('TASK_NAME=$(basename "$JSON_FN" _input.json)')

    if batch_log_dir:
        lines.append('')
        lines.append('LOG_DIR=' + shlex.quote(batch_log_dir))
        lines.append('mkdir -p "$LOG_DIR"')
        lines.append('exec > "${LOG_DIR}/${TASK_NAME}_${SLURM_ARRAY_JOB_ID}.out" '
                     '2>"${LOG_DIR}/${TASK_NAME}_${SLURM_ARRAY_JOB_ID}.err"')
        lines.append('echo "Task: $TASK_NAME   Array Job ID: $SLURM_ARRAY_JOB_ID   Task ID: $SLURM_ARRAY_TASK_ID"')

    lines.extend(_container_setup_lines())
    if node_list_file:
        lines.append('')
        lines.append('echo "$SLURMD_NODENAME" >> ' + shlex.quote(node_list_file))
        lines.append('echo "[$(date +%H:%M:%S)] Warmup task landed on $SLURMD_NODENAME"')
    lines.append('')

    # Publish a versioned SSD database only after a successful complete copy.
    version = R.digest(_environment_identity(), 16)
    q = shlex.quote
    if use_ssd and HOST_SSD_CACHE:
        cache = os.path.join(HOST_SSD_CACHE, "versions", version)
        lines += ["DB_BIND=" + q(HOST_DB_SOURCE), "CACHE=" + q(cache),
                  'if mkdir -p "$(dirname "$CACHE")" 2>/dev/null; then',
                  '  ( flock -w 1800 9 || exit 1',
                  '    if [ ! -f "$CACHE/.complete" ]; then',
                  '      STAGE="${CACHE}.stage.${SLURM_JOB_ID}.${SLURM_ARRAY_TASK_ID}"',
                  '      mkdir -p "$STAGE" || exit 1',
                  "      cp -a " + q(HOST_DB_SOURCE + "/.") + ' "$STAGE/" || exit 1',
                  '      touch "$STAGE/.complete" || exit 1',
                  '      if [ -d "$CACHE" ]; then mv "$CACHE" "${CACHE}.incomplete.$SLURM_JOB_ID" || exit 1; fi',
                  '      mv "$STAGE" "$CACHE" || exit 1',
                  '    fi',
                  '  ) 9>"${CACHE}.lock"',
                  '  if [ -f "$CACHE/.complete" ]; then DB_BIND="$CACHE"; fi',
                  'fi', 'echo "Database: $DB_BIND"']
        db_bind = '"$DB_BIND"'
    else:
        db_bind = q(HOST_DB_SOURCE)

    lines += ["mkdir -p " + q(host_out) + " " + q(os.path.join(HOST_CACHE, "assets")),
              "MSA_ROOT=" + q(host_out),
              'mkdir -p "$MSA_ROOT/.locks"',
              'exec 8>"$MSA_ROOT/.locks/$TASK_NAME.lock"',
              'flock -w 3600 8 || exit 1',
              'INPUT_JSON=' + q(host_in) + '/"$JSON_FN"',
              q(sys.executable) + ' ' + q(str(Path(AF3_PY).with_name("af3_runtime.py"))) + ' msa-start "$MSA_ROOT" "$TASK_NAME" "$INPUT_JSON" --assets ' + q(os.path.join(HOST_CACHE, "assets")),
              'START_STATUS=$?',
              'if [ "$START_STATUS" -eq 3 ]; then echo "MSA cache ready"; exit 0; fi',
              '[ "$START_STATUS" -eq 0 ] || exit "$START_STATUS"',
              'DATA_JSON="$MSA_ROOT/$TASK_NAME/${TASK_NAME}_data.json"']
    lines.append("")
    lines.append(shlex.quote(CONTAINER_RUNTIME) + " exec \\")
    lines.append(f"    --bind {q(host_in + chr(58) + CONTAINER_INPUT)} \\")
    lines.append(f"    --bind {q(host_out + chr(58) + CONTAINER_OUTPUT)} \\")
    lines.append(f"    --bind {q(HOST_MODELS + chr(58) + CONTAINER_MODELS)} \\")
    lines.append(f"    --bind {db_bind}:{CONTAINER_DB} \\")
    lines.append("    --bind " + shlex.quote(os.path.join(HOST_CACHE, "assets") + ":/root/af_assets") + " \\")
    lines.append(f"    {q(HOST_SIF)} \\")
    lines.append("    python /app/alphafold/run_alphafold.py \\")
    lines.append("    --norun_inference \\")
    lines.append("    --jackhmmer_n_cpu=$SLURM_NTASKS \\")
    lines.append("    --nhmmer_n_cpu=$SLURM_NTASKS \\")
    lines.append(f"    --json_path={CONTAINER_INPUT}/$JSON_FN \\")
    lines.append(f"    --model_dir={CONTAINER_MODELS} \\")
    lines.append(f"    --db_dir={CONTAINER_DB} \\")
    # Leave AF3's own nonempty-directory protection enabled as a second guard
    # against another program creating the same name after msa-start checks it.
    lines.append(f"    --output_dir={CONTAINER_OUTPUT}")
    lines.append("STATUS=$?")
    lines.append("")
    # Preserve the AF3 directory, JSON, and all companion files even on failure.
    lines.append(q(sys.executable) + ' ' + q(str(Path(AF3_PY).with_name("af3_runtime.py"))) + ' msa-finish "$MSA_ROOT" "$TASK_NAME" "$INPUT_JSON" "$STATUS" --assets ' + q(os.path.join(HOST_CACHE, "assets")))
    lines.append('FINISH_STATUS=$?')
    lines.append('[ "$STATUS" -eq 0 ] || exit "$STATUS"')
    lines.append('[ "$FINISH_STATUS" -eq 0 ] || exit "$FINISH_STATUS"')
    if MSA_BACKUP_DIRS:
        # Backups never replace a colleague's existing data. Failure remains
        # visible in this job log and an independent receipt, without invalidating
        # the successfully computed primary MSA used by dependent jobs.
        lines.append(q(sys.executable) + ' ' + q(str(Path(AF3_PY).with_name("af3_runtime.py"))) +
                     ' msa-replicate "$MSA_ROOT" "$TASK_NAME" ' +
                     q(json.dumps(MSA_BACKUP_DIRS, ensure_ascii=False)) +
                     ' --assets ' + q(os.path.join(HOST_CACHE, "assets")))
        lines.append('if [ "$?" -ne 0 ]; then echo "WARNING: MSA backup incomplete; primary data preserved. Check .msa_backup_receipts and use Check MSA sync." >&2; fi')
    lines.append('echo "[$(date +%H:%M:%S)] Done."')
    # templates 可观测性:AF3 在缺省时应自动搜索模板(pipeline.py 已验证)。
    # 若产物里 templates 缺失或为空,多半是集群 AF3 镜像/缺 mmCIF 库所致,提示用户。
    lines.append('if grep -q \'"templates": \\[\\]\' "$DATA_JSON" || '
                 '! grep -q \'"templates"\' "$DATA_JSON"; then')
    lines.append('    echo "[$(date +%H:%M:%S)] WARNING: data.json 中无模板(templates 为空/缺失).'
                 ' 若需要模板,请检查集群 AF3 是否启用模板搜索及 mmCIF 库是否完整."')
    lines.append('fi')
    return "\n".join(lines) + "\n"


def submit_msa_array(json_dir, json_files, cores, mc, use_ssd, slurm_name,
                     batch_log_dir, ssd_nodes=None, node_list_file=None,
                     script_save_path=None, msa_partition=None, msa_dir=None):
    msa_part = msa_partition or MSA_PARTITION
    n = len(json_files)
    array_spec = f"0-{n-1}%{mc}"
    pin_mode, pin_nodes = pin_to_nodes(ssd_nodes, msa_part)
    script = _msa_slurm_lines(
        job_name=slurm_name, cores=cores, host_in=json_dir, host_out=msa_dir or HOST_MSA_DATA,
        array_spec=array_spec, json_files=json_files, use_ssd=use_ssd,
        batch_log_dir=batch_log_dir, node_list_file=node_list_file,
        ssd_nodes=pin_nodes if pin_mode == "nodelist" else None,
        exclude_nodes=pin_nodes if pin_mode == "exclude" else None,
        partition=msa_part)
    journal=(script_save_path or os.path.join(batch_log_dir,'msa_'+R.digest(json_files)+'.sh'))+'.submission.json'
    with R.file_lock(journal+'.lock'):
        previous=R.read_json(journal,{})
        if previous and not previous.get('job_id'):
            raise RuntimeError('MSA 提交结果未确认，禁止重复提交: '+journal)
        if previous.get('job_id'):
            queue=R.queue_snapshot()
            if any(R.job_id_matches(j,previous['job_id']) for j in queue) or time.time()-previous.get('submitted_at',0)<120:
                return previous['job_id']
        history=previous.get('attempts',[])
        if previous.get('job_id'):history=history+[{'job_id':previous['job_id']}]
        R.atomic_json(journal,dict(slurm_name=slurm_name,submitted_at=time.time(),attempts=history))
        try:jid = submit_job(script, script_save_path=script_save_path,
                             deployment_config={"HOST_BASE": msa_dir or HOST_MSA_DATA,
                                                "HOST_OUTPUT": msa_dir or HOST_MSA_DATA,
                                                "HOST_MSA_DATA": msa_dir or HOST_MSA_DATA})
        except R.BusinessError:
            os.unlink(journal);raise
        R.atomic_json(journal,dict(slurm_name=slurm_name,submitted_at=time.time(),job_id=jid,attempts=history))
    note(f"  已提交 MSA array {jid}({n} 个任务,并发 ≤{mc},分区 {msa_part})")
    if ssd_nodes and pin_mode:
        note(f"  已通过 --{pin_mode} pin 到 {','.join(ssd_nodes)}")
    return jid


def wait_for_running_nodes(job_id, n_tasks, node_file=None, poll_sec=10,
                           timeout_sec=1800):
    """等 warmup 任务全部 RUNNING(或离queue),返回它们启动的节点列表。"""
    note(f"  等待 job {job_id} 的 {n_tasks} 个 warmup 任务启动 "
         f"(poll {poll_sec}s,timeout {timeout_sec}s,Ctrl-C 中止)...")
    nodes = set()
    deadline = time.time() + timeout_sec
    while True:
        if node_file and os.path.exists(node_file):
            try:
                with open(node_file) as f:
                    for ln in f:
                        if ln.strip():
                            nodes.add(ln.strip())
            except OSError:
                pass
        try:
            r = _run(["squeue", "-j", str(job_id), "-h", "-o", "%T %N"], timeout=10)
        except Exception:
            if time.time() >= deadline:
                warn("warmup 调度查询超时，后续使用共享数据库")
                break
            time.sleep(poll_sec)
            continue
        out = r.stdout.strip()
        if r.returncode != 0 or not out:
            break
        not_started = False
        for ln in out.splitlines():
            parts = ln.split(None, 1)
            state = parts[0] if parts else ""
            nodelist = parts[1].strip() if len(parts) > 1 else ""
            if state == "RUNNING":
                if nodelist:
                    nodes.add(nodelist)
            elif state in ("PENDING", "CONFIGURING", "SUSPENDED"):
                not_started = True
        if not not_started and nodes:
            break
        if time.time() >= deadline:
            warn("等待 warmup 启动超时,使用已收集到的节点")
            break
        time.sleep(poll_sec)
    return sorted(nodes)


def msa_use_ssd(n_tasks, max_concurrent):
    """批量 MSA 是否拷 SSD:(总任务数 * 6 / 最大并发) >= 15。
    例:n=18, mc=6 -> 18 >= 15 拷;n=28, mc=12 -> 14 < 15 不拷。"""
    mc = max(1, max_concurrent)
    return bool(HOST_SSD_CACHE) and n_tasks * MSA_SSD_FACTOR >= MSA_SSD_MIN_EFF * mc


def msa_cores_for(n_tasks):
    """一批次 MSA 任务数 < 6 按非批量(16 核),否则按批量(8 核)。"""
    return MSA_NTASKS_SINGLE if n_tasks < MSA_BATCH_MIN_TASKS else MSA_NTASKS_BATCH


def submit_msa_stage(missing_ents, tag, args, work_dir):
    """为缺失的 MSA 实体生成输入并提交。返回 job id 列表。
    work_dir:批次/任务目录;MSA 输入写到 <work_dir>/input/msa/,
    每任务日志写到 <work_dir>/logs/msa/(核数按任务数自动:少于 6 个 16 核,否则 8 核)。"""
    msa_dir = args.msa_dir
    msa_part = getattr(args, "msa_partition", None) or MSA_PARTITION
    for ent in missing_ents:
        key = ent['_msa_key']
        if _msa_output_occupied(msa_dir, key) and check_msa_ready(key, ent, msa_dir) != 'ready':
            raise R.BusinessError('MSA 原目录已有未完成或损坏的数据，禁止覆盖；请在 GUI 重试以生成新目录: ' + key)
    paths = batch_paths(work_dir)
    json_dir = paths["msa_input"]
    os.makedirs(json_dir, exist_ok=True)
    os.makedirs(msa_dir, exist_ok=True)

    json_files = []
    for ent in missing_ents:
        key = ent["_msa_key"]
        fingerprint = ent.get("_msa_fingerprint") or (key[4:] if key.startswith("msa_") else None)
        if not fingerprint:
            raise ValueError("MSA plan lacks input identity; create a new batch: " + key)
        R.reserve_msa(msa_dir, key, fingerprint)
        jj = generate_msa_input_json(ent, key, msa_free=ent.get("_msa_free", args.msa_free))
        jj = R.externalize_input(jj, os.path.join(HOST_CACHE, "assets"))
        fname = f"{key}_input.json"
        with open(os.path.join(json_dir, fname), "w") as f:
            json.dump(jj, f, indent=2)
        json_files.append(fname)
    note(f"  已生成 {len(json_files)} 个 MSA 输入 -> {json_dir}")

    n = len(json_files)
    cores = msa_cores_for(n)
    mc = args.max_concurrent or MSA_MAX_CONCURRENT
    use_ssd = msa_use_ssd(n, mc)
    note(f"  MSA 资源配置:{n} 个任务 x {cores} 核"
         f"({'非批量' if n < MSA_BATCH_MIN_TASKS else '批量'}),并发 ≤{mc}")
    if not use_ssd:
        note(f"  SSD 判据 ({n}*{MSA_SSD_FACTOR}/{mc}="
             f"{n * MSA_SSD_FACTOR // mc} < {MSA_SSD_MIN_EFF}):"
             f"直接读共享存储,不拷 SSD")

    batch_log_dir = paths["msa_logs"]
    os.makedirs(batch_log_dir, exist_ok=True)
    scripts_dir = paths["scripts"]
    os.makedirs(scripts_dir, exist_ok=True)
    map_path = os.path.join(batch_log_dir, f"batch_{tag}_task_map.txt")
    with open(map_path, "w") as mf:
        mf.write(f"# Batch: {tag}\n# task_id\tjob_name\n")
        for i, fname in enumerate(json_files):
            mf.write(f"{i}\t{fname.replace('_input.json', '')}\n")
    note(f"  日志目录:{batch_log_dir}")

    slurm_name = f"af3m-{tag}"
    if use_ssd and n > mc:
        # two-phase:warmup 发现 DB 缓存节点,main 立即 pin 过去
        warmup_n = min(mc, n)
        warmup_files, main_files = json_files[:warmup_n], json_files[warmup_n:]
        node_file = os.path.join(batch_log_dir, "db_cache_nodes.txt")
        if os.path.exists(node_file):
            os.remove(node_file)
        note(f"  two-phase 提交:{len(warmup_files)} warmup + {len(main_files)} main")
        warmup_jid = submit_msa_array(json_dir, warmup_files, cores, warmup_n,
                                      use_ssd, f"{slurm_name}-w", batch_log_dir,
                                      ssd_nodes=None, node_list_file=node_file,
                                      script_save_path=os.path.join(
                                          scripts_dir, f"{slurm_name}-w.sh"),
                                      msa_partition=msa_part, msa_dir=msa_dir)
        with open(map_path, "a") as mf:
            mf.write(f"# Warmup Array Job ID: {warmup_jid} (tasks 0-{warmup_n-1})\n")
        warmup_nodes = wait_for_running_nodes(warmup_jid, warmup_n, node_file=node_file)
        if warmup_nodes:
            note(f"  warmup 运行于:{', '.join(warmup_nodes)}")
        else:
            warn("无法确定 warmup 节点,main 不做 pin")
        main_jid = submit_msa_array(json_dir, main_files, cores, mc, use_ssd,
                                    slurm_name, batch_log_dir,
                                    ssd_nodes=warmup_nodes or None,
                                    script_save_path=os.path.join(
                                        scripts_dir, f"{slurm_name}.sh"),
                                    msa_partition=msa_part, msa_dir=msa_dir)
        with open(map_path, "a") as mf:
            mf.write(f"# Main Array Job ID: {main_jid}\n")
        return [warmup_jid, main_jid]
    else:
        jid = submit_msa_array(json_dir, json_files, cores, mc, use_ssd,
                               slurm_name, batch_log_dir,
                               script_save_path=os.path.join(
                                   scripts_dir, f"{slurm_name}.sh"),
                               msa_partition=msa_part, msa_dir=msa_dir)
        with open(map_path, "a") as mf:
            mf.write(f"# SLURM Array Job ID: {jid}\n")
        return [jid]


# ============================================================================
# Infer 提交(收编 af3_submit_infer.py:GPU + OOM 自动重投)
# ============================================================================

# 未知 CCD 配体的保守原子数估计;单原子离子按 1 计
_CCD_ATOM_EST = 30
_SINGLE_ATOM_CCD = {"ZN", "MG", "MN", "CA", "NA", "K", "CL", "FE", "CU",
                    "CO", "NI", "CD", "HG", "F", "BR", "I", "FE2"}


def _smiles_heavy_atoms(smiles):
    """估算 SMILES 重原子数(每个括号基团 / 原子符号 / 芳香小写原子计 1)。"""
    n = len(re.findall(r"\[[^\]]+\]|Cl|Br|[A-Z]|[bcnops]", smiles))
    return max(n, 1)


def estimate_tokens(entities):
    """估算 AF3 总 token 数:蛋白/核酸每残基 1 token,配体每原子 1 token。"""
    total = 0
    for ent in entities:
        copies = ent.get("copies", 1)
        t = ent["type"]
        if t in ("protein", "rna", "dna"):
            total += len(ent["sequence"]) * copies
        elif t == "ligand":
            if ent.get("smiles"):
                atoms = _smiles_heavy_atoms(ent["smiles"])
            else:
                atoms = sum(1 if c.upper() in _SINGLE_ATOM_CCD else _CCD_ATOM_EST
                            for c in ent.get("ccd") or [])
            total += atoms * copies
    return total


def estimate_tokens_from_json(data):
    """从 AF3 JSON(data.json / infer json)估算总 token 数。"""
    total = 0
    for entry in data.get("sequences", []):
        for t, body in entry.items():
            ids = body.get("id", "A")
            copies = len(ids) if isinstance(ids, list) else 1
            if t in ("protein", "rna", "dna"):
                total += len(body.get("sequence", "")) * copies
            elif t == "ligand":
                if body.get("smiles"):
                    atoms = _smiles_heavy_atoms(body["smiles"])
                else:
                    atoms = sum(1 if c.upper() in _SINGLE_ATOM_CCD
                                else _CCD_ATOM_EST
                                for c in body.get("ccdCodes") or [])
                total += atoms * copies
    return total


def infer_partition_for(n_tokens, override=None):
    """按总 token 选 infer 分区:> INF_BIG_TOKEN 直投大显存分区。
    --partition 显式指定时优先。"""
    if override:
        return override
    if n_tokens > INF_BIG_TOKEN and INF_FALLBACK_PARTITION:
        return INF_FALLBACK_PARTITION
    return INF_PARTITION


def check_token_limit(tokens, name):
    """Reject tasks above the configured token limit; actual capacity is hardware-dependent."""
    if tokens > INF_MAX_TOKEN:
        die(f"{name}:总 token ≈{tokens} 超过单卡上限 {INF_MAX_TOKEN},不予提交",
            hint="缩短序列(截短/切 domain)或拆分复合物后再试;"
                  "如已验证其他硬件配置，可调整 INF_MAX_TOKEN")


# JAX persistent compilation cache size limit
JAX_CACHE_MAX_BYTES = 64 * 1024 ** 3


def unified_memory_for(tokens):
    """Enable unified memory above the configured threshold. Validate for your GPU."""
    return tokens > INF_UM_TOKEN


def _infer_env_prefix(unified_memory=True):
    """Inference environment variables, passed through env inside the container.
    Default memory profiles are selected by INF_UM_TOKEN. Unified memory can
    use host RAM but does not guarantee that a large prediction will fit.
    Both profiles use the same persistent compilation-cache location."""
    if unified_memory:
        mem = ("XLA_PYTHON_CLIENT_PREALLOCATE=false "
               "TF_FORCE_UNIFIED_MEMORY=true "
               "XLA_CLIENT_MEM_FRACTION=3.2 ")
    else:
        mem = ("XLA_PYTHON_CLIENT_PREALLOCATE=true "
               "XLA_CLIENT_MEM_FRACTION=0.95 ")
    return ("env XLA_FLAGS=--xla_gpu_enable_triton_gemm=false "
            + mem +
            f"JAX_COMPILATION_CACHE_DIR={CONTAINER_JAX_CACHE} "
            f"JAX_PERSISTENT_CACHE_MAX_SIZE_BYTES={JAX_CACHE_MAX_BYTES}")


def _infer_extra_flags():
    """Use the configured bucket list and installation-specific compilation cache."""
    return [f"--buckets={INF_BUCKETS}",
            f"--jax_compilation_cache_dir={CONTAINER_JAX_CACHE}"]



def _infer_script(job_name, host_input, host_output, json_files, partition,
                  auto_resubmit, log_dir, unified_memory, array_spec=None):
    """Generate one GPU job or array using the current resource configuration."""
    q = shlex.quote
    partition = partition or INF_PARTITION
    logs = log_dir or os.path.join(host_output, "logs", "infer")
    lines = ["#!/bin/bash", "#SBATCH --job-name=" + job_name[:48],
             "#SBATCH --partition=" + partition, "#SBATCH --nodes=1",
             "#SBATCH --ntasks-per-node=" + str(INF_NTASKS),
             "#SBATCH --gres=gpu:" + str(INF_GPUS)]
    if array_spec: lines.append("#SBATCH --array=" + array_spec)
    lines += ["#SBATCH --output=/dev/null", "#SBATCH --error=/dev/null", "#SBATCH --get-user-env",
              "JSON_FILES=(" + " ".join(q(f) for f in json_files) + ")",
              'JSON_FN=${JSON_FILES[${SLURM_ARRAY_TASK_ID:-0}]}',
              'TASK_NAME=${JSON_FN%_data.json}',
              'JOB_ID=${SLURM_ARRAY_JOB_ID:-$SLURM_JOB_ID}',
              'if [ -n "${SLURM_ARRAY_TASK_ID:-}" ]; then JOB_ID=${JOB_ID}_${SLURM_ARRAY_TASK_ID}; fi',
              "OUT=" + q(host_output), "LOG_DIR=" + q(logs),
              'ATTEMPT="$OUT/.attempts/$TASK_NAME"',
              'mkdir -p "$ATTEMPT" "$LOG_DIR" || exit 1',
              'exec >"$LOG_DIR/${TASK_NAME}_${JOB_ID}.out" 2>"$LOG_DIR/${TASK_NAME}_${JOB_ID}.err"',
              'trap \'printf "143\\n" > "$ATTEMPT/$JOB_ID.exit.tmp"; mv "$ATTEMPT/$JOB_ID.exit.tmp" "$ATTEMPT/$JOB_ID.exit"; exit 143\' TERM INT',
              'echo "AF3 task=$TASK_NAME job=$JOB_ID started=$(date -Is)"',
              *_container_setup_lines(), "SECONDS=0",
              "mkdir -p " + q(HOST_JAX_CACHE),
              q(CONTAINER_RUNTIME) + " exec --nv " + " ".join("--bind " + q(a + ":" + b) for a, b in (
                  (host_input, CONTAINER_INPUT), (host_output, CONTAINER_OUTPUT),
                  (HOST_MODELS, CONTAINER_MODELS), (HOST_DB_SOURCE, CONTAINER_DB),
                  (HOST_JAX_CACHE, CONTAINER_JAX_CACHE), (os.path.join(HOST_CACHE, "assets"), "/root/af_assets"))) +
              " " + q(HOST_SIF) + " " + _infer_env_prefix(unified_memory) +
              " JAX_LOG_COMPILES=1 python /app/alphafold/run_alphafold.py --norun_data_pipeline " +
              '--json_path=' + CONTAINER_INPUT + '/"$JSON_FN" ' +
              "--model_dir=" + CONTAINER_MODELS + " --db_dir=" + CONTAINER_DB +
              " --output_dir=" + CONTAINER_OUTPUT + " --force_output_dir " + " ".join(_infer_extra_flags()) +
              ' 2>"$ATTEMPT/$JOB_ID.stderr.log"',
              "AF3_EXIT_CODE=$?", 'cat "$ATTEMPT/$JOB_ID.stderr.log" >&2',
              'echo "AF3 exit=$AF3_EXIT_CODE wall_seconds=$SECONDS finished=$(date -Is)"']
    if auto_resubmit and INF_FALLBACK_PARTITION:
        lines += ['if [ "$AF3_EXIT_CODE" -ne 0 ] && [ "${SLURM_JOB_PARTITION:-}" != ' + q(INF_FALLBACK_PARTITION) +
                  ' ] && grep -Eqi "out of memory|RESOURCE_EXHAUSTED" "$ATTEMPT/$JOB_ID.stderr.log"; then',
                  '  RETRY_ARGS=()',
                  '  if [ -n "${SLURM_ARRAY_TASK_ID:-}" ]; then RETRY_ARGS=(--array="$SLURM_ARRAY_TASK_ID"); fi',
                  '  if RETRY_ID=$(sbatch --parsable --partition=' + q(INF_FALLBACK_PARTITION) + ' "${RETRY_ARGS[@]}" "$0"); then',
                  '    RETRY_ID=${RETRY_ID%%;*}',
                  '    if [ -n "${SLURM_ARRAY_TASK_ID:-}" ]; then RETRY_ID=${RETRY_ID}_${SLURM_ARRAY_TASK_ID}; fi',
                  '    printf "%s\\n" "$RETRY_ID" > "$ATTEMPT/$JOB_ID.retry.tmp"',
                  '    mv "$ATTEMPT/$JOB_ID.retry.tmp" "$ATTEMPT/$JOB_ID.retry"',
                  '    echo "OOM retry job=$RETRY_ID"', '  fi', 'fi']
    lines += [q(sys.executable) + " " + q(str(Path(AF3_PY).with_name("af3_runtime.py"))) +
              ' finish "$OUT" "$TASK_NAME" "$JOB_ID" "$AF3_EXIT_CODE"', 'exit $?']
    return "\n".join(lines) + "\n"


def _pinned_command(work_dir, command, argument):
    source, cfg = R.snapshot(AF3_PY, work_dir, config_snapshot())
    return "env AF3_SNAPSHOT=1 PYTHONUNBUFFERED=1 AF3_CONFIG=" + shlex.quote(cfg) + " " + shlex.quote(sys.executable) + " -u " + \
           shlex.quote(source) + " " + command + " --spec " + shlex.quote(argument)


def _wait_msa(jids, spec_path, ready=None):
    """Wait on exact MSA jobs, or validated published inputs; persist live state."""
    started = time.time()
    absent_since = error_since = None
    deadline = started + WATCHER_MAX_WAIT_SEC
    while jids:
        # Atomic publication and input validation are stronger evidence than a
        # scheduler query. A transient query failure must not block ready inputs.
        if ready is not None and ready():
            update_spec(spec_path, status="msa_ready", scheduler_error=None,
                        controller_heartbeat=time.time())
            return
        try:
            queue = R.queue_snapshot(force=True)
            states = [state for jid, (_, state) in queue.items()
                      if any(R.job_id_matches(jid, expected) for expected in jids)]
            active = bool(states)
            error_since = None
            absent_since = None if active else (absent_since or time.time())
            status = ("msa_running" if any(s in ("RUNNING", "COMPLETING") for s in states)
                      else "msa_queued" if active else "msa_checking")
            update_spec(spec_path, status=status, scheduler_error=None,
                        controller_heartbeat=time.time())
            # Allow atomic results on a shared filesystem to become visible.
            if not active and time.time()-absent_since >= 120:
                return  # Caller validates every required input and reports failure.
        except R.SchedulerUnavailable as exc:
            error_since = error_since or time.time()
            update_spec(spec_path, scheduler_error=str(exc),
                        controller_heartbeat=time.time())
            warn("暂时无法查询 MSA: " + str(exc))
            if R.permanent_queue_error(str(exc)) or time.time()-error_since >= 900:
                raise RuntimeError("MSA 调度查询失败，控制器停止等待；MSA 作业未取消。请检查日志后恢复: " + str(exc))
        if time.time() > deadline: raise RuntimeError("等待 MSA 超时")
        time.sleep(WATCHER_POLL_SEC)


def _monitor_jobs(spec_path, root, names):
    deadline = time.time() + WATCHER_MAX_WAIT_SEC
    while True:
        try: queue = R.queue_snapshot(force=True)
        except R.SchedulerUnavailable:
            update_spec(spec_path, scheduler_error="调度暂不可用，保持原任务状态")
            if time.time() > deadline: raise RuntimeError("调度查询超时")
            time.sleep(WATCHER_POLL_SEC); continue
        states = {n: R.task_state(root, n, queue) for n in names}
        counts = {k: sum(st == k for st, _ in states.values()) for k in
                  ("succeeded", "failed", "running", "queued", "submitted", "confirming", "blocked")}
        if all(st in R.TERMINAL for st, _ in states.values()):
            status = "done" if counts["succeeded"] == len(names) else ("partial_failed" if counts["succeeded"] else "failed")
        else: status = "infer_running"
        update_spec(spec_path, status=status, task_states=states, counts=counts, scheduler_error=None)
        if status != "infer_running":
            R.publish_result_index(root, names)
            work = os.path.dirname(spec_path)
            if os.path.abspath(work) != os.path.abspath(root):
                R.atomic_json(os.path.join(work,'results_index.json'), R.read_json(os.path.join(root,'results_index.json')))
            return states
        if time.time() > deadline: raise RuntimeError("等待推理超时")
        time.sleep(WATCHER_POLL_SEC)


def _infer_slurm_single(job_name, host_input, json_filename, host_output,
                        partition, auto_resubmit=True, log_dir=None, unified_memory=True):
    return _infer_script(job_name, host_input, host_output, [json_filename], partition,
                         auto_resubmit, log_dir, unified_memory)


def _infer_slurm_array(job_name, host_input, host_output, json_files, array_spec,
                       partition, auto_resubmit=True, log_dir=None, unified_memory=True):
    return _infer_script(job_name, host_input, host_output, json_files, partition,
                         auto_resubmit, log_dir, unified_memory, array_spec)


def submit_infer_single(data_json_path, output_dir, tag, partition=INF_PARTITION,
                        auto_resubmit=True, log_dir=None, script_dir=None, unified_memory=True, queue=None):
    name = Path(data_json_path).name.removesuffix("_data.json")
    with R.file_lock(os.path.join(output_dir,".tasks",name+".submit.lock")):
        if queue is None: queue = R.queue_snapshot()
        intent_path=os.path.join(output_dir,'.tasks',name+'.submission.json')
        intent=R.read_json(intent_path)
        if intent:
            jid=intent.get('job_id')
            if not jid:
                matches=[j for j,(n,st) in queue.items() if n==intent.get('slurm_name')]
                if len(matches)==1:jid=matches[0]
            if not jid:
                raise RuntimeError('提交结果未确认，禁止重复提交；请核对精确作业记录: '+intent_path)
            R.record_submission(output_dir,name,jid,data_json_path,tag)
            os.unlink(intent_path)
            return jid
        state, reason = R.task_state(output_dir,name,queue)
        if state in ("queued","running","submitted","confirming","succeeded"):
            return R.read_json(R.task_path(output_dir,name),{}).get("job_id","cached")
        jid = _submit_infer_single_unlocked(data_json_path,output_dir,tag,partition,
                    auto_resubmit,log_dir,script_dir,unified_memory)
        queue[jid] = (name, 'PENDING')
        return jid


def _submit_infer_single_unlocked(data_json_path, output_dir, tag, partition=INF_PARTITION,
                        auto_resubmit=True, log_dir=None, script_dir=None,
                        unified_memory=True):
    host_in = str(Path(data_json_path).parent)
    json_fn = Path(data_json_path).name
    name = Path(data_json_path).stem
    task_name = name[:-5] if name.endswith("_data") else name
    if log_dir is None:
        log_dir = os.path.join(output_dir, task_name, "logs", "infer")
    if script_dir is None:
        script_dir = os.path.join(output_dir, task_name, "scripts")
    os.makedirs(log_dir, exist_ok=True)
    save_path = os.path.join(script_dir, f"afi_{tag}_{name}.sh") if auto_resubmit else None
    script = _infer_slurm_single(f"afi_{R.digest(tag, 12)}_{R.digest(name, 16)}", host_in, json_fn,
                                 output_dir, partition, auto_resubmit,
                                 log_dir=log_dir, unified_memory=unified_memory)
    intent_path=os.path.join(output_dir,'.tasks',task_name+'.submission.json')
    intent=dict(slurm_name=f'afi_{R.digest(tag, 12)}_{R.digest(name, 16)}',started_at=time.time())
    R.atomic_json(intent_path,intent)
    try: jid = submit_job(script, script_save_path=save_path,
                         deployment_config={"HOST_BASE": output_dir, "HOST_OUTPUT": output_dir})
    except R.BusinessError:
        os.unlink(intent_path); raise
    R.atomic_json(intent_path,dict(intent,job_id=jid))
    R.record_submission(output_dir, task_name, jid, data_json_path, tag)
    os.unlink(intent_path)
    return jid


def submit_infer_array(batch_dir, json_files, mc, slurm_name,
                       partition=INF_PARTITION, auto_resubmit=True, log_dir=None,
                       script_dir=None, unified_memory=True):
    intent_path=os.path.join(batch_dir,'.tasks','array_'+R.digest(json_files)+'.submission.json')
    with R.file_lock(intent_path+'.lock'):
        intent=R.read_json(intent_path)
        if intent:
            jid=intent.get('job_id')
            if not jid: raise RuntimeError('数组提交结果未确认，禁止重复提交: '+intent_path)
            for index,filename in enumerate(json_files):
                task=filename[:-10] if filename.endswith('_data.json') else Path(filename).stem
                expected=jid+'_'+str(index)
                if R.read_json(R.task_path(batch_dir,task),{}).get('job_id') != expected:
                    R.record_submission(batch_dir,task,expected,os.path.join(batch_dir,filename),slurm_name)
            os.unlink(intent_path);return jid
        return _submit_infer_array_unlocked(batch_dir,json_files,mc,slurm_name,partition,
            auto_resubmit,log_dir,script_dir,unified_memory,intent_path)


def _submit_infer_array_unlocked(batch_dir, json_files, mc, slurm_name,
                       partition, auto_resubmit, log_dir, script_dir, unified_memory, intent_path):
    n = len(json_files)
    array_spec = f"0-{n-1}%{mc}"
    if log_dir is None:
        log_dir = os.path.join(batch_dir, "logs", "infer")
    if script_dir is None:
        script_dir = os.path.join(batch_dir, "scripts")
    os.makedirs(log_dir, exist_ok=True)
    save_path = os.path.join(script_dir, f"{slurm_name}.sh") if auto_resubmit else None
    script = _infer_slurm_array(slurm_name, batch_dir, batch_dir, json_files,
                                array_spec, partition, auto_resubmit,
                                log_dir=log_dir, unified_memory=unified_memory)
    R.atomic_json(intent_path,dict(slurm_name=slurm_name,started_at=time.time()))
    try: jid = submit_job(script, script_save_path=save_path,
                         deployment_config={"HOST_BASE": batch_dir, "HOST_OUTPUT": batch_dir})
    except R.BusinessError:
        os.unlink(intent_path);raise
    R.atomic_json(intent_path,dict(slurm_name=slurm_name,job_id=jid))
    for index, filename in enumerate(json_files):
        task = filename[:-10] if filename.endswith("_data.json") else Path(filename).stem
        R.record_submission(batch_dir, task, jid + "_" + str(index), os.path.join(batch_dir, filename), slurm_name)
    note(f"  已提交 infer array {jid}({n} 个任务,并发 ≤{mc})"
         + (f" [OOM 自动重投 {INF_FALLBACK_PARTITION}]" if auto_resubmit and INF_FALLBACK_PARTITION else ""))
    note(f"  infer 日志:{log_dir}")
    os.unlink(intent_path)
    return jid


# ============================================================================
# controller / watcher job 提交
# ============================================================================

def _aux_partition_for(args):
    """Controller partition: --aux-partition > --msa-partition > resolved AUX_PARTITION."""
    return (getattr(args, "aux_partition", None)
            or getattr(args, "msa_partition", None) or AUX_PARTITION)


def _aux_slurm_script(job_name, time_limit, log_path, cmd, dependency=None,
                      partition=None):
    lines = [
        "#!/bin/bash",
        f"#SBATCH --job-name={job_name[:48]}",
        f"#SBATCH --partition={partition or AUX_PARTITION}",
        "#SBATCH --nodes=1",
        "#SBATCH --ntasks-per-node=1",
        f"#SBATCH --time={time_limit}",
        f"#SBATCH --output={shlex.quote(log_path)}",
        f"#SBATCH --error={shlex.quote(log_path)}",
        "#SBATCH --get-user-env",
        "",
        cmd,
    ]
    return "\n".join(lines) + "\n"


def _submit_control(spec_path, script, script_path, field, dependency=None):
    intent_path=os.path.join(os.path.dirname(spec_path),'.control_submission.json')
    intent=R.read_json(intent_path)
    if intent:
        jid=intent.get('job_id')
        if not jid:
            queue=R.queue_snapshot(force=True)
            hits=[j for j,(n,st) in queue.items() if n==intent.get('slurm_name')]
            if len(hits)==1:jid=hits[0]
        if not jid:raise RuntimeError('控制器提交结果未确认，禁止重复提交: '+intent_path)
        field=intent.get('field',field)
    else:
        job_name=re.search(r'^#SBATCH --job-name=(.+)$',script,re.M).group(1)
        intent=dict(slurm_name=job_name,field=field,started_at=time.time())
        R.atomic_json(intent_path,intent)
        try:jid=submit_job(script,script_path,dependency=dependency)
        except R.BusinessError:
            os.unlink(intent_path);raise
        R.atomic_json(intent_path,dict(intent,job_id=jid))
    update_spec(spec_path,**{field:jid})
    os.unlink(intent_path);return jid


def submit_controller(spec_path, tag, msa_jids, partition=None):
    work_dir = os.path.dirname(spec_path)
    log_path = os.path.join(work_dir, "logs", "controller_%j.out")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    cmd = _pinned_command(work_dir, "_stage_infer", spec_path)
    script = _aux_slurm_script("af3c_" + R.digest(os.path.abspath(spec_path), 16), WATCHER_TIME, log_path, cmd, partition=partition)
    return _submit_control(spec_path,script,os.path.join(work_dir, "scripts", "controller.sh"),'controller_jid',
                      dependency="afterany:" + ":".join(msa_jids) if msa_jids else None)


def submit_watcher(spec_path, tag, partition=None):
    work_dir = os.path.dirname(spec_path)
    log_path = os.path.join(work_dir, "logs", "watcher_%j.out")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    script = _aux_slurm_script("af3w_" + R.digest(os.path.abspath(spec_path), 16), WATCHER_TIME, log_path,
                               _pinned_command(work_dir, "_pulldown_watcher", spec_path), partition=partition)
    return _submit_control(spec_path,script,os.path.join(work_dir, "scripts", "watcher.sh"),'watcher_jid')


# ============================================================================
# spec.json(§11,UI 的 History 数据源)
# ============================================================================

def write_spec(spec, path):
    spec = R.pin_assets(dict(spec), os.path.join(HOST_CACHE,"assets"))
    spec.setdefault("version", 2)
    spec.setdefault("created_at", time.time())
    spec["updated_at"] = time.time()
    spec.setdefault("config", config_snapshot())
    with R.file_lock(path + ".lock"):
        if os.path.exists(path): raise R.BusinessError('计划已存在: ' + path)
        plan = os.path.join(os.path.dirname(path), "plan.json")
        if not os.path.exists(plan):
            R.snapshot(AF3_PY, os.path.dirname(path), spec["config"])
            R.atomic_json(plan, R.pack_plan(spec))
        small_keys = ('type','name','label','tag','batch_dir','outdir','output_dir','msa_dir',
                      'created_at','updated_at','status','counts','msa_only','infer_only','scan_params',
                      'msa_jids','infer_jids','controller_jid','watcher_jid','continuation_of','result_dir')
        summary = {k:spec[k] for k in small_keys if k in spec}
        summary.update(version=3, storage_version=3, plan_file='plan.json',
                       task_count=len(spec.get('pairs', spec.get('jobs', [None]))))
        R.atomic_json(path, summary)


def load_spec(path):
    value = R.read_json(path)
    if not isinstance(value, dict): raise ValueError('任务计划缺失或损坏: ' + str(path))
    return value


def update_spec(path, **kw):
    return R.update_json(path, **kw)


# ============================================================================
# job 准备与 infer 拼装提交(run / infer / _stage_infer 共用)
# ============================================================================

def build_jobs(expressions, args, batch_tag=None):
    jobs = []
    for expr in expressions:
        entities = parse_expression(expr)
        for ent in entities:
            ent["_msa_free"] = bool(getattr(args, "msa_free", False))
            resolve_msa_key(ent, args.msa_dir)
        seeds = get_seeds(args.seeds, args.num_seeds)
        bonds = parse_bonds(getattr(args, "bonds", None) or "")
        ccd = getattr(args, "user_ccd", None)
        identity = {"entities": [R.entity_identity(e) for e in entities], "seeds": seeds,
                    "msa_free": bool(getattr(args, "msa_free", False)),
                    "template_free": bool(args.template_free), "bonds": bonds,
                    "ccd": R.file_digest(ccd) if ccd else None, "environment": _prediction_environment()}
        if any(e.get('_msa_recomputed') for e in entities):
            identity['msa_recomputed'] = [e.get('_msa_recomputed') for e in entities]
        label = job_name_for_entities(entities, args.name if args.name and len(expressions) == 1 else None)
        name = sanitize_name(label, 64) + "__" + R.digest(identity, 16)
        if getattr(args, "force", False): name += "_" + str(time.time_ns())[-10:]
        jobs.append(dict(expression=expr, name=name, label=label, identity=identity,
                         entities=entities, seeds=seeds, template_free=bool(args.template_free),
                         bonds=bonds, user_ccd_path=ccd))
    return jobs


def collect_missing_msa(jobs, msa_dir=None):
    """收集所有 job 中缺失 MSA 的唯一实体(按 key 去重)。返回 (missing, ready)。"""
    seen = {}
    missing, ready = [], []
    for job in jobs:
        for ent in job["entities"]:
            key = ent.get("_msa_key")
            if key is None or key in seen:
                continue
            seen[key] = True
            status = check_msa_ready(key, ent, msa_dir or args_msa_dir_global)
            if status == "ready":
                ready.append(key)
                if _is_synthesized(key, args_msa_dir_global):
                    warn(f"{key}: 复用的 MSA 是 shared-msa 切列合成产物"
                         "(templates 为空；如需模板请选择重新 MSA，结果写入新目录并保留原数据)")
            else:
                missing.append(ent)
    return missing, ready


# collect_missing_msa 需要一个 msa_dir;用模块级变量避免层层传参
args_msa_dir_global = HOST_MSA_DATA


def assemble_and_submit_infer(jobs, args, batch_dir=None, tag=None):
    """拼装各 job 的 infer JSON 并提交。返回 (infer_jids, submitted_names, skipped)。
    单任务:infer 输入 json 直接写进任务目录(<out>/<name>/<name>_data.json),
    日志/脚本也在任务目录内;批量:json 在批次目录根部,日志/脚本在批次目录内。"""
    msa_dir = args.msa_dir
    out_base = batch_dir if batch_dir else (args.output_dir or HOST_OUTPUT)
    os.makedirs(out_base, exist_ok=True)
    if batch_dir:
        os.makedirs(batch_dir, exist_ok=True)
    tag = tag or "run"
    queue = R.queue_snapshot()
    json_files = []
    skipped = []
    for job in jobs:
        name = job["name"]
        out_dir = os.path.join(out_base, name)
        state, reason = R.task_state(out_base, name, queue)
        if state in ("queued", "running", "submitted", "confirming") or (state == "succeeded" and not args.force):
            note("  SKIP %s: %s %s" % (name, state, reason)); skipped.append(name); continue
        tokens_pre = estimate_tokens(job["entities"])
        if tokens_pre > INF_MAX_TOKEN:
            warn(f"SKIP {name}:总 token ≈{tokens_pre} 超过单卡上限 "
                 f"{INF_MAX_TOKEN}(提交即 OOM,不浪费机时)")
            skipped.append(name)
            continue
        jj = build_infer_json(
            name, job["entities"], job["seeds"], msa_dir,
            template_free=job.get("template_free", False),
            bonds=job.get("bonds") or None,
            user_ccd_path=job.get("user_ccd_path"))
        fname = f"{name}_data.json"
        if batch_dir:
            jpath = os.path.join(out_base, fname)
        else:
            # 单任务:输入 json 直接落在任务目录里(无需事后归档)
            os.makedirs(out_dir, exist_ok=True)
            jpath = os.path.join(out_dir, fname)
        R.atomic_json(jpath, jj)
        tokens = estimate_tokens(job["entities"])
        part = infer_partition_for(tokens, args.partition)
        json_files.append((fname, tokens, part, jpath))
        note(f"  已拼装 {fname}(seeds={job['seeds']}, tokens≈{tokens}, "
             f"分区 {part})")

    if not json_files:
        note("  没有需要提交的 infer 任务")
        return [], skipped

    mc = args.max_concurrent or INF_MAX_CONCURRENT
    jids = []
    if len(json_files) == 1 and not batch_dir:
        fname, tokens, part, jpath = json_files[0]
        jid = submit_infer_single(jpath, out_base, tag, partition=part,
                                  unified_memory=unified_memory_for(tokens))
        note(f"  已提交 infer job {jid}({fname}, 分区 {part})"
             + (f" [OOM 自动重投 {INF_FALLBACK_PARTITION}]" if INF_FALLBACK_PARTITION else ""))
        jids.append(jid)
    else:
        # 按 (分区, 是否开 unified memory) 分组:大 token 任务直投 fallback
        # 分区;>5120 的任务单独成组(同一 array 共享同一份编译 env)
        groups = {}
        for fname, tokens, part, jpath in json_files:
            groups.setdefault((part, unified_memory_for(tokens)), []).append(fname)
        for (part, um), files in groups.items():
            suffix = "" if part == INF_PARTITION else "-big"
            if um:
                suffix += "-um"
            jid = submit_infer_array(out_base, files, mc, f"af3i-{tag}{suffix}",
                                     partition=part, unified_memory=um)
            note(f"  分区 {part}{'(UM)' if um else ''}:{len(files)} 个任务")
            jids.append(jid)
    return jids, skipped


def print_infer_stage_notes(jobs):
    """MSA 命令下提示哪些内容将在 infer 阶段注入。"""
    for job in jobs:
        notes = []
        for ent in job["entities"]:
            if ent["copies"] > 1:
                notes.append(f"{entity_display_name(ent)}:copies={ent['copies']} 只跑 1 次 MSA,拷贝在 infer 阶段扩展")
            if ent.get("modifications"):
                notes.append(f"{entity_display_name(ent)}:PTM 将在 infer 阶段注入")
            if ent["type"] == "ligand":
                notes.append(f"{entity_display_name(ent)}:配体在 infer 阶段加入")
        if job.get("bonds"):
            notes.append("共价键 bonds 在 infer 阶段注入")
        for n in notes:
            note(f"  说明:{n}")


# ============================================================================
# 命令:run(预测唯一入口:默认端到端 MSA->infer;--msa-only / --infer-only 控制阶段;
#          旧命令 msa / infer 保留为兼容别名)
# ============================================================================

def submit_msa_stage_from_files(json_dir, json_files, tag, args, work_dir):
    """已有 MSA 输入 json 文件时直接提交(--json 高级出口用)。"""
    for filename in json_files:
        key = filename.removesuffix('_input.json')
        if _msa_output_occupied(args.msa_dir, key) and not R.msa_ready(args.msa_dir, key):
            raise R.BusinessError('MSA 原目录已有未完成或损坏的数据，禁止覆盖；请在 GUI 重试以生成新目录: ' + key)
    for filename in json_files:
        source = R.read_json(os.path.join(json_dir, filename))
        R.reserve_msa(args.msa_dir, filename.removesuffix("_input.json"),
                      R.digest({"input": R.asset_identity(source), "environment": _environment_identity()}))
    n = len(json_files)
    cores = msa_cores_for(n)
    mc = args.max_concurrent or MSA_MAX_CONCURRENT
    paths = batch_paths(work_dir)
    batch_log_dir = paths["msa_logs"]
    os.makedirs(batch_log_dir, exist_ok=True)
    os.makedirs(paths["scripts"], exist_ok=True)
    jid = submit_msa_array(json_dir, json_files, cores, mc,
                           use_ssd=msa_use_ssd(n, mc),
                           slurm_name=f"af3m-{tag}", batch_log_dir=batch_log_dir,
                           script_save_path=os.path.join(
                               paths["scripts"], f"af3m-{tag}.sh"),
                           msa_partition=getattr(args, "msa_partition", None), msa_dir=args.msa_dir)
    return [jid]


def cmd_run(args):
    _begin_msa_review(args)
    if args.command == "msa": args.msa_only = True
    if args.command == "infer": args.infer_only = True
    if not args.dry_run:
        ensure_deployment(_submission_config(args), require_msa=not args.infer_only,
                          require_infer=not args.msa_only)
    if args.json: return _cmd_run_raw_json(args)
    expressions, batch_tag = resolve_input_expressions(args.input)
    jobs = build_jobs(expressions, args, batch_tag)
    _finish_msa_review(args)
    if any(e.get('_msa_recomputed') for job in jobs for e in job['entities']):
        if args.infer_only and not args.dry_run:
            ensure_deployment(_submission_config(args), require_msa=True, require_infer=not args.msa_only)
        args.infer_only = False
    missing, ready = collect_missing_msa(jobs, args.msa_dir)
    for job in jobs:
        if not args.msa_only: check_token_limit(estimate_tokens(job["entities"]), job["name"])
    if args.infer_only and missing: die("MSA 未就绪: " + ", ".join(e["_msa_key"] for e in missing))
    tag = (args.name or batch_tag or jobs[0]["label"])
    out_base = args.output_dir or HOST_OUTPUT
    batch_dir = os.path.join(out_base, "batch_" + sanitize_name(tag, 60) + "__" + R.digest([j["name"] for j in jobs], 16)) if batch_tag else None
    work_dir = batch_dir or os.path.join(out_base, jobs[0]["name"])
    spec_path = batch_paths(work_dir)["spec"]
    note("计划: %s tasks; %s MSA reused; %s new MSA; %s samples" %
         (len(jobs), len(ready), len(missing), sum(len(j["seeds"]) * 5 for j in jobs)))
    for job in jobs: note("  %s seeds=%s" % (job["name"], job["seeds"]))
    if args.dry_run: return
    if os.path.exists(spec_path):
        if args.infer_only and load_spec(spec_path).get('msa_only'):
            return cmd_continue(argparse.Namespace(spec=spec_path, dry_run=False,
                empty_msa_policy=getattr(args, 'empty_msa_policy', 'review'),
                empty_msa_approved=dict(args.empty_msa_approved)))
        die("批次已存在，请用 retry 从原计划重试: " + spec_path)
    spec = dict(version=2, type="run", name=tag, expressions=expressions, jobs=jobs,
                batch_dir=batch_dir, output_dir=out_base, msa_dir=args.msa_dir,
                partition=args.partition, max_concurrent=args.max_concurrent or INF_MAX_CONCURRENT,
                msa_only=bool(args.msa_only), infer_only=bool(args.infer_only),
                msa_entities=missing, submit_args={k:v for k,v in vars(args).items() if k != "func" and not k.startswith("_")}, force=False,
                msa_jids=[], infer_jids=[], status="preparing")
    write_spec(spec, spec_path)
    jid = submit_controller(spec_path, tag, [], partition=_aux_partition_for(args))
    update_spec(spec_path, controller_jid=jid)
    note("已受理。controller=%s；计划与日志: %s；可关闭 GUI。" % (jid, work_dir))


def _cmd_run_raw_json(args):
    import tempfile
    data = R.read_json(args.json)
    if not isinstance(data, dict): die("JSON 不存在或格式错误: " + args.json)
    if args.seeds is not None or args.num_seeds is not None:
        data["modelSeeds"] = get_seeds(args.seeds, args.num_seeds)
    if not data.get("modelSeeds"): data["modelSeeds"] = get_seeds()
    # Resolve external paths against the input file, before hashing or snapshotting.
    def resolve(obj):
        if isinstance(obj, dict):
            return {k: (os.path.abspath(os.path.join(os.path.dirname(args.json), v))
                        if k.lower().endswith("path") and isinstance(v, str) and not os.path.isabs(v) else resolve(v))
                    for k, v in obj.items()}
        if isinstance(obj, list): return [resolve(v) for v in obj]
        return obj
    data = resolve(data)
    for item in data.get("sequences",[]):
        for kind, body in item.items():
            if args.msa_free and kind in ("protein","rna"):
                body.pop("unpairedMsaPath",None); body["unpairedMsa"]=""
                if kind=="protein": body.pop("pairedMsaPath",None); body["pairedMsa"]=""
            if args.template_free and kind=="protein": body["templates"]=[]
    if getattr(args,"bonds",None): data["bondedAtomPairs"]=parse_bonds(args.bonds)
    if getattr(args,"user_ccd",None): data.pop("userCCD",None);data["userCCDPath"]=os.path.abspath(args.user_ccd)
    # Validate the effective input, including CLI overrides. The temporary file
    # never enters a job directory or MSA pool and external paths are absolute.
    with tempfile.TemporaryDirectory(prefix='af3-input-review-') as temporary:
        review_path = os.path.join(temporary, 'input.json')
        R.atomic_json(review_path, data)
        errors = R.msa_validation_errors(review_path)
        if args.infer_only and errors:
            raise R.BusinessError('推理 JSON 不完整:\n' + '\n'.join(errors))
        if not errors:
            action = _review_data_path(review_path, args)
            # Show the user's stable source path in the dialog, not temp paths.
            context = _MSA_REVIEW_CONTEXT
            if context and review_path in context['pending']:
                item = context['pending'].pop(review_path)
                item['path'] = os.path.abspath(args.json)
                context['pending'][item['path']] = item
            _finish_msa_review(args)
            if action == 'recompute':
                _clear_empty_raw_fields(data, review_path)
                if args.infer_only and not args.dry_run:
                    ensure_deployment(_submission_config(args), require_msa=True, require_infer=not args.msa_only)
                args.infer_only = False
                data['name'] = sanitize_name(data.get('name') or Path(args.json).stem, 64) + '__recalc_' + context['nonce']
    label = sanitize_name(args.name or data.get("name") or Path(args.json).stem, 64)
    name = label + "__" + R.digest({"input": R.asset_identity(data), "environment": _prediction_environment()}, 16)
    if args.force: name += "_" + str(time.time_ns())[-10:]
    data["name"] = name
    if not args.msa_only: check_token_limit(estimate_tokens_from_json(data), name)
    # End-to-end raw jobs can also reuse an existing pipeline output by key.
    if not args.infer_only and _msa_output_occupied(args.msa_dir, name) and not R.msa_ready(args.msa_dir, name):
        name += '__repair_' + _MSA_REVIEW_CONTEXT['nonce']
        data['name'] = name
        note('MSA 原目录包含未完成或损坏的数据；将保留原目录并在新目录计算: ' + name)
    if not args.infer_only and R.msa_ready(args.msa_dir, name):
        cached_path = msa_data_path(name, args.msa_dir)
        action = _review_data_path(cached_path, args)
        _finish_msa_review(args)
        if action == 'recompute':
            name += '__recalc_' + _MSA_REVIEW_CONTEXT['nonce']
            data['name'] = name
    work_dir = os.path.join(args.output_dir or HOST_OUTPUT, name)
    note("Raw JSON 计划: " + name)
    if args.dry_run: return
    paths = batch_paths(work_dir)
    if os.path.exists(paths['spec']):
        if args.infer_only and load_spec(paths['spec']).get('msa_only'):
            return cmd_continue(argparse.Namespace(spec=paths['spec'], dry_run=False,
                empty_msa_policy=getattr(args, 'empty_msa_policy', 'review'),
                empty_msa_approved=dict(args.empty_msa_approved)))
        die('批次已存在，请用 retry: '+paths['spec'])
    data = R.externalize_input(data, os.path.join(HOST_CACHE, "assets"))
    input_path = os.path.join(paths["msa_input"], name + "_input.json")
    R.atomic_json(input_path, data)
    if R.validate_msa(input_path) and getattr(args, 'empty_msa_policy', 'review') == 'reuse':
        _review_data_path(input_path, args)
    spec = dict(version=2, type="raw_json", name=name, label=label, raw_input=input_path,
                msa_dir=args.msa_dir, output_dir=args.output_dir or HOST_OUTPUT,
                partition=args.partition, msa_only=bool(args.msa_only), infer_only=bool(args.infer_only),
                submit_args={k:v for k,v in vars(args).items() if k != "func" and not k.startswith("_")}, msa_jids=[], infer_jids=[], status="preparing")
    write_spec(spec, paths["spec"])
    jid = submit_controller(paths["spec"], name, [], partition=_aux_partition_for(args))
    update_spec(paths["spec"], controller_jid=jid)
    note("已受理 controller=%s；%s" % (jid, work_dir))


# ============================================================================
# 隐藏命令:_stage_infer(controller job 入口)
# ============================================================================



def cmd_stage_infer(args):
    spec = load_spec(args.spec)
    sargs = argparse.Namespace()
    sargs.__dict__.update(spec.get("submit_args") or {})
    for k, v in dict(msa_dir=spec.get("msa_dir", HOST_MSA_DATA),
                     output_dir=spec.get("output_dir", HOST_OUTPUT),
                     max_concurrent=spec.get("max_concurrent"), partition=spec.get("partition"),
                     msa_free=False, force=False, msa_partition=None).items():
        if not hasattr(sargs, k): setattr(sargs, k, v)
    work_dir = os.path.dirname(args.spec)
    _begin_msa_review(sargs, remote=True)
    with R.controller_lock(work_dir, timeout=1):
        update_spec(args.spec, status="preparing", error=None)
        if spec.get("type") == "raw_json":
            name = spec["name"]
            data_path = spec["raw_input"] if spec.get("infer_only") else msa_data_path(name, sargs.msa_dir)
            if not spec.get("infer_only") and not R.msa_ready(sargs.msa_dir, name):
                jp = spec["raw_input"]
                jids = submit_msa_stage_from_files(os.path.dirname(jp), [os.path.basename(jp)], name, sargs, work_dir)
                update_spec(args.spec, msa_jids=jids, status="msa_submitted")
                _wait_msa(jids, args.spec, ready=lambda: R.msa_ready(sargs.msa_dir, name))
                data_path = msa_data_path(name, sargs.msa_dir)
            if not spec.get("infer_only") and not R.msa_ready(sargs.msa_dir, name):
                raise RuntimeError("MSA job has not completed successfully: " + data_path)
            errors = R.msa_validation_errors(data_path)
            if errors: raise RuntimeError("MSA 失败或 JSON 不完整: " + data_path + '\n' + '\n'.join(errors))
            if spec.get("msa_only"):
                update_spec(args.spec, status="done",counts={"succeeded":1}); return
            _review_data_path(data_path, sargs)
            data = read_data_json(data_path)
            jj = os.path.join(work_dir, name + "_data.json"); R.atomic_json(jj, data)
            tokens = estimate_tokens_from_json(data); check_token_limit(tokens, name)
            jid = submit_infer_single(jj, sargs.output_dir, name, partition=infer_partition_for(tokens, sargs.partition),
                                      unified_memory=unified_memory_for(tokens))
            update_spec(args.spec, infer_jids=[jid], status="infer_submitted")
            _monitor_jobs(args.spec, sargs.output_dir, [name]); return
        jobs = spec["jobs"]
        missing, _ = collect_missing_msa(jobs, sargs.msa_dir)
        if missing:
            if spec.get("infer_only"): raise RuntimeError("推理依赖 MSA 缺失")
            jids = submit_msa_stage(missing, str(spec["name"]), sargs, work_dir)
            update_spec(args.spec, msa_jids=jids, status="msa_submitted")
            _wait_msa(jids, args.spec, ready=lambda: not collect_missing_msa(jobs, sargs.msa_dir)[0])
        missing, _ = collect_missing_msa(jobs, sargs.msa_dir)
        if missing: raise RuntimeError("MSA 未就绪: " + ", ".join(e["_msa_key"] for e in missing))
        if spec.get("msa_only"):
            update_spec(args.spec, status="done",counts={"succeeded":len(jobs)}); return
        jids, skipped = assemble_and_submit_infer(jobs, sargs, batch_dir=spec.get("batch_dir"), tag=spec["name"])
        update_spec(args.spec, infer_jids=jids, status="infer_submitted")
        root = spec.get("batch_dir") or sargs.output_dir
        _monitor_jobs(args.spec, root, [j["name"] for j in jobs])
        for job in jobs:
            ents = job["entities"]
            if len(ents) == 1 and ents[0]["type"] == "protein" and ents[0].get("copies", 1) == 1 and not ents[0].get("modifications"):
                pass  # PAE uses its own deterministic prediction identity.


# ============================================================================
# 命令:pulldown(§6)与 scan(§7)共用流水线
# ============================================================================

def _non_msa_label(ent):
    """dna/配体(无 MSA key)的侧标签(短、可读)。"""
    t = ent["type"]
    if t == "dna":
        return ent.get("name") or ("dna_" + md5_short(ent.get("sequence", ""), 6))
    if t == "ligand":
        return ",".join(ent.get("ccd") or []) or (ent.get("smiles") or "lig")[:12]
    if t == "rna":
        return ent.get("name") or ("rseq_" + md5_short(ent.get("sequence", ""), 6))
    return t


def _ent_label(ent):
    base = base_msa_key(ent) or _non_msa_label(ent)
    if ent.get("_frag"):
        frag=ent["_frag"]
        base="%s_t%s-%s" % (frag["parent"],frag["start"],frag["end"])
    if ent.get("modifications"):
        base += "[" + ";".join(str(m.get("ptmType", m.get("modificationType", ""))) + "@" +
                    str(m.get("ptmPosition", m.get("basePosition", ""))) for m in ent["modifications"]) + "]"
    return base + ("x" + str(ent.get("copies", 1)) if ent.get("copies", 1) > 1 else "")


def _side_label(entities):
    """侧显示名:侧内实体用 + 连接(如 Ax2+B_t1-300+C)。"""
    return "+".join(_ent_label(e) for e in entities)


def _side_sig(side):
    return tuple(sorted(R.digest(dict(R.entity_identity(e), msa_recomputed=e['_msa_recomputed'])
                                 if e.get('_msa_recomputed') else R.entity_identity(e))
                        for e in side["entities"]))


def side_entities(sides):
    """[side, ...] -> 平铺实体列表。"""
    return [e for s in sides for e in s["entities"]]


def resolve_side_group(arg, group_label):
    """pulldown/scan 的一组:每行一个"侧" = 1+ 个实体(+ 连接复合物,允许 xN)。
    侧内允许 蛋白(含 PTM :mod=)/ DNA / RNA / 配体;返回 [side, ...]。
    注意:scan 切片只切未修饰蛋白(见 cmd_scan variants)。"""
    if os.path.isfile(arg):
        entries = read_task_file(arg)
        if not entries:
            die(f"{group_label}:列表文件为空:{arg}")
    else:
        entries = [arg]
    sides = []
    for e in entries:
        parsed = parse_expression(e)
        for ent in parsed:
            if ent["type"] not in ("protein", "dna", "rna", "ligand"):
                die(f"{group_label} 条目 '{e}' 含未知实体类型({ent['type']})")
        # "expr" 保留初始输入行,写入 spec 供 rebuild resubmit 精确重提
        sides.append({"entities": parsed, "label": _side_label(parsed), "expr": e})
    return sides


def build_pairs(list_a, list_b, self_pairs, seen=None, full_length=False):
    """A侧 x B侧 组合(--self 追加组内组合),按 (sigA,sigB) 排序去重。
    list_* 的元素是 side 字典。seen 传入时跨调用去重
    (#3:全长 pair 与片段 pair 之间;全长 pair 名字带 full-length 前缀)。"""
    if seen is None:
        seen = set()
    pairs = []

    def add(sa, sb):
        siga, sigb = _side_sig(sa), _side_sig(sb)
        sig = tuple(sorted([siga, sigb]))
        if sig in seen:
            return
        if len(seen) >= MAX_PLAN_TASKS:
            die("计划超过 %s 个任务，请拆分输入批次" % MAX_PLAN_TASKS)
        seen.add(sig)
        la, lb = sa["label"], sb["label"]
        pname = sanitize_name(f"{la}_{lb}", 72) + "__" + R.digest(sig, 16)
        if full_length:
            pname = sanitize_name(f"full-length_{pname}", 100)
        pairs.append({"name": pname, "a": sa, "b": sb,
                      "a_label": la, "b_label": lb,
                      **({"full_length": True} if full_length else {})})

    for sa in list_a:
        for sb in list_b:
            if not self_pairs and _side_sig(sa) == _side_sig(sb):
                continue  # 默认跳过自配对(同组成的侧)
            add(sa, sb)
    if self_pairs:
        for lst in (list_a, list_b):
            for i in range(len(lst)):
                for j in range(i, len(lst)):
                    add(lst[i], lst[j])
    return pairs


def submit_screen(args, list_a, list_b, pairs, unique_msa_ents, screen_type, extra_spec=None):
    _review_entities(unique_msa_ents, args)
    _finish_msa_review(args)
    tag = sanitize_name(args.name or (group_tag(args.a) + "_x_" + group_tag(args.b)), 60)
    fixed = get_seeds(args.seeds, args.num_seeds)
    identity = dict(pairs=[p["name"] for p in pairs], seeds=fixed,
                    template_free=bool(args.template_free), msa_free=bool(args.msa_free),
                    extra=extra_spec or {}, bonds=getattr(args, "bonds", None),
                    ccd=R.file_digest(args.user_ccd) if getattr(args, "user_ccd", None) else None,
                    environment=_prediction_environment())
    if any(e.get('_msa_recomputed') for e in unique_msa_ents):
        identity['msa_recomputed'] = [e.get('_msa_recomputed') for e in unique_msa_ents]
    suffix = R.digest(identity, 16)
    if args.force: suffix += "_" + str(time.time_ns())[-10:]
    outdir = os.path.join(args.output_dir or HOST_OUTPUT, screen_type + "_" + tag + "__" + suffix)
    spec_path = batch_paths(outdir)["spec"]
    seen = {}; missing = []
    for ent in unique_msa_ents:
        key = ent.get("_msa_key")
        if key and key not in seen:
            seen[key] = ent
            if check_msa_ready(key, ent, args.msa_dir) != "ready": missing.append(ent)
    spec = dict(version=2, type=screen_type, name=tag, tag=tag, pairs=pairs,
                groups={"a_labels": [x["label"] for x in list_a], "b_labels": [x["label"] for x in list_b]},
                outdir=outdir, msa_dir=args.msa_dir, output_dir=args.output_dir or HOST_OUTPUT,
                partition=args.partition, max_concurrent=args.max_concurrent or INF_MAX_CONCURRENT,
                num_seeds=len(fixed), seeds=args.seeds, resolved_seeds=fixed,
                template_free=bool(args.template_free), msa_free=bool(args.msa_free),
                bonds=parse_bonds(getattr(args, "bonds", None) or ""), user_ccd_path=getattr(args, "user_ccd", None),
                topk=args.topk, msa_entities=list(seen.values()), submit_args={k:v for k,v in vars(args).items() if not k.startswith("_") and k != "func"},
                msa_jids=[], infer_jids=[], status="preparing")
    spec["self"] = bool(getattr(args, "self", False))
    if extra_spec: spec.update(extra_spec)
    for pair in pairs: pair["seeds"] = _screen_seeds(pair, spec)
    note("计划: %s pairs / %s seeds per pair / %s samples; MSA %s reused + %s missing" %
         (len(pairs), len(fixed), len(pairs)*len(fixed)*5, len(seen)-len(missing), len(missing)))
    blocked = [p for p in pairs if estimate_tokens(side_entities([p["a"],p["b"]])) > INF_MAX_TOKEN]
    if blocked: warn("%s 个组合超过已验证 token 上限，将明确记为 blocked" % len(blocked))
    note("目录: " + outdir)
    if args.dry_run: return
    if os.path.exists(spec_path): die("计划已存在；请用 retry 重试失败任务: " + spec_path)
    write_spec(spec, spec_path)
    jid = submit_watcher(spec_path, tag, partition=_aux_partition_for(args))
    update_spec(spec_path, watcher_jid=jid)
    note("已受理 watcher=%s；MSA 预热与后续提交由集群接续，可关闭 GUI。" % jid)


def _screen_seeds(pair, spec):
    if pair.get("seeds"): return pair["seeds"]
    fixed = spec["resolved_seeds"]
    if spec.get("seeds") not in (None, "random"): return list(fixed)
    rng = random.Random(int(R.digest([pair["name"], fixed], 16), 16))
    return [rng.randint(SEED_MIN, SEED_MAX) for _ in fixed]


def group_tag(arg):
    """pulldown/scan 一侧参数的批次名:文件名 / :name= / UniProt / 序列哈希。"""
    if os.path.isfile(arg):
        return sanitize_name(Path(arg).stem, 40)
    m = re.search(r":name=([A-Za-z0-9_.-]+)", arg)
    if m:
        return sanitize_name(m.group(1), 40)
    src = arg.split("+")[0]
    src = re.sub(r"^[pdrlPDRL]:", "", src).split(":")[0]
    src = re.sub(r"[xX]\d+$", "", src)
    if _is_uniprot_id(src.upper()):
        return src.upper()
    if len(src) <= 20 and re.match(r"^[A-Za-z0-9_.-]+$", src):
        return src
    return "seq_" + md5_short(src, 6)


def cmd_pulldown(args):
    _begin_msa_review(args)
    if not args.dry_run:
        ensure_deployment(_submission_config(args))
    global args_msa_dir_global
    args_msa_dir_global = args.msa_dir
    list_a = resolve_side_group(args.a, "A")
    list_b = resolve_side_group(args.b, "B")
    for ent in side_entities(list_a) + side_entities(list_b):
        resolve_msa_key(ent, args.msa_dir)
    for s in list_a + list_b:   # resolve 可能改 key,标签后算
        s["label"] = _side_label(s["entities"])
    pairs = build_pairs(list_a, list_b, args.self)
    if not pairs:
        die("没有任何 pair 可提交(检查 A/B 列表)")
    unique = list({(e["_msa_key"]): e
                   for e in side_entities(list_a) + side_entities(list_b)
                   if e.get("_msa_key")}.values())
    input_spec = {"input": {"a": [s["expr"] for s in list_a],
                            "b": [s["expr"] for s in list_b]}}
    submit_screen(args, list_a, list_b, pairs, unique, "pulldown",
                  extra_spec=input_spec)


# ============================================================================
# scan --mode pae:纯 Python PAE domain 切分(零依赖,登录节点可跑)
#   1. ChimeraX pae_domains() 建图(src/bundles/alphafold/src/pae.py,
#      源自 Tristan Croll ISOLDE):PAE 对称化取 min、下限 0.2、w=1/pae、
#      PAE<cutoff 连边;
#   2. networkx greedy_modularity_communities(Clauset-Newman-Moore)忠实
#      移植(含 MappedQueue),与 networkx 结果一致(已对拍验证);
#   3. 忠实记录多段 domain(discontinuous domain 一等公民),保守切链:
#      每个 domain 切整段 span,linker 并入前一个 domain;
#   4. 滑窗构建 D1+D2/D2+D3(相邻 overlap 1 个 domain)、小 window 迭代合并、
#      超大 domain 提高 resolution 递归再切分。
# ============================================================================


def pae_identity(ent, template_free=False):
    entity = dict(ent, copies=1)
    entity.pop("pae_path", None)
    return "pae_" + R.digest({"entity": R.entity_identity(entity), "msa_key": ent.get("_msa_key"),
                              "template_free": bool(template_free), "environment": _prediction_environment(),
                              "seed_policy": "deterministic-v1"})


def _pae_read_confidences(confidences_json, chain=None):
    import af3_pae
    import numpy as np
    pae, meta = af3_pae.load(confidences_json)
    token_chains, token_res = meta.get("token_chain_ids", []), meta.get("token_res_ids", [])
    if len(token_chains) != len(pae) or len(token_res) != len(pae):
        die("PAE 缺少可靠的 token/残基映射: " + confidences_json)
    chains = list(dict.fromkeys(token_chains))
    if chain is None:
        if len(chains) != 1: die("多链 PAE 必须通过 :pae=路径:链ID 指定链")
        chain = chains[0]
    idx, res_ids, seen = [], [], set()
    for i, (c, residue) in enumerate(zip(token_chains, token_res)):
        if c == chain and int(residue) not in seen:
            idx.append(i); res_ids.append(int(residue)); seen.add(int(residue))
    if not idx: die("PAE 中不存在链: " + str(chain))
    # A contiguous protein block is a view of the disk-backed array.
    sub = pae[idx[0]:idx[-1]+1, idx[0]:idx[-1]+1] if idx == list(range(idx[0],idx[-1]+1)) else pae[np.ix_(idx,idx)]
    return sub, res_ids


def _pae_clusters_to_domains(clusters, res_ids):
    """一个簇 = 一个 domain,忠实记录所有连续段(多段式 domain)。"""
    ordered = sorted(clusters, key=lambda c: sum(sorted(c)) / float(len(c)))
    domains = []
    for i, cluster in enumerate(ordered):
        idx = sorted(cluster)
        runs = []
        start = prev = idx[0]
        for k in idx[1:]:
            if k == prev + 1:
                prev = k
            else:
                runs.append((start, prev))
                start = prev = k
        runs.append((start, prev))
        segments = [[int(res_ids[s]), int(res_ids[e])] for s, e in runs]
        domains.append({"name": f"D{i + 1}", "segments": segments})
    return domains


def _pae_assign_cut_ranges(domains, res_ids):
    """保守切链:每个 domain 切整段 span;domain 间 linker 并入前一个
    domain;两端未聚类残基延伸首/末 domain。"""
    chain_start, chain_end = int(res_ids[0]), int(res_ids[-1])
    for d in domains:
        d["cut_range"] = [d["segments"][0][0], d["segments"][-1][1]]
    if not domains:
        return
    domains[0]["cut_range"][0] = chain_start
    domains[-1]["cut_range"][1] = chain_end
    for i in range(1, len(domains)):
        prev_end = domains[i - 1]["cut_range"][1]
        this_start = domains[i]["cut_range"][0]
        if this_start > prev_end + 1:
            domains[i - 1]["cut_range"][1] = this_start - 1


def _pae_make_windows(domains, window):
    """滑窗:每 window 含 window 个相邻 domain,步长 1(相邻 overlap 1 个
    domain);范围为成员 cut_range 并集;去重。"""
    windows = []
    seen = set()
    for i in range(len(domains) - window + 1):
        group = domains[i:i + window]
        start = min(d["cut_range"][0] for d in group)
        end = max(d["cut_range"][1] for d in group)
        if (start, end) in seen:
            continue
        seen.add((start, end))
        windows.append({"domains": [d["name"] for d in group],
                        "start": start, "end": end})
    return windows


def _pae_merge_small_windows(windows, min_len):
    """迭代:最短且 < min_len 的 window 与较短邻居合并(平手取左;首尾
    只有单侧),直到全部达标。"""
    windows = [dict(w, domains=list(w["domains"])) for w in windows]
    while len(windows) > 1:
        short_idx = None
        short_len = None
        for i, w in enumerate(windows):
            ln = w["end"] - w["start"] + 1
            if ln < min_len and (short_len is None or ln < short_len):
                short_len = ln
                short_idx = i
        if short_idx is None:
            break
        i = short_idx
        left = windows[i - 1] if i > 0 else None
        right = windows[i + 1] if i < len(windows) - 1 else None
        if left is None:
            j = i + 1
        elif right is None:
            j = i - 1
        else:
            llen = left["end"] - left["start"] + 1
            rlen = right["end"] - right["start"] + 1
            j = i - 1 if llen <= rlen else i + 1
        a, b = (j, i) if j < i else (i, j)
        dom_names = []
        for nm in windows[a]["domains"] + windows[b]["domains"]:
            if nm not in dom_names:
                dom_names.append(nm)
        merged = {
            "domains": dom_names,
            "start": min(windows[a]["start"], windows[b]["start"]),
            "end": max(windows[a]["end"], windows[b]["end"]),
        }
        windows[a:b + 1] = [merged]
    return windows


def _pae_resplit_oversized(pae, res_ids, domains, max_span, pae_cutoff,
                           base_resolution, min_size, max_rounds=3,
                           escalate=2.0):
    """span 超 max_span 的 domain:对其残基子矩阵按 escalating resolution
    (x2/轮,最多 max_rounds 轮)递归再聚类;不可再分的保留并记 note。"""
    notes = []
    if not max_span:
        return domains, notes
    res_index = {r: i for i, r in enumerate(res_ids)}
    for rnd in range(max_rounds):
        oversized = [d for d in domains
                     if d["cut_range"][1] - d["cut_range"][0] + 1 > max_span]
        if not oversized:
            break
        resolution = base_resolution * (escalate ** (rnd + 1))
        split_any = False
        for d in oversized:
            idx = []
            for s, e in d["segments"]:
                idx.extend(range(res_index[s], res_index[e] + 1))
            sub = [[pae[i][j] for j in idx] for i in idx]
            sub_res = [res_ids[i] for i in idx]
            clusters = _pae_domains_pure(sub, pae_cutoff=pae_cutoff,
                                         graph_resolution=resolution,
                                         min_size=min_size)
            if len(clusters) < 2:
                continue
            new_doms = _pae_clusters_to_domains(clusters, sub_res)
            pos = domains.index(d)
            domains[pos:pos + 1] = new_doms
            split_any = True
            notes.append(f"{d['name']} span {d['cut_range'][0]}-"
                         f"{d['cut_range'][1]} "
                         f"({d['cut_range'][1] - d['cut_range'][0] + 1} aa) "
                         f"按 resolution={resolution:.3g} 再切为 "
                         f"{len(new_doms)} 段")
        if not split_any:
            break
        domains.sort(key=lambda d: sum(s[0] for s in d["segments"]) /
                     float(len(d["segments"])))
        for i, d in enumerate(domains):
            d["name"] = f"D{i + 1}"
        _pae_assign_cut_ranges(domains, res_ids)
    for d in domains:
        ln = d["cut_range"][1] - d["cut_range"][0] + 1
        if ln > max_span:
            msg = (f"domain {d['name']} span {ln} aa > 上限 {max_span},"
                   f"无法再细分,整段保留(将按 token 数自动选大显存分区)")
            notes.append(msg)
            warn(msg)
    return domains, notes


def _pae_fragment_windows(pae, res_ids, pae_cutoff=5, resolution=0.5,
                          min_domain=10, domains_per_window=2,
                          min_window=50, max_span=None):
    """完整流水线:PAE 矩阵 -> 最终片段 window 列表。
    返回 (windows, domains, notes);window = {domains, start, end}(1-based)。"""
    notes = []
    clusters = _pae_domains_pure(pae, pae_cutoff=pae_cutoff,
                                 graph_resolution=resolution,
                                 min_size=min_domain)
    domains = _pae_clusters_to_domains(clusters, res_ids)
    _pae_assign_cut_ranges(domains, res_ids)
    domains, rs_notes = _pae_resplit_oversized(
        pae, res_ids, domains, max_span, pae_cutoff, resolution, min_domain)
    notes.extend(rs_notes)
    windows = _pae_make_windows(domains, domains_per_window)
    windows = _pae_merge_small_windows(windows, min_window)
    return windows, domains, notes


def find_confidences_json(key, out_base):
    """找单体全长预测的 <key>_confidences.json。搜索 infer_data 池与
    out_base 下的 <key>/ 及 AF3 时间戳目录 <key>_YYYYMMDD_HHMMSS/
    (AF3 在输出目录已存在时会改写到时间戳目录);池内优先,其次最新;
    池外命中时收一份进池(lazy 自填充)。"""
    hits = []
    for base in (HOST_INFER_DATA, out_base):
        if not base:
            continue
        for pat in (os.path.join(base, key, f"{key}_confidences.json"),
                    os.path.join(base, key + "_2*", f"{key}_confidences.json")):
            hits.extend(glob.glob(pat))
    if not hits:
        return None
    pool_prefix = os.path.normpath(HOST_INFER_DATA) + os.sep
    hits.sort(key=lambda p: (not os.path.normpath(p).startswith(pool_prefix),
                             -os.path.getmtime(p)))
    best = hits[0]
    return best


def make_pae_fragments(ent, args):
    """scan --mode pae:按 PAE domain window 切片段(<=split_threshold 不切)。
    依赖 args._pae_cache[key] = ((pae, res_ids), conf_path)(调用前由
    _pae_prepare 填好);PTM 位号平移规则与滑窗模式一致。"""
    seq_len = len(ent["sequence"])
    if seq_len <= args.split_threshold:
        return [ent]
    key = pae_identity(ent, getattr(args, "template_free", False))
    (pae, res_ids), conf_path = args._pae_cache[key]
    if len(res_ids) != seq_len:
        die(f"{key}:PAE 残基数 {len(res_ids)} 与序列长度 {seq_len} 不一致"
            f"({conf_path})",
            hint="--mode pae 需要该蛋白全长单体的 confidences.json;"
                 "可用 :pae= 显式指定正确的文件")
    max_span = args.pae_max_frag or args.split_threshold
    cached = getattr(args,"_pae_report",{}).get(key)
    if cached:
        windows, domains, notes = cached["windows"], cached["domains"], cached["notes"]
    else:
        windows, domains, notes = _pae_fragment_windows(
        pae, res_ids, pae_cutoff=args.pae_cutoff,
        resolution=args.pae_resolution,
        min_domain=args.pae_min_domain,
        domains_per_window=args.pae_domains_per_window,
        min_window=args.pae_min_frag, max_span=max_span)
    args._pae_report[key] = {"confidences": conf_path, "domains": domains,
                             "windows": windows, "notes": notes}
    if len(windows) <= 1:
        return [ent]
    out = []
    parent_label = ent["name"] or ent["uniprot"] or base_msa_key(ent)
    for w in windows:
        s, e = w["start"], w["end"]
        fent = dict(ent)
        fent["trunc"] = f"{s}-{e}"
        fent["sequence"] = ent["sequence"][s - 1:e]
        if ent.get("modifications"):
            fent["modifications"] = [
                {**m, "ptmPosition": m["ptmPosition"] - (s - 1)}
                for m in ent["modifications"]
                if s <= m.get("ptmPosition", -1) <= e]
        fent["_frag"] = {"parent": parent_label, "start": s, "end": e,
                         "domains": w["domains"]}
        fent['_parent_identity'] = R.entity_identity(ent)
        out.append(fent)
    return out


def _pae_scan_params_dict(args):
    return {"mode": getattr(args, "mode", "win"),
            "win": args.win, "overlap": args.overlap,
            "min_frag": args.min_frag,
            "split_threshold": args.split_threshold,
            "pae_cutoff": getattr(args, "pae_cutoff", 5.0),
            "pae_resolution": getattr(args, "pae_resolution", 0.5),
            "pae_min_domain": getattr(args, "pae_min_domain", 10),
            "pae_domains_per_window":
                getattr(args, "pae_domains_per_window", 2),
            "pae_min_frag": getattr(args, "pae_min_frag", 250),
            "pae_max_frag": getattr(args, "pae_max_frag", None)}


def _pae_split_path_opt(val):
    """':pae=' 值解析:'路径[:链ID]' -> (path, chain)。链 ID 为末尾单字符
    段(Windows 盘符 'd:\\...' 不以 ':X' 结尾,不受影响)。"""
    if not val:
        return None, None
    m = re.match(r"^(.*):([A-Za-z0-9])$", val)
    if m and os.path.isfile(m.group(1)):
        return m.group(1), m.group(2)
    return val, None


def _pae_prepare(raw_a, raw_b, args):
    """为 pae 模式准备 args._pae_cache:解析两侧全长蛋白 msa key,定位并
    加载 confidences.json。返回缺 confidences 的全长蛋白 entity 列表
    (慢路径用)。"""
    args._pae_cache = {}
    args._pae_report = {}
    missing = []
    seen = set()
    pool = args.output_dir or HOST_OUTPUT
    for ent in side_entities(raw_a) + side_entities(raw_b):
        if ent["type"] != "protein" or len(ent["sequence"]) <= args.split_threshold:
            continue
        resolve_msa_key(ent, args.msa_dir)
        key = pae_identity(ent, getattr(args, "template_free", False))
        if key in seen:
            continue
        seen.add(key)
        ppath, pchain = _pae_split_path_opt(ent.get("pae_path"))
        if ppath and not os.path.isfile(ppath):
            die(f":pae= 指定的文件不存在:{ppath}(实体 {key})")
        path = ppath or find_confidences_json(key, pool)
        if path is None:
            missing.append(ent)
            continue
        args._pae_cache[key] = (_pae_read_confidences(path, chain=pchain), path)
    return missing


# ============================================================================
# 命令:scan(§7)
# ============================================================================

def fragment_windows(length, win, overlap, min_frag, threshold):
    """滑窗切分(1-based 闭区间)。length<=threshold 返回 [(1, length)]。"""
    if win <= 0 or overlap < 0 or overlap >= win:
        raise ValueError("滑窗要求 win > 0 且 0 <= overlap < win")
    if length <= threshold:
        return [(1, length)]
    if win <= 0 or overlap < 0 or overlap >= win:
        raise ValueError("滑窗要求 win > 0 且 0 <= overlap < win")
    if min_frag <= 0 or threshold < 0:
        raise ValueError("min_frag 必须为正数，threshold 不能为负数")
    frags = []
    s = 1
    while s <= length:
        e = min(s + win - 1, length)
        frags.append((s, e))
        if e >= length:
            break
        s += win - overlap
    # 边界合并:末段 < min-frag 时并入前一段
    if len(frags) >= 2 and (frags[-1][1] - frags[-1][0] + 1) < min_frag:
        last = frags.pop()
        prev = frags.pop()
        frags.append((prev[0], last[1]))
    return frags


def make_fragments(ent, args):
    """把一个蛋白 entity 切成片段 entity 列表(<=threshold 返回原样)。
    带 PTM 的蛋白也切:窗口内的修饰位号平移到片段坐标,窗口外的修饰丢弃。
    --mode pae 时改走 PAE domain 切分(见 make_pae_fragments)。"""
    if getattr(args, "mode", "win") == "pae":
        return make_pae_fragments(ent, args)
    frags = fragment_windows(len(ent["sequence"]), args.win, args.overlap,
                             args.min_frag, args.split_threshold)
    if len(frags) == 1 and frags[0] == (1, len(ent["sequence"])):
        return [ent]
    out = []
    parent_label = ent["name"] or ent["uniprot"] or base_msa_key(ent)
    for (s, e) in frags:
        fent = dict(ent)
        fent["trunc"] = f"{s}-{e}"
        fent["sequence"] = ent["sequence"][s - 1:e]
        if ent.get("modifications"):
            fent["modifications"] = [
                {**m, "ptmPosition": m["ptmPosition"] - (s - 1)}
                for m in ent["modifications"]
                if s <= m.get("ptmPosition", -1) <= e]
        fent["_frag"] = {"parent": parent_label, "start": s, "end": e}
        fent['_parent_identity'] = R.entity_identity(ent)
        out.append(fent)
    return out


def cmd_scan(args):
    _begin_msa_review(args)
    if not args.dry_run:
        ensure_deployment(_submission_config(args))
    global args_msa_dir_global
    args_msa_dir_global = args.msa_dir
    # scan 默认每对 4 个 seed(用户未显式给 --seeds/--num-seeds 时)
    if args.seeds is None and args.num_seeds is None:
        args.num_seeds = 4
    raw_a = resolve_side_group(args.a, "A")
    raw_b = resolve_side_group(args.b, "B")

    # --mode pae:需要各全长蛋白的单体 confidences.json;有缺失走慢路径
    # (提交全长 MSA + watcher 阶段0 补单体 infer 后再建 plan)
    if args.mode == "pae":
        missing_conf = _pae_prepare(raw_a, raw_b, args)
        if missing_conf:
            _cmd_scan_pae_slow(args, raw_a, raw_b, missing_conf)
            return

    plan = build_scan_plan(args, raw_a, raw_b)
    submit_screen(args, plan["list_a"], plan["list_b"], plan["pairs"],
                  plan["unique"], "scan", extra_spec=plan["extra"])


def build_scan_plan(args, raw_a, raw_b):
    """scan 中段:切片 -> pairs -> spec extra。cmd_scan 与 watcher 阶段0 共用。
    返回 {list_a, list_b, pairs, unique, extra}。"""
    # 侧变体(#2):侧内每个蛋白各自切片(长度只看单蛋白,不看整条侧;PTM 位号
    # 随片段改写);DNA/RNA/配体不切(无氨基酸序列),侧内片段组合做笛卡尔积。
    def variants(side):
        per_ent = []
        for ent in side["entities"]:
            if ent["type"] == "protein":
                per_ent.append(make_fragments(ent, args))
            else:
                per_ent.append([ent])
        out = []
        count = 1
        for variants in per_ent: count *= len(variants)
        if count > MAX_PLAN_TASKS: die("单侧片段组合过多，请拆分输入")
        for combo in itertools.product(*per_ent):
            out.append({"entities": list(combo), "label": _side_label(list(combo))})
        return out

    list_a = [v for s in raw_a for v in variants(s)]
    list_b = [v for s in raw_b for v in variants(s)]
    note(f"切片段:A {len(raw_a)} 侧 -> {len(list_a)} 变体;"
         f"B {len(raw_b)} 侧 -> {len(list_b)} 变体")

    for ent in side_entities(list_a) + side_entities(list_b):
        if args.shared_msa and ent.get("_frag"):
            full = _full_length_entity(ent, side_entities(raw_a)+side_entities(raw_b))
            parent_key, _ = resolve_msa_key(full,args.msa_dir)
            ent["_shared_source"]={"parent_key":parent_key,"start":ent["_frag"]["start"],"end":ent["_frag"]["end"]}
        resolve_msa_key(ent, args.msa_dir)
    for s in list_a + list_b:
        s["label"] = _side_label(s["entities"])
    seen = set()
    pairs = build_pairs(list_a, list_b, getattr(args, "self", False), seen=seen)

    # #3:非 shared 时追加全长 pair(文件夹名带 full-length,参与 ranking;
    # 全长 MSA 一并补算——非 shared 本来只做片段 MSA,全长不一定在池里)
    if not args.shared_msa:
        for ent in side_entities(raw_a) + side_entities(raw_b):
            resolve_msa_key(ent, args.msa_dir)
        for s in raw_a + raw_b:
            s["label"] = _side_label(s["entities"])
        pairs += build_pairs(raw_a, raw_b, getattr(args, "self", False),
                             seen=seen, full_length=True)
    if not pairs:
        die("没有任何 pair 可提交")

    # scan 的 profile 数据(shared-msa 模式还需 parent_key / type 供切列合成)
    raws_flat = side_entities(raw_a) + side_entities(raw_b)
    vars_flat = side_entities(list_a) + side_entities(list_b)
    if args.shared_msa:
        # 只对全长做 MSA;片段 data.json 由 watcher 从全长 A3M 切列合成
        parents = {}
        frag_specs = []
        for ent in vars_flat:
            if not ent.get("_frag"):
                continue
            full = _full_length_entity(ent, raws_flat)
            resolve_msa_key(full, args.msa_dir)
            parents[full["_msa_key"]] = full
            ent["_shared_parent_key"] = full["_msa_key"]
            frag_specs.append({"key": ent["_msa_key"],
                               "parent_key": full["_msa_key"],
                               "parent": ent["_frag"]["parent"],
                               "start": ent["_frag"]["start"],
                               "end": ent["_frag"]["end"],
                               "type": ent["type"],
                               "parent_identity": R.entity_identity(full),
                               "parent_sequence": full['sequence'],
                               "sequence": ent['sequence'],
                               **({"domains": ent["_frag"]["domains"]}
                                  if ent["_frag"].get("domains") else {})})
        uncut = {e["_msa_key"]: e for e in vars_flat
                 if not e.get("_frag") and e.get("_msa_key")}
        unique = list(parents.values()) + list(uncut.values())
        extra = {"shared_msa": True, "fragments": frag_specs}
    else:
        uniq = {e["_msa_key"]: e for e in vars_flat + raws_flat if e.get("_msa_key")}
        unique = list(uniq.values())
        frag_specs = [{"key": e["_msa_key"], "parent": e["_frag"]["parent"],
                       "start": e["_frag"]["start"], "end": e["_frag"]["end"],
                       **({"domains": e["_frag"]["domains"]}
                          if e["_frag"].get("domains") else {})}
                      for e in vars_flat if e.get("_frag")]
        extra = {"shared_msa": False, "fragments": frag_specs}

    # 矩阵轴:侧变体标签 + 全长侧标签(#3 全长 pair 也要进矩阵)
    extra["groups"] = {
        "a_labels": sorted({s["label"] for s in list_a} | {s["label"] for s in raw_a}),
        "b_labels": sorted({s["label"] for s in list_b} | {s["label"] for s in raw_b}),
    }
    # 初始输入与切分参数写入 spec:rebuild resubmit 按初始输入精确重提
    extra["input"] = {"a": [s["expr"] for s in raw_a],
                      "b": [s["expr"] for s in raw_b]}
    extra["scan_params"] = _pae_scan_params_dict(args)
    if getattr(args, "_pae_report", None):
        extra["pae_report"] = args._pae_report
    return {"list_a": list_a, "list_b": list_b, "pairs": pairs,
            "unique": unique, "extra": extra}


def _cmd_scan_pae_slow(args, raw_a, raw_b, missing_conf):
    parents = {}
    for ent in side_entities(raw_a) + side_entities(raw_b):
        resolve_msa_key(ent, args.msa_dir)
        if ent.get("_msa_key"): parents[ent["_msa_key"]] = ent
    pending = {e["_msa_key"]: {"entity": dict(e), "pae_path": e.get("pae_path")}
               for e in missing_conf}
    scan_args = {k:v for k,v in vars(args).items() if k != "func" and not k.startswith("_")}
    extra = {"pae_pending": pending, "scan_args": scan_args, "raw_sides": {"a": raw_a, "b": raw_b},
             "input": {"a": [x["expr"] for x in raw_a], "b": [x["expr"] for x in raw_b]},
             "scan_params": _pae_scan_params_dict(args), "shared_msa": bool(args.shared_msa)}
    submit_screen(args, raw_a, raw_b, [], list(parents.values()), "scan", extra)


def cmd_pae_domains(args):
    """单蛋白 PAE domain 分段查看(不提交 scan)。
    池里(infer_data / 输出池)有 confidences.json:直接按当前参数算分段
    并打印;没有:提交单体 MSA + infer(1 seed,产物入 infer_data 池),
    完成后重跑本命令即可查看。"""
    _begin_msa_review(args)
    global args_msa_dir_global
    args_msa_dir_global = args.msa_dir
    entities = parse_expression(args.input)
    if len(entities) != 1 or entities[0]["type"] != "protein":
        die("pae 命令只接受单个蛋白实体",
            hint="如 af3.py pae P12345 --pae-resolution 0.5")
    ent = entities[0]
    ent["copies"] = 1                       # 分段只看单体
    resolve_msa_key(ent, args.msa_dir)
    key = pae_identity(ent, getattr(args, "template_free", False))
    pool = args.output_dir or HOST_OUTPUT
    ppath, pchain = _pae_split_path_opt(ent.get("pae_path"))
    if ppath and not os.path.isfile(ppath):
        die(f":pae= 指定的文件不存在:{ppath}")
    conf = ppath or find_confidences_json(key, pool)

    if conf is not None:
        pae, res_ids = _pae_read_confidences(conf, chain=pchain)
        if len(res_ids) != len(ent["sequence"]):
            die(f"{key}:PAE 残基数 {len(res_ids)} 与序列长度 "
                f"{len(ent['sequence'])} 不一致({conf})",
                hint="用 :pae=路径[:链ID] 显式指定全长单体预测的 confidences.json")
        windows, domains, notes = _pae_fragment_windows(
            pae, res_ids, pae_cutoff=args.pae_cutoff,
            resolution=args.pae_resolution, min_domain=args.pae_min_domain,
            domains_per_window=args.pae_domains_per_window,
            min_window=args.pae_min_frag,
            max_span=args.pae_max_frag or 500)
        note(f"{key}({len(res_ids)} aa)   PAE: {conf}")
        note(f"参数:cutoff={args.pae_cutoff} resolution={args.pae_resolution} "
             f"min_domain={args.pae_min_domain} "
             f"domains_per_window={args.pae_domains_per_window} "
             f"min_frag={args.pae_min_frag} "
             f"max_frag={args.pae_max_frag or 500}")
        note("\nDomains(忠实记录,cut = 保守切链 span):")
        for d in domains:
            nres = sum(e - s + 1 for s, e in d["segments"])
            segs = " + ".join(f"{s}-{e}" for s, e in d["segments"])
            discont = "  [discontinuous]" if len(d["segments"]) > 1 else ""
            note(f"  {d['name']}: segments {segs} ({nres} aa) -> cut "
                 f"{d['cut_range'][0]}-{d['cut_range'][1]} "
                 f"({d['cut_range'][1] - d['cut_range'][0] + 1} aa){discont}")
        note("\nFragments(scan 将使用的 window):")
        for w in windows:
            note(f"  {'+'.join(w['domains'])}: {w['start']}-{w['end']} "
                 f"({w['end'] - w['start'] + 1} aa)")
        for nt in notes:
            note(f"  note: {nt}")
        return

    note(f"{key}:需要生成单体 PAE 预测")
    _finish_msa_review(args)
    if args.dry_run:
        note(f"[DRY RUN] 单体 MSA + 推理，产物 -> {HOST_INFER_DATA}/{key}/")
        return
    ensure_deployment(_submission_config(args))
    work_dir = os.path.join(HOST_INFER_DATA, ".work", key)
    spec_path = os.path.join(work_dir, "spec.json")
    if os.path.isfile(spec_path):
        note("已有计划；查看状态或重试: af3.py retry --spec " + spec_path)
        return
    job = {"expression": args.input, "name": key, "entities": [ent],
           "seeds": [int(R.digest([key,"pae-seed"],12),16) % SEED_MAX + 1],
           "template_free": bool(getattr(args,"template_free",False)), "bonds": [], "user_ccd_path": None}
    check_token_limit(estimate_tokens([ent]), key)
    write_spec({"version":2,"type":"run","name":key,"jobs":[job],"batch_dir":None,
                "output_dir":HOST_INFER_DATA,"msa_dir":args.msa_dir,"partition":args.partition,
                "max_concurrent":args.max_concurrent or INF_MAX_CONCURRENT,"force":False,
                "submit_args":{**{k:v for k,v in vars(args).items() if k!="func" and not k.startswith("_")},"output_dir":HOST_INFER_DATA},
                "status":"preparing","msa_jids":[],"infer_jids":[]},spec_path)
    jid=submit_controller(spec_path,key,[],partition=_aux_partition_for(args))
    update_spec(spec_path,controller_jid=jid)
    note("已提交单体控制器 " + jid + "；完成后重跑当前命令查看分段")


def _full_length_entity(frag_ent, raw_ents):
    """Resolve provenance, rejecting ambiguous legacy fragments."""
    candidates = {}
    fragment = frag_ent.get('_frag', {})
    start, end = fragment.get('start'), fragment.get('end')
    for raw in raw_ents:
        identity = R.entity_identity(raw)
        if frag_ent.get('_parent_identity') is not None:
            if identity != frag_ent['_parent_identity']: continue
        else:
            if raw.get('type') != frag_ent.get('type'): continue
            if frag_ent.get('uniprot') and raw.get('uniprot') != frag_ent['uniprot']: continue
            for key in ('mut', 'mutation', 'mutations', 'modifications', 'msa_path', 'paired_msa', 'paired_msa_path', 'template'):
                if raw.get(key) != frag_ent.get(key): break
            else:
                if start and end and raw.get('sequence','')[start-1:end] == frag_ent.get('sequence'):
                    candidates[R.digest(identity)] = raw
            continue
        if start and end and raw.get('sequence','')[start-1:end] == frag_ent.get('sequence'):
            candidates[R.digest(identity)] = raw
    if len(candidates) == 1: return next(iter(candidates.values()))
    die('片段母序列缺失或身份不唯一；请从原始输入重新建立计划')


# ============================================================================
# shared-msa:对 AF3 data.json 内的 unpairedMsa/pairedMsa 按残基区间切列
# (AF3 的 MSA 嵌在 {key}_data.json 里,是 A3M 风格字符串:首条为 query,
#  大写/- 为 match 列,小写为插入列;已用真实 66MB data.json 验证)
# ============================================================================

def slice_a3m(a3m_text, start, end):
    """按 query 残基区间 [start, end](1-based)切 AF3 MSA 字符串的列。
    AF3 data.json 的 unpairedMsa/pairedMsa 是 A3M 风格:首条记录为 query,
    大写/'-' 为 match 列,小写为插入列。注意插入列是行各自的(其他行在
    该位置没有占位字符),因此每行必须独立按 match 序号切片:
    match 列(大写/-)落在区间则保留;插入列(小写)跟随左侧保留的 match 列。"""
    entries = []
    header, seq_lines = None, []
    for line in a3m_text.splitlines():
        if line.startswith(">"):
            if header is not None:
                entries.append((header, "".join(seq_lines)))
            header, seq_lines = line, []
        elif line.strip():
            seq_lines.append(line.strip())
    if header is not None:
        entries.append((header, "".join(seq_lines)))
    if not entries:
        return a3m_text
    out = []
    for header, s in entries:
        out.append(header)
        if s == s.upper():
            # 无插入列:字符位置即 match 序号,直接切片
            out.append(s[start - 1:end])
            continue
        kept = []
        idx = 0
        last_kept = False
        for c in s:
            if c.islower():
                if last_kept:
                    kept.append(c)
            else:
                idx += 1
                last_kept = start <= idx <= end
                if last_kept:
                    kept.append(c)
        out.append("".join(kept))
    return "\n".join(out) + "\n"


def synthesize_fragment_data(frag, msa_dir, local_dir=None):
    """从全长 data.json 合成片段 data.json(shared-msa 模式,watcher 调用)。

    local_dir 给出时(#11):产物写到批次本地目录,且**总是从全长重新切**——
    刻意不读共享池里的片段文件(那可能是非 shared 任务写的、含 templates 的
    真 MSA,用了就破坏 shared 近似语义、也破坏对照实验)。未切片的短蛋白不进这里,
    它们的全长 MSA 仍走共享池(与方法无关,谁算都一样)。"""
    key = frag["key"]
    dst_dir = local_dir or msa_dir
    dst = msa_data_path(key, dst_dir)
    if R.msa_ready(dst_dir, key):
        return True
    if local_dir:
        os.makedirs(local_dir, exist_ok=True)
    src_path = msa_data_path(frag["parent_key"], msa_dir)
    if not R.msa_ready(msa_dir, frag["parent_key"]):
        return False
    data = read_data_json(src_path)
    etype = frag.get("type", "protein")
    for entry in data.get("sequences", []):
        if etype in entry:
            data = dict(data)
            body = dict(entry[etype])
            data["sequences"] = [{etype: body}]
            break
    else:
        return False
    s, e = frag["start"], frag["end"]
    if frag.get('parent_sequence') and body['sequence'] != frag['parent_sequence']:
        raise ValueError('共享 MSA 母序列与计划不一致: ' + src_path)
    if not 1 <= s <= e <= len(body['sequence']): raise ValueError('片段坐标超出母序列')
    body["sequence"] = body["sequence"][s - 1:e]
    if frag.get('sequence') and body['sequence'] != frag['sequence']:
        raise ValueError('共享 MSA 片段序列与计划不一致')
    for inline, pathkey in (("unpairedMsa", "unpairedMsaPath"), ("pairedMsa", "pairedMsaPath")):
        if body.get(pathkey):
            with open(R.host_asset_path(body.pop(pathkey)), encoding="utf8") as stream: body[inline] = stream.read()
    if body.get("unpairedMsa"):
        body["unpairedMsa"] = slice_a3m(body["unpairedMsa"], s, e)
    if body.get("pairedMsa"):
        body["pairedMsa"] = slice_a3m(body["pairedMsa"], s, e)
    # 片段 infer 为 template-free:全长 data.json 里的 templates 无法按列切
    # (mmCIF 含全原子坐标),且全长池文件中的 templates 保留不动,供全长复用
    body["templates"] = []
    data["name"] = key
    R.atomic_json(dst, data)
    _mark_synthesized(key, dst_dir)
    where = "批次本地" if local_dir else "共享池"
    note(f"[watcher] 已从全长 MSA 切列合成 {key}_data.json -> {where}")
    return True


# ============================================================================
# 隐藏命令:_pulldown_watcher(§6 流水线)
# ============================================================================

def _pair_msa_ready(pair, msa_dir, local_dir=None):
    for side in ("a", "b"):
        s = pair[side]; ents = s.get("entities", [s])
        for ent in ents:
            key = ent.get("_msa_key")
            if not key: continue
            folder = local_dir if local_dir and R.msa_ready(local_dir, key, ent) else msa_dir
            if check_msa_ready(key, ent, folder) != "ready": return False
    return True


def _pair_result_exists(outdir, pair_name):
    return R.valid_result(outdir, pair_name)


def _archive_input_json(outdir, pair_name):
    # Keep the submitted path stable for retries; copy only the small reference JSON.
    src = os.path.join(outdir, pair_name + "_data.json")
    dst = os.path.join(outdir, pair_name, pair_name + "_data.json")
    if os.path.isfile(src) and os.path.isdir(os.path.dirname(dst)) and not os.path.exists(dst):
        shutil.copy2(src, dst)


def _watcher_pae_stage0(spec, spec_path, deadline, submit_ready=None):
    """scan --mode pae 慢路径阶段0(增量流水):
    全长 MSA -> 单体全长 infer(1 seed)-> confidences 一就绪就立刻对该蛋白
    算 PAE domain;只含已就绪蛋白的 pair 立即建出、增量提交片段 MSA(非
    shared),并经 submit_ready 回调直接进入 pair infer 提交流程——不等
    其余蛋白。全部就绪后回写完整 spec,返回最终 pairs。"""
    tag = spec["tag"]
    outdir = spec["outdir"]
    msa_dir = spec.get("msa_dir", HOST_MSA_DATA)
    pool = spec.get("output_dir") or HOST_OUTPUT
    # 注意:scan_args 含键 "self"(scan --self),不能 Namespace(**dict)
    scan_args = argparse.Namespace()
    scan_args.__dict__.update(spec["scan_args"])
    paths = batch_paths(outdir)
    shared_msa = bool(scan_args.shared_msa)
    raw_a = spec.get("raw_sides", {}).get("a") or [s for e in spec["input"]["a"] for s in resolve_side_group(e, "A")]
    raw_b = spec.get("raw_sides", {}).get("b") or [s for e in spec["input"]["b"] for s in resolve_side_group(e, "B")]

    # 需要 PAE 的全长蛋白(>split_threshold),按 key 去重
    all_ents = {}
    for ent in side_entities(raw_a) + side_entities(raw_b):
        if ent["type"] != "protein" or len(ent["sequence"]) <= scan_args.split_threshold:
            continue
        resolve_msa_key(ent, scan_args.msa_dir)
        all_ents.setdefault(pae_identity(ent, spec.get("template_free", False)), ent)
    scan_args._pae_cache = {}
    scan_args._pae_report = {}
    note(f"[watcher] 阶段0(增量):{len(all_ents)} 个蛋白待 PAE;"
         f"就绪一个处理一个,不等全部")

    def side_ready(side):
        for ent in side["entities"]:
            if ent["type"] != "protein" or len(ent["sequence"]) <= scan_args.split_threshold:
                continue
            k = pae_identity(ent, spec.get("template_free", False))
            if k not in scan_args._pae_cache:
                return False
        return True

    ready_conf = {}        # key -> confidences 路径
    failed_conf = set()    # 超单卡上限、放弃 PAE 的 key(不阻塞其余蛋白)
    infer_submitted = set()
    # 提交时已交过的全长 MSA(防止增量重建时重复提交)
    msa_submitted = set(spec.get("fl_msa_keys") or [])
    cur_pairs, cur_fragments = [], []
    last_built = -1

    def rebuild():
        """只用 PAE 已就绪的蛋白重建 plan 并回写 spec;返回是否有 pair。"""
        nonlocal cur_pairs, cur_fragments
        ra = [s for s in raw_a if side_ready(s)]
        rb = [s for s in raw_b if side_ready(s)]
        if not ra or not rb:
            return False
        try:
            plan = build_scan_plan(scan_args, ra, rb)
        except SystemExit:
            return False                    # 暂时组不出 pair(如 --self 边界)
        cur_pairs = plan["pairs"]
        cur_fragments = plan["extra"]["fragments"]
        update_spec(spec_path, pairs=cur_pairs,
                    groups=plan["extra"]["groups"],
                    fragments=cur_fragments,
                    shared_msa=plan["extra"]["shared_msa"],
                    pae_report=dict(scan_args._pae_report))
        # 非 shared:新片段/全长的 MSA 增量提交(已交过的 key 跳过)
        if not shared_msa:
            missing = []
            for ent in plan["unique"]:
                k = ent.get("_msa_key")
                if not k or k in msa_submitted:
                    continue
                msa_submitted.add(k)
                if check_msa_ready(k, ent, scan_args.msa_dir) != "ready":
                    missing.append(ent)
            if missing:
                note(f"[watcher] 增量提交 {len(missing)} 个片段/全长 MSA")
                new_jids = submit_msa_stage(missing, tag, scan_args, outdir)
                old = load_spec(spec_path)
                update_spec(spec_path, msa_jids=old.get("msa_jids", []) + new_jids,
                            msa_entities=list({e["_msa_key"]:e for e in old.get("msa_entities", []) + plan["unique"]}.values()))
        return True

    while len(ready_conf) + len(failed_conf) < len(all_ents):
        try: queue = R.queue_snapshot(force=True)
        except R.SchedulerUnavailable:
            if time.time() > deadline: raise RuntimeError("PAE 阶段调度查询超时")
            time.sleep(WATCHER_POLL_SEC); continue
        for key, ent in all_ents.items():
            if key in ready_conf or key in failed_conf:
                continue
            ppath, pchain = _pae_split_path_opt(ent.get("pae_path"))
            conf = ppath or find_confidences_json(key, pool)
            if conf:
                ready_conf[key] = conf
                scan_args._pae_cache[key] = (
                    _pae_read_confidences(conf, chain=pchain), conf)
                note(f"[watcher] PAE 就绪:{key}({conf})")
                continue
            state, reason = R.task_state(HOST_INFER_DATA, key, queue)
            if state in ("queued", "running", "submitted", "confirming"): continue
            if state == "failed" and R.read_json(R.task_path(HOST_INFER_DATA, key), {}).get("submitted_at", 0) >= spec.get("retry_requested_at", 0):
                failed_conf.add(key); continue
            if key in infer_submitted: continue
            if check_msa_ready(ent["_msa_key"], ent, msa_dir) != "ready":
                if time.time() - spec.get("created_at", time.time()) > 180 and not any(j.split("_",1)[0] in load_spec(spec_path).get("msa_jids", []) for j in queue):
                    failed_conf.add(key)
                continue                     # 全长 MSA 未好,下轮再看
            # 已在队列的不重复提交(精确匹配 infer job 名)
            if sum(R.task_state(HOST_INFER_DATA, k, queue)[0] in ("running", "queued", "submitted", "confirming") for k in all_ents) >= spec.get("max_concurrent", INF_MAX_CONCURRENT):
                break
            ent1 = dict(ent)
            ent1["copies"] = 1               # PAE 一律单体
            try:
                jj = build_infer_json(key, [ent1], [int(R.digest([key, "pae-seed"], 12),16) % SEED_MAX + 1], msa_dir,
                                      template_free=spec.get("template_free", False))
            except SystemExit:
                update_spec(spec_path, status="failed",
                            error=f"拼装全长 infer 失败:{key}")
                die(f"[watcher] 拼装全长 infer 失败:{key}")
            # input json 放批次目录,不预建池内结果目录(AF3 时间戳目录坑)
            jpath = os.path.join(outdir, f"{key}_data.json")
            with open(jpath, "w") as f:
                json.dump(jj, f, indent=2)
            tokens = estimate_tokens([ent1])
            if tokens > INF_MAX_TOKEN:
                warn(f"[watcher] {key} 全长 ≈{tokens} token 超过单卡上限 "
                     f"{INF_MAX_TOKEN},跳过单体 infer(该蛋白不做 PAE 切分;"
                     f"含它的 pair 不会生成)")
                failed_conf.add(key)
                infer_submitted.add(key)
                continue
            part = infer_partition_for(tokens, spec.get("partition"))
            jid = submit_infer_single(jpath, HOST_INFER_DATA, tag,
                                      partition=part,
                                      log_dir=paths["infer_logs"],
                                      script_dir=paths["scripts"],
                                      unified_memory=unified_memory_for(tokens), queue=queue)
            current=load_spec(spec_path)
            update_spec(spec_path,pae_infer_jids=list(dict.fromkeys(current.get('pae_infer_jids',[])+[jid])))
            infer_submitted.add(key)
            note(f"[watcher] 提交全长单体 infer {jid}:{key}"
                 f"(1 seed,tokens≈{tokens},分区 {part})")
        # 就绪集合增长 -> 重建 plan;随后尝试提交一切可提交的 pair
        if len(ready_conf) != last_built:
            if rebuild():
                last_built = len(ready_conf)
        if submit_ready and cur_pairs:
            submit_ready(cur_pairs, cur_fragments, shared_msa)
        if len(ready_conf) + len(failed_conf) >= len(all_ents):
            break
        time.sleep(WATCHER_POLL_SEC)
        if time.time() > deadline:
            update_spec(spec_path, status="failed",
                        error="watcher 超时(阶段0 全长 MSA/infer 未完成)")
            die("[watcher] 超时:阶段0 未完成,退出")

    rebuild()
    if submit_ready and cur_pairs:
        submit_ready(cur_pairs, cur_fragments, shared_msa)
    update_spec(spec_path, status="running", pae_pending=(spec.get("pae_pending") if failed_conf else None), pae_failed=sorted(failed_conf))
    note(f"[watcher] 阶段0 完成:{len(cur_pairs)} pairs,进入正常提交循环"
         + (f"({len(failed_conf)} 个蛋白超 token 上限被跳过:{', '.join(sorted(failed_conf))})"
            if failed_conf else ""))
    return cur_pairs


def cmd_pulldown_watcher(args):
    spec = load_spec(args.spec)
    outdir = spec["outdir"]; tag = spec["tag"]; msa_dir = spec["msa_dir"]
    paths = batch_paths(outdir)
    maxc = spec.get("max_concurrent", INF_MAX_CONCURRENT)
    local = paths["msa_local"] if spec.get("shared_msa") else None
    deadline = time.time() + WATCHER_MAX_WAIT_SEC
    retry_at = spec.get("retry_requested_at", 0)
    blocked = {}; states = {}; empty_msa_rounds = 0
    subargs = argparse.Namespace(); subargs.__dict__.update(spec.get("submit_args", {}))
    for k,v in dict(msa_dir=msa_dir, msa_free=spec.get("msa_free", False), max_concurrent=None, msa_partition=None).items():
        if not hasattr(subargs,k): setattr(subargs,k,v)
    _begin_msa_review(subargs, remote=True)
    with R.controller_lock(outdir, timeout=1):
        entities = spec.get("msa_entities", [])
        missing = [e for e in entities if e.get("_msa_key") and check_msa_ready(e["_msa_key"], e, msa_dir) != "ready"]
        if missing:
            jids = submit_msa_stage(missing, tag, subargs, outdir)
            update_spec(args.spec, msa_jids=jids, status="msa_submitted", fl_msa_keys=[e["_msa_key"] for e in missing])
            spec = load_spec(args.spec)

        def submit_ready(cur_pairs, fragments, shared):
            nonlocal states
            queue = R.queue_snapshot(force=True)
            states = {p["name"]: R.task_state(outdir, p["name"], queue) for p in cur_pairs}
            active = sum(st in ("running", "queued", "submitted", "confirming") for st, _ in states.values())
            if shared:
                for frag in fragments: synthesize_fragment_data(frag, msa_dir, local_dir=local)
            progress = False
            for pair in cur_pairs:
                name = pair["name"]; state, reason = states[name]
                if name in blocked: states[name] = blocked[name]; continue
                if state in ("running", "queued", "submitted", "confirming", "succeeded"): continue
                rec = R.read_json(R.task_path(outdir, name), {})
                if state == "failed" and rec.get("submitted_at", 0) >= retry_at: continue
                ents = side_entities([pair["a"], pair["b"]]); tokens = estimate_tokens(ents)
                if tokens > INF_MAX_TOKEN:
                    blocked[name] = states[name] = ("blocked", "超过 token 上限 %s" % INF_MAX_TOKEN); continue
                if not _pair_msa_ready(pair, msa_dir, local_dir=local): continue
                if active >= maxc: break
                try:
                    jj = build_infer_json(name, ents, _screen_seeds(pair, spec), msa_dir,
                        template_free=spec.get("template_free", False), bonds=spec.get("bonds"),
                        user_ccd_path=spec.get("user_ccd_path"), local_msa_dir=local)
                    jpath = os.path.join(outdir, name + "_data.json"); R.atomic_json(jpath, jj)
                    jid = submit_infer_single(jpath, outdir, tag, partition=infer_partition_for(tokens, spec.get("partition")),
                        log_dir=paths["infer_logs"], script_dir=paths["scripts"], unified_memory=unified_memory_for(tokens), queue=queue)
                    states[name] = ("submitted", jid); active += 1; progress = True
                    note("infer %s: %s" % (jid, name))
                except (ValueError, OSError, SystemExit) as exc:
                    blocked[name] = states[name] = ("blocked", "输入拼装或提交失败: " + str(exc))
            return progress

        if spec.get("pae_pending"):
            pairs = _watcher_pae_stage0(spec, args.spec, deadline, submit_ready=submit_ready)
            spec = load_spec(args.spec)
            # The initial plan stays immutable; resolved PAE plan is separately published.
            R.atomic_json(os.path.join(outdir, "resolved_plan.json"), spec)
        pairs = spec["pairs"]
        while True:
            if time.time() > deadline: raise RuntimeError("watcher 超时，详情见各任务状态")
            try:
                submit_ready(pairs, spec.get("fragments", []), spec.get("shared_msa"))
                queue = R.queue_snapshot()
            except R.SchedulerUnavailable as exc:
                update_spec(args.spec, scheduler_error=str(exc))
                time.sleep(WATCHER_POLL_SEC); continue
            current = load_spec(args.spec)
            msa_ids = current.get("msa_jids", [])
            msa_active = any(j.split("_",1)[0] in msa_ids for j in queue)
            empty_msa_rounds = 0 if msa_active else empty_msa_rounds + 1
            for p in pairs:
                name = p["name"]
                if states[name][0] == "waiting" and empty_msa_rounds >= 3 and not _pair_msa_ready(p, msa_dir, local):
                    blocked[name] = states[name] = ("blocked", "MSA 依赖失败或缺失，可重试")
                if states[name][0] == "succeeded": _archive_input_json(outdir, name)
            counts = {k: sum(st == k for st,_ in states.values()) for k in
                      ("succeeded", "failed", "blocked", "running", "queued", "submitted", "confirming", "waiting")}
            finished = all(st in R.TERMINAL for st,_ in states.values())
            status = "infer_running"
            if finished:
                status = "done" if counts["succeeded"] == len(pairs) and not spec.get("pae_failed") else ("partial_failed" if counts["succeeded"] else "failed")
            update_spec(args.spec, task_states=states, counts=counts, status=status, scheduler_error=None)
            if finished: break
            time.sleep(WATCHER_POLL_SEC)
        rank_info = do_screen_rank(load_spec(args.spec))
        update_spec(args.spec, rank=rank_info)
        note("批次状态: " + status)


# ============================================================================
# 原地 rank(pulldown/scan 内部,§6/§7)
# ============================================================================

def extract_scores(folder, pair=None):
    files = sorted(glob.glob(os.path.join(folder, "*_summary_confidences.json")))
    if not files: return None, None, None
    data = R.read_json(files[0], {})
    iptm, ptm, pae_min = data.get("iptm"), data.get("ptm"), None
    cpp, cip = data.get("chain_pair_pae_min"), data.get("chain_pair_iptm")
    try:
        if pair:
            na = sum(e.get("copies",1) for e in pair["a"]["entities"])
            nb = sum(e.get("copies",1) for e in pair["b"]["entities"])
            # Restrict to the requested A/B interface; both directions for asymmetric PAE.
            indices = [(a,b) for a in range(na) for b in range(na,na+nb)]
            values = [float(cpp[a][b]) for a,b in indices] + [float(cpp[b][a]) for a,b in indices] if cpp else []
            pae_min = min(values) if values else None
            if cip:
                values = [float(cip[a][b]) for a,b in indices if cip[a][b] is not None]
                iptm = sum(values)/len(values) if values else None
            elif na != 1 or nb != 1:
                iptm = None  # No misleading overall score for an unavailable multi-chain interface.
        elif cpp and len(cpp) == 2: pae_min = float(cpp[0][1])
    except (TypeError, ValueError, IndexError): return None, ptm, None
    return iptm, ptm, pae_min


def extract_seed_stats(folder, pair=None):
    """汇总结果目录下所有 seed-*_sample-* 子目录的 iptm -> (mean, std, n)。
    子目录里是每个 seed/每个 sample 的独立模型;根目录的 *_summary_confidences.json
    是 AF3 top-ranked 模型的复制品,不参与统计(否则与 best 重复计数)。
    没有子目录(旧版跑法/单模型)返回 (None, None, 0)。std 用样本标准差(n-1)。"""
    vals = []
    for path in glob.glob(os.path.join(folder, "seed-*_sample-*",
                                       "*_summary_confidences.json")):
        try:
            v, _, _ = extract_scores(os.path.dirname(path), pair)
            if v is not None:
                vals.append(float(v))
        except (OSError, ValueError):
            continue
    if not vals:
        return None, None, 0
    mean = sum(vals) / len(vals)
    std = None
    if len(vals) > 1:
        std = (sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5
    return mean, std, len(vals)


def extract_iptm_ptm(folder):
    """兼容旧接口:只取 (iptm, ptm)。"""
    iptm, ptm, _ = extract_scores(folder)
    return iptm, ptm


def ranked_folder_name(rank, name, iptm, pae_min=None):
    """rank 目录名:rank_001_<name>_iptm_0.62[_paeMin_1.47]。"""
    new_name = f"rank_{rank:03d}_{name}_iptm_{format_iptm_for_name(iptm)}"
    if pae_min is not None:
        new_name += f"_paeMin_{format_iptm_for_name(pae_min)}"
    return new_name


def _collect_screen_rows(spec):
    """按 spec 的 pairs 收集各 pair 的分数与所在目录(兼容已 rank 重命名的目录)。"""
    outdir = spec["outdir"]
    rows = []
    for p in spec["pairs"]:
        if spec.get("version",1)>=2:
            current = spec.get('task_states',{}).get(p['name'])
            state, reason = current if current is not None else R.task_state(outdir,p['name'],None)
            if state != "succeeded":
                rows.append({**p,"iptm":None,"ptm":None,"pae_min":None,"mean_iptm":None,"std_iptm":None,"n_models":0,"folder":p["name"],"status":state})
                continue
        folder = os.path.join(outdir, p["name"])
        if not os.path.isdir(folder):
            # 可能已 rank 过(重跑 watcher):找 rank_*_<name>_iptm*
            hits = glob.glob(os.path.join(outdir, f"rank_*_{p['name']}_iptm*"))
            if hits:
                folder = hits[0]
            else:
                rows.append({**p, "iptm": None, "ptm": None, "pae_min": None,
                             "mean_iptm": None, "std_iptm": None, "n_models": 0,
                             "folder": p["name"],
                             "status": "missing"})
                continue
        iptm, ptm, pae_min = extract_scores(folder, p)
        mean, std, n = (None, None, 0)
        if iptm is not None:
            mean, std, n = extract_seed_stats(folder, p)
        rows.append({**p, "iptm": iptm, "ptm": ptm, "pae_min": pae_min,
                     "mean_iptm": mean, "std_iptm": std, "n_models": n,
                     "folder": os.path.basename(folder),
                     "status": "ok" if iptm is not None else "no_score"})
    return rows


def do_screen_rank(spec):
    """写 ranking.csv / iptm_matrix.csv，保留结果目录;
    scan 另写 iptm_profile.csv / scan_hits.csv / iptm_matrix_protein.csv / report.md。"""
    outdir = spec["outdir"]
    pairs = spec["pairs"]
    topk = spec.get("topk")
    rows = _collect_screen_rows(spec)

    scored = [r for r in rows if r["iptm"] is not None]
    scored.sort(key=lambda r: r["iptm"], reverse=True)
    k = topk if topk else len(scored)
    k = min(k, len(scored))

    for rank, row in enumerate(scored, start=1): row["rank"] = rank if rank <= k else ""
    ranking_path = os.path.join(outdir, "ranking.csv")
    text = io.StringIO(newline="")
    fields = ["rank", "name_a", "name_b", "iptm", "mean_iptm", "std_iptm", "n_models", "ptm", "pae_min", "folder", "status", "task_id"]
    writer = csv.writer(text); writer.writerow(fields)
    for row in scored + [r for r in rows if r["iptm"] is None]:
        writer.writerow([row.get("rank", ""), row["a_label"], row["b_label"], row.get("iptm"),
                         row.get("mean_iptm"), row.get("std_iptm"), row.get("n_models"), row.get("ptm"),
                         row.get("pae_min"), row["folder"], row["status"], row["name"]])
    R.atomic_text(ranking_path, text.getvalue())
    entries=[]
    for row in scored:
        cifs=sorted(Path(outdir,row['folder']).glob('*_model.cif'))
        if not cifs: continue
        a,b=row['a_label'],row['b_label']
        entries.append(dict(label='rank %s · %s+%s · ipTM %.2f' % (row.get('rank','?'),a,b,row['iptm']),
            path=str(cifs[0]),a=a,b=b,rank=row.get('rank',''),iptm=row['iptm']))
    R.atomic_json(os.path.join(outdir,'results_index.json'),dict(version=1,entries=entries))
    note("[rank] 写出排名索引，结果目录保持稳定: " + ranking_path)

    # iptm_matrix.csv(A 行 x B 列)
    groups = spec.get("groups") or {}
    a_labels = groups.get("a_labels") or sorted({r["a_label"] for r in rows})
    b_labels = groups.get("b_labels") or sorted({r["b_label"] for r in rows})
    cell = {}
    for row in rows:
        if row["iptm"] is not None:
            key = (row["a_label"], row["b_label"])
            cell[key] = max(row["iptm"], cell.get(key, -1))
    if len(a_labels)*len(b_labels)>1000000:
        warn("矩阵超过 100 万单元，保留完整 ranking.csv，跳过稀疏大矩阵以避免内存膨胀")
        if spec.get("type")=="scan": _write_scan_reports(spec,rows)
        return {"ranked":k,"total":len(scored),"ranking_csv":ranking_path,"matrix_skipped":True}
    matrix_path = os.path.join(outdir, "iptm_matrix.csv")
    text = io.StringIO(newline=""); writer = csv.writer(text)
    writer.writerow(["A\\B"] + b_labels)
    for a in a_labels: writer.writerow([a] + [cell.get((a,b), "") for b in b_labels])
    R.atomic_text(matrix_path, text.getvalue())
    note(f"[rank] 写出 {matrix_path}")
    _try_plot_matrix(cell, a_labels, b_labels, outdir)

    # scan:报告全套(按数据规模自动精简,见 _write_scan_reports)
    if spec.get("type") == "scan":
        _write_scan_reports(spec, rows)

    return {"ranked": k, "total": len(scored), "ranking_csv": ranking_path}


def _try_plot_matrix(cell, a_labels, b_labels, outdir, png_name="iptm_matrix.png"):
    if not a_labels or not b_labels or len(a_labels)*len(b_labels)>1000000: return
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return
    try:
        data = [[cell.get((a, b), float("nan")) for b in b_labels] for a in a_labels]
        fig, ax = plt.subplots(figsize=(min(16, max(4, len(b_labels) * 0.6)),
                                        min(14, max(3, len(a_labels) * 0.5))))
        im = ax.imshow(data, cmap="viridis", vmin=0, vmax=1)
        xs = list(range(0, len(b_labels), max(1, (len(b_labels) + 39) // 40)))
        ys = list(range(0, len(a_labels), max(1, (len(a_labels) + 39) // 40)))
        ax.set_xticks(xs)
        ax.set_xticklabels([b_labels[i] for i in xs], rotation=90, fontsize=7)
        ax.set_yticks(ys)
        ax.set_yticklabels([a_labels[i] for i in ys], fontsize=7)
        fig.colorbar(im, label="ipTM")
        fig.tight_layout()
        fig.savefig(os.path.join(outdir, png_name), dpi=150)
        plt.close(fig)
        note(f"[rank] 写出 {png_name}")
    except Exception as e:
        warn(f"画矩阵图失败:{e}")


def _fmtf(v, fmt="%.4f"):
    """数值格式化;None -> 空串(CSV 可选列)。"""
    return fmt % v if v is not None else ""


def parse_frag_key(label):
    """'Q12345_t201-500' -> ('Q12345', 201, 500);非片段标签 -> (label, None, None)。"""
    m = re.match(r"^(.*)_t(\d+)-(\d+)$", label or "")
    if m:
        return m.group(1), int(m.group(2)), int(m.group(3))
    return label, None, None


def _parent_of(label):
    """片段/全长标签 -> 所属蛋白(parent)。'Q12345_t201-500' -> 'Q12345'。"""
    return parse_frag_key(label)[0]


def _parent_labels(labels):
    """标签列表 -> 去重保序的 parent 列表。"""
    seen, out = set(), []
    for la in labels:
        p = _parent_of(la)
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _musigma(r):
    """人类可读的 seed 统计:'μ0.71±0.03';单模型无 std 时只给均值。"""
    m = r.get("mean_iptm")
    if m is None:
        return ""
    s = r.get("std_iptm")
    if s is None or (r.get("n_models") or 0) < 2:
        return "μ%.2f" % m
    return "μ%.2f±%.2f" % (m, s)


def _write_scan_profile(spec, rows):
    """iptm_profile.csv:只含 A 侧(groupA)实体的片段剖面。
    列:frag,best_iptm,mean_iptm,std_iptm,n_models,best_pae_min,best_partner
      - frag 即 'Q12345_t201-500' 式标签(位置含在名字里,不再单独给列);
      - 蛋白间按各自最优片段的 ipTM 降序;同一蛋白内按 best_iptm 降序;
      - mean/std/n 汇总自该对结果的所有 seed-*_sample-* 子目录模型。
    组别无需反推:pair 的 a 侧就是 A 组。未被切片的 A 蛋白以单行(全长)出现。
    返回 (best, parent_order, by_parent) 供 report/摘要复用。"""
    outdir = spec["outdir"]
    groups = spec.get("groups") or {}
    best = {}  # a_label -> 最优 row(按 iptm)
    for row in rows:
        if row["iptm"] is None:
            continue
        la = row.get("a_label")
        if not la:
            continue
        cur = best.get(la)
        if cur is None or row["iptm"] > cur["iptm"]:
            best[la] = row
    # A 标签全集:优先 spec 输入顺序(groups.a_labels),再补 spec 未记录的
    a_labels = list(groups.get("a_labels") or [])
    known = set(a_labels)
    if not a_labels:
        a_labels = sorted(best, key=_natural_key)
        known = set(a_labels)
    for la in sorted(set(best) - known, key=_natural_key):
        a_labels.append(la)
    # 按 parent 分组;蛋白间按最优片段 iptm 降序(无分数的按输入顺序垫底)
    by_parent = {}
    first_seen = []
    for la in a_labels:
        p = _parent_of(la)
        if p not in by_parent:
            by_parent[p] = []
            first_seen.append(p)
        by_parent[p].append(la)
    input_idx = {p: i for i, p in enumerate(first_seen)}

    def parent_sort_key(p):
        scores = [best[la]["iptm"] for la in by_parent[p] if la in best]
        if scores:
            return (0, -max(scores), input_idx[p])
        return (1, 0, input_idx[p])

    parent_order = sorted(first_seen, key=parent_sort_key)
    path = os.path.join(outdir, "iptm_profile.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer=csv.writer(f)
        writer.writerow(["frag","best_iptm","mean_iptm","std_iptm","n_models","best_pae_min","best_partner"])
        for p in parent_order:
            def frag_sort_key(la):
                _, s, _ = parse_frag_key(la)
                return (-best[la]["iptm"], s if s is not None else 0)
            labs = sorted([la for la in by_parent[p] if la in best],
                          key=frag_sort_key)
            labs += [la for la in by_parent[p] if la not in best]
            for la in labs:
                r = best.get(la)
                if r is None:
                    writer.writerow([la]+[""]*6)
                    continue
                writer.writerow((
                    la, r["iptm"],
                    _fmtf(r.get("mean_iptm"), "%.2f"),
                    _fmtf(r.get("std_iptm"), "%.2f"),
                    r.get("n_models") or "", _fmtf(r.get("pae_min"), "%.2f"),
                    r.get("b_label") or ""))
    note(f"[rank] 写出 {path}(A 侧,{len(best)}/{len(a_labels)} 有分数,"
         f"按 ipTM 降序)")
    _plot_scan_profiles(outdir, parent_order, by_parent, best)
    return best, parent_order, by_parent


def _plot_scan_profiles(outdir, parent_order, by_parent, best):
    """每蛋白一张片段 ipTM 条形图:x 轴按序列位置排(定位互作区域),
    柱子按 best_partner 着色 + 图例(一眼看出每个区域是和谁结合的)。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return
    partners = sorted({r["b_label"] for r in best.values() if r.get("b_label")})
    try:
        cmap = plt.get_cmap("tab20")
        pcolor = {p: cmap(i % 20) for i, p in enumerate(partners)}
    except Exception:
        pcolor = {}
    for parent in parent_order:
        labs = [la for la in by_parent[parent] if la in best]
        if len(labs) < 2:  # 0/1 个片段:一根柱子没有"沿序列定位"的信息,不画
            continue

        def pos_key(la):
            _, s, _ = parse_frag_key(la)
            return s if s is not None else 0

        labs.sort(key=pos_key)
        xs, ys, colors, legends = [], [], [], []
        for la in labs:
            r = best[la]
            _, s, e = parse_frag_key(la)
            xs.append(f"{s}-{e}" if s is not None else la)
            ys.append(r["iptm"])
            bp = r.get("b_label") or ""
            colors.append(pcolor.get(bp, "steelblue"))
            legends.append(bp)
        try:
            fig, ax = plt.subplots(figsize=(max(4, len(labs) * 0.8), 3.2))
            bars = ax.bar(range(len(xs)), ys, color=colors)
            ax.set_xticks(range(len(xs)))
            ax.set_xticklabels(xs, rotation=90, fontsize=7)
            ax.set_ylabel("best ipTM")
            ax.set_title(parent)
            ax.set_ylim(0, 1)
            seen, handles = set(), []
            for b, bp in zip(bars, legends):
                if bp and bp not in seen:
                    seen.add(bp)
                    handles.append(b)
            if handles:
                ax.legend(handles, list(seen), fontsize=6,
                          loc="upper right", title="best partner",
                          framealpha=0.9)
            fig.tight_layout()
            fig.savefig(os.path.join(
                outdir, f"iptm_profile_{sanitize_name(parent, 40)}.png"), dpi=150)
            plt.close(fig)
        except Exception as e:
            warn(f"画 {parent} 的 profile 图失败:{e}")


def _write_scan_hits(spec, rows):
    """scan_hits.csv:蛋白级互作汇总——每对 A蛋白 x B蛋白 一行,
    给出该蛋白对下最好的片段组合及其 ipTM / 均值±std / pae_min,降序排列。
    先看这张表挑蛋白,再看 iptm_profile.csv 定位片段。返回按 iptm 降序的行列表。"""
    outdir = spec["outdir"]
    hit = {}  # (parent_a, parent_b) -> 最优 row
    for row in rows:
        if row["iptm"] is None:
            continue
        key = (_parent_of(row.get("a_label")), _parent_of(row.get("b_label")))
        if key not in hit or row["iptm"] > hit[key]["iptm"]:
            hit[key] = row
    items = sorted(hit.values(), key=lambda r: r["iptm"], reverse=True)
    path = os.path.join(outdir, "scan_hits.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer=csv.writer(f)
        writer.writerow(["A_protein","B_protein","best_iptm","mean_iptm","std_iptm","n_models","best_A_frag","best_B_frag","best_pae_min","folder"])
        for r in items:
            writer.writerow((
                _parent_of(r["a_label"]), _parent_of(r["b_label"]),
                r["iptm"], _fmtf(r.get("mean_iptm"), "%.2f"),
                _fmtf(r.get("std_iptm"), "%.2f"),
                r.get("n_models") or "", r["a_label"], r["b_label"],
                _fmtf(r.get("pae_min"), "%.2f"), r.get("folder") or ""))
    note(f"[rank] 写出 {path}({len(items)} 对 A x B 蛋白,降序)")
    return items


def _write_protein_matrix(spec, rows):
    """iptm_matrix_protein.csv(+png):蛋白 x 蛋白矩阵,
    单元格 = 该蛋白对下所有片段组合的最高 ipTM。比片段级矩阵小得多,先看全局。"""
    outdir = spec["outdir"]
    groups = spec.get("groups") or {}
    scored = [r for r in rows if r["iptm"] is not None]
    a_parents = _parent_labels(groups.get("a_labels") or
                               sorted({r["a_label"] for r in scored}))
    b_parents = _parent_labels(groups.get("b_labels") or
                               sorted({r["b_label"] for r in scored}))
    cell = {}
    for row in scored:
        key = (_parent_of(row["a_label"]), _parent_of(row["b_label"]))
        cell[key] = max(row["iptm"], cell.get(key, -1))
    if len(a_parents)*len(b_parents)>1000000:
        warn("蛋白矩阵过大，保留完整 scan_hits.csv");return
    path = os.path.join(outdir, "iptm_matrix_protein.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer=csv.writer(f);writer.writerow(["A\\B"]+b_parents)
        for a in a_parents:
            vals = [f"{cell[(a, b)]:.4f}" if (a, b) in cell else ""
                    for b in b_parents]
            writer.writerow([a]+vals)
    note(f"[rank] 写出 {path}")
    _try_plot_matrix(cell, a_parents, b_parents, outdir,
                     png_name="iptm_matrix_protein.png")


def _write_scan_report(spec, rows, prof, hit_items):
    """report.md:终端 `more` 即可读的 scan 汇总报告——
    top 互作对、每个 A 蛋白总览、蛋白级矩阵、seed 波动预警(best − μ > 0.05)。"""
    outdir = spec["outdir"]
    best, parent_order, by_parent = prof
    scored = sorted([r for r in rows if r["iptm"] is not None],
                    key=lambda r: r["iptm"], reverse=True)
    groups = spec.get("groups") or {}
    n_a = len(groups.get("a_labels") or [])
    n_b = len(groups.get("b_labels") or [])
    n_models = sum((r.get("n_models") or 0) for r in rows)
    L = [f"# scan 报告:{spec.get('name', '?')}", ""]
    L.append(f"- A 组 {n_a} 个实体,B 组 {n_b} 个实体;"
             f"pairs {len(scored)}/{len(rows)} 有分数")
    if spec.get("num_seeds"):
        L.append(f"- 每对 {spec['num_seeds']} 个 seed,共汇总 {n_models} 个模型"
                 f"(mean/std 来自各 seed-*_sample-* 子目录)")
    L += ["", "## Top 15 互作对(按 best ipTM 降序)", "",
          "| # | A 片段 | B 片段/蛋白 | ipTM | seed 统计 | pae_min | 目录 |",
          "|---|--------|------------|------|----------|--------|------|"]
    for i, r in enumerate(scored[:15], 1):
        L.append(f"| {i} | {r['a_label']} | {r['b_label']} | {r['iptm']:.4f} "
                 f"| {_musigma(r)} | {_fmtf(r.get('pae_min'), '%.2f')} "
                 f"| {r.get('folder', '')} |")
    L += ["", "## A 蛋白总览(按 best 片段降序)", ""]
    for p in parent_order:
        scored_labs = [la for la in by_parent[p] if la in best]
        if not scored_labs:
            L.append(f"- **{p}**:无分数")
            continue
        top_la = max(scored_labs, key=lambda la: best[la]["iptm"])
        r = best[top_la]
        _, s, e = parse_frag_key(top_la)
        where = f"t{s}-{e}" if s is not None else "全长"
        flag = ""
        m = r.get("mean_iptm")
        if m is not None and r["iptm"] - m > 0.05:
            flag = "  ⚠ seed 波动大"
        mu = _musigma(r)
        L.append(f"- **{p}**:best {r['iptm']:.4f}"
                 f"{f' ({mu})' if mu else ''} @ {where} "
                 f"→ {r.get('b_label') or '?'}{flag}")
    if hit_items:
        L += ["", "## 蛋白级 ipTM 矩阵(单元格 = 该蛋白对最好的片段组合)", ""]
        a_parents = _parent_labels([r["a_label"] for r in hit_items])
        b_parents = _parent_labels([r["b_label"] for r in hit_items])
        cell = {(_parent_of(r["a_label"]), _parent_of(r["b_label"])): r["iptm"]
                for r in hit_items}
        L.append("| A\\B | " + " | ".join(b_parents) + " |")
        L.append("|" + "---|" * (len(b_parents) + 1))
        for a in a_parents:
            vals = [f"{cell[(a, b)]:.2f}" if (a, b) in cell else "·"
                    for b in b_parents]
            L.append(f"| {a} | " + " | ".join(vals) + " |")
    unstable = [(la, r) for la, r in best.items()
                if r.get("mean_iptm") is not None
                and r["iptm"] - r["mean_iptm"] > 0.05]
    if unstable:
        unstable.sort(key=lambda t: t[1]["iptm"] - t[1]["mean_iptm"],
                      reverse=True)
        L += ["", "## ⚠ seed 波动预警(best − μ > 0.05,疑似单 seed 偶然)", ""]
        for la, r in unstable[:10]:
            L.append(f"- {la}:best {r['iptm']:.4f} vs {_musigma(r)} "
                     f"(best partner: {r.get('b_label') or '?'})")
        if len(unstable) > 10:
            L.append(f"- …等共 {len(unstable)} 条")
    path = os.path.join(outdir, "report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    note(f"[rank] 写出 {path}")


def _scan_parent_counts(spec, rows):
    """scan 两侧各有几个蛋白(parent 数;片段归回其全长蛋白)。"""
    groups = spec.get("groups") or {}
    scored = [r for r in rows if r["iptm"] is not None]
    a = groups.get("a_labels") or sorted({r["a_label"] for r in scored})
    b = groups.get("b_labels") or sorted({r["b_label"] for r in scored})
    return len(_parent_labels(a)), len(_parent_labels(b))


def _write_scan_reports(spec, rows):
    """scan 全套报告,按数据规模自动精简:
    - iptm_profile.csv / report.md:总是生成;
    - scan_hits.csv:A、B 各只有 1 个蛋白时跳过(与 profile 信息重复);
    - iptm_matrix_protein.csv/png:任一侧不足 2 个蛋白时跳过(构不成矩阵);
    - iptm_profile_<蛋白>.png:只有 1 个片段的蛋白不画(在 _plot_scan_profiles 内)。
    返回 profile 结构供终端摘要复用。"""
    prof = _write_scan_profile(spec, rows)
    n_pa, n_pb = _scan_parent_counts(spec, rows)
    hit_items = []
    if n_pa >= 2 or n_pb >= 2:
        hit_items = _write_scan_hits(spec, rows)
    else:
        note("[rank] A、B 各只有 1 个蛋白:省略 scan_hits.csv(信息同 profile)")
    if n_pa >= 2 and n_pb >= 2:
        _write_protein_matrix(spec, rows)
    else:
        note(f"[rank] A 侧 {n_pa} 个、B 侧 {n_pb} 个蛋白:"
             f"省略蛋白级矩阵(两侧都 ≥2 个才生成)")
    _write_scan_report(spec, rows, prof, hit_items)
    return prof


def _print_profile_summary(prof):
    """终端分组打印 profile(A 侧,每蛋白 best 在前)。"""
    best, parent_order, by_parent = prof
    note("")
    note("== iptm profile(A 侧;蛋白按 best 降序,组内按 best_iptm 降序)==")
    for p in parent_order:
        labs = [la for la in by_parent[p] if la in best]
        if not labs:
            continue
        labs.sort(key=lambda la: best[la]["iptm"], reverse=True)
        note(f"\n{p}  (best {best[labs[0]]['iptm']:.4f})")
        for la in labs:
            r = best[la]
            _, s, e = parse_frag_key(la)
            where = f"t{s}-{e}" if s is not None else "全长"
            mu = _musigma(r)
            note(f"  {where:<14} {r['iptm']:.4f}  {mu:<18} "
                 f"-> {r.get('b_label') or ''}")


# ============================================================================
# 命令:profile(为已完成的 scan 批次重新生成 iptm_profile.csv,不重 rank)
# ============================================================================

def cmd_profile(args):
    """为已完成的 scan 批次重新生成全套报告(iptm_profile.csv / scan_hits.csv /
    iptm_matrix_protein.csv / report.md)。只读结果目录,
    不改动任何文件夹、不重排名。spec 新布局(批次内 spec.json)与旧布局都支持。"""
    outdir = _resolve_batch_dir(args.dir)
    spec_path = batch_paths(outdir)["spec"]
    if not os.path.isfile(spec_path):
        legacy = _spec_for_tag(_batch_tag_of(outdir))
        if legacy:
            spec_path = legacy
    if not os.path.isfile(spec_path):
        die(f"找不到该批次的 spec.json(试过批次目录内与旧版 .specs/)",
            hint="该批次若是很早期没有 spec 的版本跑的,无法自动生成 profile")
    spec = load_spec(spec_path)
    if spec.get("type") != "scan":
        die(f"该批次不是 scan(spec type={spec.get('type')}),无 profile 可生成")
    spec["outdir"] = outdir  # 以实际目录为准(批次可能被移动过)
    rows = _collect_screen_rows(spec)
    ok = sum(1 for r in rows if r["iptm"] is not None)
    note(f"{os.path.basename(outdir)}:{ok}/{len(rows)} 对有分数,重新生成报告 ...")
    prof = _write_scan_reports(spec, rows)
    _print_profile_summary(prof)


def cmd_refresh(args):
    """按最新脚本重新汇总批次结果(do_screen_rank:更新排名索引 +
    重写 ranking.csv / iptm_matrix.csv,scan 另重写 profile/hits/蛋白矩阵/report)。
    不重跑 msa/infer;只读现有结果目录。_collect_screen_rows 兼容已 rank 的目录,幂等。"""
    outdir = _resolve_batch_dir(args.dir)
    spec_path = batch_paths(outdir)["spec"]
    if not os.path.isfile(spec_path):
        legacy = _spec_for_tag(_batch_tag_of(outdir))
        if legacy:
            spec_path = legacy
    if not os.path.isfile(spec_path):
        die(f"找不到该批次的 spec.json(试过批次目录内与旧版 .specs/)",
            hint="早期无 spec 的批次无法 refresh")
    spec = load_spec(spec_path)
    if spec.get("type") not in ("pulldown", "scan"):
        die(f"refresh 只支持 pulldown/scan 批次(spec type={spec.get('type')})")
    spec["outdir"] = outdir  # 以实际目录为准
    info = do_screen_rank(spec)
    note(f"[refresh] {os.path.basename(outdir)}:重汇总完成,"
         f"更新排名 {info['ranked']}/{info['total']} 对 -> {info['ranking_csv']}")


# ============================================================================
# 命令:rank(独立排序,§9,收编 batch_rank.py)
# ============================================================================

def _rank_collect_folders(inputs, source_dir):
    seen = set()
    folders = []
    for inp in inputs:
        p = Path(inp)
        if p.is_dir():
            rp = str(p.resolve())
            if rp not in seen:
                seen.add(rp)
                folders.append(p)
                note(f"  [dir]   {p}")
            continue
        if p.is_file() and p.suffix.lower() in (".txt", ".tsv", ".csv"):
            idents = read_task_file(str(p))
            if not idents:
                continue
            note(f"  [list]  {p}({len(idents)} 个标识符)")
            for ident in idents:
                ident = ident.split(",")[0].split("+")[0].strip()
                matched = sorted(Path(source_dir).glob(f"{ident}_*"))
                matched = [m for m in matched if m.is_dir()]
                direct = Path(source_dir) / ident
                if direct.is_dir():
                    matched.insert(0, direct)
                if not matched:
                    warn(f"在 {source_dir} 中没有匹配 '{ident}' 的结果目录")
                    continue
                for m in matched:
                    rp = str(m.resolve())
                    if rp not in seen:
                        seen.add(rp)
                        folders.append(m)
                        note(f"      + {m.name}")
            continue
        die(f"'{inp}' 既不是目录也不是清单文件(.txt/.tsv/.csv)")
    return folders


def cmd_rank(args):
    source = args.source_dir or HOST_OUTPUT
    folders = _rank_collect_folders(args.inputs, source)
    dest = Path(args.output) if args.output and ("/" in args.output or os.sep in args.output) else Path(args.output_dir or HOST_OUTPUT)/("batch_rank_"+(args.output or "results"))
    copying = bool(getattr(args,"copy",False))
    if args.delete_originals and not copying: die("--delete-originals 必须显式搭配 --copy")
    rows=[]
    for folder in folders:
        iptm,ptm,pae=extract_scores(str(folder))
        if iptm is not None: rows.append((float(iptm),ptm,pae,folder))
    rows.sort(key=lambda row:row[0],reverse=True)
    if not rows: die("没有有效 ipTM 结果")
    if args.dry_run:
        note(f"[DRY RUN] {len(rows)} 个结果，模式={'复制' if copying else '索引'}，输出 {dest}");return
    dest.mkdir(parents=True,exist_ok=True)
    out=io.StringIO(newline=""); writer=csv.writer(out);writer.writerow(["rank","name","iptm","ptm","pae_min","folder"])
    copied=[]
    for rank,(iptm,ptm,pae,folder) in enumerate(rows,1):
        target=folder.resolve()
        if copying:
            target=dest/ranked_folder_name(rank,folder.name,iptm,pae)
            if target.exists(): die("复制目标已存在，请选择新输出目录: " + str(target))
            if folder.resolve() in dest.resolve().parents or folder.resolve()==dest.resolve(): die("输出目录不能在源结果目录内")
            shutil.copytree(str(folder),str(target));copied.append(folder)
        writer.writerow([rank,folder.name,iptm,ptm,pae,str(target.resolve())])
    R.atomic_text(dest/"ranking.csv",out.getvalue())
    if args.delete_originals:
        for folder in copied: shutil.rmtree(str(folder))
    note(f"完成: {len(rows)} 个结果，{dest / 'ranking.csv'}")


# ============================================================================
# 批次目录 / spec 解析辅助(profile 等命令共用)
# ============================================================================

def _resolve_batch_dir(arg):
    """批次参数 -> 批次输出目录(支持路径 / 批次名 / scan_<名> 等)。"""
    resolved = R.resolve_spec(arg, HOST_OUTPUT, HOST_INFER_DATA)
    if resolved:
        spec = R.spec_summary(resolved)
        return spec.get('outdir') or os.path.dirname(resolved)
    cands = [arg, os.path.join(HOST_OUTPUT, arg)]
    for prefix in ("scan_", "pulldown_", "batch_", "batch_rank_"):
        cands.append(os.path.join(HOST_OUTPUT, prefix + arg))
    for c in cands:
        if os.path.isdir(c):
            return c
    die(f"找不到批次输出目录:{arg}")


def _batch_tag_of(outdir):
    """从输出目录名推批次 tag:scan_<tag> / pulldown_<tag> / batch_<tag>。"""
    base = os.path.basename(outdir.rstrip("/\\"))
    return re.sub(r"^(scan|pulldown|batch)_", "", base)


def _spec_for_tag(tag):
    """按 tag 找 spec.json:新布局 output/<批次目录>/spec.json 优先,
    兼容旧布局 output/.specs/。"""
    resolved=R.resolve_spec(tag,HOST_OUTPUT,HOST_INFER_DATA)
    if resolved:return resolved
    for prefix in ("scan_", "pulldown_", "batch_", "batch_rank_", ""):
        p = os.path.join(HOST_OUTPUT, prefix + tag, "spec.json")
        if os.path.isfile(p):
            return p
    hits = glob.glob(os.path.join(HOST_SPECS, f"{tag}.*.spec.json")) + \
        glob.glob(os.path.join(HOST_SPECS, f"{tag}.spec.json"))
    if len(hits)>1:die('多个历史计划匹配，请指定 spec:\n'+'\n'.join(hits))
    return hits[0] if hits else None


def _natural_key(s):
    """自然排序键:数字段按数值比较(t201-500 排在 t1001-1300 前)。
    每段包成 (是否非数字, 值) 避免 int/str 混比。"""
    return [(1, t) if not t.isdigit() else (0, int(t))
            for t in re.split(r"(\d+)", s)]


# ============================================================================
# 命令:status(§8)
# ============================================================================

def _log_files_for(name):
    """收集文件名中含 name 的日志文件(.out/.err)。
    新布局:各批次/任务目录的 logs/(msa/ infer/ watcher/controller);
    旧布局兼容:output/.logs/、batch_*_out/、infer_logs/、批次根部的 watcher 日志。"""
    cands = []
    roots = [HOST_LOGS]  # 旧布局
    for d in glob.glob(os.path.join(HOST_OUTPUT, "*")):
        if not os.path.isdir(d):
            continue
        logs_dir = os.path.join(d, "logs")  # 新布局
        if os.path.isdir(logs_dir):
            roots.append(logs_dir)
        for sub in glob.glob(os.path.join(d, "batch_*_out")) + \
                [os.path.join(d, "infer_logs")]:  # 旧布局
            if os.path.isdir(sub):
                roots.append(sub)
        cands.extend(glob.glob(os.path.join(d, "*_watcher_*.out")))
        cands.extend(glob.glob(os.path.join(d, "*_controller_*.out")))
    for r in roots:
        if os.path.isdir(r):
            cands.extend(glob.glob(os.path.join(r, "**", "*"), recursive=True))
    out = []
    for f in cands:
        base = os.path.basename(f)
        if os.path.isfile(f) and name in base and \
                (base.endswith(".err") or base.endswith(".out")):
            out.append(f)
    return out


def _diagnose_logs(name):
    """扫日志区,对任务失败原因分类。返回诊断字符串或 None。"""
    patterns = _log_files_for(name)
    if not patterns:
        return None
    patterns.sort(key=os.path.getmtime, reverse=True)
    for logf in patterns[:3]:
        try:
            with open(logf, errors="replace") as f:
                content = f.read()[-20000:]
        except OSError:
            continue
        low = content.lower()
        if "out of memory" in low:
            return "GPU OOM(若已自动重投,请检查 fallback 分区 recovery job)"
        if "db copy verification failed" in low or "db copy by another task failed" in low:
            return "数据库拷贝失败"
        if "due to time limit" in low or "time limit" in low and "slurm" in low:
            return "超时被杀"
        if "traceback" in low:
            lines = [l for l in content.splitlines() if l.strip()]
            tail = lines[-1][:120] if lines else ""
            exc = ""
            for l in reversed(content.splitlines()):
                m = re.match(r"^(\w+(?:Error|Exception|Interrupt))\b", l.strip())
                if m:
                    exc = l.strip()[:120]
                    break
            return f"Python 异常:{exc or tail}"
        if "error" in low:
            for l in reversed(content.splitlines()):
                if "error" in l.lower():
                    return "报错:" + l.strip()[:120]
    return None


def _status_of_job_name(name, batch_dir, squeue):
    out_base = batch_dir or HOST_OUTPUT
    out_dir = os.path.join(out_base, name)
    if os.path.isdir(out_dir) and \
            glob.glob(os.path.join(out_dir, "*_summary_confidences.json")):
        return "DONE", ""
    for jname, state in squeue:
        if name in jname:
            return state, ""
    diag = _diagnose_logs(name)
    if diag:
        return "FAILED", diag
    if os.path.isdir(out_dir):
        return "RUNNING", "(输出目录已建但无结果)"
    return "MISSING", "未开始 / 无日志"


def _status_spec(spec_path, squeue):
    spec = load_spec(spec_path)
    queue = R.queue_snapshot()
    ids = R.registered_job_ids(spec, os.path.dirname(spec_path))
    root = spec.get("outdir") or spec.get("batch_dir") or spec.get("output_dir",HOST_OUTPUT)
    items = spec.get("pairs") or spec.get("jobs") or ([{"name":spec["name"]}] if spec.get("type")=="raw_json" else [])
    states={item["name"]:R.task_state(root,item["name"],queue) for item in items}
    if spec.get('msa_only'):
        if spec.get('type')=='raw_json':
            ready=R.msa_ready(spec['msa_dir'],spec['name'])
            states={spec['name']:('msa_ready' if ready else 'waiting','')}
        else:
            states={j['name']:('msa_ready' if not collect_missing_msa([j],spec['msa_dir'])[0] else 'waiting','') for j in items}
    return dict(spec=spec_path,type=spec.get("type"),name=spec.get("name"),status=spec.get("status"),
                error=spec.get("error"),counts=spec.get("counts",{}),task_states=states,
                active_jobs=[dict(id=j,name=n,state=st) for j,(n,st) in queue.items() if j in ids or j.split("_",1)[0] in ids],
                pairs_done=sum(st in ('succeeded','msa_ready') for st,_ in states.values()),pairs_total=len(items))


def cmd_status(args):
    squeue = squeue_jobs()
    report = {}

    if args.jobs:
        src = args.jobs
        resolved = R.resolve_spec(src, HOST_OUTPUT, HOST_INFER_DATA)
        if resolved:
            report = _status_spec(resolved, squeue)
        else:
            expressions, batch_tag = resolve_input_expressions(src)
            tasks = []
            for expr in expressions:
                matches = [p for p in R.find_specs(HOST_OUTPUT, HOST_INFER_DATA)
                           if expr in load_spec(p).get('expressions',[]) or any(j.get('expression')==expr for j in load_spec(p).get('jobs',[]))]
                if len(matches)>1: die('输入对应多个任务，请指定 spec:\n'+'\n'.join(matches))
                tasks.append(_status_spec(matches[0],squeue) if matches else dict(expression=expr,state='MISSING',reason='无匹配的任务计划'))
            report = {"jobs_file": src, "tasks": tasks}
    else:
        # 总览
        by_state = {}
        for _, st in squeue:
            by_state[st] = by_state.get(st, 0) + 1
        msa_count = count_msa_products(HOST_MSA_DATA)
        out_count = len([d for d in os.listdir(HOST_OUTPUT)
                         if os.path.isdir(os.path.join(HOST_OUTPUT, d))
                         and not d.startswith(".")]) \
            if os.path.isdir(HOST_OUTPUT) else 0
        specs = sorted(glob.glob(os.path.join(HOST_OUTPUT, "*", "spec.json")))
        spec_names = [os.path.basename(os.path.dirname(s)) for s in specs]
        if os.path.isdir(HOST_SPECS):  # 旧布局
            spec_names += [os.path.basename(s) for s in sorted(
                glob.glob(os.path.join(HOST_SPECS, "*.spec.json")))]
        report = {"squeue": by_state, "my_jobs": len(squeue), "queue_jobs": R.queue_snapshot(),
                  "msa_products": msa_count, "output_dirs": out_count,
                  "specs": spec_names}

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        _print_status(report)


def _print_status(report):
    if "tasks" in report:
        counts = {}
        for t in report["tasks"]:
            counts[t["state"]] = counts.get(t["state"], 0) + 1
            line = f"  {t['state']:<8} {t.get('name', t['expression'])}"
            if t.get("reason"):
                line += f"   ({t['reason']})"
            print(line)
        print("- " * 30)
        print("  汇总:" + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    elif "spec" in report:
        for k, v in report.items():
            print(f"  {k}: {v}")
    else:
        print("squeue 状态:" + (json.dumps(report["squeue"]) if report["squeue"] else "(空)"))
        print(f"  我的 job 数:{report['my_jobs']}")
        print(f"  MSA 产物:{report['msa_products']}   infer 输出目录:{report['output_dirs']}")
        if report["specs"]:
            print(f"  specs({len(report['specs'])}):" + ", ".join(report["specs"][:10]))


# ============================================================================
# argparse
# ============================================================================

EPILOG_RUN = """
例子:
  af3.py run P12345                          # 单体(默认 MSA->infer 端到端)
  af3.py run P12345x6                        # homo 六聚体
  af3.py run P12345+Q12345                   # 复合物
  af3.py run "P12345+d:ACGTACGT+l:ATPx2"     # 蛋白+DNA+配体
  af3.py run "P12345+l:NAG,FUC"              # 糖链配体
  af3.py run "P12345+l:CC(=O)Oc1ccccc1C(=O)O"  # SMILES 配体
  af3.py run "P12345+l:ATP" --bonds "A:145:SG->C:1:C04"
  af3.py run "P12345:mod=SEP@128,TPO@216" --num-seeds 3
  af3.py run P12345+Q12345 --template-free
  af3.py run my_jobs.txt                     # 批量(txt/tsv/csv)
  af3.py run --json full_example.json        # 手写 JSON 高级出口
阶段控制:
  af3.py run --msa-only P12345               # 只补 MSA(已存在则跳过)
  af3.py run --msa-only my_jobs.txt          # 批量预建 MSA 池
  af3.py run --infer-only P12345+Q12345      # 只做 infer(要求 MSA 已就绪)
  af3.py run --infer-only --json full.json   # 手写完整 JSON 直接 infer
说明:copies/配体/PTM/bonds/seeds 不影响 MSA,将在 infer 阶段注入。
"""

EPILOG_MSA = """
[已并入 run]'af3.py msa ...' 等价于 'af3.py run --msa-only ...',仍可使用。
例子:
  af3.py run --msa-only P12345               # 只做 MSA(已存在则跳过)
  af3.py run --msa-only my_jobs.txt          # 批量 MSA
  af3.py run --msa-only P12345:trunc=1-300   # 截短(独立 MSA)
说明:copies/配体/PTM/bonds/seeds 不影响 MSA,将在 infer 阶段注入。
"""

EPILOG_INFER = """
[已并入 run]'af3.py infer ...' 等价于 'af3.py run --infer-only ...',仍可使用。
例子:
  af3.py run --infer-only P12345             # 要求 MSA 已就绪
  af3.py run --infer-only P12345+Q12345 --num-seeds 3
  af3.py run --infer-only my_jobs.txt
  af3.py run --infer-only --json full.json   # 手写完整 JSON 直接提交
"""

EPILOG_PULLDOWN = """
例子:
  af3.py pulldown groupA.txt groupB.txt          # 两组全组合
  af3.py pulldown P12345 /path/to/your/library.tsv             # 单蛋白 x 列表
  af3.py pulldown A.txt B.txt --topk 20          # 只标记前 20 名
  af3.py pulldown A.txt B.txt --self             # 追加组内组合
  af3.py pulldown A.txt B.txt --num-seeds 2
复合侧(每行一个侧,bait 先成复合物再筛 target;允许 xN):
  af3.py pulldown "P12345+Q12345" /path/to/your/library.tsv        # (P12345+Q12345) x 库
  af3.py pulldown "P12345x2+Q12345" /path/to/your/library.tsv      # 同源二聚体复合物 x 库
说明:每行一个"侧" = 1+ 个实体(+ 连接);侧内所有实体写进同一份 AF3 输入。
侧内允许 蛋白(含 PTM :mod=)/ DNA / RNA / 配体。
"""

EPILOG_SCAN = """
例子:
  af3.py scan P12345 Q12345              # 单对(>500aa 自动切窗)
  af3.py scan groupA.txt groupB.txt      # 批量
  af3.py scan A B --win 400 --overlap 150
  af3.py scan A B --shared-msa           # 全长 MSA + 切列(省机时,近似)
复合侧(每行一个侧,+ 连接,允许 xN;切片只看侧内单蛋白长度):
  af3.py scan "Ax2+B+C" X                # 侧内片段组合做笛卡尔积
说明:scan 默认每对 4 个 seed(--num-seeds/--seeds 可改);
     非 shared 时同时做全长 pair(文件夹名带 full-length,参与 ranking);
     shared-msa 的切列产物只写批次目录 msa_local/(不进共享池)。
"""

EPILOG_RANK = """
例子:
  af3.py rank list.txt -o my_ranking
  af3.py rank output/jobA output/jobB -o my_ranking
  af3.py rank list.txt --dry-run
"""

EPILOG_STATUS = """
例子:
  af3.py status                          # 总览
  af3.py status --jobs my_jobs.txt       # 每任务状态 + 失败诊断
  af3.py status --jobs output/scan_XX/spec.json
  af3.py status --json                   # 机器可读
"""

EPILOG_PROFILE = """
为已跑完的 scan 批次重新生成全套报告:
  iptm_profile.csv         A 侧片段剖面(frag,best_iptm,mean_iptm,std_iptm,
                           n_models,best_pae_min,best_partner;只含 groupA 的
                           片段,蛋白间/组内均按 ipTM 降序)
  scan_hits.csv            蛋白级汇总(每对 A x B 蛋白一行,先挑蛋白看这张)
  iptm_matrix_protein.csv  蛋白 x 蛋白矩阵(单元格 = 最好的片段组合)+ 热图 png
  report.md                终端 more 可读的汇总(top 互作、A 蛋白总览、
                           蛋白级矩阵、seed 波动预警 best − μ > 0.05)
按数据规模自动精简:单蛋白对(A、B 各 1 个)省略 scan_hits/蛋白级矩阵;
只有 1 个片段的蛋白不画条形图。
只读结果目录,不改动任何文件夹、不重新 rank;已 rank 重命名的目录也能识别。
spec 新布局(批次内 spec.json)与旧布局(.specs/)都支持。
mean/std 汇总自各 pair 结果目录下 seed-*_sample-* 子目录的所有模型。

例子:
  af3.py profile scan_HW-shared-msa
  af3.py profile /path/to/your/scan_batch
"""


def _add_common_submit_args(p):
    p.add_argument('--empty-msa-policy', choices=('review', 'reuse', 'recompute'), default='review',
                   help='已有 MSA 含合法空字段时：review 等待确认；reuse 复用当前文件；recompute 在新目录重新计算')
    p.add_argument("--seeds", default=None,
                   help="seeds:'12345' | '1,2,3' | 'random'(默认 1 个随机)")
    p.add_argument("--num-seeds", type=int, default=None,
                   help="生成 N 个随机 seed(覆盖 --seeds)")
    p.add_argument("--template-free", action="store_true",
                   help="infer 时每条链 templates:[](模板-free)")
    p.add_argument("--msa-free", action="store_true",
                   help="MSA-free(unpairedMsa/pairedMsa 置空)")
    p.add_argument("--bonds", default=None,
                   help="共价键:'A:145:SG->C:1:C04;I:1:O6->I:2:C1'")
    p.add_argument("--user-ccd", default=None,
                   help="自定义配体词典 cif 文件(userCCDPath)")
    p.add_argument("--msa-dir", default=HOST_MSA_DATA,
                   help=f"MSA 产物池目录(默认 {HOST_MSA_DATA})")
    p.add_argument("--output-dir", default=HOST_OUTPUT,
                   help=f"infer 输出根目录(默认 {HOST_OUTPUT})")
    p.add_argument("--max-concurrent", type=int, default=None,
                   help=f"SLURM 并发上限(默认 MSA {MSA_MAX_CONCURRENT} / infer {INF_MAX_CONCURRENT})")
    p.add_argument("--partition", default=None,
                   help=f"infer GPU 分区（默认使用 INF_PARTITION；配置备用分区后，"
                        f">{INF_BIG_TOKEN} token 自动选备用分区；"
                        f">{INF_MAX_TOKEN} token 按配置上限拒绝提交）")
    p.add_argument("--msa-partition", default=None,
                   help="MSA CPU 作业分区（默认使用配置的 MSA_PARTITION；不申请 GPU）")
    p.add_argument("--aux-partition", default=None,
                   help="watcher/controller 提交分区(默认跟随 --msa-partition,"
                        "否则使用配置的 AUX_PARTITION；后者为空时跟随 MSA_PARTITION；1 核轻量任务)")
    p.add_argument("--fallback-partition", default=None,
                   help="大 token / OOM 备用分区（默认使用配置；显式空字符串关闭自动转投）")
    p.add_argument("--name", default=None, help="自定义任务/批次名")
    p.add_argument("--dry-run", action="store_true", help="只打印计划,不提交")
    p.add_argument("--force", action="store_true", help="覆盖已有输出/同名 spec")



def cmd_continue(args):
    from contextlib import ExitStack
    source = os.path.abspath(args.spec if os.path.isfile(args.spec) else os.path.join(args.spec, 'spec.json'))
    work = os.path.dirname(source)
    with R.lifecycle_lock(work), ExitStack() as resources:
        old = load_spec(source)
        pinned_config = R.read_json(os.path.join(work, '.runtime', 'config.json')) or old.get('config') or {}
        pinned_cache = pinned_config.get('HOST_CACHE', HOST_CACHE)
        resources.callback(setattr, R, 'ASSET_ROOT', R.ASSET_ROOT)
        resources.callback(globals().__setitem__, 'HOST_CACHE', HOST_CACHE)
        globals()['HOST_CACHE'] = pinned_cache
        R.ASSET_ROOT = os.path.join(pinned_cache, 'assets') if pinned_cache else ''
        if not old.get('msa_only') or old.get('status') != 'done':
            die('只能从已完成的 MSA-only 计划继续推理')
        if old.get('type') not in ('run','raw_json'): die('该计划不支持阶段续接')
        raw_data = msa_data_path(old['name'], old['msa_dir']) if old.get('type') == 'raw_json' else None
        raw_repair = bool(raw_data and not R.msa_ready(old['msa_dir'], old['name']) and
                          _msa_output_occupied(old['msa_dir'], old['name']))
        review_args = argparse.Namespace()
        review_args.__dict__.update(old.get('submit_args', {}))
        review_args.msa_dir = old['msa_dir']
        review_args.dry_run = bool(getattr(args, 'dry_run', False))
        review_args.empty_msa_policy = getattr(args, 'empty_msa_policy', 'review')
        review_args.empty_msa_approved = dict(getattr(review_args, 'empty_msa_approved', {}) or {})
        incoming_approvals = getattr(args, 'empty_msa_approved', None)
        if isinstance(incoming_approvals, dict): review_args.empty_msa_approved.update(incoming_approvals)
        _begin_msa_review(review_args)
        entities = [entity for job in old.get('jobs', []) for entity in job.get('entities', [])]
        original_keys = [entity.get('_msa_key') for entity in entities]
        _review_entities(entities, review_args)
        raw_recompute = bool(raw_data and not raw_repair and _review_data_path(raw_data, review_args) == 'recompute')
        _finish_msa_review(review_args)
        recompute = raw_recompute or raw_repair or original_keys != [entity.get('_msa_key') for entity in entities]
        missing = collect_missing_msa(old.get('jobs', []), old['msa_dir'])[0]
        if not recompute and (missing or (raw_data and not R.msa_ready(old['msa_dir'], old['name']))):
            die('MSA 缓存缺失，无法继续推理；请从原输入创建新计划')
        child = work + '__infer'
        if recompute:
            child += '__recalc_' + R.digest([time.time_ns(), [entity.get('_msa_key') for entity in entities]], 12)
        target = os.path.join(child, 'spec.json')
        if os.path.exists(target):
            note('推理续接已存在；不会重复提交: ' + target); return target
        if getattr(args, 'dry_run', False): note('[DRY RUN] 继续已固定的计划 -> ' + target); return target
        ensure_deployment(pinned_config or _submission_config(review_args), require_msa=recompute)
        spec = {k:v for k,v in old.items() if k not in ('storage_version','plan_file','detail_files','controller_jid',
            'watcher_jid','counts','task_states','revision','created_at','updated_at','error','scheduler_error')}
        output = os.path.join(child, 'results')
        spec.update(msa_only=False, infer_only=not recompute, continuation_of=source, status='preparing',
                    config=pinned_config or old.get('config') or config_snapshot(),
                    output_dir=output, batch_dir=(child if old.get('batch_dir') else None), msa_jids=[], infer_jids=[])
        spec['submit_args'] = {key: value for key, value in vars(review_args).items() if key != 'func' and not key.startswith('_')}
        spec['submit_args'].update(msa_only=False, infer_only=not recompute, output_dir=output, dry_run=False)
        spec['msa_entities'] = collect_missing_msa(spec.get('jobs', []), old['msa_dir'])[0]
        if raw_data: spec['raw_input'] = raw_data
        if raw_recompute or raw_repair:
            recipe_path = old['raw_input'] if raw_repair else raw_data
            data = copy.deepcopy(R.read_json(recipe_path))
            if not isinstance(data, dict):
                die('原始 JSON 配方缺失或损坏，不能自动恢复；请提供原始输入创建新计划')
            if raw_recompute: _clear_empty_raw_fields(data, raw_data)
            def resolve_relative(value):
                if isinstance(value, list): return [resolve_relative(child) for child in value]
                if not isinstance(value, dict): return value
                return {key: (os.path.abspath(os.path.join(os.path.dirname(recipe_path), child))
                              if key.lower().endswith('path') and isinstance(child, str) and not os.path.isabs(child)
                              else resolve_relative(child)) for key, child in value.items()}
            data = resolve_relative(data)
            name = sanitize_name(old.get('label') or old['name'], 64) + '__recalc_' + R.digest([child, data], 16)
            data['name'] = name
            data = R.externalize_input(data, os.path.join(HOST_CACHE, 'assets'))
            raw_input = os.path.join(batch_paths(child)['msa_input'], name + '_input.json')
            R.atomic_json(raw_input, data)
            spec.update(name=name, raw_input=raw_input)
            spec['submit_args']['json'] = raw_input
        write_spec(spec, target)
        jid = submit_controller(target, spec['name'], [], partition=_aux_partition_for(review_args))
        update_spec(target, controller_jid=jid)
        note('已提交推理续接 controller=%s；%s' % (jid,target))
        return target


def cmd_retry(args):
    path = args.spec if os.path.isfile(args.spec) else os.path.join(args.spec, 'spec.json')
    with R.lifecycle_lock(os.path.dirname(path)):
        return _cmd_retry_locked(args)


def _cmd_retry_locked(args):
    from contextlib import ExitStack
    spec_path = args.spec if os.path.isfile(args.spec) else os.path.join(args.spec, "spec.json")
    spec = load_spec(spec_path)
    if spec.get("version", 1) < 2: die("旧版批次请从输入重新提交，不能推测其任务身份")
    pinned_config = os.path.join(os.path.dirname(spec_path), ".runtime", "config.json")
    saved_config = R.read_json(pinned_config, None)
    if not isinstance(saved_config, dict):
        raise R.BusinessError("Pinned configuration is missing or invalid: " + pinned_config)
    with ExitStack() as resources:
        pinned_cache = saved_config.get('HOST_CACHE', HOST_CACHE)
        resources.callback(setattr, R, 'ASSET_ROOT', R.ASSET_ROOT)
        resources.callback(globals().__setitem__, 'HOST_CACHE', HOST_CACHE)
        globals()['HOST_CACHE'] = pinned_cache
        R.ASSET_ROOT = os.path.join(pinned_cache, 'assets') if pinned_cache else ''
        runtime = os.path.join(os.path.dirname(spec_path), ".runtime", "af3.py")
        try:
            with open(runtime, encoding='utf-8') as stream:
                guarded_runtime = 'MSA_REUSE_REVIEW_JSON=' in stream.read()
        except OSError:
            guarded_runtime = False
        if not guarded_runtime:
            die('该旧任务快照不支持 MSA 完整性确认，不能用旧控制器重试；请从原输入创建新计划，原目录会保留。')
        review_args = argparse.Namespace()
        review_args.__dict__.update(spec.get('submit_args', {}))
        review_args.msa_dir = spec.get('msa_dir', HOST_MSA_DATA)
        review_args.dry_run = bool(getattr(args, 'dry_run', False))
        review_args.empty_msa_policy = getattr(args, 'empty_msa_policy', 'review')
        review_args.empty_msa_approved = dict(getattr(review_args, 'empty_msa_approved', {}) or {})
        incoming_approvals = getattr(args, 'empty_msa_approved', None)
        if isinstance(incoming_approvals, dict): review_args.empty_msa_approved.update(incoming_approvals)
        _begin_msa_review(review_args)
        entities, visited = [], set()
        def gather(value):
            if isinstance(value, list):
                for child in value: gather(child)
            elif isinstance(value, dict):
                if id(value) in visited: return
                visited.add(id(value))
                if value.get('_msa_key') and value.get('type') in ('protein', 'rna'):
                    entities.append(value)
                for child in value.values():
                    if isinstance(child, (dict, list)): gather(child)
        for key in ('jobs', 'pairs', 'msa_entities', 'raw_sides', 'fragments', 'pae_pending'):
            gather(spec.get(key))
        original_keys = [entity.get('_msa_key') for entity in entities]
        local_dir = batch_paths(spec.get('outdir') or os.path.dirname(spec_path))['msa_local'] if spec.get('shared_msa') else None
        _review_entities(entities, review_args, local_dir=local_dir)
        raw_path, raw_recompute, raw_repair = None, False, False
        if spec.get('type') == 'raw_json':
            if spec.get('infer_only'):
                raw_path = spec.get('raw_input')
            else:
                raw_path = msa_data_path(spec['name'], review_args.msa_dir)
            if raw_path:
                raw_ready = R.validate_msa(raw_path) if spec.get('infer_only') else R.msa_ready(review_args.msa_dir, spec['name'])
                raw_repair = bool(not raw_ready and (os.path.lexists(raw_path) if spec.get('infer_only') else
                                  _msa_output_occupied(review_args.msa_dir, spec['name'])))
                if raw_ready: raw_recompute = _review_data_path(raw_path, review_args) == 'recompute'
        _finish_msa_review(review_args)
        recompute = raw_recompute or raw_repair or original_keys != [entity.get('_msa_key') for entity in entities]
        if review_args.dry_run:
            note('[DRY RUN] 已检查固定计划的 MSA；不修改计划或提交任务。')
            return spec_path
        ensure_deployment(saved_config, require_msa=recompute or not spec.get("infer_only", False),
                          require_infer=not spec.get("msa_only", False))
        queue = R.queue_snapshot(force=True)
        if any(str(spec.get(k)) in queue for k in ("controller_jid", "watcher_jid")):
            die("该批次控制器仍在队列中")
        submission_args = {key: value for key, value in vars(review_args).items() if key != 'func' and not key.startswith('_')}
        if recompute:
            old_work = os.path.abspath(os.path.dirname(spec_path))
            child = old_work + '__recalc_' + R.digest([time.time_ns(), [entity.get('_msa_key') for entity in entities]], 12)
            target = os.path.join(child, 'spec.json')
            renewed = {key: value for key, value in spec.items() if key not in
                       ('storage_version', 'plan_file', 'detail_files', 'controller_jid', 'watcher_jid',
                        'counts', 'task_states', 'revision', 'created_at', 'updated_at', 'error',
                        'scheduler_error', 'retry_requested_at', 'fl_msa_keys')}
            output = os.path.join(child, 'results')
            renewed.update(config=saved_config, continuation_of=os.path.abspath(spec_path), status='preparing',
                           infer_only=False, output_dir=output, msa_jids=[], infer_jids=[], pae_infer_jids=[])
            if spec.get('type') in ('scan', 'pulldown'):
                renewed['outdir'] = child
            elif spec.get('batch_dir'):
                renewed['batch_dir'] = child
            submission_args.update(infer_only=False, output_dir=output, dry_run=False)
            renewed['submit_args'] = submission_args
            if spec.get('shared_msa'):
                # A user-requested recomputation must not immediately synthesize the
                # same empty fragment from its old parent. This fresh branch uses
                # independent fragment MSA; original shared products are preserved.
                renewed['shared_msa'] = False
                submission_args['shared_msa'] = False
                for entity in entities:
                    if (entity.get('_shared_source') or entity.get('_shared_parent_key')) and not R.msa_ready(review_args.msa_dir, entity['_msa_key'], entity):
                        _repair_entity(entity, review_args.msa_dir)
                        entity.pop('_shared_source', None)
                        entity.pop('_shared_parent_key', None)
            if isinstance(renewed.get('scan_args'), dict):
                renewed['scan_args'] = dict(renewed['scan_args'], empty_msa_policy=review_args.empty_msa_policy,
                                            empty_msa_approved=review_args.empty_msa_approved)
                if spec.get('shared_msa'):
                    renewed['scan_args']['shared_msa'] = False
            # The new controller may regenerate missing or explicitly reviewed MSA;
            # every old cache and its original plan remain available unchanged.
            renewed['msa_entities'] = list({entity['_msa_key']: entity for entity in entities}.values())
            if raw_recompute or raw_repair:
                recipe_path = spec['raw_input'] if raw_repair else raw_path
                data = copy.deepcopy(R.read_json(recipe_path))
                if not isinstance(data, dict):
                    die('原始 JSON 配方缺失或损坏，不能自动恢复；请提供原始输入创建新计划')
                if raw_recompute: _clear_empty_raw_fields(data, raw_path)
                def resolve_relative(value):
                    if isinstance(value, list): return [resolve_relative(child) for child in value]
                    if not isinstance(value, dict): return value
                    return {key: (os.path.abspath(os.path.join(os.path.dirname(recipe_path), child))
                                  if key.lower().endswith('path') and isinstance(child, str) and not os.path.isabs(child)
                                  else resolve_relative(child)) for key, child in value.items()}
                data = resolve_relative(data)
                name = sanitize_name(spec.get('label') or spec['name'], 64) + '__recalc_' + R.digest([child, data], 16)
                data['name'] = name
                data = R.externalize_input(data, os.path.join(HOST_CACHE, 'assets'))
                raw_input = os.path.join(batch_paths(child)['msa_input'], name + '_input.json')
                R.atomic_json(raw_input, data)
                renewed.update(name=name, raw_input=raw_input)
                submission_args['json'] = raw_input
            write_spec(renewed, target)
            if spec.get('type') in ('scan', 'pulldown'):
                jid = submit_watcher(target, renewed.get('tag') or renewed['name'], partition=_aux_partition_for(review_args))
                update_spec(target, watcher_jid=jid)
            else:
                jid = submit_controller(target, renewed['name'], [], partition=_aux_partition_for(review_args))
                update_spec(target, controller_jid=jid)
            note('已创建重新 MSA 的独立计划；原计划保持不变: ' + target)
            return target
        # Retry the pinned version/config, never silently use newly edited code/settings.
        cfg = os.path.join(os.path.dirname(runtime), "config.json")
        command = "_pulldown_watcher" if spec.get("type") in ("scan","pulldown") else "_stage_infer"
        cmd = "env AF3_SNAPSHOT=1 AF3_CONFIG=" + shlex.quote(cfg) + " " + shlex.quote(sys.executable) + " " + shlex.quote(runtime) + " " + command + " --spec " + shlex.quote(spec_path)
        log = os.path.join(os.path.dirname(spec_path), "logs", "retry_%j.out")
        updates = dict(retry_requested_at=time.time(), status="preparing", error=None, submit_args=submission_args)
        if isinstance(spec.get('scan_args'), dict):
            updates['scan_args'] = dict(spec['scan_args'], empty_msa_policy=review_args.empty_msa_policy,
                                        empty_msa_approved=review_args.empty_msa_approved)
        update_spec(spec_path, **updates)
        script = _aux_slurm_script("af3retry_" + R.digest(spec_path,12), WATCHER_TIME, log, cmd, partition=R.read_json(cfg,{}).get("AUX_PARTITION",AUX_PARTITION))
        jid = _submit_control(spec_path,script, os.path.join(os.path.dirname(spec_path), "scripts", "retry.sh"),
                              'watcher_jid' if command=='_pulldown_watcher' else 'controller_jid')
        update_spec(spec_path, **{("watcher_jid" if command == "_pulldown_watcher" else "controller_jid"):jid})
        note("已提交失败任务重试: " + jid)
        return spec_path


def _validate_args(args):
    settings = {}
    arguments = (("partition", "INF_PARTITION"), ("msa_partition", "MSA_PARTITION"),
                 ("aux_partition", "AUX_PARTITION"), ("fallback_partition", "INF_FALLBACK_PARTITION"),
                 ("output_dir", "HOST_OUTPUT"), ("msa_dir", "HOST_MSA_DATA"))
    for argument, key in arguments:
        value = getattr(args, argument, None)
        if value is not None:
            settings[key] = value
    settings = R.normalize_config(settings)
    for argument, key in arguments:
        if key in settings:
            setattr(args, argument, settings[key])
    if getattr(args,"seeds",None) is not None and getattr(args,"num_seeds",None) is not None:
        raise ValueError("--seeds 与 --num-seeds 只能指定一个")
    for key in ("num_seeds","max_concurrent"):
        val=getattr(args,key,None)
        if val is not None and val <= 0: raise ValueError(key + " 必须为正整数")
    if args.command == "scan" and args.mode == "win":
        fragment_windows(1,args.win,args.overlap,args.min_frag,args.split_threshold)


def cmd_install_gui(_args):
    command = R.install_gui_command(Path(__file__).parent)
    print('Installed: ' + str(command))
    print('With this environment active, run af3_gui from any directory.')
    print('After moving the application, rerun python -B af3.py install-gui from its new directory.')


def build_parser():
    p = argparse.ArgumentParser(
        description="af3.py - AlphaFold 3 统一提交脚本(MSA / Infer / Rank)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    p.add_argument("--version", action="version", version="AF3 Console " + R.VERSION)
    sub = p.add_subparsers(dest="command")

    installer = sub.add_parser('install-gui',
        help='Register the af3_gui command in the active Conda/venv environment',
        description='Run once on Linux after activating your environment. No AF3 resources or X11 required.')
    installer.set_defaults(func=cmd_install_gui)

    sp = sub.add_parser("run", help="预测:默认 MSA->infer 端到端(--msa-only/--infer-only 控制阶段)",
                        formatter_class=argparse.RawDescriptionHelpFormatter,
                        epilog=EPILOG_RUN)
    sp.add_argument("input", nargs="?", help="表达式或任务文件")
    sp.add_argument("--json", default=None, help="手写 AF3 JSON 直接提交")
    stage = sp.add_mutually_exclusive_group()
    stage.add_argument("--msa-only", "--msa", dest="msa_only",
                       action="store_true",
                       help="只做 MSA(按序列去重跳过)")
    stage.add_argument("--infer-only", "--infer", dest="infer_only",
                       action="store_true",
                       help="只做 Infer(要求 MSA 已就绪)")
    _add_common_submit_args(sp)
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("msa", help="[旧别名] 等价于 run --msa-only",
                        formatter_class=argparse.RawDescriptionHelpFormatter,
                        epilog=EPILOG_MSA)
    sp.add_argument("input", nargs="?", help="表达式或任务文件(.txt/.tsv/.csv)")
    sp.add_argument("--json", default=None, help="手写 AF3 JSON 直接提交")
    _add_common_submit_args(sp)
    sp.set_defaults(func=cmd_run, msa_only=False, infer_only=False)

    sp = sub.add_parser("infer", help="[旧别名] 等价于 run --infer-only",
                        formatter_class=argparse.RawDescriptionHelpFormatter,
                        epilog=EPILOG_INFER)
    sp.add_argument("input", nargs="?", help="表达式或任务文件")
    sp.add_argument("--json", default=None, help="手写 AF3 JSON 直接提交")
    _add_common_submit_args(sp)
    sp.set_defaults(func=cmd_run, msa_only=False, infer_only=False)

    sp = sub.add_parser("pulldown", help="A x B 全组合筛选 + 排序 + ipTM 矩阵(每行一个侧,支持 + 复合物)",
                        formatter_class=argparse.RawDescriptionHelpFormatter,
                        epilog=EPILOG_PULLDOWN)
    sp.add_argument("a", help="蛋白/复合物或列表文件(.txt/.tsv/.csv,每行一个侧)")
    sp.add_argument("b", help="蛋白/复合物或列表文件")
    sp.add_argument("--topk", type=int, default=None,
                    help="只标记 ipTM 前 K 对的排名，目录保持稳定")
    sp.add_argument("--self", action="store_true",
                    help="追加 A x A 与 B x B 组内组合(含自配对)")
    _add_common_submit_args(sp)
    sp.set_defaults(func=cmd_pulldown)

    sp = sub.add_parser("scan", help="长蛋白切片段 A x B 筛选,定位互作位点(每行一个侧,支持 + 复合物)",
                        formatter_class=argparse.RawDescriptionHelpFormatter,
                        epilog=EPILOG_SCAN)
    sp.add_argument("a", help="蛋白/复合物或列表文件(每行一个侧)")
    sp.add_argument("b", help="蛋白/复合物或列表文件")
    sp.add_argument("--win", type=int, default=300, help="滑窗长度(默认 300)")
    sp.add_argument("--overlap", type=int, default=100, help="重叠长度(默认 100)")
    sp.add_argument("--min-frag", type=int, default=100,
                    help="末段短于此长度时并入前段(默认 100)")
    sp.add_argument("--split-threshold", type=int, default=500,
                    help="长度超过此值才切窗(默认 500)")
    sp.add_argument("--mode", choices=["win", "pae"], default="win",
                    help="切分模式:win=固定滑窗(默认);pae=按 PAE domain 切"
                         "(需全长单体预测,缺则自动两阶段补算)")
    sp.add_argument("--pae-cutoff", type=float, default=5.0,
                    help="pae 模式:PAE 连边阈值(默认 5,同 ChimeraX)")
    sp.add_argument("--pae-resolution", type=float, default=0.5,
                    help="pae 模式:聚类分辨率,越小 domain 越大(默认 0.5)")
    sp.add_argument("--pae-min-domain", type=int, default=10,
                    help="pae 模式:最小 domain 残基数(默认 10)")
    sp.add_argument("--pae-domains-per-window", type=int, default=1,
                    help="pae 模式:每个 window 含几个相邻 domain(默认 1;"
                         "设 2 即 D1+D2/D2+D3,相邻 overlap 1 个 domain)")
    sp.add_argument("--pae-min-frag", type=int, default=250,
                    help="pae 模式:window 小于此长度时与较短邻居合并(默认 250)")
    sp.add_argument("--pae-max-frag", type=int, default=None,
                    help="pae 模式:domain span 超此值则提高 resolution 递归"
                         "再切分(默认取 --split-threshold 的值)")
    sp.add_argument("--shared-msa", action="store_true",
                    help="只对全长做 MSA,片段从全长 A3M 切列(省机时,近似;"
                         "此时 infer 为 template-free)")
    sp.add_argument("--topk", type=int, default=None)
    _add_common_submit_args(sp)
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("pae", help="单蛋白 PAE domain 分段查看(不提交 scan;"
                        "缺 confidences 时自动提交单体 MSA+infer 入 infer_data 池)",
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    sp.add_argument("input", help="单个蛋白表达式(如 P12345 或 "
                                  "P12345:pae=/path/to/your/confidences.json:A)")
    sp.add_argument("--pae-cutoff", type=float, default=5.0,
                    help="PAE 连边阈值(默认 5,同 ChimeraX)")
    sp.add_argument("--pae-resolution", type=float, default=0.5,
                    help="聚类分辨率,越小 domain 越大(默认 0.5)")
    sp.add_argument("--pae-min-domain", type=int, default=10,
                    help="最小 domain 残基数(默认 10)")
    sp.add_argument("--pae-domains-per-window", type=int, default=1,
                    help="每个 window 含几个相邻 domain(默认 1)")
    sp.add_argument("--pae-min-frag", type=int, default=250,
                    help="window 小于此长度时与较短邻居合并(默认 250)")
    sp.add_argument("--pae-max-frag", type=int, default=None,
                    help="domain span 超此值则提高 resolution 递归再切分"
                         "(默认 500;scan 模式默认取 --split-threshold 的值)")
    _add_common_submit_args(sp)
    sp.set_defaults(func=cmd_pae_domains)

    sp = sub.add_parser("rank", help="按 ipTM 排序(收编 batch_rank.py)",
                        formatter_class=argparse.RawDescriptionHelpFormatter,
                        epilog=EPILOG_RANK)
    sp.add_argument("inputs", nargs="+", help="清单文件(.txt)或结果目录")
    sp.add_argument("-o", "--output", default=None,
                    help="输出名(写到 output/batch_rank_<名>/)或完整路径")
    sp.add_argument("--source-dir", default=HOST_OUTPUT,
                    help=f"清单标识符的结果搜索目录(默认 {HOST_OUTPUT})")
    sp.add_argument("--output-dir", default=HOST_OUTPUT, help=argparse.SUPPRESS)
    sp.add_argument("--copy", action="store_true", help="显式复制完整结果；默认仅写排名索引")
    sp.add_argument("--delete-originals", action="store_true",
                    help="拷贝完成后删除原目录(默认保留)")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_rank)

    sp = sub.add_parser("status", help="进度 + 失败诊断",
                        formatter_class=argparse.RawDescriptionHelpFormatter,
                        epilog=EPILOG_STATUS)
    sp.add_argument("--jobs", default=None, help="任务文件或 spec.json")
    sp.add_argument("--json", action="store_true", help="机器可读输出")
    sp.add_argument("--watch", type=int, default=None, help="每 N 秒刷新")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("profile", help="为已完成的 scan 批次重新生成全套报告(profile/hits/矩阵/report)",
                        formatter_class=argparse.RawDescriptionHelpFormatter,
                        epilog=EPILOG_PROFILE)
    sp.add_argument("dir", help="scan 批次目录或批次名(如 scan_HW)")
    sp.set_defaults(func=cmd_profile)

    sp = sub.add_parser("refresh", help="按最新脚本重新汇总批次结果(ranking/矩阵/报告),"
                        "不重跑 msa/infer(UI 的 Dashboard Update 按钮调它)",
                        formatter_class=argparse.RawDescriptionHelpFormatter,
                        epilog="""例子:
  af3.py refresh output/pulldown_HW
  af3.py refresh scan_HW            # 同 profile 的批次名写法
说明:只读现有结果目录重汇总;_collect_screen_rows 兼容已 rank 的目录,可反复执行。
""")
    sp.add_argument("dir", help="pulldown/scan 批次目录或批次名")
    sp.set_defaults(func=cmd_refresh)

    # 隐藏命令
    sp = sub.add_parser("_stage_infer", help=argparse.SUPPRESS)
    sp.add_argument("--spec", required=True)
    sp.set_defaults(func=cmd_stage_infer)

    sp = sub.add_parser("_pulldown_watcher", help=argparse.SUPPRESS)
    sp.add_argument("--spec", required=True)
    sp.set_defaults(func=cmd_pulldown_watcher)

    retry = sub.add_parser("retry", help="从固定计划重试失败/取消/依赖缺失的任务")
    retry.add_argument("--spec", required=True)
    retry.add_argument('--dry-run', action='store_true')
    retry.add_argument('--empty-msa-policy', choices=('review', 'reuse', 'recompute'), default='review')
    retry.set_defaults(func=cmd_retry)
    continuation = sub.add_parser('continue', help='Continue a completed MSA-only plan with its fixed seeds')
    continuation.add_argument('--spec', required=True)
    continuation.add_argument('--dry-run', action='store_true')
    continuation.add_argument('--empty-msa-policy', choices=('review', 'reuse', 'recompute'), default='review')
    continuation.set_defaults(func=cmd_continue)
    return p


def main():
    global INF_FALLBACK_PARTITION, _MSA_FREE_POLICY
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    if args.command in ("msa", "infer", "run"):
        if not getattr(args, "input", None) and not getattr(args, "json", None):
            parser.error(f"af3.py {args.command} 需要 <表达式|任务文件> 或 --json F")
    if args.command == "status" and args.watch:
        try:
            while True:
                cmd_status(args)
                time.sleep(args.watch)
                print("=" * 60)
        except KeyboardInterrupt:
            return
    try:
        _validate_args(args)
        if getattr(args, "fallback_partition", None) is not None:
            INF_FALLBACK_PARTITION = args.fallback_partition
        _MSA_FREE_POLICY = bool(getattr(args, "msa_free", False))
        args.func(args)
    except (Exception, SystemExit) as exc:
        if getattr(args, "spec", None) and str(args.command).startswith("_"):
            try: update_spec(args.spec, status="failed", error=str(exc))
            except Exception: pass
        if isinstance(exc, SystemExit): raise
        print('ERROR: ' + str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
