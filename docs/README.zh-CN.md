# AF3 Console

[English](../README.md) · [完整使用指南](usage.zh-CN.md) · [相关工具对比](comparison.zh-CN.md)

AF3 Console 是面向**在 Slurm 集群上使用 AlphaFold 3 的科研人员**的双语桌面与命令行工作台，整合输入准备、作业提交与监控，并汇总预测结构和互作筛选分数，便于查看结果。

**当前版本：0.1.0（预发布）。** `main` 是供用户配置后运行的部署版；[source 分支](https://github.com/luckingclark/AF3-Console/tree/source)保存可读 GUI 源码、测试与构建工具。

## 三种工作模式

### Run

根据表达式或 AF3 输入 JSON 预测指定蛋白或复合物，也可选择仅生成 MSA 或仅进行推理。

### Pulldown

批量筛选两组蛋白或复合物之间的组合，并汇总预测分数。

### Scan

使用固定滑动窗口（**Window Scan**）或单体 PAE 引导的窗口（**PAE Domain Window**）与候选伙伴逐一预测，并将分数映射回原序列坐标。

长蛋白的全长预测可能掩盖局部互作信号，仅凭整体 ipTM 低分排除候选，存在漏检风险。片段扫描旨在定位值得验证的区域，为截短体设计和实验验证提供依据。

固定窗口可能切断结构域，产生依赖截断边界的高分伪互作。PAE 结构域窗口借鉴 ChimeraX **Color PAE Domains**，依据单体 PAE 聚类残基并构造片段，尽量保留候选结构单元，无需现成的结构域注释；其降低假阳性的实际效果仍需系统比较和实验验证。

![Run、Pulldown 与 Scan 综合流程图，Scan 包含固定窗口和 PAE 结构域窗口](images/workflow-zh-CN.svg)

PAE 引导的扫描需要单体 PAE，缺少时可能需要额外运行单体预测。复合物、截短体及批量列表的写法见[输入规范](usage.zh-CN.md#输入规范)。预测分数及 PAE 划分的结构单元用于提出待验证的候选，不能作为结合的实验证据或已确认的结构域注释。

## 安装前需要什么

实际预测前，需要取得以下资源的使用权限：

- **Linux＋Slurm** 集群及 CPU、GPU 分区。提交检查需要 `sbatch`、`squeue` 和 `flock`。
- 单独取得的兼容 **AlphaFold 3 容器、模型参数和数据库**。生成的作业在容器内调用 `python /app/alphafold/run_alphafold.py`。默认容器运行时为 **Singularity**，可配置运行时命令及可选的 `module load`。
- 可写的工作目录，以及相关计算节点均能访问的资源路径。
- GUI 所需的 **X11 转发**；命令行入口不需要图形显示。

本仓库提供工作台，不包含 AF3 引擎、权重、数据库或课题数据。尚无这些资源时，请先向集群管理员确认部署位置与可用分区。

[environment.yml](../environment.yml) 声明了 **Python 3.12**、PySide6、NumPy、pandas、Matplotlib、six、python-dateutil、zstandard 和 qtawesome 及其版本范围。这些是工作台的依赖；创建该环境不会安装 AF3 预测引擎。

## 下载和安装

选择仓库的 **main** 分支，点击 **Code → Download ZIP**，解压到集群的软件目录。请保留整个解压目录，包括字体和许可证。在包含 `environment.yml` 的解压目录中，用 **Bash** 执行：

```bash
conda env create -f environment.yml
conda activate af3-console
cd AF3_Console
python -B install_command.py
```

### 最小离线检查

保持上述环境已激活，并位于 `AF3_Console/` 目录，执行：

```bash
python -B af3.py --version
python -B -c "import af3; print(len(af3.parse_expression('P12345+Q12345', fetch=False)))"
```

预期输出：

```text
AF3 Console 0.1.0
2
```

`2` 表示表达式成功解析为两个蛋白条目。**P12345、Q12345 仅为语法占位示例。** 此检查不下载序列、不提交作业，也不生成预测文件；它验证命令行入口和表达式解析，不代表 AF3 资源或集群运行已经通过验证。

### 打开界面

已经在用最初的 GitHub 版本时，只需更新 `af3_gui` 并新增 `install_command.py`，见[升级清单](usage.zh-CN.md#从最初的-github-版本升级到当前启动方式)。

上述安装脚本只需运行一次，会在当前环境中注册 `af3_gui` 命令。以后包括新开的 SSH 会话，执行 `conda activate af3-console` 后，都可在**任意目录**运行：

```bash
af3_gui
```

请保留解压后的程序目录。搬动或升级程序后，在新的 `AF3_Console/` 目录重新执行 `python -B install_command.py`，即可更新命令指向。不需要管理员权限，也不需要修改 shell 启动文件。

此阶段的成功标志是主窗口打开，能够访问**设置**及**帮助 → 关于**；AF3 资源可以随后填写。若无法打开图形显示，请检查 X11 转发，参见[使用指南](usage.zh-CN.md)。

## 配置并完成第一次预测

1. 已有配置 JSON 时，在**设置**中点击**导入配置 JSON**，再按自己的账号修改；也可手动填写。设置工作目录（默认 `~/AF3`）、AF3 容器文件、模型参数目录、数据库目录，以及实际可用的 CPU／GPU 分区。使用真实集群路径，不要保留 `/path/to/your/...` 占位符。[config.example.json](../AF3_Console/config.example.json)用于说明字段，并非可直接提交作业的集群配置。
2. 点击**检测资源**，解决提示的问题后保存。导入配置会保存为个人默认设置，下次自动读取，不会将模板选为保存位置。个人设置通常写入 `~/.config/af3_console/config.json`。`AF3_CONFIG` 可指定其他配置文件，`AF3_BASE` 可覆盖工作目录；无需 `.env` 文件。优先级详见[配置说明](usage.zh-CN.md#precedence-and-persistence)。
3. 从 **Run** 和你自己的小规模输入开始。预测两蛋白复合物时，用 `+` 连接两个实际 UniProt ID；也可按[输入规范](usage.zh-CN.md#输入规范)提供 AF3 输入 JSON。UniProt 表达式可能需要联网获取序列。
4. 先**预览**，核对任务数量、运行阶段和 seeds。预览／CLI `--dry-run` 不提交作业，但可能获取序列及检查缓存，不一定是离线操作。
5. **提交**后在仪表盘查看任务状态。Slurm 作业编号仅表示提交成功；完整预测还需确认任务完成，并检查下文的结构和置信度文件。

批量输入、扫描设置、CLI 命令以及 CPU／A40／A100 的资源参考见[完整指南](usage.zh-CN.md)。资源上限是配置选项，不代表已经验证某个 token 数必定不会导致 GPU 显存不足。

### 复用与备份 MSA

设置支持 **1–3 个 MSA 池目录**。新产出的 MSA 会复制到配置的池中；每次 GUI 启动时，检测到新增内容可由用户确认后同步。同步只补充缺失文件，不删除已有数据，也不覆盖冲突文件。权限不足或目标目录不可用可能导致备份未完成，请查看操作结果。

保留 AF3 原生目录，包括 `P12345_20260602_140829/P12345_data.json` 这样的时间戳目录。不完整的 MSA 数据会被拒绝复用；合法但为空的 `unpairedMsa`、`pairedMsa` 或 `templates: []` 会提示选择复用、重算或取消。具体检查范围和限制见 [MSA 说明](usage.zh-CN.md#msa-pools)。

## 在哪里找结果，怎样确认成功

下表使用默认工作目录；配置可改变路径，实际以提交任务记录的位置为准。

| 位置或文件 | 用途与成功标志 |
|---|---|
| `~/AF3/output/` | 任务计划、状态、日志及预测结果。在仪表盘查看对应任务目录；`spec.json`／`plan.json` 表示任务计划，不代表推理已完成。 |
| `~/AF3/msa_data/` | 可复用的 MSA，例如 `P12345/P12345_data.json`。仅 MSA 任务应检查该阶段完成及数据生成，不会产出结构。 |
| `*_model.cif`、`*_summary_confidences.json` | 结果查看器使用的 AF3 结构及置信度摘要。完整预测应确认预期任务成功，且这些结果可以打开；目录层级取决于 AF3 引擎和任务。 |
| `ranking.csv`、`results_index.json` | Pulldown／Scan 的分数汇总与结果索引。解释排名前先检查任务状态和缺失结果。 |
| `iptm_profile.csv`、`report.md` | Scan 的附加汇总，用于沿原始序列坐标查看分数。 |

缺少结果、存在失败任务或仅有部分排名，都不能视为整批筛选完成。结果获取和后续操作见[结果与升级说明](usage.zh-CN.md#结果升级与故障处理)。

## 验证范围

2026-09-30 的配置导入更新在本地 Windows 通过 194 项测试，1 项符号链接测试因平台权限跳过；其中新增 4 项测试覆盖导入、修改、保存及新进程自动读取。

此前的 [Linux CI](https://github.com/luckingclark/AF3-Console/actions/runs/35969165333) 在 Ubuntu 24.04、Python 3.12 下通过部署检查和 **191 项测试，0 跳过**，包括运行文件与源码一致性、隔离启动、离线许可证、人工数据与模拟调度检查。这些不代表已在你的集群完成真实 AF3 预测。

真实 Slurm／AF3 执行、全新 Linux Conda 安装、交互式 X11 使用、GPU token 上限和跨集群兼容性仍需部署验收。参见[验收记录](https://github.com/luckingclark/AF3-Console/blob/56059d3188ecc0c278edeee118badd10535b765c/docs/validation.md)。本项目不声称比[相关工具](comparison.zh-CN.md)有性能优势。

## 作者、许可与引用

项目作者：**PKU-Gaolab, Ming-Ao Lu**。本项目代码利用 **Kimi-K3 和 GPT-6**，通过 AI 辅助的 **vibe coding** 方式完成。反馈问题前请先脱敏日志和截图；可读源码与开发检查位于 [source 分支](https://github.com/luckingclark/AF3-Console/tree/source)。

- 原创代码和文档采用 **MIT**，见 [LICENSE](../LICENSE)。
- `af3_pae_domains.py` 采用 **LGPL-2.1-only**，改编自 UCSF ChimeraX **Color PAE Domains** 实现，并保留对 Tristan Croll／ISOLDE 的来源归功。
- `af3_networkx_community.py` 采用 **BSD-3-Clause**，改编自 NetworkX 的聚类与映射队列代码。
- 随附字体保留自身条款。完整第三方说明和许可证见 [THIRD_PARTY_NOTICES.md](../AF3_Console/THIRD_PARTY_NOTICES.md) 及 [LICENSES/](../AF3_Console/LICENSES/)，也可从帮助 → 关于离线访问。

具体来源与修改见[算法来源](https://github.com/luckingclark/AF3-Console/blob/03c29106fd23b20928c9591b35b22b2508c9adfd/docs/algorithm-provenance.md)。本项目与 Google DeepMind、UCSF ChimeraX 无官方隶属关系；单独取得的 AF3 软件、参数和数据库适用各自条款。

软件引用见 [CITATION.cff](../CITATION.cff)，请记录实际使用的 commit／版本。本项目没有声称关联论文或 DOI。按使用情况引用上游方法；引用不能代替许可义务。
