# 输入规范

[English](inputs.en.md) · [使用指南](usage.zh-CN.md)

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

容器文件（`/path/to/your/alphafold3.sif`）、参数目录（`/path/to/your/model_parameters`）、数据库目录（`/path/to/your/databases`）和工作目录（`/path/to/your/workspace`）是部署资源，不能互相替代；详见[配置说明](configuration.md)。请勿将真实序列或私人路径加入 Issue 或 Git 历史。
