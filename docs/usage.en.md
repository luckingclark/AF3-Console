# AF3 Console usage

[中文指南](usage.zh-CN.md) · [Home](../README.md) · [Input formats](#input-formats)

For **0.1.0, deployment edition 2026-09-24**. Install the environment from the repository root, then enter `AF3_Console/` to configure and run. Download the complete current main branch; do not mix it with the older September 12 five-file package.

## Before installing

You need access to a **Linux Slurm cluster**, an AlphaFold 3 **Singularity container**, its separately obtained **model parameters and databases**, and a Python environment visible to the compute nodes. The GUI uses **X11 forwarding**. AF3 Console supplies the UI and orchestration scripts; it does not include the prediction engine, GPU drivers, parameters, databases, or a Python environment.

Desktop preview and synthetic tests can run without those cluster resources. Real prediction jobs require a working cluster deployment. This is an early release: the exact checks performed and untested deployment boundaries are listed in [validation](https://github.com/luckingclark/AF3-Console/blob/03c29106fd23b20928c9591b35b22b2508c9adfd/docs/validation.md).

## Download and install

Keep the branch selector on **main** and choose **Code → Download ZIP**. Unpack the complete directory into a location accessible to login and compute nodes. The default download is the deployment edition; the source branch is optional for developers. No formal GitHub Release has been created.

From the unpacked directory, create an isolated environment:

```bash
conda env create -f environment.yml
conda activate af3-console
cd AF3_Console
python -B af3.py --version
```

Alternatively, on a supported system with Python 3.12:

```bash
python -m venv /path/to/your/af3-console-env
source /path/to/your/af3-console-env/bin/activate
python -m pip install "PySide6>=6.6,<7" "numpy>=1.26,<3" "pandas>=2.2,<4" "matplotlib>=3.8,<4" "six>=1.16,<2" "python-dateutil>=2.9,<3" "zstandard>=0.23,<1" "qtawesome>=1.3,<2"
cd /path/to/your/af3-console/AF3_Console
```

Conda is the recommended route for cluster installations. Dependency ranges are declared, rather than exporting a developer's environment or private package-channel URLs. Older Linux/glibc installations may require a separately validated environment; no blanket compatibility claim is made.

## Configure your resources

Either fill out **Setup** in the GUI or copy the example to your user configuration:

```bash
mkdir -p ~/.config/af3_console
cp config.example.json ~/.config/af3_console/config.json
```

Replace every `your_...` and `/path/to/your/...` placeholder. This template is intentionally not a runnable cluster configuration. The required locations are the container file, model parameter directory, and database directory; enter your actual CPU and GPU partition names. The optional SSD cache and fallback GPU partition default to disabled.

`~/AF3` is the default personal work directory, independent of the software location. Setup saves user JSON, not Python source. See [configuration](#configuration) for precedence, environment overrides, shared paths, and module loading. Never commit your live configuration.

**MSA pools and backups:** use **＋** beside the MSA path to configure 1–3 independent directories. Completed new products are copied to the backups; a startup check asks before synchronizing existing additions between pools. Conflicting products remain untouched, with no overwrite or deletion. Before reuse, incomplete data is rejected and valid empty `unpairedMsa`, `pairedMsa` or `templates` prompts a reuse/recompute/cancel decision. See [MSA backup, synchronization and empty-field handling](#msa-pools).

### Hardware reference for Resource limits

These are the development environment's hardware specifications, supplied by the project author on **2026-09-14**, and its existing configuration. They do not constitute a new cluster validation of the public release. The CPU model was not supplied; only scheduler-reported topology and memory are listed. Account, node, partition and deployment-path identifiers are omitted.

| Resource | Node specification | Existing configuration |
|---|---|---|
| CPU node | 2 sockets × 24 cores × 1 thread, 48 CPUs; Slurm `RealMemory=510000 MB` | Small MSA batches (< 6 tasks): `MSA_NTASKS_SINGLE=16`; batches of ≥ 6: `MSA_NTASKS_BATCH=8`. |
| A40 node | 4 × NVIDIA A40 per node; **46,068 MiB** total memory per card reported by `nvidia-smi` | Configure its partition as the primary GPU partition; route total tokens **≤ 3,584** here. |
| A100 node | 8 × NVIDIA A100 80GB PCIe per node; **81,920 MiB** total memory per card | Configure its partition as the fallback GPU partition; route total tokens **> 3,584 and ≤ 7,168** here. |

This describes automatic partition selection. A manual partition override takes precedence; an empty fallback disables automatic routing to A100. The application submits to partitions without detecting GPU models, so verify allocation if a partition contains mixed hardware. Each inference job uses **1 GPU and 12 CPUs** (`INF_GPUS=1`, `INF_NTASKS=12`); the application does not combine the memory of a node's 4 or 8 cards for one task.

| Total tokens | Existing inference policy |
|---|---|
| ≤ 3,584 | A40, regular device-memory settings. |
| 3,585–5,120 | A100 80GB, regular device-memory settings. |
| 5,121–7,168 | A100 80GB with unified memory; sufficient GPU-node host RAM and job memory allowance are needed. |
| > 7,168 | Reject before submission under the configured protective limit. |

The settings are `INF_BIG_TOKEN=3584`, `INF_UM_TOKEN=5120`, `INF_MAX_TOKEN=7168`, with compilation buckets ending at 7,168. Count all entities and copies in a task: for standard proteins this is approximately the sum of chain residue counts; mixed entities require actual tokenization. Preview estimates and bucket padding also affect capacity assessment.

**OOM evidence:** historical development-code comments report an **A100 80GB compilation OOM in the 7,680-token bucket with unified memory enabled**; the existing configuration therefore stops at 7,168. The original log was not available for this review and the test was not rerun. This does not establish that every input above 7,168 fails or every input below it fits. Likewise, 3,584 is the A40-to-A100 routing threshold, not a measured physical A40 limit. See the [AF3 v3.0.2 GPU-memory documentation](https://github.com/google-deepmind/alphafold3/blob/v3.0.2/docs/performance.md#gpu-memory) for the regular 5,120-token settings and unified-memory mechanism. Actual AF3/JAX/CUDA versions, buckets and GPU-node host memory matter; the CPU node's RAM does not establish the GPU nodes' RAM.

**MSA threads and concurrency:** the current script uses 16/8 both as the Slurm CPU request and the per-process Jackhmmer/Nhmmer thread setting. Protein MSA searches can run against four databases concurrently, so 16 threads per search is not a 16-thread total job limit; this configuration can oversubscribe CPUs. These values document the development setup, not an optimum for a 48-core node. Strictly matching total CPU allocation to per-search threads requires separating those settings and changing the script. See the [AF3 data-pipeline documentation](https://github.com/google-deepmind/alphafold3/blob/v3.0.2/docs/performance.md#data-pipeline).

The existing `MSA_MAX_CONCURRENT=12` and `INF_MAX_CONCURRENT=16` are batch scheduling caps, not per-node thread/card counts or promises of free resources; Slurm queues work when resources are unavailable. On another cluster, start validation with one small job at a time and adjust for CPU, RAM, GPUs and quotas. MSA threads, the unified-memory threshold and buckets can be set in user JSON even when absent from the Resource limits panel. Keep the public `config.example.json` as a placeholder template and store actual lab paths and partitions outside the repository.

## Launch the GUI

Connect with X11 forwarding enabled in your SSH client, for example:

```bash
ssh -X your_username@your_login_host
conda activate af3-console
cd /path/to/your/af3-console/AF3_Console
python -B af3_gui
```

MobaXterm users can enable its X server and X11 forwarding. In Setup, choose **Check resources**, correct the reported issues, and save. No X11 display is required for CLI help.

## First preview and run

The identifiers below are **syntax placeholders**, not a biological example. Substitute your own identifiers before using the prediction commands. Even `--dry-run` may resolve sequences over the network.

```bash
python -B af3.py run 'P12345+Q12345' --seeds 11 --dry-run
```

The command shows the planning interface without scheduling jobs. After configuring resources and replacing the identifiers, remove `--dry-run` to submit. In the GUI, use Submit → Preview, review the plan, and then submit.

Further syntax examples:

```bash
python -B af3.py run 'P12345' --msa-only
python -B af3.py pulldown 'P12345' 'Q12345' --seeds 11
python -B af3.py scan 'P12345' 'Q12345' --mode win --win 300 --overlap 100
python -B af3.py scan 'P12345' 'Q12345' --mode pae
python -B af3.py status --jobs /path/to/your/batch/spec.json --json
python -B af3.py continue --spec /path/to/your/batch/spec.json
python -B af3.py retry --spec /path/to/your/batch/spec.json
```

PAE-guided scans can first schedule full-length monomer predictions when their required PAE data is missing. Check the plan and resource limits before submitting a large screen. [Input formats](#input-formats) provide more details.

## Results and upgrades

By default, each batch lives under `~/AF3/output/`; MSA and full-length inference pools live under `~/AF3/msa_data/` and `~/AF3/infer_data/`. Results remain on the cluster. Use the displayed folder paths with your SFTP client to retrieve them. Closing the GUI does not cancel submitted Slurm jobs.

New ordinary UniProt MSA jobs use each protein's ID, independently of the user-supplied run job name or pulldown/scan batch name, and retain AF3's native layout (the ID is a syntax placeholder):

```text
msa_data/
└── P12345/
    └── P12345_data.json
```

The application no longer moves JSON files into the pool root or deletes AF3 output directories and companion files. Different sequences, external MSAs or recorded environments for one ID, or labels that require shortening, use names such as `P12345__<hash>/P12345__<hash>_data.json` to prevent accidental reuse. Truncations and mutations also appear in names; anonymous sequences retain content-based names. Old flat JSON and hash products are left in place. Exact old hash identities remain reusable. Ordinary inputs may also reuse untracked ID products after schema and sequence validation, but their historical container/database environment cannot be determined from filenames. External MSA and MSA-free inputs remain separate.

Pulldown prepares a separate MSA for each ordinary UniProt input. Scan with shared full-length MSA also names each original full-length MSA by ID; derived fragment data includes its residue range and other identity information. Without shared full-length MSA, independently computed fragment MSAs need range identifiers too, rather than all using the same full-length ID.

Timestamped AF3 directories for a single UniProt ID are also supported: only the directory adds a timestamp; the JSON retains the ID:

```text
msa_data/
└── P12345_20260602_140829/
    └── P12345_data.json
```

For untracked results, valid timestamped outputs are tried newest first by directory timestamp, then the plain native directory and old flat file. Automatic lookup by ID expects the directory's ID prefix to match the JSON's ID. Select a particular directory or JSON explicitly when several runs exist. An existing MSA JSON can be used for inference directly:

```bash
python -B af3.py run --infer-only --json /path/to/your/msa_data/P12345_20260602_140829/P12345_data.json
```

Historical outputs can also use custom job names. Text such as `_and_` in a name does not determine the number of inputs; the JSON contents do. Select the JSON explicitly for a custom name or a directory prefix that differs from the filename. Keep or copy the complete output directory together with companion files and relative references.

The pool's `.msa_records/` stores identity and completion state; `.locks/` coordinates writes. New jobs become ready only after successful exit and output validation. Failed files remain for diagnosis but are not completed products. Preserve these records when copying a pool; deleting them is not a way to force reuse. Existing task snapshots keep their original storage behavior; the new layout applies to tasks created with this version.

Install a new release in a new directory and keep your user configuration. Do not move or delete an installation, Python environment, or task snapshot that queued/running jobs still reference. Retry and continue use task-specific state; do not manually edit its fixed plan. Shared cache changes and a newer application version can require recomputation.

## Troubleshooting

| Symptom | Check |
|---|---|
| No GUI / empty `DISPLAY` | Enable X11 forwarding and the local X server. |
| Qt xcb or platform plugin error | Check the Linux libraries required by your installed Qt build; changing Python code will not install system libraries. |
| Resource check fails | Replace template paths and partitions; check file permissions and compute-node visibility. |
| Container is available only via a module | Set `CONTAINER_MODULE`; the job loads it before checking the runtime. |
| Setup changes appear overridden | Inspect `AF3_CONFIG` and `AF3_BASE` in the launch environment. |
| OOM or rejected token count | Inspect GPU capacity and configured thresholds; there is no universal safe maximum. |
| A result directory exists but the task failed | Read the exit receipt and logs; a directory alone does not prove success. |

Before opening an issue, remove research targets, sequences, unpublished results, hosts, usernames, private paths, and credentials from logs/screenshots.


## Input formats

Prepare your own input; no research or downloadable synthetic dataset is supplied. `P12345` and `Q12345` are syntax placeholders, not biological examples. Replace them before a real run. Even `--dry-run` may resolve sequences over the network.

| Input | Contents and purpose |
|---|---|
| UniProt expression | An identifier such as `P12345`, or a complex such as `P12345+Q12345`; Run predicts the specified input. |
| Pulldown groups | Two groups of identifiers/expressions; their combinations form the screen. See UI Help for list syntax. |
| Scan inputs | Long protein and candidate partner inputs; fixed-window scans use window length and overlap, PAE scans use monomer confidence data. |
| Native AF3 input JSON | AF3 input schema containing entities and sequences; select explicitly in the GUI or with `--json /path/to/your/input.json`. |
| Existing MSA product | AF3 `*_data.json` plus its complete directory and referenced companions. Preserve native or timestamped directories; schema, sequence and required fields are checked before reuse. Valid empty MSA/template fields require a reuse/recompute decision. |
| Unpaired MSA | `P12345:msa=/path/to/your/alignment.a3m`, compatible with the selected sequence. |
| Monomer PAE | `P12345:pae=/path/to/your/confidences.json`, an AF3 confidence JSON containing the PAE matrix for that sequence. |
| Template | `P12345:tpl=/path/to/your/template.cif:0,1:0,1`, a template mmCIF with zero-based query/template residue mappings. |

Container (`/path/to/your/alphafold3.sif`), model-parameter directory (`/path/to/your/model_parameters`), database directory (`/path/to/your/databases`), and work directory (`/path/to/your/workspace`) are deployment resources, not interchangeable input files. Configure them as described in [configuration](#configuration). Keep local paths and real sequences out of issues and version control.

## Configuration

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

`MSA_BACKUP_DIRS` entries use the same path normalization as `HOST_MSA_DATA`. The complete set must have at most three distinct, non-nested directories. Backups must already exist and be readable/writable before an MSA submission; inference-only work does not require backup write access. Setup's resource check includes all configured backups. See [MSA pool behavior](#msa-pools) for startup consent, add-only synchronization, backup receipts and reuse decisions.

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

## MSA pools

In **Setup → MSA cache directory**, use **＋** to the left of the primary path to add up to two backup rows. Removing a row changes configuration only. Save the settings before checking synchronization. `HOST_MSA_DATA` is the primary pool; `MSA_BACKUP_DIRS` is a list of zero to two backups. Use distinct, non-nested absolute directories. Create backups explicitly and make them accessible from login and compute nodes. Coordinate shared access with the directory owners; rsync is not required.

New AF3 output is first completed and validated in the primary pool, then copied as a whole product to each configured backup. Native AF3 and timestamped directories retain their filenames and companion files. Already queued jobs keep their original configuration snapshot. Backup failures appear in job logs and `.msa_backup_receipts/<task-name>.json`; they do not invalidate a successful primary MSA. Repair the problem and use the manual synchronization check to retry.

Once per GUI launch, a background scan checks the saved pools for additions. Copying starts only after confirmation, with No as the default. A manual check is also available. Content hashing can take time on large or remote pools. Identical products are skipped; different same-name products remain conflicts. Existing directories are neither overwritten nor merged. Sources are checked for changes and destinations are published without replacing existing data. Missing fields, incomplete managed jobs, unstable sources, links/junctions and references outside a product are reported rather than copied. Flat legacy JSON is supported when self-contained.

Synchronization never mirrors deletions. GUI cleanup protects current and task-pinned MSA pools, their contents and enclosing directories. Only staging artifacts owned by the current failed copy may be cleaned up. Multiple copies on the same storage do not provide protection against complete device failure; use your storage service's backups when needed.

Before reuse, invalid or incomplete MSA data is rejected. Valid data with empty protein `unpairedMsa`, `pairedMsa` or `templates` triggers a **Reuse / Recompute MSA / Cancel** choice, identifying the file and empty fields; Cancel is the default. RNA checks only its applicable `unpairedMsa`. Recompute keeps the original product and uses a distinct new product; it cannot guarantee nonempty search hits. Approval applies to the checked file version. Later changes or newly generated empty products require a new decision; remote controllers stop affected inference until review. Preview does not authorize submission. CLI users explicitly choose `--empty-msa-policy reuse` or `--empty-msa-policy recompute`.

Damaged existing products also remain intact: repair planning uses a new directory while preserving the original custom input recipe. A remote controller cannot overwrite an occupied incomplete directory; it stops and asks for replanning. Invalid raw inference-only JSON must first be corrected or processed through an MSA-generating workflow.

MSA commands leave AF3's force-overwrite option disabled. If an external program creates the same directory after our check, AF3 may write a timestamped directory; all outputs are retained and the managed task can fail its expected-output check. Inspect its logs and replan. Coordinate concurrent work because unrelated scripts do not necessarily respect this application's locks.

Valid empty products may be backed up; synchronization consent does not grant inference reuse consent. Validation supports inline and external references, including gzip/xz/zstd alignments, and checks query sequences and template index consistency. It does not replace AF3's full structural parsing or establish biological quality.

Readable GUI source, tests and build tools are in the [source branch](https://github.com/luckingclark/AF3-Console/tree/source). Third-party modules in the deployment directory remain editable and replaceable; retain their licenses.
