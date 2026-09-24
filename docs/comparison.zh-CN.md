# 相关工具：功能与侧重点

[English](comparison.md) · [返回 README](README.zh-CN.md)

核对日期：**2026-09-13**。下表依据各项目的官方 README，项目名链接到对应来源。这里只比较功能和工作方式，没有安装对测，也不评价速度、资源消耗或预测质量。未列出的功能表示本次未核实，不能据此认定不支持。

| 项目 | 能实现什么、怎样使用 | 侧重点及与 AF3 Console 的关系 |
|---|---|---|
| [AF3 Console](README.zh-CN.md) | 双语 Qt 桌面＋CLI；Slurm 提交、监控、继续及重试；两组组合筛选、固定窗口／PAE 片段扫描、MSA 复用和结果查看。 | 将 AF3 的日常集群操作集中在同一工作台，目标部署为 Linux＋Slurm＋Singularity＋X11；真实集群验收状态见[验收记录](https://github.com/luckingclark/AF3-Console/blob/03c29106fd23b20928c9591b35b22b2508c9adfd/docs/validation.md)。 |
| [AlphaPulldown](https://github.com/KosinskiLab/AlphaPulldown#readme) | 支持 AlphaFold 2、AlphaFold 3、AlphaLink 后端；组合筛选、区域与副本表达式、预计算特征／MSA 复用；可配合 AlphaJudge、APLit 分析和查看结果。 | 侧重多种预测后端的复合物建模工具链。筛选、片段输入和 MSA 复用与本项目重叠；AF3 Console 侧重 AF3 的桌面与命令行操作。 |
| [AlphaPulldownSnakemake](https://github.com/KosinskiLab/AlphaPulldownSnakemake#readme) | 用样本表和配置定义 AlphaPulldown 任务；由 Snakemake 管理本机或 Slurm 执行、重试、特征复用及结果汇总。 | 侧重由工作流引擎组织批量计算，与本项目的 Slurm 任务管理重叠。它是 AlphaPulldown 的相关工作流封装；上游当前推荐通过它运行。 |
| [AFusion](https://github.com/Hanziwww/AlphaFold3-GUI#readme) | Streamlit 浏览器 UI；生成含蛋白、RNA、DNA、配体等实体的 AF3 JSON；配置 MSA／模板／修饰／键，通过 Docker 执行并显示输出；提供 Python API 批处理及结构／PAE 查看。 | 侧重浏览器中的 AF3 输入、运行和可视化；与本项目的图形操作及结果查看重叠。文档展示的执行方式是 Docker，本项目面向 Qt 桌面和 Slurm／Singularity。 |
| [af3_pulldown](https://github.com/luisherfurth/af3_pulldown#readme) | 分阶段脚本和 Slurm 数组作业；一个诱饵与多个候选逐一建立双链 AF3 任务，复用诱饵 MSA，再用外部 AFM-LIS 评分和生成汇总热图。 | 侧重单诱饵候选筛选及 LIS／iLIS 分析。AF3 Console 提供两组蛋白或复合物组合、片段扫描和统一任务界面，结果评分功能应按各自文档理解。 |
| [AFragmenter](https://github.com/sverwimp/AFragmenter#readme) | 从 PAE 用 Leiden 聚类划分结构域；调整分辨率和过滤条件；支持 Notebook／Python／CLI、PAE 与结构可视化、CSV 和结构域 FASTA 导出。 | 侧重交互探索和导出 PAE 分域结果。本项目则把分域衔接到片段窗口生成和后续 AF3 作业；两者的分域实现不同。 |
| [pae_to_domains](https://github.com/tristanic/pae_to_domains#readme) | 可嵌入其他程序的 Python 代码，也有 CLI；从 PAE 得到残基簇，导出 CSV，可选择 NetworkX 或 igraph 后端并调整阈值与分辨率。 | 侧重可复用的分域计算组件。本项目在相关方法之外提供片段扫描、调度和界面；具体代码来源仍以[算法溯源](https://github.com/luckingclark/AF3-Console/blob/03c29106fd23b20928c9591b35b22b2508c9adfd/docs/algorithm-provenance.md)为准。 |

按所需的操作方式选择即可：工作流引擎、多后端建模、浏览器操作、特定筛选流程和单独分域分析，各有适用场景。AF3 Console 的定位是双语桌面与命令行集成；这不表示表中功能为本项目独有。

AFusion 的公开输入演示站仅生成 JSON，表中的执行能力指其本地安装工具。AlphaPulldown 的 AlphaJudge／APLit 是配套项目。这里的功能比较也不表示 AF3 Console 集成了上述所有工具；实际复用的代码和资产见[第三方声明](../AF3_Console/THIRD_PARTY_NOTICES.md)。
