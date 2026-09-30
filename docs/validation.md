# Validation / 验收记录

## Built-in GUI command and mirror option — 2026-09-30

The existing CLI now provides `python -B af3.py install-gui`; no separate installer is distributed. Tests cover environment selection, managed-command migration, repeat installation, relocation, conflict protection, failed writes and CLI error reporting. The POSIX test invokes the real CLI from a minimal venv, then launches a synthetic GUI through PATH from an unrelated directory, including paths and arguments with spaces and shell characters. The local Windows run completed **201 tests: 199 passed, two skipped** (POSIX execution and platform-specific symlink permissions), with no failures/errors. Linux CI runs the POSIX test; the Windows result does not establish its outcome.

The user-supplied `environment_THUmirrors.yml` is retained unchanged. Its environment name and dependency constraints match `environment.yml`; only channel selection differs. Deployment/source consistency, isolated GUI startup, offline licenses, bilingual command equality and the release privacy audit passed. The mirror environment has not been solved or installed on a real Linux cluster during this review, and no download-speed claim is made. The mirrors and deployment instructions are alternatives for fresh installation; an existing working environment does not need recreation.

## Configuration import update — 2026-09-30

Setup now imports a configuration JSON for review and saves a personal default without selecting the template as its save destination. Existing personal settings are backed up before replacement. Tests cover fresh-process persistence, advanced parameters absent from the form, shared paths, invalid input, canceled selection and failed-write recovery. The local Windows/Python 3.12 run completed **195 tests: 194 passed, one platform-specific symlink test skipped, zero failures/errors**. Deployment/source equality, decoded GUI, privacy audit, fonts, bilingual About and offline licenses also passed. Real Slurm, AF3 and interactive cluster X11 execution were not performed for this update. Earlier records below describe the previous build.

Release: **0.1.0**. Review date: **2026-09-24**.

This file records local release-preparation evidence, not a claim that all cluster installations have been tested. No real research input, prediction result, remote Slurm job, or AF3 model parameter was used in the automated public checks.

## Environment

The local validation host is Windows with Python 3.12.0. Installed versions used for the offline checks: PySide6/shiboken6 6.11.1, NumPy 2.3.5, pandas 3.0.5, matplotlib 3.11.1, six 1.17.0, python-dateutil 2.9.0.post0, qtawesome 1.4.2, and zstandard 0.25.0. The independent PAE reference uses NetworkX 3.6.1. Bash syntax and shell behavior tests use Git Bash when available.

The application deployment target remains Linux + Slurm + Singularity + X11. The Linux GitHub Actions workflow passed on Ubuntu 24.04.5 using Python 3.12 and pip-installed development dependencies; see the CI evidence below. The declared Conda environment has not been solved or installed on a new Linux cluster during this preparation.

## Linux CI — 2026-09-24

[GitHub Actions run 35967066227](https://github.com/luckingclark/AF3-Console/actions/runs/35967066227) passed on commit `e4e653b3b5ba352d696f481887e292c4d85a4d03`: **191 tests passed, 0 skipped, 0 failures/errors**. GUI payload/privacy audits, workflow graphic consistency and clean source/runtime packaging also passed. The runner installed dependencies from `requirements-dev.txt` with pip; this is not a test of a fresh Conda environment or a real Slurm/AF3 deployment. Subsequent upload-finalization changes update documentation and normalize a provenance JSON's line endings; application code, dependencies and tests remain the same.

GitHub Linux CI 已实际通过：**191 项全部通过，0 跳过**，并通过源码审计、流程图一致性与打包检查。此次使用 pip 安装测试依赖，不代表 Conda 或真实集群已完成验收。

## Automated checks

The final local run passed **191 tests: 190 passed, one skipped, and zero failures or errors**. See the [machine-readable results](validation-results.json). The original baseline files retain their original hashes. The extracted algorithm implementations and ten remaining PAE functions were compared against that baseline at the syntax-tree level; their executable behavior was preserved. MSA storage behavior intentionally changes to retain native AF3 directories as described in the README.

最终本地验收：**共 191 项测试，190 项通过、1 项跳过，失败和错误均为 0**。原始发布基准文件未被修改。MSA 新行为保留 AF3 原生目录，也兼容时间戳目录与旧版平铺文件。此结果包含离线界面与模拟调度检查，不包含真实集群运行。

```bash
python -B merge_gui.py
python -B -m unittest discover -s tests -v
python -B packaging/audit_release.py
python -B packaging/build_release.py
```

Tests cover:

- Default/site/user/environment precedence, blank optional settings, relative/home paths, CLI overrides, stage-specific preflight, missing resources, and checks before scheduling.
- Container/module preparation, special-character shell paths, disabled SSD/fallback behavior, and auxiliary log paths.
- PAE graph extraction against the pre-release baseline; public tests compare artificial matrices to independent NetworkX reference results, including 24 randomized inputs.
- Asymmetry, zero/floor/cutoff boundaries, isolated nodes, queue tie/update behavior, and expected fragment windows.
- Worker stdout remaining valid JSON while diagnostic warnings go to stderr.
- MSA/cache identity, immutable plans and input assets, staged orchestration, failed-task retries, slow-path PAE scans, interface-score aggregation, and pagination with synthetic inputs.
- Native and timestamped MSA directories, unchanged companion files, old flat/hash cache reuse, relative alignment/template references, readable names and collision handling, completion records, and concurrent identity reservation.
- Actual run, pulldown and scan planning through controller/watcher MSA input generation, using synthetic UniProt responses and a simulated scheduler. Per-protein names remain independent of run labels and batch/list filenames; shared, independent and PAE scan paths retain the full-length/fragment distinction. Explicit selection also supports a directory prefix that differs from its JSON filename.
- Generated MSA Bash scripts with a simulated AF3 command: success and failure both retain the complete native output directory; failed or incomplete managed products cannot enter inference. On Windows, the shell `flock` command is mocked because Git Bash does not supply it; Python metadata-lock concurrency is tested separately. Shared-cluster shell locking and real AF3 execution still require cluster acceptance; the Linux CI uses temporary local storage and a simulated AF3 command.
- Bilingual help, offline third-party license views, dynamic input parsing, and isolated offscreen Qt settings.
- Up to three MSA pools, settings persistence and overlap rejection; complete-product backups and receipts; add-only three-way synchronization with conflict, changing-source, unreadable-path and no-clobber safeguards; cleanup protection for current and task-pinned pools.
- MSA required-field/type/query checks and template-index validation, external resource changes, compressed gzip/xz/zstd alignments, and valid empty-field reporting. Actual Qt dialogs exercise Reuse / Recompute / Cancel through the common submission entry. Recompute and resume retain original products; remote controllers reject later empty products without an applicable approval.
- Reproducible allowlisted ZIPs, per-file checksums, clean-directory CLI and runtime GUI execution, source/payload consistency, and recipients' ability to modify the LGPL module.
- Markdown links, placeholder identifiers, private-site patterns, representative credential patterns, and decoded GUI text including its outer wrapper.

Synthetic fixtures are small artificial matrices, minimal file records, and constructed amino-acid strings. No NetworkX package is needed at application runtime; install `requirements-dev.txt` to run its independent test reference. GUI/reference checks explicitly skip when their optional test dependencies are unavailable. The recorded Windows run skips one real-symlink creation test because of OS privileges; mocked symlink/junction safeguards are exercised. The real-symlink test also passed on Linux CI; shared-cluster filesystem behavior still needs deployment testing.

## Visual and privacy checks

The English and Chinese READMEs each display one self-contained SVG overview covering Run, Pulldown, and Scan's fixed-window and PAE-guided branches. The presentation layout uses system font fallbacks without external fonts, scripts or images. Both overviews were rendered and visually inspected on light and dark page backgrounds in local headless Chromium (four renders); text stays within the figure bounds. The light presentation palette is retained on either page background. Editable Mermaid sources and a deterministic standard-library SVG generator are included in the source release, with a generation-consistency check supplied for CI. The new private GitHub repository now contains both READMEs and the matching figures; the English rendered page DOM was checked for the introduction, attribution and image links. Visual rendering on GitHub has not been independently verified.

`packaging/capture_previews.py` creates six screenshots from empty task lists, placeholder paths, and isolated settings: Setup in Chinese/English and About in both languages and themes. No pre-existing screenshot is reused. The retained cat PNG has no text/EXIF metadata; its public redistribution was authorized by the contributor.

The private research-data directory is not a release input. A local-only denylist can supplement the generic public audit via `--private-denylist`; do not commit that list. Audit findings report categories and locations, not the secret values themselves. Automated pattern checks cannot prove the absence of every kind of sensitive information, so publication still includes a human review of the allowlisted text and screenshots.

## Real cluster acceptance still required / 尚待真实集群验收

Final public-directory review found 79 allowlisted source files and 47 runtime files, with no extra source files or personal configuration. Text, SVG, Mermaid source and decoded GUI audits, including the local private denylist, passed. Required documentation, original and third-party licenses, replaceable algorithm modules, citation metadata and packaging files are present. The **0.1.0 pre-release** source has been uploaded to the independent private repository, with all 79 remote file blobs verified against the local allowlist. No GitHub Release or tag was created; the outstanding deployment validation below remains visible. The actual repository URL is recorded in `CITATION.cff`; no formal Release date is claimed.

Use data you are authorized to process, keeping all inputs and output outside the repository:

1. Start the GUI over X11 and verify compute-node access to the installation, Python, container, parameters, database, and work directories.
2. Submit one small full workflow; check job IDs, exit receipts, output validity, and result display.
3. Confirm its native MSA directory and companion files remain intact. Reuse its MSA in an inference-only run, import an existing timestamped AF3 MSA JSON, and continue a completed MSA-only plan.
4. Test a small two-group screen, a fixed-window scan, and a PAE scan requiring a monomer first.
5. Check cancellation/failure visibility and retry; verify that already successful work is not needlessly submitted again.
6. If enabling optional fallback or SSD staging, validate those separately with your site administrator's resource layout.
7. On the actual shared filesystem, configure temporary test pools, produce one small MSA, confirm whole-product backups, add a separate external AF3 product, restart the UI, and verify that synchronization requires consent. Test a same-name conflict without changing either original. Check disk quotas and group permissions.
8. With an artificial empty-field fixture, test Cancel, Reuse and Recompute. Verify that the original file remains unchanged, and that new empty output stops affected inference until reviewed again. Confirm failures appear in logs and backup receipts.

This preparation does not validate GPU performance, real AF3 predictions, site-specific Slurm wrappers/account/QoS requirements, compute-node mounts, X11 over a live connection, or old Linux/glibc compatibility. Publish as an early/pre-release version and preserve these qualifications until corresponding evidence is available.
