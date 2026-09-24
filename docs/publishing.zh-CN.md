# 第一次把 AF3 Console 发布到 GitHub

只发布已经清理并验收的独立源码目录。不要把研究工作目录、现有实验结果、个人配置或旧 ZIP 一起上传。

## 先理解几个词

| 名称 | 意思 |
|---|---|
| Repository / 仓库 | 保存公开源码、说明和修改历史的地方 |
| Commit / 提交 | 一次可以查看差异的修改记录 |
| Branch / 分支 | 一条开发线，首发通常使用 `main` |
| Tag / 标签 | 给一个固定版本起名字，例如 `v0.1.0` |
| Release / 发布 | 与标签关联的版本说明和下载附件 |
| Issue | 用户反馈问题或建议的讨论记录 |
| Pull request | 提议把一组代码修改合入项目 |

README 是仓库首页；LICENSE 表示别人能怎样使用代码；`.gitignore` 只阻止未跟踪文件被通常方式加入，并不能清除已经提交的隐私历史。`CITATION.cff` 让别人方便引用软件，不代表已经有论文或 DOI。[GitHub 引用说明](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-citation-files)

## 发布前的本地检查

先完成部署检查和离线测试。在公开源码目录执行：

```bash
python -B merge_gui.py
python -B packaging/build_workflow_diagram.py --check
python -B -m unittest discover -s tests -v
python -B packaging/audit_release.py
python -B packaging/build_release.py
```

构建脚本只收录明确文件清单，不读取你的 AF3 用户配置。产物在 `dist/`：源码 ZIP、精简运行 ZIP、逐文件清单以及压缩包 SHA-256 校验值。`dist/` 被 Git 忽略；它是 Release 附件，不需要再提交到源码历史。

检查 UI 中英文帮助、关于和许可证页面，以及新压缩包解压后的运行。真实集群验收还没有完成时，公开说明这一点，把首发标为预发布，不要写成“所有集群均可直接使用”。

原创代码已经使用 MIT 与 PKU-Gaolab, Ming-Ao Lu 署名。第三方许可必须一起保留，不能通过在 GitHub 选择另一个许可证把它们覆盖。若所属机构或合作约定对代码对外发布有要求，发布者应先核对适用约定。

## 创建仓库并推送

1. 注册并登录 GitHub。创建新仓库，名称建议 `af3-console`，简介采用 README 首段。
2. 本项目首次上传选择 **Private**，仅上传脱敏源码。改为 Public 应作为之后单独决定的操作。
3. 因为本地已有 README、许可证和忽略文件，创建空仓库时不要再勾选生成这些文件。
4. 确认终端处于独立的公开源码目录，再执行下面命令。仓库 URL 和邮箱请使用你自己真实的 GitHub 设置值。

```bash
git init -b main
git config user.name 'Ming-Ao Lu'
git config user.email 'YOUR_GITHUB_NOREPLY_EMAIL'
git add .
git status --short
git diff --cached --stat
git diff --cached
```

在继续前检查：没有 `site_config.json`、个人数据、日志、镜像、环境目录或旧压缩包；GUI 内嵌源码已由审计脚本解压扫描。上述 `git add .` **仅用于这个已经清理的公开目录**。

```bash
git commit -m 'Prepare AF3 Console 0.1.0 public release'
git remote add origin https://github.com/YOUR_GITHUB_USERNAME/af3-console.git
git push -u origin main
```

GitHub 会要求通过支持的登录方式认证。不要把 token 写进 remote URL、脚本或仓库；采用 Git 的凭据管理器或 GitHub 提供的登录流程。提交邮箱可使用 GitHub 账号设置显示的 noreply 地址，避免公开个人邮箱。[GitHub 创建仓库教程](https://docs.github.com/en/get-started/start-your-journey/creating-a-repository-for-your-project-on-github)

首次推送后打开仓库首页：确认英文 README 的流程图可见，再点中文说明检查中文版；两份 README 都引用仓库内的 SVG 图片，无需安装额外插件。可编辑的 Mermaid 图源和矢量图生成脚本随源码发布。也要查看 Actions 中首次 Linux 检查的实际结果，不能把本地通过等同于 GitHub CI 已通过。

## 后续可选：发布 v0.1.0（首次上传不执行）

1. 更新 `CITATION.cff` 中实际仓库 URL 和发布日期；没有 DOI 就不填写。
2. 核对 `af3_runtime.py` 的版本、版本说明 和验证记录；版本信息一致后重新构建。
3. 提交最终修改并推送，然后创建标签：

```bash
git tag -a v0.1.0 -m 'AF3 Console 0.1.0'
git push origin v0.1.0
```

4. 在 GitHub 的 Releases 创建对应 `v0.1.0` 的发布，粘贴版本说明；早期首发选择预发布。
5. 上传 `dist/` 中运行 ZIP、源码 ZIP、manifest 和 `SHA256SUMS.txt`。说明两个 ZIP 都不含 Python/AF3/权重/数据库。
6. 在另一处重新下载、解压并复查安装。GitHub 自动提供的 Source code ZIP 是标签对应源码；自建 runtime ZIP 是精简交付物，两者用途应写清楚。[GitHub Release 说明](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)

## 以后怎样维护

修复时先编辑可读源码，再运行构建与测试；用 commit 记录修改，在 版本说明 描述用户能感知的变化。新版本使用新标签，不覆盖已经发布的版本附件或标签。若更新了运行模块，应整套升级，避免不同版本混用。

Issues 和截图默认可能公开。要求报告者脱敏，不请用户上传全部预测结果或个人配置。自动检查会检查源码、GUI payload、许可清单和人工测试，但不能识别所有研究秘密；提交前仍需人工检查。

不必为了首发就建立网站、发布 PyPI 包或制作大安装器。清楚的 README、明确许可、可运行下载、诚实的验证记录和可用反馈渠道已经足够构成一个规范的初始项目。
