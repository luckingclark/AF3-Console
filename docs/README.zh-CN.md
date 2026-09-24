# AF3 Console

[English](../README.md) · [完整使用指南](usage.zh-CN.md) · [输入规范](usage.zh-CN.md#输入规范) · [功能对比](comparison.zh-CN.md)

AF3 Console 在基于 AlphaFold 3 的批量互作筛选（Pulldown）之外，引入了面向长蛋白的片段扫描（Scan）模式。超长序列的全长预测可能难以突出局部互作信号，仅依据整体 ipTM 低分排除候选，存在漏掉潜在互作的假阴性风险。Scan 将长蛋白分段后与候选伙伴逐一预测，并将结果映射回原序列坐标，旨在减少全长筛选中的漏检，同时定位值得进一步验证的互作区域，为截短体设计和实验验证提供依据。

在固定长度滑动窗口（Window Scan）的基础上，项目进一步引入 PAE 结构域窗口（PAE Domain Window），旨在减少人为截断造成的假阳性。固定窗口可能从结构域内部切开，破坏原有折叠单元，例如移除自身 β 链后，其他蛋白片段可能在预测中“补位”，形成依赖截断边界的高分伪互作。为此，项目复用 ChimeraX **Color PAE Domains** 的分域逻辑，根据单体预测的 PAE 将相对位置较确定的残基聚为结构单元候选，再据此构造扫描片段，尽量保留结构完整性、减少任意断域，而无需依赖现成的结构域注释。该策略为切片提供了结构依据，其降低假阳性的实际效果仍需通过系统比较和实验验证。

面向 Slurm 集群用户的双语桌面与命令行工作台，整合 AlphaFold 3 输入准备、提交、监控、批量筛选、片段扫描及结果查看。项目作者：**PKU-Gaolab, Ming-Ao Lu**。

## 模式流程

![Run、Pulldown 与 Scan 总览，Scan 包含固定窗口与 PAE 结构域窗口](images/workflow-zh-CN.svg)

Run 预测指定蛋白或复合物；Pulldown 筛选两组输入的组合；Scan 筛选片段窗口。缺少单体 PAE 时可能先补算单体预测。阶段选择、MSA 复用、1–3 个 MSA 池以及经用户同意的同步见[完整指南](usage.zh-CN.md)和[MSA 说明](usage.zh-CN.md#msa-pools)。

## 安装与启动

**新用户只需下载 main。** 这是部署版；完整可读源码、测试与构建工具放在 [source 分支](https://github.com/luckingclark/AF3-Console/tree/source)。运行核心为 7 个程序文件与 1 个字体，均位于 `AF3_Console/`。相比 9 月 12 日旧包，新增模块用于多 MSA 池同步和第三方算法的独立分发；请连同许可证整套保留。

在仓库点击 **Code → Download ZIP**，解压到登录节点和计算节点均能访问的软件目录，然后执行：

```bash
conda env create -f environment.yml
conda activate af3-console
cd AF3_Console
python -B af3_gui
```

实际预测需要 **Linux＋Slurm**、自行取得的 AF3 容器、模型参数、数据库和 GPU 分区；GUI 需要 **X11 转发**。[安装、配置、首次预览、提交、硬件参考和故障排查](usage.zh-CN.md)从创建环境开始说明。公开 `AF3_Console/config.example.json` 仅含占位符，真实配置请放在仓库外。

当前版本 **0.1.0（预发布）**，部署目录与指南更新于 **2026-09-24**。[Linux CI](https://github.com/luckingclark/AF3-Console/actions/runs/35969165333)：**部署检查及 191 项测试全部通过，0 跳过**。[验收记录](https://github.com/luckingclark/AF3-Console/blob/03c29106fd23b20928c9591b35b22b2508c9adfd/docs/validation.md)列出实际完成的检查及待验证事项；真实 Slurm／AF3 运行、全新 Linux Conda 安装、GPU 容量和跨集群兼容性仍需部署验收。本仓库不分发 AF3 引擎、权重、数据库或课题数据。

## 作者、开发方式与来源

本项目代码利用 **Kimi-K3 和 GPT-6**，通过 AI 辅助的 **vibe coding** 方式完成。项目作者：**PKU-Gaolab, Ming-Ao Lu**。可通过 Issues／Pull requests 反馈问题或提交修改，请先脱敏日志和截图；开发检查见[指南](https://github.com/luckingclark/AF3-Console/tree/source)。作者署名不代表持续维护承诺。

[功能对比](comparison.zh-CN.md)只比较已有工具的能力与侧重点，不比较性能。PAE 建图改编自 LGPL 授权的 UCSF ChimeraX 特定实现，上游归功于 Tristan Croll／ISOLDE；聚类及映射队列改编自 NetworkX。具体来源与修改见[算法来源](https://github.com/luckingclark/AF3-Console/blob/03c29106fd23b20928c9591b35b22b2508c9adfd/docs/algorithm-provenance.md)及[第三方声明](../AF3_Console/THIRD_PARTY_NOTICES.md)。

原创代码和文档采用 **MIT**，Copyright © 2026 PKU-Gaolab, Ming-Ao Lu；PAE 衍生模块保留 **LGPL-2.1-only**，NetworkX 衍生模块保留 **BSD-3-Clause**，字体保留自身条款。[LICENSE](../LICENSE) 和 [LICENSES/](../AF3_Console/LICENSES/) 包含完整通知。项目与 Google DeepMind、UCSF ChimeraX 无官方隶属关系，AF3 软件及参数另受对应版本条款约束。

软件引用见 [CITATION.cff](../CITATION.cff)，请记录实际使用的 commit／版本；不虚构论文或 DOI。按使用情况引用上游方法，引用不能代替许可义务。
