# AF3 Console 使用指南

[English](usage.en.md) · [首页](README.zh-CN.md) · [输入规范](#输入规范)

对应 **0.1.0，2026-09-24 部署版**。从仓库根目录安装环境，再进入 `AF3_Console/` 启动和配置。请整套下载当前 main，勿与 9 月 12 日旧五文件包混用。

## 安装前需要什么

实际预测需要已有的 **Linux Slurm 集群、AF3 Singularity 容器、模型参数、数据库以及 GPU 资源**。程序、Python 环境和工作目录必须能被计算节点访问。桌面窗口通过 X11 转发显示。

这个仓库提供 UI 和调度脚本，不包含 AF3 引擎、模型参数、数据库、GPU 驱动或完整 Python 环境。打开界面、阅读帮助不需要配置完整集群；正式提交前会检查资源。不同集群和旧版 Linux 的兼容性须分别验证，详见[验收记录](https://github.com/luckingclark/AF3-Console/blob/03c29106fd23b20928c9591b35b22b2508c9adfd/docs/validation.md)。

## 下载和安装

保持分支为 **main**，点击 **Code → Download ZIP**，将解压后的整个目录上传至登录节点和计算节点都能访问的位置。默认下载已是部署版，不需要 source 分支。尚未创建正式 Release；仍需安装 Python 依赖。

在解压后的仓库根目录中执行：

```bash
conda env create -f environment.yml
conda activate af3-console
cd AF3_Console
python -B af3.py --version
```

也可以使用系统支持的 Python 3.12：

```bash
python -m venv /path/to/your/af3-console-env
source /path/to/your/af3-console-env/bin/activate
python -m pip install "PySide6>=6.6,<7" "numpy>=1.26,<3" "pandas>=2.2,<4" "matplotlib>=3.8,<4" "six>=1.16,<2" "python-dateutil>=2.9,<3" "zstandard>=0.23,<1" "qtawesome>=1.3,<2"
cd /path/to/your/af3-console/AF3_Console
```

集群优先采用 Conda。仓库声明依赖范围，不分发开发者完整环境或私人软件源地址；实际验证的依赖版本记录在验收文档中。

## 配置资源

可以直接在 GUI 设置页填写，也可复制模板：

```bash
mkdir -p ~/.config/af3_console
cp config.example.json ~/.config/af3_console/config.json
```

随后编辑用户配置，将所有 `your_...` 和 `/path/to/your/...` 替换成自己的资源。模板中的占位值不能直接运行。

| 配置 | 应填写什么 |
|---|---|
| `HOST_SIF` | AF3 的 `.sif` 容器文件 |
| `HOST_MODELS` | 模型参数所在目录 |
| `HOST_DB_SOURCE` | AF3 数据库根目录 |
| `HOST_BASE` | 个人工作目录，默认 `~/AF3` |
| `MSA_PARTITION` / `INF_PARTITION` | 实际 CPU / GPU Slurm 分区 |
| `CONTAINER_MODULE` | 可选的环境模块名；运行命令已在 PATH 中时留空 |

SSD 缓存和备用 GPU 分区默认关闭。设置保存到用户 JSON，不修改源码；个人配置不可提交到 GitHub。更多路径、环境变量和优先级见[配置说明](#configuration)。

**MSA 池与备份：** 点击 MSA 缓存路径左侧的 **＋**，可配置 1–3 个独立目录。新产物完成后复制到各备份池；每次启动检查一次各池新增内容，询问后才互相同步。同名冲突保留双方数据，不覆盖、不删除。复用前校验完整性；合法但 `unpairedMsa`、`pairedMsa` 或 `templates` 为空时，提示选择复用、重新计算或取消。详见 [MSA 备份、同步与空字段处理](#msa-pools)。

### “资源上限”的硬件配置参考

以下是开发环境的硬件与既有配置参考，硬件信息由项目作者于 **2026-09-14** 提供，不代表公开版本已在这些节点上重新验收。CPU 型号未提供，仅列调度器报告的拓扑与内存；不公开账号、节点名、分区名或部署路径。

| 资源 | 节点规格 | 既有配置的用法 |
|---|---|---|
| CPU 节点 | 2 插槽 × 24 核，每核 1 线程，共 48 CPU；Slurm `RealMemory=510000 MB` | 小批 MSA（任务数 < 6）：`MSA_NTASKS_SINGLE=16`；批量（≥ 6）：`MSA_NTASKS_BATCH=8`。 |
| A40 节点 | 每节点 4 × NVIDIA A40；每卡 `nvidia-smi` 报告总显存 **46,068 MiB** | 将 A40 所在分区配置为主 GPU 分区；总 token **≤ 3,584** 时默认投这里。 |
| A100 节点 | 每节点 8 × NVIDIA A100 80GB PCIe；每卡总显存 **81,920 MiB** | 将 A100 所在分区配置为备用 GPU 分区；总 token **> 3,584 且 ≤ 7,168** 时默认投这里。 |

上表描述自动选择分区的情况；手动指定分区会覆盖自动选择，备用分区留空也不会自动转到 A100。程序按分区提交，并不检测 GPU 型号；分区中若混有不同型号，需要自行核实调度结果。每个推理作业的既有配置是 **1 张 GPU＋12 CPU**（`INF_GPUS=1`、`INF_NTASKS=12`），节点上 4／8 张卡的显存不会在本程序中合并给一个任务使用。

| 总 token 范围 | 既有推理策略 |
|---|---|
| ≤ 3,584 | A40，常规显存配置。 |
| 3,585–5,120 | A100 80GB，常规显存配置。 |
| 5,121–7,168 | A100 80GB，启用 unified memory（统一内存），允许使用 GPU 节点的主机内存；需要足够的主机内存和作业内存配额。 |
| > 7,168 | 在提交前拒绝，属于当前配置的保护上限。 |

对应设置为 `INF_BIG_TOKEN=3584`、`INF_UM_TOKEN=5120`、`INF_MAX_TOKEN=7168`，编译桶最大为 7,168。这里统计的是整个任务所有实体及副本的 token；纯标准蛋白时近似为各链残基数之和，混合实体时以实际分词为准。界面预估与编译桶填充也会影响容量判断。

**关于 OOM：**历史开发代码的注释记录过 **A100 80GB＋统一内存，在 7,680 token 的编译桶发生 OOM**，因此既有配置把上限设为 7,168。本轮未取得该次原始日志，也未重新运行该测试，不能据此认定所有超过 7,168 的输入必然 OOM，或低于它的输入必然成功；3,584 同样是 A40→A100 的调度阈值，并非测定的 A40 物理极限。5,120 的常规显存设置及统一内存机制可参考 [AF3 v3.0.2 官方说明](https://github.com/google-deepmind/alphafold3/blob/v3.0.2/docs/performance.md#gpu-memory)，容量仍受实际 AF3／JAX／CUDA 版本、桶配置和 GPU 节点内存影响。上表的 CPU 节点内存不能用来推断 GPU 节点内存。

**MSA 线程与并发：**当前脚本把 16／8 同时用于 Slurm 申请的 CPU 数和每个 Jackhmmer／Nhmmer 搜索进程的线程参数。AF3 的蛋白 MSA 可同时搜索 4 个数据库，所以每进程 16 线程并不代表整个作业只产生 16 个计算线程；现有配置可能出现线程超订阅。这些数值用于说明开发配置，不是 48 核节点的最优设置。严格匹配总 CPU 配额与各搜索进程线程，需要进一步拆分配置和调整脚本。[AF3 数据流水线说明](https://github.com/google-deepmind/alphafold3/blob/v3.0.2/docs/performance.md#data-pipeline)

原配置的 `MSA_MAX_CONCURRENT=12`、`INF_MAX_CONCURRENT=16` 是批次的调度并发上限，不是每节点线程数或卡数，也不保证有相应空闲资源；资源不足时由 Slurm 排队。其他集群可先从并发 1 的小任务验证，再结合 CPU、内存、GPU 和配额调整。MSA 线程、统一内存阈值和桶表等未出现在“资源上限”折叠栏中的项，可在个人 JSON 中设置；保留公开的 `config.example.json` 占位模板，把含实际路径和分区的实验室配置另存于仓库外。

## 打开界面

在 MobaXterm 中开启 X server 和 X11 转发，或使用：

```bash
ssh -X your_username@your_login_host
conda activate af3-console
cd /path/to/your/af3-console/AF3_Console
python -B af3_gui
```

在“设置”页检查资源、修正问题并保存。纯命令行帮助不要求 X11。

## 第一次预览与提交

下面所有 `P12345`、`Q12345` 都是**语法占位符**，没有指定生物学对象，也不保证长度或突变位点有效。正式使用前替换为自己的输入：

```bash
python -B af3.py run 'P12345+Q12345' --seeds 11 --dry-run
python -B af3.py run 'P12345' --msa-only
python -B af3.py pulldown 'P12345' 'Q12345' --seeds 11
python -B af3.py scan 'P12345' 'Q12345' --mode win --win 300 --overlap 100
python -B af3.py scan 'P12345' 'Q12345' --mode pae
```

`--dry-run` 不提交作业，但可能联网解析 UniProt。确认配置、输入和预览后，去掉它才会提交。GUI 对应“提交 → 预览”，检查后再提交。PAE 扫描缺少全长单体结果时可能先提交单体预测；大批量筛选前请检查任务数量和资源要求。

```bash
python -B af3.py status --jobs /path/to/your/batch/spec.json --json
python -B af3.py continue --spec /path/to/your/batch/spec.json
python -B af3.py retry --spec /path/to/your/batch/spec.json
```

更多文件输入示例见 [输入规范](#输入规范)，完整参数说明见 UI 帮助页。

## 结果、升级与故障处理

默认批次输出在 `~/AF3/output/`，共享 MSA 池在 `~/AF3/msa_data/`，全长推理池在 `~/AF3/infer_data/`。结果保存在集群；复制界面显示的目录后，用 SFTP 下载。关闭窗口不会取消已提交的 Slurm 作业。

新建的普通 UniProt MSA 按每个蛋白的 ID 命名，与用户填写的 run 任务名或 pulldown／scan 批次名无关，并保留 AF3 原生目录，例如（ID 仅为语法占位符）：

```text
msa_data/
└── P12345/
    └── P12345_data.json
```

程序不再把 JSON 移到池根目录，也不删除 AF3 的输出子目录和附带文件。同一 ID 的序列、外部 MSA 或已记录的计算环境不同，或者名称过长需要缩短时，会使用 `P12345__<hash>/P12345__<hash>_data.json` 等名称防止混用；截短和突变也会体现在名称中。匿名序列仍按内容命名。旧的根目录 JSON 和哈希缓存保持原样：匹配旧哈希身份的缓存可原地复用；普通输入也可在 JSON 完整性和序列校验通过后复用无管理记录的旧 ID 缓存，但其历史容器／数据库环境无法由文件名确定。外部 MSA、MSA-free 等特殊输入单独区分。

pulldown 中每个普通 UniProt 输入分别生成自己的 MSA。scan 开启共享全长 MSA 时，原始全长 MSA 也分别以 ID 命名；片段复用数据包含截短范围等标识。scan 未开启共享全长 MSA 时，独立计算的片段 MSA 同样需要范围标识，不能全部命名为同一个全长 ID。

同样兼容单个 UniProt ID 对应的 AF3 时间戳目录：仅目录名添加时间戳，JSON 仍以该 ID 命名：

```text
msa_data/
└── P12345_20260602_140829/
    └── P12345_data.json
```

没有管理记录时，按目录名中的时间戳从新到旧选择校验通过的结果，再尝试无时间戳原生目录和旧平铺文件。自动按 ID 查找时，目录的 ID 前缀与 JSON 的 ID 应一致。多个结果需要指定某一次时，直接选择其目录或 JSON；已有 MSA JSON 可这样用于推理：

```bash
python -B af3.py run --infer-only --json /path/to/your/msa_data/P12345_20260602_140829/P12345_data.json
```

历史输出也可以使用自定义 job 名；名称中的 `_and_` 等文字不用于判断输入个数，实际输入以 JSON 内容为准。自定义名称或目录前缀与文件名不一致时，可显式选择已有 JSON。保留或复制完整 AF3 输出目录即可，伴随文件及相对引用无需拆开整理。

池中的 `.msa_records/` 保存输入身份与完成状态，`.locks/` 协调写入。新作业只有成功退出且产物校验通过才算 MSA 就绪；失败文件保留但不会作为完成结果使用。复制整个池时保留这些记录，不要通过删除记录来强行复用。旧任务快照继续使用各自原来的存储逻辑，新目录行为用于新版创建的任务。

升级时将新版放在新目录，保留个人配置。不要移动或删除仍被排队/运行作业引用的安装目录、Python 环境和任务快照；不要手动改写已固定的计划。缓存身份变化可能需要重新计算。

| 问题 | 检查方向 |
|---|---|
| 没有窗口、`DISPLAY` 为空 | X11 转发和本地 X server |
| Qt xcb/platform plugin 错误 | 当前 Qt 所需的 Linux 系统库 |
| 资源检查失败 | 占位配置、路径权限、分区名、计算节点可见性 |
| 容器需要 module 才能使用 | 填写 `CONTAINER_MODULE` |
| 保存设置后未生效 | `AF3_CONFIG` / `AF3_BASE` 是否覆盖当前值 |
| OOM 或 token 超限 | GPU 容量和配置的阈值；没有通用安全上限 |
| 有输出文件夹却显示失败 | 退出回执和日志；目录存在不代表任务成功 |

反馈问题前，请删除日志和截图中的课题目标、序列、未公开结果、账号、主机、私人路径及凭据。


## 输入规范

输入由使用者自行准备；仓库不提供课题数据或供下载的人工数据集。`P12345` 和 `Q12345` 仅为语法占位符，不是生物学示例。正式运行前替换；即使 `--dry-run` 也可能联网解析序列。

| 输入 | 内容与用途 |
|---|---|
| UniProt 表达式 | 如单个 `P12345` 或复合物 `P12345+Q12345`；Run 预测指定输入。 |
| Pulldown 两组输入 | 两组 ID／表达式的组合构成筛选任务；列表语法见 UI 帮助。 |
| Scan 输入 | 长蛋白和候选伙伴；固定窗口需要窗口长度与重叠长度，PAE 扫描使用单体置信度数据。 |
| 原生 AF3 输入 JSON | 符合 AF3 输入规范，包含实体与序列；在 GUI 中明确选择或用 `--json /path/to/your/input.json` 指定。 |
| 已有 MSA 产物 | AF3 `*_data.json` 及完整目录和引用的伴随文件；保留原生或带时间戳的目录。复用前检查格式、序列和必需字段；合法空 MSA／模板字段仍需用户决定复用或重算。 |
| 非配对 MSA | `P12345:msa=/path/to/your/alignment.a3m`，需与选定序列匹配。 |
| 单体 PAE | `P12345:pae=/path/to/your/confidences.json`，为对应序列的 AF3 PAE 置信度 JSON。 |
| 模板 | `P12345:tpl=/path/to/your/template.cif:0,1:0,1`，模板 mmCIF 加从 0 开始的 query／template 残基映射。 |

容器文件（`/path/to/your/alphafold3.sif`）、参数目录（`/path/to/your/model_parameters`）、数据库目录（`/path/to/your/databases`）和工作目录（`/path/to/your/workspace`）是部署资源，不能互相替代；详见[配置说明](#configuration)。请勿将真实序列或私人路径加入 Issue 或 Git 历史。

<a id="configuration"></a>

## 配置说明

程序与数据分别存放。安装目录及其 Python 环境必须能被计算节点访问；个人任务与缓存应放在可写的工作目录中。发布包不包含真实的站点配置。

<a id="locations"></a>

### 路径与目录

| 配置项 | 含义 | 示例或默认行为 |
|---|---|---|
| `HOST_BASE` | 个人工作目录 | `~/AF3` |
| `HOST_SIF` | 容器**文件** | `/path/to/your/alphafold3.sif` |
| `HOST_MODELS` | 模型参数目录 | `/path/to/your/model_parameters` |
| `HOST_DB_SOURCE` | 数据库目录 | `/path/to/your/public_databases` |
| `HOST_OUTPUT` | 批次结果目录 | 根据 `HOST_BASE` 派生为其下的 `output/` |
| `HOST_MSA_DATA` | 可复用的 MSA 主池 | 根据 `HOST_BASE` 派生为其下的 `msa_data/` |
| `MSA_BACKUP_DIRS` | 最多两个独立的 MSA 备份池 | 默认 `[]`；填写目录路径列表 |
| `HOST_INFER_DATA` | 全长推理池 | 根据 `HOST_BASE` 派生为其下的 `infer_data/` |
| `HOST_CACHE` | 一般缓存目录 | 根据 `HOST_BASE` 派生为其下的 `cache/` |
| `HOST_JAX_CACHE` | 编译缓存目录 | 根据 `HOST_BASE` 派生为其下的 `af3_buckets_cache/` |
| `HOST_SSD_CACHE` | 可选的节点本地数据库缓存 | 留空时关闭此功能 |

必填路径为空时，不会将其视为当前目录。非空的 `HOST_*` 路径以及 CLI 的 `--output-dir`／`--msa-dir` 会展开 `~` 并转换为绝对路径；相对路径以进程启动目录为基准。建议使用绝对路径，避免从不同位置启动时指向不同目录。路径不能含换行符或 NUL 字符。`/path/to/your/...` 是文档占位符，提交前必须替换。

在 Linux 上，上述主机路径不能包含 `:` 或 `,`，因为它们是 Singularity 挂载参数的分隔符；这一规则并不适用于输入表达式中的所有文件引用。构造 shell 命令时，程序会对空格、单引号和美元符号进行引用处理。JSON 路径值中应直接保存实际路径，不要额外加入 shell 引号。

容器内部挂载点是程序接口，不是私人主机路径，不应按文档占位符的方式替换。

`MSA_BACKUP_DIRS` 与 `HOST_MSA_DATA` 使用相同的路径规范化规则。主池与备份池合计最多三个目录，必须互不相同、互不包含。提交 MSA 任务前，备份目录必须已存在且可读写；仅推理任务不要求备份目录写权限。设置页的资源检查涵盖所有已配置的备份目录。启动确认、只新增同步、备份回执与复用选择见 [MSA 池说明](#msa-pools)。

<a id="precedence-and-persistence"></a>

### 配置优先级与保存

配置优先级从低到高为：

1. 程序内置默认值。
2. 程序旁的 `site_config.json`（可选，已由 Git 忽略）。
3. `AF3_CONFIG` 指定的用户 JSON，默认使用 `~/.config/af3_console/config.json`。
4. 普通程序进程中的 `AF3_BASE` 环境变量覆盖工作目录。

未显式配置的派生目录跟随最终工作目录。已显式指定的共享模型或缓存路径不会随工作目录变化。`AUX_PARTITION` 未单独设置时，跟随最终 CPU 分区。

排队任务使用完整配置快照和 `AF3_SNAPSHOT=1`，之后修改交互环境中的 `AF3_BASE` 不会改变已有任务的路径。内部快照变量不属于安装配置。设置页以原子方式写入用户 JSON，不修改安装目录中的源码。如果已导出 `AF3_BASE`，它会持续覆盖保存的工作目录，直到该环境变量被取消。

```bash
export AF3_CONFIG=/path/to/your/private/config.json
export AF3_BASE=/path/to/your/work_directory
python -B af3_gui
```

程序不读取 `.env` 文件。可用 `AF3_PAE_CACHE` 调整本地 PAE 数值缓存位置。若覆盖 GUI 所用的解释器或脚本路径，两者必须指向匹配的安装版本。不要把凭据写进配置，也不要将包含个人环境设置的文件提交到仓库。

<a id="slurm-and-container-settings"></a>

### Slurm 与容器设置

- `MSA_PARTITION` 和 `INF_PARTITION`：填写实际可用的 CPU／GPU 分区名，属于必填项。
- `AUX_PARTITION`：控制及监控作业的可选分区，未设置时跟随 CPU 分区。
- `INF_FALLBACK_PARTITION`：留空时关闭自动转投，大任务仍使用主 GPU 分区；这不保证显存足够。
- `CONTAINER_RUNTIME`：默认为 `singularity`，也可填写管理员提供的可执行文件路径；该值作为一个可执行程序处理，不能填写任意 shell 代码片段。
- `CONTAINER_MODULE`：可选的环境模块名，例如站点实际提供的 Singularity 模块。留空时使用当前 `PATH`。作业先检查模块是否可用，按配置加载后再检查运行时；仅通过模块提供的安装不会因登录节点初始 `PATH` 中没有命令而直接被拒绝。
- `HOST_SSD_CACHE`：留空时不向节点本地复制数据库；只有在管理员确认缓存布局合适且可写时才应启用。

CPU 数、并发数、编译桶和 token 阈值沿用可配置的 `MSA_*`／`INF_*` 设置。这些是调度策略默认值，不是硬件能力保证。首发面向当前的 Singularity 命令接口和标准 Slurm 指令；需要额外 account／QoS 封装或其他运行时的站点，应单独验证。

设置页和 CLI 提交共用部署检查，按所选阶段核对资源、已有文件的权限、新输出目录父目录的可写性，以及必需的 Slurm 工具。集群配置不完整时，仍可使用离线帮助、PAE 分析和任务规划。计算节点上的实际挂载与 GPU 环境仍需用真实小任务验证。

<a id="data-and-network-behavior"></a>

### 数据与网络行为

UniProt 表达式可能查询 UniProt，以获取序列和显示名称。因此，即使预览不提交 Slurm 作业，也可能发送网络请求。报告、任务快照、JSON 输入和缓存可能包含课题信息；请保存在仓库之外，分享诊断材料前先脱敏。

<a id="msa-pools"></a>

## MSA 池

### 设置 1–3 个目录

在 **设置 → MSA 缓存目录** 的第一个路径框左侧点击 **＋**，可在下方增加第二、第三个路径框。第一项是主池，其他项是备份池；移除路径框只修改配置，不删除文件。保存后生效。目录必须相互独立，不能相同，也不能互相包含。

```json
{
  "HOST_MSA_DATA": "/path/to/your/primary_msa_pool",
  "MSA_BACKUP_DIRS": [
    "/path/to/your/first_backup_msa_pool",
    "/path/to/your/second_backup_msa_pool"
  ]
}
```

`MSA_BACKUP_DIRS` 默认是 `[]`，最多两项。路径展开 `~`，相对路径以启动目录为基准，建议填绝对路径。请事先创建备份目录，并确保登录节点和计算节点都能读写；设置检查和启动扫描不会创建它们。使用同事的目录前，应由双方约定共享范围和访问权限。无需安装或配置 rsync。

### 新产物备份与已有文件同步

**新计算的产物：** AF3 先在主池内完成写入；校验通过后，将完整产物复制到每个已配置的备份池。保留 AF3 的目录、JSON 文件名和附带文件，继续兼容原生目录、时间戳目录及旧版平铺文件。备份按任务提交时的配置快照执行，之后修改设置不会改变排队任务的目的地。

**已有产物：** 每次打开 GUI，后台检查一次已保存的目录。发现某个池有其他池缺少的完整产物时，弹窗显示待复制项、大小及问题，由用户决定是否互相同步；默认不执行。可在设置页手动再次检查。扫描会读取内容并计算校验值，大文件或网络目录较多时可能需要一些时间。

同步采用“只新增”规则：

- 相同内容跳过；不同内容但同名的目录/文件列为冲突，保留双方版本，不自动选最新、不覆盖、不合并目录。
- 复制整个原生或带时间戳的目录，包括其内部引用的相对路径文件。只有内容自包含的旧平铺 JSON 才能直接复制。
- 缺字段、格式无效、生成尚未完成、来源仍在变化、指向产物目录外的文件引用，以及符号链接或 Windows junction，不会作为完整可移植产物复制；结果中给出原因。
- 复制前后复查源文件；目标在扫描后被别人创建也会跳过并报告冲突。无法可靠执行“目标存在则失败”的文件系统会报错。
- 不执行删除镜像同步。清理功能也会拒绝删除已配置或任务快照记录的 MSA 池、池内内容以及包含池的父目录。同步失败只清理本次操作自己的临时暂存内容，不删除已有产物。

新产物备份失败时，成功的主池数据仍保留，推理可以继续；作业日志显示警告，主池的 `.msa_backup_receipts/<任务名>.json` 记录哪些备份未完成。修复权限、容量或冲突后，在 GUI 中重新检查同步。**一次主池计算成功不等于所有备份成功**，应查看这份记录或同步结果。

多个目录增加了副本，但同一存储设备上的副本不能防止设备整体损坏。同步没有版本删除/恢复功能；需要历史版本或灾难恢复时，应结合存储系统已有的快照和备份服务。

### 复用之前的检查

程序区分两类情况：

| 检查结果 | 行为 |
|---|---|
| 必需字段缺失、类型错误、比对无法读取、模板索引不合法等 | 不作为完整缓存复用，给出具体原因。 |
| 数据格式完整，但蛋白链的 `unpairedMsa`、`pairedMsa` 或 `templates` 为空 | 列出具体文件、链和空字段，让用户选择 **复用 / 重新计算 MSA / 取消**；默认取消。RNA 只检查其适用的 `unpairedMsa`。 |

空字符串和 `templates: []` 是 AF3 的合法输入/结果，可能表示无搜索命中或显式关闭相应功能；本项目仍提示，因为用户可能预期有 MSA 和模板。重新计算保留原目录，使用新的计算身份和目录，不覆盖共享缓存。**重新运行并不保证一定得到非空结果**，需核对搜索设置、输入和数据库。

已有损坏产物也不会被 AF3 原位覆盖：新计划或重试会为修复分配新的目录，保留原自定义输入配方；正在运行的控制任务若发现旧目录已有不完整数据，会停止并提示重新规划。直接用于仅推理的无效 JSON 会被拒绝，需先修正输入或选择能够重新运行 MSA 的流程。

MSA 命令不启用 AF3 的强制覆盖目录选项。若其他脚本在检查后抢先写入同名目录，AF3 可能自行生成时间戳目录；所有目录均保留，本次受管理的任务会因预期输出未就绪而报错，需检查日志并重新规划。同事的脚本不遵循本程序锁时，仍需双方避免同时操作同一产物。

确认复用针对当时检查到的文件版本；不能把一次确认作为将来所有空缓存的永久许可。新计算或后来变化的数据仍需检查。远程控制任务无法弹窗时，会停止相关推理并记录需确认状态，之后在 GUI 中重试处理。预览不授权正式提交。

CLI 遇到需要确认的缓存会给出文件与空字段信息并退出；明确使用 `--empty-msa-policy reuse` 或 `--empty-msa-policy recompute` 后再提交。合法但为空的文件可以备份和同步；同步确认与推理复用确认是两个独立决定。

完整性检查支持内嵌内容和 AF3 的外部文件引用，包含 gzip、xz 和 zstd 压缩的比对文本。检查字段、比对查询序列、模板内容和索引的一致性，不代替 AF3 对结构文件的完整解析，也不能证明 MSA 或模板具有生物学质量。公开测试只使用人工数据。



完整源码、测试和构建工具见 [source 分支](https://github.com/luckingclark/AF3-Console/tree/source)。部署目录中的第三方模块可以直接修改或替换；请保留许可证。
