# Configuration / 配置说明

The program and the data have separate locations. Keep the installation and its Python environment accessible from compute nodes; place personal work and caches under a writable work directory. No real site configuration is included in releases.

## Locations

| Key | Meaning / 含义 | Example |
|---|---|---|
| `HOST_BASE` | Personal work directory / 个人工作目录 | `~/AF3` |
| `HOST_SIF` | Container **file** / 容器文件 | `/path/to/your/alphafold3.sif` |
| `HOST_MODELS` | Parameter directory / 模型参数目录 | `/path/to/your/model_parameters` |
| `HOST_DB_SOURCE` | Database directory / 数据库目录 | `/path/to/your/public_databases` |
| `HOST_OUTPUT` | Per-batch results / 批次结果 | Derived from `HOST_BASE/output` |
| `HOST_MSA_DATA` | Reusable MSA pool / MSA 池 | Derived from `HOST_BASE/msa_data` |
| `MSA_BACKUP_DIRS` | Up to two independent backup pools / 最多两个独立备份池 | `[]` by default; list of directory paths |
| `HOST_INFER_DATA` | Full-length inference pool / 全长推理池 | Derived from `HOST_BASE/infer_data` |
| `HOST_CACHE` | General cache / 一般缓存 | Derived from `HOST_BASE/cache` |
| `HOST_JAX_CACHE` | Compilation cache / 编译缓存 | Derived from `HOST_BASE/af3_buckets_cache` |
| `HOST_SSD_CACHE` | Optional node-local database cache / 可选节点 SSD | Empty disables this feature |

Empty required paths are not treated as the current directory. Nonempty `HOST_*` paths and CLI `--output-dir` / `--msa-dir` overrides expand `~` and become absolute; resolve relative paths from the process working directory. Prefer absolute paths to avoid changes when launching from a different folder. Newlines and NUL are invalid. `/path/to/your/...` values are documentation placeholders and must be replaced before submission.

On Linux, these host paths cannot contain `:` or `,` because those characters delimit Singularity bind specifications. This rule does not describe all file references inside input expressions. Spaces, single quotes, and dollar signs are quoted when constructing shell commands. Do not add shell quotes inside a JSON path value; store the actual path.

空的必填路径不应变成当前目录；相对路径以启动目录为基准。推荐使用绝对路径。内部容器挂载点是应用接口，不是私人路径，不需要按文档占位规则修改。

`MSA_BACKUP_DIRS` entries use the same path normalization as `HOST_MSA_DATA`. The complete set must have at most three distinct, non-nested directories. Backups must already exist and be readable/writable before an MSA submission; inference-only work does not require backup write access. Setup's resource check includes all configured backups. See [MSA pool behavior](msa-pools.md) for startup consent, add-only synchronization, backup receipts and reuse decisions.

## Precedence and persistence

From lower to higher priority:

1. Built-in defaults.
2. `site_config.json` beside the application (optional; ignored by Git).
3. User JSON selected by `AF3_CONFIG`, or `~/.config/af3_console/config.json` by default.
4. `AF3_BASE` overrides the work directory for ordinary application processes.

Derived directories follow the resolved base unless explicitly configured. An explicitly configured shared model/cache location remains fixed when the base changes. `AUX_PARTITION` follows the resolved CPU partition unless explicitly set.

Queued jobs use a complete configuration snapshot and `AF3_SNAPSHOT=1`, so a later change to your interactive `AF3_BASE` does not redirect an existing job. Internal snapshot variables are not installation settings. Setup writes user JSON atomically; it never edits installed source code. If `AF3_BASE` is exported, it continues to override a saved work directory until unset.

优先级：内置默认值 → 程序旁站点配置 → 用户配置 → 普通进程的 `AF3_BASE`。任务快照固定原配置，不随之后的环境变量改变。

```bash
export AF3_CONFIG=/path/to/your/private/config.json
export AF3_BASE=/path/to/your/work_directory
python -B af3_gui
```

There is no `.env` loader. `AF3_PAE_CACHE` optionally changes the local PAE numerical cache. GUI interpreter/script overrides, when used, must point to a matching installation. Do not put secrets in configuration or commit your environment export.

## Slurm and container settings

- `MSA_PARTITION` and `INF_PARTITION`: required actual CPU/GPU partition names.
- `AUX_PARTITION`: optional controller/watcher partition; otherwise follows CPU.
- `INF_FALLBACK_PARTITION`: empty disables automatic fallback. Large jobs stay on the primary configured GPU partition; memory success is not guaranteed.
- `CONTAINER_RUNTIME`: default `singularity`, or the executable path supplied by your administrator. The command is treated as one executable, not an arbitrary shell snippet.
- `CONTAINER_MODULE`: optional module name, such as the actual site-specific Singularity module. Empty means use the current PATH. A job checks module availability, loads it if configured, then checks the runtime. A module-only installation is not rejected solely because the executable is absent from the login node's initial PATH.
- `HOST_SSD_CACHE`: empty prevents node-local database copying; only enable it with a writable, appropriate cache layout agreed with your administrator.

CPU counts, concurrency, buckets, and token thresholds retain the existing configurable `MSA_*` / `INF_*` settings. They are policy defaults, not hardware guarantees. The initial release targets the existing Singularity command interface and standard Slurm directives; sites requiring additional account/QoS wrappers or other runtimes need separate validation.

Setup and CLI submission share deployment checks. They check the resources needed by the selected stage, permissions on existing files or writable parents of new output directories, and required Slurm tools. Offline help/PAE analysis and planning are not blocked by an incomplete cluster deployment. The actual compute-node mount and GPU environment still need a real test job.

## Data and network behavior

UniProt expressions can query UniProt to resolve sequences and display names. A preview may therefore make network requests, even though it does not submit Slurm jobs. Reports, task snapshots, JSON inputs, and caches can contain research information; keep them outside the repository and sanitize any diagnostic material before sharing.
