# MSA pools, backups and reuse / MSA 池、备份与复用

## 设置 1–3 个目录

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

## 新产物备份与已有文件同步

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

## 复用之前的检查

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

## English guide

In **Setup → MSA cache directory**, use **＋** to the left of the primary path to add up to two backup rows. Removing a row changes configuration only. Save the settings before checking synchronization. `HOST_MSA_DATA` is the primary pool; `MSA_BACKUP_DIRS` is a list of zero to two backups. Use distinct, non-nested absolute directories. Create backups explicitly and make them accessible from login and compute nodes. Coordinate shared access with the directory owners; rsync is not required.

New AF3 output is first completed and validated in the primary pool, then copied as a whole product to each configured backup. Native AF3 and timestamped directories retain their filenames and companion files. Already queued jobs keep their original configuration snapshot. Backup failures appear in job logs and `.msa_backup_receipts/<task-name>.json`; they do not invalidate a successful primary MSA. Repair the problem and use the manual synchronization check to retry.

Once per GUI launch, a background scan checks the saved pools for additions. Copying starts only after confirmation, with No as the default. A manual check is also available. Content hashing can take time on large or remote pools. Identical products are skipped; different same-name products remain conflicts. Existing directories are neither overwritten nor merged. Sources are checked for changes and destinations are published without replacing existing data. Missing fields, incomplete managed jobs, unstable sources, links/junctions and references outside a product are reported rather than copied. Flat legacy JSON is supported when self-contained.

Synchronization never mirrors deletions. GUI cleanup protects current and task-pinned MSA pools, their contents and enclosing directories. Only staging artifacts owned by the current failed copy may be cleaned up. Multiple copies on the same storage do not provide protection against complete device failure; use your storage service's backups when needed.

Before reuse, invalid or incomplete MSA data is rejected. Valid data with empty protein `unpairedMsa`, `pairedMsa` or `templates` triggers a **Reuse / Recompute MSA / Cancel** choice, identifying the file and empty fields; Cancel is the default. RNA checks only its applicable `unpairedMsa`. Recompute keeps the original product and uses a distinct new product; it cannot guarantee nonempty search hits. Approval applies to the checked file version. Later changes or newly generated empty products require a new decision; remote controllers stop affected inference until review. Preview does not authorize submission. CLI users explicitly choose `--empty-msa-policy reuse` or `--empty-msa-policy recompute`.

Damaged existing products also remain intact: repair planning uses a new directory while preserving the original custom input recipe. A remote controller cannot overwrite an occupied incomplete directory; it stops and asks for replanning. Invalid raw inference-only JSON must first be corrected or processed through an MSA-generating workflow.

MSA commands leave AF3's force-overwrite option disabled. If an external program creates the same directory after our check, AF3 may write a timestamped directory; all outputs are retained and the managed task can fail its expected-output check. Inspect its logs and replan. Coordinate concurrent work because unrelated scripts do not necessarily respect this application's locks.

Valid empty products may be backed up; synchronization consent does not grant inference reuse consent. Validation supports inline and external references, including gzip/xz/zstd alignments, and checks query sequences and template index consistency. It does not replace AF3's full structural parsing or establish biological quality.
