"""Application-owned interface text. Source keys never identify scientific tasks."""
TEXT = {}


def register(english, chinese):
    TEXT[english] = (chinese, english)
    TEXT[chinese] = (chinese, english)


for _en, _zh in {
    'Add an MSA backup path (up to three paths in total)': '添加 MSA 备份路径（合计最多 3 个路径）',
    'Add MSA backup path': '添加 MSA 备份路径',
    'Remove MSA backup path': '移除 MSA 备份路径',
    'Remove this path from settings only; no files are deleted': '仅从设置中移除此路径，不删除任何文件',
    'MSA backup path; complete new outputs are copied here': 'MSA 备份路径；完整的新产物会复制到这里',
    'MSA backup; add-only synchronization': 'MSA 备份，仅同步新增完整产物',
    'Check MSA synchronization': '检查 MSA 同步',
    'MSA synchronization': 'MSA 同步',
    'MSA paths': 'MSA 路径',
    'New copies': '待新增副本',
    'Conflicts': '冲突',
    'Incomplete or unsupported data': '不完整或不支持的数据',
    'Errors': '错误',
    'Save the MSA paths before checking synchronization.': '请先保存 MSA 路径，再检查同步。',
    'Add and save at least one MSA backup path first.': '请先添加并保存至少一个 MSA 备份路径。',
    'Checking MSA pools in the background…': '正在后台检查 MSA 池…',
    'MSA pool check failed. No files were copied.': 'MSA 池检查失败，未复制文件。',
    'Conflicts: {0}; incomplete/unsupported: {1}; errors: {2}.': '冲突：{0}；不完整或不支持：{1}；错误：{2}。',
    'MSA pool check finished.': 'MSA 池检查完成。',
    'No new complete products are available to copy.': '没有可新增复制的完整产物。',
    'Copy {0} new product units ({1}) between the saved MSA paths?': '是否在已保存的 MSA 路径之间新增复制 {0} 份完整产物（{1}）？',
    'Only missing complete products are added. Existing files are never overwritten or deleted.': '仅补充缺少的完整产物，不覆盖或删除已有文件。',
    'Copying complete MSA products in the background…': '正在后台复制完整 MSA 产物…',
    'MSA synchronization stopped. Some products may already have been copied.': 'MSA 同步中断，部分产物可能已经复制完成。',
    'MSA synchronization finished: {0} copied; {1} skipped; {2} conflicts; {3} incomplete/unsupported; {4} errors.': 'MSA 同步完成：已复制 {0}；已跳过 {1}；冲突 {2}；不完整或不支持 {3}；错误 {4}。',
    'Preview found cached inputs with empty fields.': '预览发现含有空字段的缓存输入。',
    'Choose whether to reuse or regenerate these inputs when submitting.': '正式提交时需选择复用这些输入或重新生成 MSA。',
    'Cancelled. No prediction submitted.': '已取消，未提交预测任务。',
    'Existing MSA files were left unchanged.': '已有 MSA 文件保持不变。',
    'MSA inputs need your decision': '请确认 MSA 输入',
    '{0} cached inputs contain empty MSA or template fields.': '{0} 份缓存输入含有空的 MSA 或模板字段。',
    'Empty fields are valid AF3 input, but may omit information you expect. Reuse them as they are, generate MSA again in a new directory, or cancel. Existing files are kept.': '空字段符合 AF3 输入格式，但可能缺少你预期使用的信息。请选择直接复用、在新目录重新生成 MSA，或取消。已有文件会保留。',
    'Reuse these inputs': '复用这些输入',
    'Generate MSA again': '重新生成 MSA',
}.items(): register(_en, _zh)


for _en, _zh in {
    'Container runtime':'容器运行时',
    'Environment module (optional)':'环境模块（可选）',
    'Fallback GPU partition (optional)':'备用 GPU 分区（可选）',
    'Controller CPU partition (optional)':'控制任务 CPU 分区（可选）',
    'Node-local database cache (optional)':'节点本地数据库缓存（可选）',
    'AF3 container image (.sif)':'AF3 容器镜像（.sif）',
    'Python interpreter (current)':'Python 解释器（当前）',
    'Active environment overrides':'当前环境变量覆盖',
    'Environment overrides take precedence over saved settings.':'环境变量覆盖优先于保存的配置。',
    'Passed':'通过', 'Needs configuration':'需要配置', 'License text':'许可全文',
}.items(): register(_en, _zh)


for _en,_zh in {
    'Continue inference':'继续推理',
    'Preview complete (not submitted)':'预览完成（未提交）',
    'Accepted; check stages, logs and failures in Dashboard.':'已受理；请在任务看板查看阶段、日志和失败原因。',
    'Failed, exit code {0}':'失败，退出码 {0}',
    'Rendering PAE…':'正在后台绘制 PAE…',
    'Cancel analysis':'取消分析',
    'Cancelled':'已取消',
    'Search all result rows':'搜索全部结果行',
    '{0}–{1} / {2} (all results)':'{0}–{1} / {2}（全部结果）',
    'state.confirming':'正在确认完成状态',
    'type.pae':'PAE 单体准备',
    'Run feedback':'运行与反馈', 'Directory map':'目录结构与用途', 'Personal workspace':'个人工作目录',
    'Plans, logs and prediction results':'任务计划、日志与预测结果',
    'Shared alignment inputs; reused by predictions':'MSA 比对池，供各任务复用',
    'AF3 model weights':'AF3 模型权重', 'Sequences and application assets':'序列与应用缓存',
    'JAX compilation cache':'JAX 编译缓存', 'Reusable monomer inference results':'全长单体推理结果池',
    'AF3 container image':'AF3 运行容器', 'Sequence and template databases':'序列与模板数据库',
    'Total: {0} PAE domains; split into {1} fragments':'共 {0} 个PAE结构域；共切分为 {1} 个片段',
    'Red dashed boundaries mark the split fragments.':'红色虚线表示最终切分的片段边界。',
}.items(): register(_en,_zh)


for _line in """
Setup|设置
Submit|提交任务
Pulldown|互作筛选
Scan|片段扫描
Dashboard|任务监控
Results|结果分析
Help|帮助
Setup & deploy paths|设置与资源
Submit a prediction|提交预测任务
Pulldown screen|蛋白互作筛选
Scan screen|蛋白片段扫描
Help & guide|使用指南
Task|任务名称
Type|类型
Status|状态
Updated|更新时间
Progress|完成进度
Ranking|排名结果
Ready|已生成
Task name|任务名称
Switch task|切换任务
Open results|查看结果
Search task / UniProt ID / identifier|搜索任务名称、UniProt ID 或标识
All types|全部类型
All states|全部状态
All|全部
{0} tasks|{0} 个任务
MSA / inference pool|推理缓存
Output|结果目录
Output and command|运行反馈与命令 · 点击展开
Task details|任务详情 · 点击展开
Details|详情
More|更多
Refresh|刷新
Refresh list|刷新列表
View in Results|查看结果
Re-aggregate results|重新汇总结果
Retry failed tasks|重试失败任务
Copy log directory|复制日志目录
Rebuild resubmit command|重建提交命令
Fill into editor|填回输入表单
Clean batch data…|清理批次数据…
Filter:|筛选：
auto-refresh every 30s|每 30 秒自动刷新
name / type / status …|名称、类型或状态…
Queue status unavailable|暂时无法查询队列
No successful refresh yet|尚未成功查询队列
Last successful refresh: {0}|上次成功查询：{0}
Local task list could not be read|本地任务列表读取失败
My jobs: {0}|我的作业：{0}
Running {0} · Pending {1}|运行中 {0} · 排队中 {1}
MSA pool {0} · Output {1}|MSA 缓存 {0} · 结果目录 {1}
Batches: {0}|批次：{0}
Select a task to view its results.|选择任务后查看结果。
The selected task is no longer in the list. Choose another task.|所选任务已不在列表中，请选择其他任务。
No tasks yet. Submit a prediction, then refresh the list.|暂无任务。提交预测后刷新列表。
Reading results index…|正在读取结果索引…
Results are not ready yet. Check progress in Dashboard.|结果尚未生成，可在任务监控查看进度。
Interface score: mean chain-pair ipTM across A/B.|界面置信度分数：A/B 之间链对 ipTM 的均值。
Identifier|内部标识
Directory|目录
Source|来源
Working directory|工作目录
Compute resource paths|计算资源路径
Program information|程序信息
AF3 singularity image (.sif)|AF3 容器镜像（.sif）
AF3 database dir|AF3 数据库目录
Output dir (derived, editable)|结果目录
MSA data pool (derived, editable)|MSA 缓存目录
Model weights (derived, editable)|模型权重目录
Cache dir (derived, editable)|应用缓存目录
JAX compile cache (derived, editable)|JAX 编译缓存目录
Program location (fixed)|程序位置（固定）
Python (provided by installation)|Python（随安装包提供）
Resource limits · expand|资源上限（点击展开）
Save user settings|保存用户配置
Copy configuration JSON|复制配置 JSON
Check resources|检测资源
Configuration summary|配置摘要
Check resources to verify paths and permissions.|点击“检测资源”验证路径与权限。
Program and user data are independent.|程序与用户数据独立。
MSA (CPU) partition|MSA（CPU）分区
Infer (GPU) partition|推理（GPU）分区
Big-token / OOM partition|大任务 / OOM 分区
MSA max concurrent|MSA 最大并发数
Infer max concurrent|推理最大并发数
Big-token threshold|大任务 token 阈值
Max token (hard reject above)|最大 token 数（超过则拒绝）
Checking resources…|正在检测资源…
No configuration changes|配置没有变化
Could not save settings|配置保存失败
1 · Mode|1 · 选择模式
End-to-end (MSA->infer)|完整预测（MSA → 推理）
MSA only (--msa-only)|仅生成 MSA
Infer only (--infer-only)|仅推理（复用 MSA）
Raw JSON (--json)|导入 AF3 JSON
PAE domains (pae)|PAE 结构域分析
Choose end-to-end for a first prediction; use other modes to reuse data or analyse domains.|首次预测选择“完整预测”；其他模式用于复用数据或分析结构域。
2 · Expressions (one task per line; '#' = comment)|2 · 输入表达式（每行一个任务，# 为注释）
Expression syntax examples|表达式示例
Load expression file…|导入表达式文件…
Parse preview|解析输入
Preview plan (--dry-run)|预览执行计划
Preview plan|预览执行计划
Fetch seq + fragment preview (online)|获取序列与片段预览（联网）
auto (first UniProt id, e.g. P12345_n3)|留空自动命名，例如 P12345_n3
auto (e.g. P12345x13_vs_Q12345x4)|留空自动命名，例如 P12345x13_vs_Q12345x4
Group {0} · one expression per line|{0} 组 · 每行一个表达式
Load {0} file…|导入 {0} 组文件…
{0} input lines|{0} 行输入
Scan slicing parameters|片段切分参数
win — fixed sliding window|固定滑动窗口
pae — PAE domain windows|PAE 结构域窗口
Advanced options  ▸|高级选项  ▸
Advanced options  ▾|高级选项  ▾
show / hide advanced options|展开 / 收起高级选项
--name|任务名称
--mode|切分方式
--split-threshold|切分长度阈值
--win|窗口长度
--overlap|重叠长度
--min-frag|最短片段
--topk (0=all)|保留前 K 项（0 = 全部）
--self|包括组内配对
--num-seeds|种子数量
--seeds|指定种子
--partition|推理分区
--max-concurrent|最大并发数
--msa-partition|MSA 分区
--bonds|共价键
--user-ccd|自定义配体 CCD
--template-free|不使用模板
--msa-free|不使用 MSA
--force|创建新的计算尝试
--shared-msa|片段共享全长 MSA
--pae-cutoff|PAE 阈值
--pae-resolution|聚类分辨率
--pae-min-domain|最小结构域
--pae-domains-per-window|每窗口结构域数
--pae-min-frag|最短窗口
--pae-max-frag|最长窗口
auto (=threshold)|自动（使用切分阈值）
auto (=500)|自动（500）
default: 4 seeds (af3.py scan)|默认：4 个种子（Scan）
default: 1 random seed (af3.py)|默认：1 个随机种子
blank = automatic seeds; e.g. 1,2,3|留空随机生成；例如 1,2,3 或 random
blank = saved GPU resource policy|留空使用已保存的 GPU 资源策略
blank = saved concurrency limit|留空使用已保存的并发上限
blank = configured MSA partition|留空使用设置页中的 MSA 分区
none; e.g. A:145:SG->C:1:C04;...|留空不指定；例如 A:145:SG->C:1:C04;...
none (custom ligand CCD .cif path)|留空不指定（自定义配体 CCD .cif 路径）
AF3 JSON absolute path on the cluster|集群上的 AF3 JSON 文件绝对路径
JSON file|JSON 文件
Execution stage|执行阶段
Full pipeline: MSA + inference|完整流程：MSA + 推理
MSA only|仅生成 MSA
Inference only (JSON contains MSA)|仅推理（JSON 已含 MSA）
What is this?|查看参数说明
Close|关闭
Cancel|取消
Open|打开
Select|加载模型
Copy|复制
Copy all|复制全部
Copy content|复制内容
Copy path|复制路径
Copy folder path|复制目录路径
Copy file content to clipboard (paste into a local editor)|复制文件内容，可粘贴到本地编辑器
Copy the CONTAINING FOLDER path (paste into MobaXterm SFTP panel address bar)|复制所在目录，可粘贴到 MobaXterm SFTP 地址栏
Copy cell text|复制单元格
Copy selected rows (TSV)|复制选中行（TSV）
Copy absolute path|复制绝对路径
Copy spec.json path|复制任务计划路径
Search help|搜索帮助主题与内容
Copy example|复制示例
Use example|填入示例
Quick start|快速上手
Reading task summary…|正在读取任务摘要…
Please select a batch first.|请先选择一个批次。
Choose a batch with a recorded plan.|请选择有任务计划的批次
No spec|缺少计划
This batch has no spec.json.|此批次没有 spec.json。
Cannot rebuild|无法重建命令
Cannot prefill|无法填回输入
Invalid input|输入无效
Read failed|读取失败
Copy failed|复制失败
Refresh failed|刷新失败
Update failed|更新失败
Update results|更新结果
Confirm resubmit|确认重新提交
Too large|文件过大
File > 2 MB; please download it instead.|文件超过 2 MB，请下载文件。
Export failed|导出失败
HTML report written|HTML 报告已生成
Not supported|不支持此操作
No help available.|暂无说明。
Previous page|上一页
Next page|下一页
Copy selected directory|复制选中目录
View selected PAE|查看选中 PAE
Reading…|读取中…
Plotting in background…|正在后台绘图…
Interface ranking|界面置信度排名
Interface matrix|界面置信度矩阵
Hit summary|蛋白对汇总
Fragment overview|片段评分概览
Protein matrix|蛋白对置信度矩阵
PAE viewer|PAE 查看器
PAE domains…|PAE 结构域…
summary confidences|置信度摘要
All chains|全部链
all chains|全部链
ranking score|模型排序分数
disordered fraction|预测无序区域比例
Select a ranking row first.|请先选择一条排名记录。
load this model's PAE plot and confidence summary|加载此模型的 PAE 图及置信度摘要
recompute domains with these parameters and redraw|按当前参数重新分析结构域并绘图
overlay PAE-domain fragment boundaries on the PAE plot; for complexes each protein chain's own diagonal block is segmented independently|在 PAE 图上叠加片段边界；复合物按各蛋白链的对角块独立分域
(press Select to load)|（点击“加载模型”查看）
No *_model.cif yet (job not finished).|尚无模型文件，任务可能仍在运行。
select a model first|请先加载模型
no protein chain in this model|此模型没有蛋白链
Scan failed|扫描失败
Nothing to clean|没有可清理的数据
No output dir or MSA products found for this batch.|未找到此批次的可清理产物。
Delete selected|删除选中数据
Confirm delete|确认删除
Clean failed|清理失败
Partial clean|部分清理完成
both (AND)|同时满足（AND）
either (OR)|任意满足（OR）
Parse / command / output (double-click preview)|运行反馈与命令（双击放大）
Fetching sequences / fragmenting…|正在获取序列并切分片段…
Computing task plan…|正在计算任务计划…
Enter A/B inputs to see the task plan|填写 A/B 输入后显示任务计划
Use Preview plan for an exact plan of a large input.|大列表请点击“预览执行计划”获取精确计划。
Needs sequences or PAE data; use Preview plan for an exact plan.|需读取序列或 PAE；点击“预览执行计划”查看精确计划。
Preview complete (nothing submitted)|预览完成（未提交）
Accepted. Check Dashboard for stages, logs and failures.|已受理；请在任务监控查看阶段、日志和失败原因。
Please enter a JSON file path first.|请先填写 JSON 文件路径
Switch language / 切换语言|切换语言 / Switch language
Switch theme / 切换主题|切换主题 / Switch theme
font size (global)|调整全局字号
expression syntax cheat sheet (same content as the ? next to Expressions)|查看表达式语法与示例
scanning batch data sizes…|正在统计批次数据大小…
Output: {0} · Accepted cluster jobs continue after this window closes.|结果：{0} · 已受理的集群任务在关闭窗口后继续运行。
Accessible|可访问
Missing or inaccessible|缺失或无权限
Not found (not required for a local preview)|未找到（本机预览不需要）
state.unknown|未知
state.preparing|准备中
state.submitted|已提交
state.msa_submitted|MSA 已提交
state.msa_running|MSA 运行中
state.msa_queued|MSA 排队中
state.msa_checking|MSA 已离队，等待控制器核查
state.msa_ready|MSA 已就绪
state.scheduler_unavailable|控制器调度查询失败
state.controller_stopped|控制器已停止，请检查日志
state.legacy_controller|旧控制器，查询不兼容
state.infer_queued|推理排队中
state.msa_done|MSA 已完成
state.infer_submitted|推理已提交
state.infer_running|推理运行中
state.running|运行中
state.queued|排队中
state.waiting|等待中
state.pending|排队中
state.done|已完成
state.succeeded|已完成
state.failed|失败
state.partial_failed|部分失败
state.cancelled|已取消
type.run|预测
type.scan|片段扫描
type.pulldown|互作筛选
type.pae|PAE 分析
""".strip().splitlines():
    _en, _zh = _line.split("|", 1)
    register(_en, _zh)

# Enum keys are fixed, while their English labels remain human-readable.
for _key in list(TEXT):
    if _key.startswith(("state.", "type.")):
        _zh = TEXT[_key][0]
        _en = _key.split(".", 1)[1].replace("_", " ").capitalize()
        TEXT[_key] = (_zh, _en)

for _en, _zh in {
    'Apply':'应用', 'Light':'浅色', 'Dark':'深色', 'chains:':'链：',
    'computing…':'正在计算…', 'ipTM <':'ipTM <', 'rank >':'排名 >',
    'AlphaFold 3 unified submission · {0}@{1}':'AlphaFold 3 任务工作台 · {0}@{1}',
    'A —   B —   pairs —   models —':'A —   B —   配对 —   模型 —',
    'A %s   B %s   tasks %s   seeds/task %s   samples %s':'A %s   B %s   任务 %s   每任务种子 %s   样本 %s',
    '%s–%s / %s（排序当前页）':'%s–%s / %s（排序当前页）',
    'Read failed: ':'读取失败: ', 'Plot failed: ':'绘图失败: ', 'Invalid plan: ':'计划无效: ',
    'UI update failed: ':'界面更新失败: ',
    'overlap must be smaller than win':'参数错误：overlap 必须小于 win',
    'Missing reliable chain mapping; check input and token metadata before domain analysis.':'缺少可靠链映射，无法进行分域；请检查输入和 token 元数据',
    'No <name>_confidences.json next to this cif.':'模型旁缺少对应的 confidences.json。',
    'no <name>_summary_confidences.json next to this cif (older batch?).':'模型旁缺少置信度摘要，可能是旧版产物。',
    '(no recognizable metrics in summary json)':'（摘要中没有可识别的指标）',
    'matplotlib not installed':'未安装 matplotlib',
    'matplotlib not installed — cannot draw the PAE plot.':'未安装 matplotlib，无法绘制 PAE。',
    'PAE load failed: {0}':'PAE 加载失败：{0}', 'PAE plot failed: {0}':'PAE 绘图失败：{0}',
    'read failed: {0}':'读取失败：{0}', 'failed: {0}':'失败：{0}',
    'A-side profile read failed: {0}':'A 侧片段评分概览读取失败：{0}',
    'A-side: no usable rows in iptm_profile.csv (old batch? use Dashboard → Update to regenerate)':'A 侧没有可用的片段评分概览，请在任务监控的“更多 → 重新汇总结果”中生成。',
    'loading PAE of {0} …':'正在加载 {0} 的 PAE…',
    'loading plot library (matplotlib)… — re-renders automatically when ready; if this stays, matplotlib is not installed. Use the CSV downloads below.':'正在加载绘图库，完成后自动显示；若持续不可用，可先下载 CSV。',
    'plot/table libs failed to load: {0}':'绘图或表格依赖加载失败：{0}',
    'no model entry for {0}+{1} (not finished?)':'尚未找到 {0}+{1} 的模型，可能还未完成。',
    'auto (={0})':'自动（={0}）', 'font size: {0} pt':'字号：{0} pt', 'theme: {0}':'主题：{0}',
    'refreshing {0}…':'正在汇总 {0}…', '{0}: results updated':'{0}：结果已更新',
    'cleaned: {0} freed':'已清理，释放 {0}', 'Clean batch data — {0}':'清理批次数据 — {0}',
    'Select what to delete. This cannot be undone.\nDelete happens on the cluster, immediately.':'选择需要删除的数据。操作在集群上立即执行，无法撤销。',
    'Batch: {0}\nReally delete:\n{1}\n\nThis cannot be undone.':'批次：{0}\n确认删除：\n{1}\n\n此操作无法撤销。',
    'Re-aggregate results for batch:\n  {0}\n\nThis updates ranking.csv / iptm_matrix.csv (+ scan reports), preserves result paths, and regenerates results_report.html.\nIt does NOT rerun msa / infer. Continue?':'重新汇总批次：\n{0}\n\n更新排名、矩阵和报告，保留结果路径，不重新运行 MSA 或推理。是否继续？',
    'seed prune   (computing…)\n{0}':'清理采样文件（正在统计…）\n{0}',
    'seed prune   (no matching pairs)\n{0}':'清理采样文件（没有匹配的配对）\n{0}',
    'seed prune   (scan failed: {0})\n{1}':'清理采样文件（统计失败：{0}）\n{1}',
    'seed prune   (tick to compute affected size)\n{0}':'清理采样文件（勾选后统计空间）\n{0}',
    'seed prune   ({0}, {1} files in {2} pairs)\n{3}':'清理采样文件（{0}，{2} 个配对共 {1} 个文件）\n{3}',
    'delete the seed-*/ subdirectories of matching pairs (the bulkiest files); top-level model.cif / confidences / summary / data.json / ranking_scores.csv are kept':'删除匹配配对的 seed-*/ 子目录；保留顶层模型、置信度、摘要、输入和排名文件。',
    '{0}   ({1}, {2} files)\n{3}':'{0}（{1}，{2} 个文件）\n{3}',
    '⚠ MSA is a SHARED pool: other batches using the same proteins will have to recompute those MSAs.':'MSA 属于共享池；其他批次可能依赖这些比对产物。',
    'spec read failed':'任务计划读取失败',
    "Batch type '{0}' cannot be prefilled.":'无法填回类型为 {0} 的批次。',
    '{0}\n\nCommand:\n{1}\n\nRun it?':'{0}\n\n命令：\n{1}\n\n是否执行？',
    '{0}\n\nFolder path copied — paste into MobaXterm SFTP panel and drag results_report.html to your laptop.':'{0}\n\n目录路径已复制，可粘贴到 MobaXterm SFTP 面板下载 results_report.html。',
    '{0} copied — paste locally (Ctrl+V) or into MobaXterm SFTP panel: {1}':'已复制{0}，可在本地或 MobaXterm SFTP 面板粘贴：{1}',
    '{0}: old batch without recorded original input — prefilled from processed pairs':'{0}：旧批次未记录原始输入，已根据处理后的配对填回',
    ' (fragment-level approximation for scan)':'（Scan 为片段级近似输入）',
    '; please review before submitting.':'；请核对后再提交。',
    '{0}: original input filled into the {1} page.':'{0}：原始输入已填回 {1} 页面。',
    '{0}: {1} domains':'{0}：{1} 个结构域',
    'Saved %s settings: %s':'已保存 %s 项配置：%s',
    'Path':'路径', 'Folder path':'目录路径', 'File content':'文件内容', 'Cell text':'单元格', 'Config block':'配置',
    'YES (check structure)':'是（请检查结构）',
    'clash':'原子碰撞', 'no':'否', 'chain {0} {1}':'链 {0} {1}',
    'A-side fragment profile':'A 侧片段评分概览', 'B-side fragment profile (computed from ranking.csv)':'B 侧片段评分概览（来自排名表）',
    'A-side profile':'A 侧概览', 'B-side profile':'B 侧概览',
}.items(): register(_en, _zh)

# Compatibility keys used by old UI source, all routed to the same catalog entry.
for _old,_new in {
    '大列表请点击 Preview plan 获取精确计划':'Use Preview plan for an exact plan of a large input.',
    '需读取序列或 PAE；点击 Preview plan 查看精确计划':'Needs sequences or PAE data; use Preview plan for an exact plan.',
    '程序与用户数据独立':'Program and user data are independent.',
    '按自己的集群配置资源上限。':'Resource limits · expand',
}.items(): TEXT[_old] = TEXT[_new]
TEXT['%s–%s / %s（排序当前页）'] = ('%s–%s / %s（排序当前页）','%s–%s / %s (sort current page)')
register('Advanced options','高级选项')
for _key,_english in {
    '--name':'Task name','--mode':'Slicing mode','--split-threshold':'Split above (aa)',
    '--win':'Window (aa)','--overlap':'Overlap (aa)','--min-frag':'Min fragment (aa)',
    '--topk (0=all)':'Top K (0 = all)','--self':'Include within-group pairs',
    '--num-seeds':'Seed count','--seeds':'Explicit seeds','--partition':'Inference partition',
    '--max-concurrent':'Max concurrency','--msa-partition':'MSA partition','--bonds':'Covalent bonds',
    '--user-ccd':'Custom CCD','--template-free':'Disable templates','--msa-free':'Disable MSA',
    '--force':'New computation attempt','--shared-msa':'Share full-length MSA',
    '--pae-cutoff':'PAE cutoff (Å)','--pae-resolution':'Resolution','--pae-min-domain':'Min domain',
    '--pae-domains-per-window':'Domains per window','--pae-min-frag':'Min window','--pae-max-frag':'Max window',
}.items(): TEXT[_key] = (TEXT[_key][0],_english)
for _en,_zh in {
    'Workspace':'工作空间', 'Configuration file':'配置文件', 'Installed core':'当前核心程序',
    'Configure resource limits for your cluster.':'请按自己的集群设置资源上限。',
    'New submissions pin their plan, inputs and runtime version.':'新任务会固定保存计划、输入和运行版本。',
    'Program information':'程序信息', 'Read':'读取', 'Write':'写入',
    'Load list':'导入清单','Load expressions':'导入表达式',
    'Text files (*.txt *.tsv *.csv);;All files (*)':'文本文件 (*.txt *.tsv *.csv);;所有文件 (*)',
    'File content':'文件内容', 'best ipTM':'最佳 ipTM',
    'Task model and confidence':'模型与置信度',
}.items(): register(_en,_zh)

TEXT["state.confirming"] = ("正在确认完成状态", "Confirming completion")
TEXT["type.pae"] = ("PAE 单体准备", "PAE preparation")
register('Minimal', '简约')
register("The selected result has no model yet.", "所选结果尚无模型。")
for _en, _zh in {
    'Open feedback details':'展开反馈…', 'Feedback details':'反馈详情',
    'No action yet.':'尚未执行操作。', 'Fill in the form, then preview the plan.':'填写表单后，可先预览执行计划。',
    'Recent command output; full task logs are available in Dashboard.':'显示最近的命令输出；任务完整日志可从任务监控查看。',
    'No prediction submitted.':'本次未提交预测任务。', 'Sequence retrieval can take a while.':'正在获取序列，请稍候。',
    'Preview failed.':'预览失败。', 'Open details, fix the reported issue, then retry.':'展开反馈查看原因，修正后再重试。',
    'Fragment preview complete.':'片段预览完成。',
    'A: {0} inputs → {1} fragments; B: {2} inputs → {3} fragments':'A 组：{0} 条输入 → {1} 个片段；B 组：{2} 条输入 → {3} 个片段',
    'Review fragment details, then preview the execution plan.':'核对片段明细后，再预览执行计划。',
    'Checking the execution plan…':'正在检查执行计划…', 'Preparing the operation…':'正在准备操作…',
    'Preview does not submit jobs.':'预览不会提交作业。',
    'Wait for confirmation; command success is not prediction completion.':'等待操作结果；命令成功不代表预测已完成。',
    'Task: {0}':'任务：{0}', 'Attention: {0}':'请注意：{0}', 'Latest: {0}':'最新反馈：{0}',
    'Checking inputs and available data…':'正在检查输入与已有数据…', 'Operation failed.':'操作失败。',
    'Command completed; check task status in Dashboard.':'命令执行完成；任务状态请到任务监控查看。',
    'Review the plan and any warnings before submitting.':'请核对计划与提示，确认后再提交。',
    'Prediction progress and failures are shown in Dashboard.':'预测进度与失败原因请在任务监控查看。',
    'No summary tables yet. Check PAE viewer for available models.':'尚无结果汇总表；可在 PAE 查看器中检查已有模型。',
    'No input to parse.':'尚未填写待检查的输入。', 'Expression check complete.':'表达式检查完成。',
    'Checked {0} of {1} inputs; {2} errors.':'已检查 {0}/{1} 条输入；发现 {2} 项错误。',
    'Syntax only: sequences not fetched; no prediction submitted.':'仅检查语法：未获取序列，未提交预测。',
    'Preview the execution plan to check data and task counts.':'下一步预览执行计划，核对数据与任务数量。',
}.items(): register(_en,_zh)
