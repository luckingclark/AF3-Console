"""Offline bilingual guides and parameter explanations, sharing one content source."""
import html
from pathlib import Path
import af3_runtime as R

EXAMPLES = dict(single='P12345', complex='P12345+Q12345', fragment='P12345:trunc=1-300',
                ligand='P12345+l:ATP', copies='P12345x2+Q12345')

# key: title zh/en, explanation zh/en. Scientific defaults stay in af3.py.
PARAMETERS = {}
for line in '''
mode|运行模式|Execution mode|首次使用选完整预测。仅 MSA 准备比对缓存；仅推理要求匹配 MSA 已就绪。JSON 导入 AF3 输入后单独选择阶段。PAE 分析单蛋白结构域。|Use end-to-end for a first prediction. MSA-only prepares alignments; inference-only requires matching MSA. JSON accepts AF3 input with a separate stage choice. PAE analyses one protein's domains.
name|任务名称|Task name|填写易识别的名称，如 P12345_example；留空自动命名。目录中的计算身份后缀用于区分输入、种子和设置。恢复原批次请用任务监控中的重试。|Use a recognizable name such as P12345_example, or leave blank for automatic naming. Folder suffixes distinguish inputs, seeds and settings. Use Dashboard retry to continue an original batch.
screen_ab|A / B 组|Groups A / B|每行一个侧：单蛋白或 + 连接的复合物。程序计算 A × B 组合；xN 表示拷贝数。先检查输入，再预览实际任务数。|One side per line: a protein or a complex joined by +. The program computes A × B combinations; xN means copy count. Inspect input, then preview task counts.
load_expr|导入文件|Import inputs|导入文本、TSV 或 CSV 后先检查编辑器内容。每行一个表达式，# 开头是注释。文件必须在运行 GUI 的集群上可读。|Inspect editor contents after importing text, TSV or CSV. One expression per line; # starts a comment. Files must be readable on the cluster running the GUI.
topk|保留前 K 项|Top K|汇总时保留前 K 个结果；0 表示全部。它不是 GPU 作业数量限制，实际计算量以预览为准。|Keep top K aggregated results; 0 means all. This is not a GPU job limit; preview the actual computation count.
self_pairs|组内配对|Within-group pairs|在 A × B 外追加 A × A 和 B × B。最终去重后的数量以预览计划为准。|Add A × A and B × B to A × B. Preview the final deduplicated task count.
scan_mode|切分方式|Slicing mode|固定窗口按长度和重叠切片；PAE 窗口按全长单体的预测结构域切片。缺少 PAE 时先准备单体预测，等待时间会增加。|Fixed windows use length and overlap; PAE windows use full-length monomer domains. Missing PAE requires monomer preparation first and adds waiting time.
win|窗口长度 --win|Window length --win|固定窗口长度，单位为氨基酸。末端片段还受最短片段限制。|Fixed-window length in amino acids; terminal fragments also depend on the minimum-fragment setting.
overlap|重叠长度 --overlap|Overlap --overlap|相邻窗口共享的残基数，必须小于窗口长度。更大重叠通常产生更多计算任务。|Residues shared by neighboring windows; must be smaller than window length. Larger overlap usually creates more tasks.
min_frag|最短片段 --min-frag|Minimum fragment --min-frag|控制很短的尾部片段；修改后看片段预览，确认关注区域的覆盖。|Controls very short terminal fragments. Inspect fragment coverage after changing it.
threshold|切分阈值|Split threshold|蛋白超过此长度才切分；DNA、RNA 和配体保持完整。|Only proteins longer than this threshold are sliced. DNA, RNA and ligands remain whole.
pae_cutoff|PAE 阈值|PAE cutoff|PAE 分域连边阈值，单位 Å；它不是判断互作命中的阈值。|PAE graph connection threshold in Å; not an interaction-hit cutoff.
pae_resolution|聚类分辨率|Clustering resolution|控制 PAE 分域的细分程度。修改后比较边界预览，不能只看结构域数量。|Controls PAE domain granularity. Compare boundary previews, not only domain counts.
pae_min_domain|最小结构域|Minimum domain|保留结构域的最小残基数；与预测窗口的最短长度不同。|Minimum residues in retained domains; distinct from minimum prediction-window length.
pae_domains_per_window|每窗口结构域数|Domains per window|把结构域组合成预测窗口；最终窗口也受最短和最长长度限制。|Combines domains into prediction windows, subject to minimum and maximum lengths.
pae_min_frag|最短 PAE 窗口|Minimum PAE window|PAE 分域后组装窗口的最短长度，不改变原始 PAE 精度。|Minimum assembled window length after PAE analysis; does not change original PAE precision.
pae_max_frag|PAE 窗口目标上限|PAE window length target|尝试细分过大结构域时的目标长度；自动值随当前模式确定。最终合并后的片段可能更长，须检查预览。|Target length when attempting to split oversized domains; Auto depends on the current mode. Final merged fragments may be longer; inspect the preview.
shared_msa|共享全长 MSA|Share full-length MSA|Scan 从全长比对按坐标裁剪复用，减少重复搜索。父序列和坐标身份均保留。|Scan crops full-length alignments by coordinates to reduce repeated searches. Parent-sequence and coordinate identities are retained.
num_seeds|种子数量|Number of seeds|每任务随机种子数量；留空使用模式默认值。更多种子增加计算量。实际种子写入计划，重试时保留。|Seeds per task; blank uses the mode default. More seeds increase work. Actual seeds are saved in the plan and preserved by retry.
seeds|指定种子|Explicit seeds|例如 1,2,3，用于可复现比较。改变 GUI 设置不影响已经提交的任务。|For example 1,2,3, for reproducible comparisons. GUI setting changes do not alter submitted tasks.
partition|推理分区|Inference partition|留空使用保存的资源策略。覆盖前确认分区及账号权限。|Blank uses the saved resource policy. Verify the partition and account permissions before overriding.
msa_partition|MSA 分区|MSA partition|留空使用设置页中保存的 MSA 分区，只影响比对阶段。|Blank uses the MSA partition saved in Setup; affects the alignment stage only.
max_conc|最大并发数|Maximum concurrency|并发上限应按集群配额设置。增加并发不保证更快，还会受队列配额限制。|Set concurrency limits for your cluster quotas. More concurrency does not guarantee faster completion and is constrained by queue quotas.
template_free|禁用模板|Disable templates|不使用结构模板，不代表禁用 MSA；此设置参与计算身份。|Disables structural templates, not MSA. This setting participates in computation identity.
msa_free|禁用 MSA|Disable MSA|使用无 MSA 的科学输入，与“仅推理”不同。除非有意比较，使用默认完整流程。|Uses MSA-free scientific input, different from inference-only. Use the default pipeline unless making an intentional comparison.
force|新计算尝试 --force|New attempt --force|创建新的计算尝试，不用于恢复原批次。保留输入、种子并重试失败项，请使用任务监控中的“重试失败任务”。|Creates a new computation attempt, not a resume. To preserve inputs and seeds and retry failed work, choose Retry failed tasks in Dashboard.
bonds|共价键|Covalent bonds|例如 A:145:SG->C:1:C04，多条用分号分隔。链、残基和原子编号需与输入一致。|For example A:145:SG->C:1:C04; separate bonds with semicolons. Chain, residue and atom identifiers must match the input.
user_ccd|自定义配体 CCD|Custom ligand CCD|提供集群可读的 .cif 路径。文件内容随计划固定保存，后续源文件编辑不改变已提交任务。|Provide a cluster-readable .cif path. Content is pinned with the plan; subsequent source edits do not change submitted work.
adv_options|高级选项|Advanced options|先根据自己的集群完成配置。按实验需要修改种子、模板或输入资产，提交前预览核对。|Configure resource settings for your cluster first. Change seeds, templates or input assets as needed and preview before submission.
setup_host_base|工作目录|Working directory|自己的集群可写目录；未手动覆盖的派生路径随它联动。|Your writable cluster workspace; derived paths follow it unless manually overridden.
setup_host_sif|容器镜像|Container image|集群可读的 AlphaFold 3 .sif 文件。|Cluster-readable AlphaFold 3 .sif file.
setup_host_db_source|数据库|Databases|现有 AF3 数据库目录；GUI 安装包不包含数据库。|Existing AF3 database directory; databases are not included in the GUI installer.
setup_host_models|模型权重|Model weights|可以使用共享目录，账号需要读取权限。|May use a shared directory; your account needs read access.
setup_host_output|结果目录|Output directory|自己的可写目录，计算节点也必须可见。|Your writable directory, also visible to compute nodes.
setup_host_msa_data|MSA 池与备份|MSA pool and backups|第一个路径是主 MSA 池。点击左侧 + 可添加至多两个备份路径；完整的新产物会复制到备份。每次启动检查一次新增产物，经确认才互相同步；只新增，不覆盖或删除。移除路径框仅修改设置。|The first path is the primary MSA pool. Use + on its left for up to two backups; complete new products are copied to backups. Each launch checks for additions and asks before mutual synchronization. It only adds products, without overwrites or deletions. Removing a field only changes settings.
setup_host_cache|应用缓存|Application cache|序列缓存和固定输入资产，需要写权限。离线序列库放在此目录的 uniprot/uniprot.sqlite3；程序自动读取，不会拆成海量小文件。|Sequence cache and pinned input assets; needs write access. An offline sequence index at uniprot/uniprot.sqlite3 is used automatically without creating per-sequence files.
setup_host_uniprot_shared|共享 UniProt 序列|Shared UniProt sequences|可选，只读。填写直接包含 uniprot.sqlite3 或 ID.seq 文件的文件夹。先查个人序列缓存和索引，再查共享目录，最后联网。不会修改或删除共享文件，也不会共享其他应用缓存。离线库只提供序列，不替代 MSA。|Optional, read-only. Select the folder directly containing uniprot.sqlite3 or ID.seq files. Personal sequences and index are checked first, then this shared folder, then the network. Shared files are never modified or deleted; other application caches remain personal. This supplies sequences, not MSAs.
setup_host_jax_cache|JAX 缓存|JAX cache|保存编译产物，可共享但需要读写权限。|Compilation artifacts; may be shared with read/write access.
setup_af3_py|程序位置|Program location|只读显示当前核心路径，不随工作目录移动。|Read-only current core path; independent of workspace changes.
setup_python|解释器|Interpreter|此处显示当前 Python 解释器。源码安装需要先安装依赖；程序包是否包含解释器以发行说明为准。|Shows the running Python interpreter. Source installations need dependencies; release notes state whether a binary package includes Python.
'''.strip().splitlines():
    key, *values = line.split('|'); PARAMETERS[key] = tuple(values)
for key, zh, en in [('msa_partition','MSA 分区','MSA partition'),('inf_partition','推理分区','Inference partition'),
                   ('inf_fallback_partition','OOM 分区','OOM partition'),('msa_max_concurrent','MSA 并发','MSA concurrency'),
                   ('inf_max_concurrent','推理并发','Inference concurrency'),('inf_big_token','大任务阈值','Large-task threshold'),
                   ('inf_max_token','最大 token 数','Maximum tokens'),('inf_buckets','编译桶','Compilation buckets')]:
    PARAMETERS['setup_'+key] = (zh,en,'请根据集群分区、配额和硬件设置参数，并从小任务开始验证。','Choose settings for your partitions, quotas and hardware, then validate with a small task.')

PARAMETERS.update({
    'setup_container_runtime': ('容器运行时', 'Container runtime',
        '填写 singularity 或 apptainer 命令名，也可提供可执行文件的绝对路径。请填写一个命令，不要附加 shell 参数。',
        'Enter singularity or apptainer, or the absolute path of the executable. Supply one command without shell arguments.'),
    'setup_container_module': ('环境模块（可选）', 'Environment module (optional)',
        '若集群通过 module load 提供容器运行时，填写模块名，如 apptainer/1.3。已经可直接使用运行时则留空。',
        'If the cluster provides the runtime through module load, enter its module name, such as apptainer/1.3. Leave empty if the runtime is already available.'),
    'setup_host_ssd_cache': ('节点本地数据库缓存（可选）', 'Node-local database cache (optional)',
        '可留空以直接使用共享数据库。启用前确认各节点的缓存路径、容量和集群政策；此路径不是个人结果目录。',
        'Leave empty to read the shared databases directly. Before enabling, verify cache paths, capacity and cluster policy on compute nodes; this is not your results directory.'),
    'setup_aux_partition': ('控制任务 CPU 分区（可选）', 'Controller CPU partition (optional)',
        '为控制器和汇总任务填写 CPU 分区；留空使用 MSA 分区。',
        'CPU partition for controller and aggregation jobs; leave empty to use the MSA partition.'),
    'setup_inf_fallback_partition': ('备用 GPU 分区（可选）', 'Fallback GPU partition (optional)',
        '较大任务或显存不足重试使用的 GPU 分区。留空表示不切换分区，请根据实际硬件验证输入规模。',
        'GPU partition for larger inputs or an out-of-memory retry. Leave empty to avoid switching partitions; validate input sizes against your hardware.'),
})


PAGE_INTRO = {k:(z,e) for k,z,e in [
    ('setup','配置目录及资源。','Configure paths and resources.'),('submit','解析、预览，再提交。','Parse, preview, then submit.'),
    ('pulldown','完整输入 A × B 筛选。','Screen complete A × B inputs.'),('scan','切片后进行互作筛选。','Slice, then screen interactions.'),
    ('dashboard','查看阶段与失败原因。','Inspect stages and failures.'),('results','查看排名与 PAE。','Inspect ranking and PAE.'),('help','按步骤完成第一次预测。','Follow your first prediction step by step.') ]}
UI_HINTS = {
    'submit_autoname':('名称可留空；每行一个任务。先预览，再提交。','Name is optional; one task per line. Preview before submitting.'),
    'screen_autoname':('每行一个侧，进行 A × B 筛选；失败后从任务监控重试。','One side per line, screened as A × B; retry failed work from Dashboard.'),
    'struct_hint':('选择排名中的模型，点击“加载模型”查看 PAE 和置信度。','Choose a ranked model and click Select for PAE and confidence.'),
    'pae_idle':('选择模型后点击“加载模型”。','Choose a model, then click Select.'),
    'matrix_hover':('悬停查看 A / B、ipTM 和排名。','Hover for A / B, ipTM and rank.'),
    'bar_hover':('悬停查看片段、最佳分数和配对对象。','Hover for fragment, best score and partner.'),
    'seed_warn':('⚠ 表示种子间波动较大，请检查各次采样一致性。','⚠ indicates seed variation; inspect consistency between samples.'),
}


EXPRESSION_REFERENCE = ("表达式语法(每行一个任务,# 开头为注释)\n\n【基本形式】\n  P12345                      UniProt accession,单体\n  P12345x6                    同源多聚体(xN = N 个拷贝)\n  P12345+Q12345               复合物(+ 连接多个实体)\n\n【实体类型(前缀区分)】\n  P12345                      蛋白:UniProt 号(提交时自动抓序列)\n  p:<synthetic_sequence>           蛋白:p: + 裸序列\n  d:ACGTACGT                  DNA 序列\n  r:ACGUACGU                  RNA 序列\n  l:ATP                       配体:CCD 三字码(ATP、NAG、FUC ...)\n  l:NAG,FUC                   多个配体(逗号分隔,如糖链)\n  l:CC(=O)Oc1ccccc1C(=O)O     配体:SMILES 表达式\n  l:ATPx2                     配体也可 xN 指定拷贝数\n\n【修饰与截短(: 追加,可叠加)】\n  P12345:trunc=1-300          截短,只取 1-300 位(独立做 MSA)\n  P12345:mod=SEP@128,TPO@216  PTM 修饰(CCD 码@残基位)\n  p:<synthetic_sequence>:name=myfrag        裸序列起名(默认按内容命名)\n\n【组合写法(截短 / 拷贝数 / 复合物 / 配体自由叠加)】\n  P12345x6:trunc=1-200               # 同源六聚体,且每条链只取 1-200\n  P12345:trunc=1-300+Q12345          # 复合物:只截短其中一条\n  P12345+Q12345x2                    # 三聚体:B 侧两拷贝\n  P12345:mod=SEP@128+Q12345          # 复合物:一条带 PTM\n  P12345x2+d:ACGTACGT                # 蛋白二聚体 + DNA\n  P12345+l:ATPx2+l:MG                # 蛋白 + 2 个 ATP + 镁离子\n  p:<synthetic_sequence>:name=frag1+P12345   # 裸序列片段 + UniProt 蛋白\n\n【完整例子(与命令行 af3.py run 完全一致)】\n  P12345                                    # 单体\n  P12345x6                                  # 同源六聚体\n  P12345+Q12345                             # 蛋白复合物\n  P12345+d:ACGTACGT+l:ATPx2                 # 蛋白 + DNA + 2 个 ATP\n  P12345+l:NAG,FUC                          # 蛋白 + 糖链\n  P12345+l:CC(=O)Oc1ccccc1C(=O)O            # 蛋白 + 阿司匹林(SMILES)\n  P12345:mod=SEP@128,TPO@216                # 磷酸化修饰\n\n【要点】\n  · 每行 = 一个独立任务;多行会打包成任务文件批量提交\n  · 共价键在下方 --bonds 里写:'A:145:SG->C:1:C04'\n  · copies/配体/PTM/bonds/seeds 不影响 MSA,在 infer 阶段注入\n  · 更高级需求:Mode 选 Raw JSON,直接提交手写 AF3 JSON\n", "Expression syntax (one task per line, '#' = comment)\n\n[Basic forms]\n  P12345                      UniProt accession, monomer\n  P12345x6                    homo-oligomer (xN = N copies)\n  P12345+Q12345               complex ('+' joins entities)\n\n[Entity types (distinguished by prefix)]\n  P12345                      protein: UniProt ID (sequence fetched on submit)\n  p:<synthetic_sequence>           protein: p: + raw sequence\n  d:ACGTACGT                  DNA sequence\n  r:ACGUACGU                  RNA sequence\n  l:ATP                       ligand: CCD three-letter code (ATP, NAG, FUC ...)\n  l:NAG,FUC                   several ligands (comma-separated, e.g. a glycan)\n  l:CC(=O)Oc1ccccc1C(=O)O     ligand: SMILES string\n  l:ATPx2                     ligands also accept xN copy numbers\n\n[Modifications & truncation (append with ':', stackable)]\n  P12345:trunc=1-300          truncation, residues 1-300 only (separate MSA)\n  P12345:mod=SEP@128,TPO@216  PTM (CCD code @ residue number)\n  p:<synthetic_sequence>:name=myfrag        name a raw-sequence fragment (default: by content)\n\n[Combinations (truncation / copies / complex / ligand stack freely)]\n  P12345x6:trunc=1-200               # homo-hexamer, each chain 1-200 only\n  P12345:trunc=1-300+Q12345          # complex: truncate just one chain\n  P12345+Q12345x2                    # trimer: two copies of the B side\n  P12345:mod=SEP@128+Q12345          # complex: one chain carries a PTM\n  P12345x2+d:ACGTACGT                # protein dimer + DNA\n  P12345+l:ATPx2+l:MG                # protein + 2 ATP + magnesium\n  p:<synthetic_sequence>:name=frag1+P12345   # raw-sequence fragment + UniProt protein\n\n[Full examples (identical to the CLI af3.py run)]\n  P12345                                    # monomer\n  P12345x6                                  # homo-hexamer\n  P12345+Q12345                             # protein complex\n  P12345+d:ACGTACGT+l:ATPx2                 # protein + DNA + 2 ATP\n  P12345+l:NAG,FUC                          # protein + glycan\n  P12345+l:CC(=O)Oc1ccccc1C(=O)O            # protein + aspirin (SMILES)\n  P12345:mod=SEP@128,TPO@216                # phosphorylation\n\n[Key points]\n  · each line = one independent task; multiple lines are packed into a task file\n  · covalent bonds go in --bonds below: 'A:145:SG->C:1:C04'\n  · copies / ligands / PTM / bonds / seeds do not affect MSA; injected at inference\n  · advanced needs: choose Mode = Raw JSON and submit a hand-written AF3 JSON\n")

# The guide uses native Qt rich text. Topic structure follows the subject,
# rather than forcing every article into the same short checklist.
import re


def _document(body, palette=None):
    p = palette or dict(ink='#242321', primary='#a9583e', surface_soft='#f5f0e8', muted='#6c6a64', hairline='#e6dfd8')
    return '''<html><head><style>
    body { color:%s; }
    h1 { font-size:140%%; font-weight:600; margin-top:0; margin-bottom:14px; }
    h2 { font-size:113%%; font-weight:600; margin-top:22px; margin-bottom:8px; }
    h3 { font-size:100%%; font-weight:600; margin-top:16px; margin-bottom:6px; }
    p { margin-top:0; margin-bottom:12px; }
    a { color:%s; }
    pre { background:%s; padding:10px; margin-top:8px; margin-bottom:14px; white-space:pre-wrap; }
    code, pre { font-family:'DejaVu Sans Mono','Consolas',monospace; }
    th { background:%s; font-weight:600; }
    td { border-bottom:1px solid %s; }
    p.route { color:%s; margin-top:8px; margin-bottom:18px; }
    </style></head><body>%s</body></html>''' % (p['ink'], p['primary'], p['surface_soft'], p['surface_soft'], p.get('hairline','#e6dfd8'), p.get('muted',p['ink']), body)


def _paragraphs(text):
    return ''.join('<p>'+html.escape(p.strip())+'</p>' for p in text.strip().split('\n\n') if p.strip())


def _table(headers, rows):
    widths = ('48%', '44%', '8%') if len(headers) == 3 else ('45%', '55%')
    return ('<table width="100%" cellspacing="0" cellpadding="8"><tr>' +
            ''.join('<th align="left" width="'+width+'">'+html.escape(h)+'</th>' for h,width in zip(headers,widths)) + '</tr>' +
            ''.join('<tr>'+''.join('<td valign="top">'+cell+'</td>' for cell in row)+'</tr>' for row in rows) + '</table>')


# Longer explanations are shared by inline guide sections and field help.
PARAMETER_DETAILS = {
    'pae_cutoff': (
        '分域时，程序根据残基之间的 PAE 建立连接，再寻找联系紧密的残基群。cutoff 决定哪些连接会被考虑。例如 5 Å 是分域计算的输入参数，不能解释成“低于 5 Å 就一定互作”。\n\n调小阈值会使连边条件更严格，调大则允许更多连接，但结构域数量并不保证逐级变化。建议一次只改一个参数，点击“应用”，对照对角线附近的块状区域和片段边界。该操作重新分析当前 PAE，不会重新预测结构。',
        'Domain detection builds connections between residues using their PAE, then finds groups with strong internal connections. The cutoff controls which connections are considered. A value of 5 Å is an analysis setting, not evidence that two proteins interact below that value.\n\nA smaller cutoff makes the connection criterion stricter; a larger one admits more connections. The domain count need not change monotonically. Change one setting at a time, Apply, and compare diagonal blocks with the proposed boundaries. This reanalyses the current PAE; it does not predict a new structure.'),
    'pae_resolution': (
        'resolution 调节聚类对“大块”和“小块”的偏好；数值较高通常倾向于更细的划分，较低通常倾向于较大的群。它没有 Å 单位，也不改变 PAE 图的像素或原始数值精度。\n\n例如先记录当前分域，再小幅调整分辨率并应用，比较原来一个结构域是否被拆开、边界是否仍符合图中的低 PAE 块。不要为了得到指定数量而反复调参：还要看残基范围、三维结构和后续窗口长度。相同分域也可能因窗口参数不同而生成不同片段。',
        'Resolution controls the clustering preference for larger or smaller groups. Higher values generally favor finer partitions; lower values favor larger groups. It has no Å unit and does not control the image resolution or numerical precision of the PAE matrix.\n\nRecord the current partition, make a small adjustment, then Apply and compare whether a domain split and where its boundaries moved. Do not tune solely to obtain a desired count: inspect residue ranges, the structure and the eventual window lengths. The same domains can produce different fragments when window settings change.'),
    'pae_min_domain': (
        '这里按聚类包含的残基数筛选结构域。它作用于分域阶段；“最短片段”则作用于之后的窗口组装，两者不要混用。\n\n增大此值会排除更小的聚类。如果你关注小型结合区域，先看分域报告再决定是否提高。某些结构域由不连续残基组成，报告中的 segments 是实际组成，cut/span 是供后续连续切片使用的范围。',
        'This filters clusters by their residue count during domain detection. Minimum fragment length acts later, when windows are assembled; these settings serve different purposes.\n\nIncreasing this value excludes smaller clusters. If a small binding region matters, inspect the domain report before raising it. For discontinuous domains, segments lists the constituent residues, while cut/span describes the range used for subsequent contiguous slicing.'),
    'pae_domains_per_window': (
        '结构域是聚类结果，窗口是实际送去 Scan 预测的连续片段。这个参数指定初步组装时每个窗口组合多少个结构域。\n\n即使设为 1，也不保证一个结构域最终就是一个窗口：短窗口可能继续合并，不连续结构域还要按完整跨度处理。因此请以“共切分为 N 个片段”、报告中的 Fragments 和图中的红色虚线为准。',
        'Domains are clustering results; windows are the contiguous fragments used for Scan predictions. This setting controls how many domains are grouped during initial window assembly.\n\nEven a value of 1 does not guarantee one final window per domain. Short windows may be merged and discontinuous domains require their full span. Use the final fragment count, the Fragments report and the red dashed lines to determine the actual windows.'),
    'pae_min_frag': (
        '分域完成后，过短的预测窗口会与相邻窗口合并，避免生成很小的独立任务。长度单位是残基。\n\n例如 5 个结构域中，前 3 个都比较短，合并后可能只剩 2 个预测片段。这并不表示分域丢失了：结构域报告仍描述组成，Fragments 列出最终用于扫描的范围。调参后检查自己关心的区域有没有被并入相邻窗口。',
        'After domain detection, short prediction windows are merged with neighboring windows to avoid very small standalone tasks. Length is measured in residues.\n\nFor example, five domains can yield two prediction fragments after several short domains are merged. The domains have not simply disappeared: the domain report describes the components, while Fragments lists the final scanning ranges. Check where a region of interest ended up after changing this setting.'),
    'pae_max_frag': (
        '这是处理过大结构域时的目标长度。程序会尝试提高分辨率再分域；之后还要进行结构域组装和短片段合并。因此最终片段有时仍可超过目标长度，不能把它理解为绝对硬切上限。\n\n界面中的“自动”使用当前模式的自动值。应用后以 Fragments 的最终起止坐标和长度为准；如果仍然过长，结合最短片段、每窗口结构域数一起检查，再看提交预览中的实际计算规模。',
        'This is the target used when attempting to split an oversized domain at higher resolution. Domain assembly and short-fragment merging happen afterwards, so a final fragment can still exceed the target. It is not an absolute hard slicing limit.\n\nAuto uses the value defined for the current mode. After Apply, check the final Fragments ranges and lengths. If a fragment remains too long, review minimum fragment length and domains per window as well, then inspect the actual submission plan.'),
    'force': (
        '如果只是上次某几个任务失败，请保留原批次，到任务监控选择“重试失败任务”。这样才能沿用原计划和种子，避免把一次恢复变成不同的实验。\n\n如果你确实想创建一次新的计算尝试，再使用 --force；改变输入、种子或模板设置后，也应先预览确认。重试不会自动替换旧批次固定的运行代码，旧版本的故障需要按对应更新说明处理。',
        'If a few tasks in an existing batch failed, keep that batch and use Retry failed tasks in Dashboard. This preserves its plan and seeds rather than turning recovery into a different experiment.\n\nUse --force when you intentionally want a new computation attempt. Preview again after changing inputs, seeds or template settings. Retry does not automatically replace a batch\'s pinned runtime code; failures in older runtime versions need the corresponding update instructions.'),
    'shared_msa': (
        '普通片段流程为片段准备匹配的 MSA；共享全长 MSA 则先为父蛋白准备一次全长比对，再按片段坐标裁剪，适合包含很多重叠窗口的扫描。\n\n裁剪复用和单独搜索片段并不是完全相同的科学输入。比较结果时应记录这一选择。共享池按内容和输入身份管理，不要仅把别的 JSON 改成相同文件名来强行复用。',
        'The regular fragment workflow prepares matching alignments for each fragment. Shared full-length MSA prepares the parent alignment and crops it by fragment coordinates, which is useful for scans with many overlapping windows.\n\nCropped reuse and an independent fragment search are different scientific inputs. Record the choice when comparing results. The pool tracks content and input identity; renaming an unrelated JSON file cannot make it a matching alignment.'),
    'topk': (
        '例如设为 20，表示汇总排名时保留前 20 项。它不会把一个含有 500 个组合的预测计划缩减为 20 个任务。\n\n想减少实际计算，请减少 A/B 候选、减少片段组合或调整种子数量，并重新预览。想保留所有排名行则设为 0。',
        'A value of 20 keeps the top 20 entries when aggregating the ranking. It does not reduce a 500-pair prediction plan to 20 computations.\n\nTo reduce work, narrow the A/B candidates, reduce fragment combinations or change the seed count, then preview again. Set 0 to keep all ranking rows.'),
    'seeds': (
        '指定 1,2,3 可以让不同输入使用同一组采样种子，便于比较；最终计划会记录实际使用的数值。种子是采样设置，不是任务优先级。\n\n如果只指定“种子数量”，程序按该数量生成种子。已有批次重试保留原来的数值。比较多次模型时，既看最佳分数，也看不同种子间的结构和界面是否一致。',
        'Explicit values such as 1,2,3 let different inputs use the same sampling seeds for comparison; the plan records the actual values. Seeds control sampling, not queue priority.\n\nWhen only a count is supplied, the program generates that many seeds. Retrying a batch retains its values. Compare structural and interface consistency across seeds as well as the best score.'),
    'bonds': (
        'A:145:SG->C:1:C04 表示把链 A 第 145 个残基的 SG 原子连接到链 C 第 1 个残基的 C04 原子。链顺序、截短后的编号以及配体 CCD 中的原子名都要与实际 AF3 输入一致。\n\n单纯用 + 把配体放入复合物，并不会自动指定你想要的共价连接。涉及多条键、自定义糖链或复杂配体时，先检查解析和 JSON；表达式无法充分描述时使用原始 JSON 模式。',
        'A:145:SG->C:1:C04 connects atom SG of residue 145 on chain A to atom C04 of residue 1 on chain C. Chain order, numbering after truncation and ligand CCD atom names must match the actual AF3 input.\n\nAdding a ligand with + does not automatically specify the covalent connection you intend. For multiple bonds, custom glycans or complex ligands, inspect parsing and JSON first; use raw JSON when the expression is insufficient.'),
}
PARAMETER_DETAILS['num_seeds'] = PARAMETER_DETAILS['seeds']


def _parameter_body(key, lang):
    p = PARAMETERS[key]
    body = _paragraphs(p[2 if lang == 'zh' else 3])
    extra = PARAMETER_DETAILS.get(key)
    if extra:
        body += _paragraphs(extra[0 if lang == 'zh' else 1])
    return body


def _inline_parameters(keys, lang):
    return ''.join('<h3>'+html.escape(PARAMETERS[key][0 if lang == 'zh' else 1])+'</h3>'+_parameter_body(key, lang) for key in keys)


GUIDES = [
('quickstart', '从第一次预测开始', 'Your first prediction', '''
<p>如果你已经能打开这个窗口，就可以从一个小任务开始熟悉流程。不必先理解全部参数，也不需要手动写调度脚本。这里以预测一个蛋白为例，带你从输入走到结果。</p>
<p class="route">检查配置　→　输入蛋白　→　预览计划　→　提交　→　查看结果</p>
<h2>先确认结果会保存在哪里</h2>
<p>打开“设置”，把工作目录设为自己的集群目录。右侧目录树会告诉你结果、MSA 和缓存各自的位置。容器镜像、数据库和模型权重通常沿用集群已有的共享路径；它们不需要为每次预测复制一份。点击“检测资源”，处理提示的问题，再保存用户配置。</p>
<h2>输入一个你认识的蛋白</h2>
<p>在“提交任务”中保留“完整预测（MSA → 推理）”。任务名称可以填一个方便记忆的名字，例如 P12345_example；下方表达式则填写真正的 UniProt accession，例如 P12345。名称只是标签，不能代替蛋白编号。也可以直接提供 p: 开头的氨基酸序列。</p>
<p>P12345 和 Q12345 仅为文档占位编号，请先替换为自己的输入。解析语法不会联网；执行计划预览可能查询 UniProt 并更新本地缓存。</p>
<pre>P12345</pre>
<p>一行代表一个独立预测。想预测两个蛋白组成的复合物，把它们用 + 写在同一行；如果分成两行，得到的是两个独立任务。更复杂的写法在“表达式与完整示例”中都有对照例子。</p>
<h2>解析、预览和提交分别做什么</h2>
<p>“解析输入”帮助你检查蛋白组成、拷贝数和截短范围。“预览执行计划”进一步检查实际任务和阶段安排，但不会提交 Slurm 作业。获取编号对应的序列可能需要联网，也可能使用已有缓存，所以预览并不总是瞬间完成。</p>
<p>确认输入与计算规模符合预期后，再点“提交任务”。下方“运行与反馈”会显示命令和返回信息。看见受理信息或控制器作业编号，说明集群接到了任务；仅看到一行命令并不能证明提交成功。如果返回错误，先读末尾的原因，修正后再提交。</p>
<h2>接下来不用守着窗口</h2>
<p>到“任务监控”找到任务，观察它从 MSA 阶段进入推理阶段。排队时间取决于集群负载。已受理的集群作业在关闭 GUI 后会继续运行，下次打开仍可以查看。</p>
<p>完成后双击任务，或选中它点击“查看结果”。在结果页选择模型并点击“加载模型”，就能查看 PAE 和置信度。需要把结构带回本机时，复制目录路径，在 MobaXterm 的 SFTP 面板中进入该目录下载 .cif 文件。</p>
''', '''
<p>If this window opens, you can start with a small prediction. You do not need to learn every parameter or write a scheduler script first. This walkthrough takes one protein from input to an inspectable result.</p>
<p class="route">Check paths → Enter protein → Preview → Submit → Inspect results</p>
<h2>Decide where your results belong</h2>
<p>In Setup, choose your own writable cluster workspace. The directory tree explains where results, alignments and caches live. The container, databases and model weights normally use existing shared cluster paths; you do not copy them for each prediction. Check resources, resolve any reported problems and save your configuration.</p>
<h2>Start with a protein you recognize</h2>
<p>In Submit, keep End-to-end prediction selected. Give the task a memorable label such as P12345_example, and enter its actual UniProt accession, for example P12345, in the expression editor. A label does not identify the protein. You can also supply an amino-acid sequence prefixed with p:.</p>
<p>P12345 and Q12345 are documentation placeholders; replace them with your inputs first. Syntax parsing is offline; an execution-plan preview may query UniProt and update local caches.</p>
<pre>P12345</pre>
<p>Each line is an independent prediction. Join two proteins with + on one line to predict a complex. Putting them on separate lines submits two separate tasks instead. The full expression reference includes more elaborate combinations.</p>
<h2>Parse, preview, then submit</h2>
<p>Parse inputs checks the components, copy numbers and residue ranges. Preview plan checks the proposed work and stages without submitting Slurm jobs. Accession lookup may use the network or an existing cache, so a preview can take a little time.</p>
<p>Once the inputs and workload look right, submit. Run feedback displays the command and its response. Acceptance information or a controller job ID indicates that the cluster received the task; a command line alone is not proof of successful submission. If an error appears, read the final explanation and correct it before submitting again.</p>
<h2>You can leave the window</h2>
<p>Find the task in Dashboard and follow its alignment and inference stages. Queue time depends on cluster load. Accepted cluster jobs continue after you close the GUI and can be inspected when you return.</p>
<p>When finished, double-click the task or choose View Results. Select a model and load it to see PAE and confidence information. To take the structure to your computer, copy the directory path, open it in MobaXterm's SFTP panel and download the .cif file.</p>
'''),
('setup', '配置、目录与共享资源', 'Paths and shared resources', '''
<p>设置页区分“程序放在哪里”和“数据写到哪里”。安装目录存放 GUI 与计算脚本；工作目录存放你的任务和缓存。两者可以不同，修改工作目录不会搬动程序，也不会移动已经生成的数据。</p>
<h2>第一次配置，优先核对这几处</h2>
<p>已有同事提供的配置 JSON 时，点击“导入配置 JSON”填入设置，再修改个人工作目录及其他项目，检测资源后点击“保存用户配置”。导入本身不写文件；保存会写入自己的 ~/.config/af3_console/config.json，并保留其中已有设置的备份，实验室模板不作为保存位置。未显示在表单中的 MSA 线程数、编译桶等参数也会保留。下次启动自动读取个人设置，无需重复导入或设置 AF3_CONFIG。</p>
<p>工作目录应当是你在集群上的可写目录，而且计算节点也能访问。不要把长期使用的程序或结果放在登录节点的临时目录。容器镜像指向 .sif 文件，数据库指向已有 AF3 数据库目录，模型权重指向可读取的权重目录。路径是在集群上解释的，不是本机 Windows 路径。</p>
<p>工作目录变化时，没有手动覆盖的派生路径会跟着变化。若你把模型或 JAX 缓存改成了共享路径，之后修改工作目录仍会保留这个覆盖。保存前看一遍右侧目录树，最容易发现结果写错位置或共享路径意外改动的问题。</p>
<h2>这些目录各自保存什么</h2>
<table width="100%" cellspacing="0" cellpadding="8">
<tr><th align="left">目录</th><th align="left">用途与使用方式</th></tr>
<tr><td>output</td><td>批次计划、状态、日志以及结果入口。分析结果时优先从 GUI 给出的实际路径进入。</td></tr>
<tr><td>msa_data</td><td>按输入身份复用的 MSA 数据池。它是推理的材料库，不是每个批次都需要单独展示的结果。</td></tr>
<tr><td>infer_data</td><td>可复用的推理产物池。旧任务的模型可能在这里；不要假定所有 .cif 都直接位于 output。</td></tr>
<tr><td>models / 数据库 / .sif</td><td>AF3 权重、序列数据库与运行容器。通常共享读取，GUI 安装包不包含这些大文件。</td></tr>
<tr><td>cache</td><td>序列获取等应用缓存，以及固定输入资产。需要写权限。</td></tr>
<tr><td>JAX 编译缓存</td><td>保存编译产物，帮助兼容任务减少重复编译。共享时需要相应读写权限。</td></tr>
</table>
<h2>检测成功之后，再保存</h2>
<p>“检测资源”检查路径和权限；“保存用户配置”才把更改写入个人配置 JSON。配置摘要显示实际配置文件位置，默认是 ~/.config/af3_console/config.json。保存不会改写 af3.py。</p>
<p>检测资源还会检查容器运行时、Slurm 命令和分区是否已配置。空的备用分区、环境模块和节点本地缓存是有效选项；必填项尚未完成时可以保存，正式提交会被部署检查阻止。它不会远程验证分区权限。</p>
<p>AF3_CONFIG 可指定自己的配置 JSON，例如 /path/to/your/config.json；AF3_BASE 会覆盖工作目录，并在摘要中显示。覆盖生效期间该字段只读，要改用配置中的值，应先在启动终端移除该环境变量后重新打开 UI。</p>
<p>检测反映的是当前登录环境，计算节点的挂载和权限也必须匹配。若检测通过但作业里报“找不到文件”，对照日志中的完整路径检查计算节点，而不是重新安装 GUI。</p>
<h2>先配置自己的分区与资源上限</h2>
<p>CPU/GPU 分区需填写自己集群的真实名称。并发、资源上限和编译桶需要结合硬件与配额验证，从少量任务开始。备用 GPU 分区、环境模块和节点本地数据库缓存可留空。保存新设置只影响之后创建的任务，已提交任务继续使用保存时的计划与配置。</p>
''', '''
<p>Setup separates the program location from your data workspace. The installation contains the GUI and computation scripts; the workspace holds tasks and caches. Changing the workspace neither moves the program nor relocates existing data.</p>
<h2>Check these paths first</h2>
<p>If a colleague supplies a configuration JSON, choose Import configuration JSON, adjust your workspace and other fields, check resources, then Save user settings. Import only fills the form. Saving writes ~/.config/af3_console/config.json and backs up any previous personal file; the template is not selected as the save destination. Advanced parameters such as MSA threads and compilation buckets are retained. Future launches read your personal settings without another import or AF3_CONFIG.</p>
<p>Your workspace must be writable and visible to compute nodes. Keep long-lived program files and results out of login-node temporary directories. Select the .sif container file, the existing AF3 database directory and readable model weights. These are cluster paths, not paths on your Windows computer.</p>
<p>Derived paths follow workspace changes unless you have overridden them. A manually chosen shared model or JAX cache path is retained. Before saving, review the directory tree to catch an unintended output location or shared-path change.</p>
<h2>What lives in each directory</h2>
<table width="100%" cellspacing="0" cellpadding="8">
<tr><th align="left">Directory</th><th align="left">Purpose</th></tr>
<tr><td>output</td><td>Batch plans, status, logs and result entry points. Follow the actual path shown by the GUI when analysing results.</td></tr>
<tr><td>msa_data</td><td>Alignment data reused by input identity. This is material for inference, rather than another result that every batch needs to display.</td></tr>
<tr><td>infer_data</td><td>A pool of reusable inference products. Older models may live here; not every .cif is directly inside output.</td></tr>
<tr><td>models / databases / .sif</td><td>AF3 weights, sequence databases and runtime container. Usually shared read-only; the GUI installer does not include these large resources.</td></tr>
<tr><td>cache</td><td>Application caches such as retrieved sequences, and pinned input assets. Requires write access.</td></tr>
<tr><td>JAX cache</td><td>Compilation products that can reduce repeat compilation for compatible jobs. Sharing requires appropriate read/write access.</td></tr>
</table>
<h2>Check access, then save</h2>
<p>Check resources inspects paths and permissions. Save user config writes your choices to a personal JSON file. The summary displays its actual location, normally ~/.config/af3_console/config.json. Saving does not rewrite af3.py.</p>
<p>Checks also inspect the container runtime, Slurm commands and whether partitions are configured. Empty fallback partition, module and node-local cache settings are valid. You can save unfinished required settings; deployment checks prevent actual submission until they are ready. Checks do not remotely verify partition permissions.</p>
<p>AF3_CONFIG selects a config JSON, for example /path/to/your/config.json. AF3_BASE overrides the workspace and appears in the summary. That field is read-only while the override is active; unset the variable in the launching terminal and restart the UI to use the saved value.</p>
<p>Checks reflect the current login environment; compute-node mounts and permissions must also match. If checks pass but a job reports a missing file, compare the full path in its log with the compute-node environment rather than reinstalling the GUI.</p>
<h2>Configure your partitions and resource limits</h2>
<p>Enter the actual CPU/GPU partitions for your cluster. Validate concurrency, resource limits and compilation buckets against its hardware and quotas, starting with a small batch. The fallback GPU partition, environment module and node-local database cache are optional. Saved settings affect subsequently created tasks; submitted tasks keep their recorded plan and configuration.</p>
'''),
('modes', '怎样选择运行模式', 'Choosing a run mode', '''
<p>模式决定从哪一步开始、希望得到什么。第一次做结构预测，选择“完整预测”最直接；已经有中间数据时，再考虑只运行其中一个阶段。</p>
<h2>完整预测：从序列走到结构</h2>
<p>输入蛋白或复合物表达式，程序先准备缺少的 MSA，再提交 GPU 推理。匹配且有效的缓存可以被复用。适合新蛋白、新组合以及不想手动衔接两个阶段的情况。缓存能否复用取决于实际输入身份，不只看任务名称。</p>
<h2>仅生成 MSA：先备好比对材料</h2>
<p>适合给后续多组实验预先准备同一批蛋白的比对。此模式只产生 MSA，不会接着生成结构，因此任务结束后没有模型属于正常情况。等材料就绪后，可使用完整预测或仅推理继续研究。</p>
<h2>仅推理：明确使用已有匹配 MSA</h2>
<p>适合已有比对、准备调整种子或复合物组合的情况。若预览提示缺少匹配 MSA，请先补比对，或改用完整预测。“仅推理”不是“禁用 MSA”：前者要求已有材料，后者刻意改变科学输入。</p>
<h2>导入 AF3 JSON：直接使用完整输入</h2>
<p>需要描述复杂配体、共价键、自定义模板或表达式不能覆盖的情况时，可以选择 JSON 模式。填写集群可读的 AF3 JSON 路径，并选择界面中的阶段。文件内容必须符合当前集群 AF3 的输入格式；只有文件扩展名正确还不够。</p>
<p>先预览，让程序检查输入和引用资产。若 JSON 中仍需读取外部 MSA、模板或 CCD 文件，也要确认这些路径可读。保存过的任务固定其输入，之后编辑原始文件不会悄悄改变已经提交的计算。</p>
<h2>PAE 结构域分析：为切片找边界</h2>
<p>这个模式关注单蛋白的结构域和后续扫描窗口，不是普通复合物筛选。它使用单体预测的 PAE；缺少所需单体预测时，前置准备会增加等待时间。已有模型时，也可以直接在“结果分析”加载模型后进行 PAE 分域。</p>
<h2>高级选项先按研究目的选择</h2>
<p>“禁用模板”控制结构模板，“禁用 MSA”控制比对输入，两者彼此独立。它们会改变预测条件，不是加速按钮。种子设置用于采样与可复现比较；提交前在计划中核对实际数值。运行模式和语言切换不会替你修改已提交的任务。</p>
''', '''
<p>A mode specifies where to start and which outputs you want. End-to-end is the most direct choice for a first structure prediction. Use an individual stage when you already have the required intermediate data.</p>
<h2>End-to-end: from sequence to structure</h2>
<p>Enter a protein or complex expression. The program prepares missing MSA and then submits GPU inference, reusing matching valid caches where possible. This suits new proteins, new combinations and workflows where you do not want to connect the stages manually. Cache reuse depends on actual input identity, not just the task label.</p>
<h2>MSA-only: prepare alignments ahead of time</h2>
<p>Use this to prepare proteins that will appear in several later experiments. It generates alignments and stops; the absence of a structure after completion is expected. Once the material is ready, choose end-to-end or inference-only for subsequent predictions.</p>
<h2>Inference-only: use existing matching MSA</h2>
<p>This is useful when alignments are ready and you want to change seeds or complex combinations. If preview reports missing matching MSA, prepare it first or choose end-to-end. Inference-only is different from Disable MSA: the former expects existing material, while the latter intentionally changes the scientific input.</p>
<h2>AF3 JSON: provide a complete input</h2>
<p>Choose JSON for complex ligands, covalent bonds, custom templates or other inputs that expressions cannot describe adequately. Supply a cluster-readable AF3 JSON path and select the stage in the interface. Its contents must match the installed AF3 input format; the extension alone is insufficient.</p>
<p>Preview to check the input and its referenced assets. Any external MSA, template or CCD files must also be readable. Submitted tasks pin their inputs, so later edits to the original source files do not silently alter accepted computations.</p>
<h2>PAE domains: find useful slicing boundaries</h2>
<p>This mode focuses on one protein's domains and eventual scanning windows. It uses monomer PAE; preparing a missing monomer prediction adds waiting time. If a model already exists, you can also load it in Results and analyse its PAE there.</p>
<h2>Choose advanced options for the experiment</h2>
<p>Disable templates and Disable MSA independently control structural templates and alignments. They change prediction conditions rather than acting as generic speed controls. Seeds specify sampling and reproducible comparisons; verify their actual values in the plan. Mode and language changes do not modify submitted jobs.</p>
'''),
]

GUIDES += [
('msa', 'MSA：准备与复用比对', 'Preparing and reusing MSA', '''
<p>MSA 是多序列比对，是结构预测使用的输入材料之一。它通常在 CPU 分区上生成；真正的结构推理随后在 GPU 分区进行。因此，一个完整任务先出现 CPU 作业、过一段时间才出现 GPU 作业，是正常流程。</p>
<h2>为什么第二次使用同一蛋白可能更快</h2>
<p>有效的 MSA 保存在 msa_data 池中，可以被不同批次复用。程序根据序列和相关输入身份查找匹配数据，而不是根据你填写的任务名称查找。把同一蛋白从“实验一”改名为“实验二”通常不需要重新搜索比对；改变序列、截短范围或相关输入条件则可能需要另一份材料。</p>
<p>新建的普通 UniProt MSA 使用可读名称，例如 msa_data/P12345/P12345_data.json（P12345 仅为语法占位符）。保留 AF3 的原生子目录及附带文件，不再把 JSON 移到池根目录。相同 ID 的不同输入或环境可能带短哈希后缀；匿名序列仍按内容命名。匹配的旧哈希缓存可原地复用，旧文件不会自动改名或移动。普通输入可在 JSON 完整性和序列匹配后原地复用没有管理记录的旧文件，但无法核实其历史计算环境。外部 MSA、MSA-free 等特殊输入单独区分。</p>
<p>run、pulldown 和 scan 中，普通全长 UniProt MSA 使用每个蛋白的 ID 命名，不使用任务或批次名称。scan 共享全长 MSA 的原始数据也遵循此规则；片段数据需要带范围等标识，不能与全长混用。</p>
<p>也兼容单个 ID 的时间戳目录，例如 P12345_20260602_140829/P12345_data.json。没有管理记录时按目录时间戳从新到旧找完整 JSON，然后兼容原生无时间戳目录和旧平铺文件。按 ID 自动查找时目录的 ID 前缀与文件的 ID 应一致；名称不一致或使用自定义 job 名时，可显式选择 JSON。名称中的 _and_ 等文字不表示输入个数，实际输入以 JSON 内容为准。目录和附带文件均保持原样。</p>
<h2>主池与最多两个备份</h2>
<p>在设置页 MSA 路径框左侧点击 +，可添加第二、第三个路径。第一个路径接收计算产物，完整性校验通过后再复制到备份路径；任务使用提交时保存的路径。各路径都应在登录节点和计算节点上可访问，并具有相应读写权限。</p>
<p>GUI 每次启动在后台检查一次这些路径之间的新增完整产物，发现后先弹出确认消息。可展开详情查看来源、目标、冲突和跳过原因；默认不执行同步。保存新路径后，也可点击“检查 MSA 同步”手动检查。扫描只读，同步只补充缺少的完整产物，原生目录及其附带文件作为整体复制。遇到同名但不同内容的产物会跳过并报告，不以新文件覆盖旧文件，也不删除已有文件。路径旁的 − 只移除配置项，不删除数据。</p>
<h2>复用前检查完整性</h2>
<p>每次复用前检查 JSON 及所引用的文件。蛋白链必须明确提供 unpairedMsa、pairedMsa 和 templates，或适用的外部文件路径；字段缺失或 null 不能当成“已完成”。空字符串形式的 MSA 与空数组 templates 可以表达有意不使用这些材料，并不等于损坏。模板存在时还要检查 mmcif 或其文件、queryIndices 和 templateIndices；文件路径必须可读。完整性检查不能证明比对或模板在科学上合适，仍需确认序列与预期输入一致。</p>
<p>若准备复用的合法缓存含有空 MSA 或空模板，正式提交前会列出文件和空字段，并要求选择“复用这些输入”“重新生成 MSA”或“取消”（默认）。重新生成使用新的目录，保留原缓存。预览只展示报告，不替正式提交授权；缺失字段或格式错误的文件不能通过选择复用绕过校验。</p>
<h2>先批量准备，还是直接完整预测</h2>
<p>如果只是做几个预测，直接使用完整预测即可。若很多批次会反复使用同一组蛋白，可以先以“仅生成 MSA”准备比对，确认完成后再开始后续组合。这样更容易把“材料准备”和“结构采样”分开观察。</p>
<p>MSA 作业通过名称锁协调写入；原生目录可能在作业完成前出现。程序只有在 AF3 成功退出、产物与输入校验通过并写入完成记录后才复用新产物。失败文件保留用于诊断，存在 JSON 不等于成功。并发规划若发生名称身份冲突会要求重建计划，不会静默覆盖另一份数据。</p>
<h2>MSA 作业离开队列，不一定代表成功</h2>
<p>正常结束、失败、取消和超时都会使作业离开 squeue。控制器还需要确认匹配的 MSA 产物已经发布并可读取，才会进入下一阶段。看到“MSA 已提交”只说明计划曾提交过这个阶段，不等于该作业此刻仍在运行。</p>
<p>若迟迟不进入推理，先看任务详情中的当前状态和日志。没有产物时查 MSA 日志；产物已有但控制器报队列查询错误时查控制器日志。刷新暂时失败时，界面会保留上次成功信息并显示时间，不把未知状态冒充成功。</p>
<h2>Scan 中的共享全长 MSA</h2>
{shared_msa}
''', '''
<p>MSA means multiple sequence alignment, one of the inputs used in structure prediction. It is normally prepared on CPU nodes before inference runs on a GPU. Seeing CPU jobs first and GPU jobs later is therefore expected for an end-to-end task.</p>
<h2>Why reusing a protein can be faster</h2>
<p>Valid alignments are stored in the msa_data pool and can be reused across batches. Matching uses sequence and relevant input identity rather than the task label. Renaming an experiment usually does not require another alignment search; changing its sequence, residue range or relevant input conditions may require different material.</p>
<p>New ordinary UniProt MSA jobs use readable names, for example msa_data/P12345/P12345_data.json (P12345 is a syntax placeholder). AF3 output directories and companion files stay in place; JSON files are no longer flattened into the pool root. Different inputs or environments for one ID may receive a hash suffix; anonymous sequences keep content-based names. Matching old hash caches can be reused in place. Old files are not automatically renamed or moved. Ordinary inputs may reuse untracked legacy products after schema and sequence validation, although their historical environment cannot be verified. External MSA and MSA-free inputs remain separate.</p>
<p>In run, pulldown and scan, ordinary full-length UniProt MSAs are named for each protein's ID, independently of the job or batch name. Original full-length data for shared-MSA scan follows this rule too; fragment data needs range and other identity information to distinguish it from full-length data.</p>
<p>Single-ID timestamp directories such as P12345_20260602_140829/P12345_data.json are also supported. Without a management record, valid timestamped products are tried newest first, followed by the plain native directory and legacy flat file. Automatic lookup by ID expects matching directory and file ID prefixes; select the JSON explicitly for differing prefixes or custom job names. Text such as _and_ in a name does not determine the number of inputs; the JSON contents do. Directories and companion files stay in place.</p>
<h2>A primary pool and up to two backups</h2>
<p>Use + to the left of the MSA path in Setup to add a second or third path. Computation writes to the first path; complete validated products are then copied to backups. Jobs use the paths saved when submitted. Paths must be accessible from login and compute nodes, with the required read/write permissions.</p>
<p>Each GUI launch checks these pools once in the background and asks before copying newly found complete products between them. Expand details for sources, destinations, conflicts and skipped products; the default is No. After saving new paths, use Check MSA synchronization to check manually. Scanning is read-only, and synchronization only adds missing complete products, copying native directories and companion files as whole units. Same-name products with different contents are skipped and reported. Existing files are never overwritten or deleted. The − button removes a setting, not data.</p>
<h2>Completeness is checked before reuse</h2>
<p>Every reuse validates the JSON and referenced files. Each protein chain must explicitly provide unpairedMsa, pairedMsa and templates, or applicable external file paths; missing or null fields do not mean preparation has finished. Empty MSA strings and an empty templates array can intentionally disable those inputs and are not automatically corruption. Included templates must also have mmcif or its file, queryIndices and templateIndices; referenced files must be readable. Completeness does not establish scientific suitability, so confirm that the sequence matches the intended input.</p>
<p>If a schema-valid cache selected for reuse contains empty MSA or template fields, submission lists the files and empty fields and requires Reuse these inputs, Generate MSA again, or Cancel (the default). Regeneration uses a new directory and retains the original cache. Preview reports do not authorize a later submission, and choosing reuse cannot bypass missing-field or malformed-file checks.</p>
<h2>Prepare a pool, or run end-to-end?</h2>
<p>For a few predictions, end-to-end is sufficient. If many batches will reuse the same proteins, MSA-only can prepare the alignments first. Waiting for that preparation to complete makes it easier to separate alignment readiness from structural sampling.</p>
<p>MSA jobs coordinate writes using a name lock; a native directory may appear before a job completes. New products are reused only after AF3 exits successfully, output/input validation passes, and a completion record is written. Failed files remain available for diagnosis; a JSON file alone is not proof of success. A naming-identity race requires a new plan instead of silently overwriting another product.</p>
<h2>A job leaving the queue is not proof of success</h2>
<p>Completion, failure, cancellation and time limits all remove jobs from squeue. The controller must also verify that matching MSA products were published and are readable. MSA submitted records a stage submission; it does not prove the job is still running now.</p>
<p>If inference does not follow, inspect task details and logs. Missing products call for the MSA log; available products together with scheduler errors call for the controller log. When refresh fails, the GUI retains the last successful information with its timestamp rather than reporting an unknown state as success.</p>
<h2>Shared full-length MSA in Scan</h2>
{shared_msa}
'''),
('infer', '推理、种子与计算缓存', 'Inference, seeds and caches', '''
<p>推理阶段把蛋白、核酸、配体、MSA、模板和种子等输入组合起来，由 AF3 在 GPU 上生成结构。任务量不仅取决于你输入了多少行，还取决于每行的链数、长度、配体和采样设置。</p>
<h2>为什么已完成 MSA，还需要等待</h2>
<p>控制器先检查并准备推理输入，再向 GPU 分区提交作业。进入队列后可能继续等待空闲资源；开始运行后也可能先进行 JAX 编译。MSA 完成、GPU 已提交和模型已生成是三个不同阶段，不能只根据等待时长判断卡住。</p>
<p>界面使用你保存的资源配置，包括 GPU 分区、任务分组和编译桶。若预览提示输入规模超过支持范围，应重新检查拷贝数、截短或拆分研究对象。把并发数字调大不会降低单个复合物的显存需求。</p>
<h2>种子用来采样，不是排队优先级</h2>
{seeds}
<h2>结果复用与 JAX 缓存不是一回事</h2>
<p>结果复用是找到了相同计算身份对应的有效产物；JAX 缓存保存的是兼容计算的编译结果，不能替代结构预测本身。即使编译缓存命中，模型仍需要执行推理。镜像或运行环境变化也可能使原编译缓存无法复用。</p>
<p>任务目录中带有的内部后缀用来区分输入、种子和设置。界面优先显示你填写的名称，详情保留完整标识。同名的两个任务不一定是同一次计算；比较结果时同时核对输入和种子。</p>
<h2>只重试失败项，还是开始新尝试</h2>
{force}
''', '''
<p>Inference combines proteins, nucleic acids, ligands, alignments, templates and seeds, then runs AF3 on a GPU to produce structures. Workload depends on chain counts, lengths, ligands and sampling settings, not simply the number of input lines.</p>
<h2>Why there can be a wait after MSA</h2>
<p>The controller validates and prepares inference inputs before submitting GPU jobs. Those jobs may wait for resources and may compile with JAX after starting. MSA ready, inference submitted and model generated are different stages; elapsed time alone cannot identify a stuck task.</p>
<p>The interface uses your saved resource policy, including partitions, grouping and compilation buckets. If preview reports that an input is too large, review copy numbers, truncation or the research design. Increasing concurrency cannot reduce the memory required by one complex.</p>
<h2>Seeds control sampling, not queue priority</h2>
{seeds}
<h2>Result reuse differs from compilation reuse</h2>
<p>Result reuse finds valid products for a matching computation identity. The JAX cache stores compiled work for compatible computations, not predicted structures. Inference still runs after a compilation cache hit. Changes to the container or runtime can also prevent an older compilation cache from matching.</p>
<p>Internal directory suffixes distinguish inputs, seeds and settings. The GUI shows your label first and retains the full identifier in Details. Two identically named tasks can represent different computations; compare their inputs and seeds as well as their scores.</p>
<h2>Retry failed work or start a new attempt</h2>
{force}
'''),
('screen', '互作筛选与片段扫描', 'Pulldown and fragment scans', '''
<p>“互作筛选”适合问“这些候选里，哪些更值得检查”；“片段扫描”适合进一步问“可能是长蛋白的哪一段参与互作”。两者都从 A/B 两组输入出发，但扫描会先准备片段，因此计算量可能明显增加。</p>
<h2>A 和 B 每一行都是一个完整的侧</h2>
<p>A 可以放一个诱饵，B 放多个候选。若诱饵本身是一个复合物，就把它写成 P12345+Q12345 放在 A 的同一行；程序会把这一整侧与 B 中每一侧组合。不要把复合物的成分分到 A 的不同行，否则它们会成为不同候选。</p>
<pre>A 组：
P12345+Q12345

B 组：
P12345
Q12345</pre>
<p>这里的“A 组 / B 组”是说明文字，复制时只把各自表达式放进对应编辑器。上述例子在未切片时构成两个 A × B 组合。xN 表示一侧中的实体拷贝数，不是把输入行重复 N 次。</p>
<h2>固定窗口扫描：长度、重叠和尾段</h2>
<p>蛋白超过切分阈值时才切片。窗口长度为 300、重叠为 100 时，常规相邻窗口可从 1–300、201–500 开始；具体尾段还受最短片段和蛋白长度影响。重叠必须小于窗口长度，重叠更大通常会产生更多窗口。</p>
<p>最短片段用于避免很短的末端独立窗口，末段可能并入前一段。每次调整后都应查看片段预览，确认研究区域被覆盖。DNA、RNA 和配体不按蛋白窗口规则切片。</p>
<h2>按 PAE 扫描：先知道结构，再决定片段</h2>
<p>PAE 模式利用全长单体预测中的结构域来安排窗口。缺少单体数据时需要先准备，所以提交后可能先看到前置预测。窗口不是结构域的简单一对一映射：若几个结构域太短，它们可能合成一个实际扫描片段。</p>
<p>“PAE 分域与窗口”一节解释各参数和红色虚线的含义。得到片段预览后，再检查最终执行计划，确认全长任务、片段组合和种子设置是否符合预期。</p>
<h2>把计算量看清楚再提交</h2>
<p>A/B 候选数、每侧内部片段组合以及种子数量都会增加工作量。勾选组内配对还会增加 A × A 和 B × B。最终经过去重及模式规则处理后的数量以执行预览为准，不能只把编辑器的行数相乘。</p>
{topk}
<p>常用操作顺序是：填 A/B → 获取序列与片段预览 → 预览执行计划 → 提交。结果页中的界面置信度排名、矩阵和片段评分概览对应不同层级；高分片段可以帮助缩小后续检查范围，但仍要加载结构查看实际界面。</p>
''', '''
<p>Pulldown helps screen candidate interactions. Scan asks which region of a long protein might be involved. Both start with groups A and B, but Scan prepares fragments first and can substantially increase the workload.</p>
<h2>Each line is a complete side</h2>
<p>You can place one bait in A and several candidates in B. If the bait is itself a complex, put P12345+Q12345 on one line in A. The whole side is combined with each side in B. Separate lines represent separate candidates rather than components of the same complex.</p>
<pre>Group A:
P12345+Q12345

Group B:
P12345
Q12345</pre>
<p>The group headings above are annotations; copy only the expressions into the corresponding editors. Without slicing, this example creates two A × B combinations. xN specifies copies within a side rather than N repeated input rows.</p>
<h2>Fixed windows: length, overlap and the final fragment</h2>
<p>Proteins are sliced only above the split threshold. A 300-residue window with 100-residue overlap normally begins with ranges such as 1–300 and 201–500. Terminal handling also depends on minimum fragment length and protein length. Overlap must be smaller than the window; larger overlaps usually produce more windows.</p>
<p>The minimum fragment setting avoids a very short standalone terminal window, which may instead merge with its neighbor. Inspect coverage after every adjustment. DNA, RNA and ligands are not sliced using the protein-window rule.</p>
<h2>PAE windows: use structure to choose fragments</h2>
<p>PAE mode uses domains from full-length monomer predictions. Missing monomer data require preparation, so precursor predictions may appear first. Domains and windows are not necessarily one-to-one: several short domains can become one actual scanning fragment.</p>
<p>The PAE domains and windows topic explains each parameter and the red dashed boundaries. After inspecting fragments, review the final execution plan for full-length tasks, fragment combinations and seeds.</p>
<h2>Understand the workload before submitting</h2>
<p>A/B candidate counts, fragment combinations within a side and seed counts all contribute to work. Within-group pairing adds A × A and B × B. The execution preview includes deduplication and mode-specific rules, so simply multiplying editor row counts is insufficient.</p>
{topk}
<p>The usual sequence is: enter A/B, fetch sequences and inspect fragments, preview the plan, then submit. Interface rankings, matrices and fragment summaries show different levels of the results. High-scoring fragments can focus further inspection; load the structures to examine the actual interfaces.</p>
'''),
('progress', '排队、失败与重试', 'Queues, failures and retry', '''
<p>任务监控同时展示集群当前作业和本地保存的批次。这两类信息更新时间可能不同：表格中的批次可以早已结束，顶部作业数则是最近一次查询 Slurm 的结果。先看查询时间，再判断当前发生了什么。</p>
<h2>沿着阶段看进度</h2>
<p>完整预测通常经历 MSA 排队/运行、产物确认、推理排队/运行和结果汇总。控制器负责衔接阶段，是一个小型 CPU 作业；它存在时不代表有 GPU 正在计算。仅 MSA 模式完成后不会继续推理。</p>
<p>“已提交”说明阶段曾被提交；“排队中”说明等待资源；“运行中”说明相应作业在执行；“已完成”表示该任务所需产物已满足完成条件。部分失败的批次仍可能有可查看结果，完成进度也不等于顶部的作业数量。</p>
<h2>同名任务怎样区分</h2>
<p>名称便于识别，真实目录是选择任务的依据。同名项会带来源与短标识，详情中可查看完整路径和计划。已确认对应同一产物的旧 MSA/结果池条目会合并展示；不同的计算不会因为名字相同就被隐藏。</p>
<h2>长时间没变化时，先找证据</h2>
<p>先刷新，查看独立错误提示和上次成功查询时间。若 Slurm 暂时不可用，本地结果仍然可以浏览，旧统计不是新的实时数据。然后选中任务打开详情，复制日志路径。</p>
<p>控制器日志解释阶段为什么等待或停止；MSA 日志解释比对是否失败；推理日志解释 GPU 运行、显存和输入错误。如果作业已经从队列消失，也要检查是否超时或失败，而不是直接判断完成。</p>
<h2>如何安全地继续失败批次</h2>
<p>确认原控制器已退出后，选中对应任务使用“重试失败任务”。它沿用原输入和种子，复用已有有效结果。仍在运行的任务不要重复提交；需要改变序列或采样设置时，可以填回编辑器，预览后作为新任务提交。</p>
<p>每批任务保存自己的运行版本。更新 GUI 不会替换已提交任务的旧快照，因此旧代码兼容性故障可能仍出现在旧批次中。新任务使用更新后的程序；旧任务是否迁移或恢复应单独处理，普通重试并不等于代码升级。</p>
''', '''
<p>Dashboard combines current cluster jobs with locally saved batches. They need not have the same update time: a batch may have finished long ago, while the top job count comes from the most recent Slurm query. Check its timestamp before interpreting the current situation.</p>
<h2>Follow the stages</h2>
<p>End-to-end work normally passes through MSA queuing/running, product validation, inference queuing/running and aggregation. The controller connects these stages in a small CPU job; its presence does not mean a GPU is computing. MSA-only intentionally stops before inference.</p>
<p>Submitted records a stage submission. Pending means waiting for resources, running means a relevant job is executing, and done means the required completion products were found. A partially failed batch can still have usable results. Its completion count is not the same as the top-level job count.</p>
<h2>Distinguish identical names</h2>
<p>Names are labels; actual directories identify selections. Duplicates include a source and short identifier, with full paths and plans available in Details. Confirmed aliases of the same older MSA/result product are combined, but distinct computations are not hidden merely because their names match.</p>
<h2>When progress stops, gather evidence</h2>
<p>Refresh and inspect the separate error notice and last successful query time. Local results remain accessible during a Slurm outage, but retained statistics are not fresh live data. Select the task and copy its log path from Details.</p>
<p>The controller log explains stage transitions and waits; MSA logs explain alignment failures; inference logs explain GPU, memory and input errors. A job disappearing from the queue may have timed out or failed, so do not infer completion from disappearance alone.</p>
<h2>Continue a failed batch</h2>
<p>After the original controller has exited, choose Retry failed tasks for the correct batch. It preserves inputs and seeds and reuses valid products. Do not resubmit running work. If you want different sequences or sampling settings, fill the inputs back into the editor and preview a new task.</p>
<p>Each batch pins its runtime version. A GUI update does not replace snapshots used by previously submitted jobs, so an old compatibility fault can remain in an old batch. New tasks use the updated program; migration or recovery of old tasks is separate from ordinary retry.</p>
'''),
('results', '读懂模型、排名和 PAE', 'Reading models, rankings and PAE', '''
<p>结果页把“这批任务算了什么”和“模型是否值得进一步检查”放在一起。先确定当前任务，再从分数筛选候选，最后回到结构和 PAE 验证自己的判断。</p>
<h2>找到你要看的那一次计算</h2>
<p>点击标题旁的“切换任务”，输入名称、UniProt 编号或内部标识中的一部分。默认按更新时间排列，也可以组合筛选类型和状态。选中后确认才会加载结果；取消不会改变当前模型。“刷新列表”用于发现新任务或更新列表状态。</p>
<p>顶部常驻显示当前任务、完整标识及实际目录。若名称相同，核对更新时间和目录。需要下载文件时使用这里的“复制路径”，不必猜测结果位于 output 还是 infer_data。</p>
<h2>普通预测与筛选结果的内容不同</h2>
<p>普通单蛋白预测主要查看模型、PAE 和置信度；没有互作排名表通常是正常的。Pulldown/Scan 则提供组合排名，并可能有矩阵、片段评分概览和汇总报告。筛选未完成时可出现部分结果，先结合任务监控判断完整性。</p>
<p>排名中的 A/B 界面置信度分数是跨 A/B 两侧链对 ipTM 的均值，用于这里的筛选排序。它与包含多条链的整个复合物 ipTM 不是同一个量。比较不同体系时，先确认两行使用了怎样的组成与采样条件。</p>
<p>“蛋白对汇总”列出每对原蛋白的最高分片段组合；“蛋白对置信度矩阵”按原蛋白对展示该最高分。“片段评分概览”用于定位片段范围。这些都是预测评分汇总，并不表示已经确认发生互作。</p>
<h2>分数回答不同的问题</h2>
<p>pTM 描述整体结构可信度相关信息，ipTM 关注链间相对构型；ranking score 用于模型排序。它们不应被混成同一个“成功率”。同时查看预测无序区域比例、碰撞提示和不同种子的一致性，通常比只挑最高一行更有帮助。</p>
<p>高分预测是值得检查的候选，不等于实验已证明结合。尤其当高分只出现在一次采样、界面很小或依赖大段不确定区域时，应打开三维结构，查看接触方式是否符合你的体系。</p>
<h2>PAE 图怎样读</h2>
<p>选择模型后点击“加载模型”。预测比对误差（PAE）表示在结构比对中对齐某一位置后，另一位置相对坐标的预测误差，单位为 Å。低值表示模型对相对位置较有把握，高值表示不确定。请根据色标读数，而不是把颜色直接当作互作结论。</p>
<p>对角线附近的低 PAE 块常帮助识别相对稳定的局部区域；跨链区域则用于检查不同链的相对位置是否稳定。大矩阵为了显示响应可以降采样，分域计算仍使用原始精度的数据。</p>
<h2>想进一步切片时</h2>
<p>打开“PAE 结构域”参数并应用，会生成结构域、实际片段数和红色虚线。PAE 阈值控制分域连边条件，聚类分辨率控制划分粒度；两者都不等于互作命中阈值。完整解释和“5 个结构域如何变成 2 个片段”的实例在左侧“PAE 分域与窗口”章节中，直接阅读即可，不需要逐个打开参数链接。</p>
''', '''
<p>Results brings together what the batch computed and which models deserve closer inspection. Confirm the selected task, use scores to narrow candidates, then return to the structure and PAE to assess your interpretation.</p>
<h2>Find the intended computation</h2>
<p>Use Switch task beside the title and search part of a name, UniProt accession or internal identifier. Tasks are ordered by modification time; type and state filters can be combined. Results load only after confirmation, while Cancel preserves the current model. Refresh list discovers new tasks and updates the list.</p>
<p>The current task, identifier and actual directory remain visible at the top. For duplicate names, verify the timestamp and directory. Copy path gives the download location without guessing whether it is under output or infer_data.</p>
<h2>Ordinary predictions and screens have different outputs</h2>
<p>A monomer prediction mainly exposes models, PAE and confidence; an absent interaction-ranking table is normally expected. Pulldown and Scan provide pair rankings and may include matrices, fragment views and summary reports. Partial results can appear before a screen finishes, so check Dashboard for completeness.</p>
<p>The A/B interface score used here is the mean of chain-pair ipTM values across the two sides. It is not the same quantity as the overall ipTM of a multichain complex. Check compositions and sampling conditions before comparing different systems.</p>
<p>Hit summary lists the highest-scoring fragment combination for each parent protein pair; Protein matrix displays that maximum for each pair. Fragment overview helps locate the sequence ranges. These summarize prediction scores, not confirmed interactions.</p>
<h2>Scores answer different questions</h2>
<p>pTM relates to confidence in the overall structure, ipTM concerns interchain arrangement, and ranking score orders models. They are not interchangeable success probabilities. Consider disorder, clash indicators and agreement across seeds rather than selecting only the highest row.</p>
<p>A high-scoring prediction is a candidate for inspection, not experimental proof of binding. If a score appears in only one sample, involves a tiny interface or depends on uncertain regions, open the structure and examine whether the contact makes sense for your system.</p>
<h2>Reading the PAE image</h2>
<p>Select and load a model. PAE reports predicted error in the relative position of one location when aligned on another, in Å. Low values indicate greater confidence in relative placement; high values indicate uncertainty. Read the color scale rather than treating a color as an interaction verdict.</p>
<p>Low-PAE blocks near the diagonal help identify relatively stable local regions. Cross-chain regions help assess the relative placement of chains. Large matrices may be downsampled for responsive display; domain analysis still uses full-precision data.</p>
<h2>Moving from PAE to fragments</h2>
<p>Open the PAE domain controls and Apply to obtain domains, final fragments and red dashed boundaries. The cutoff controls graph connections and resolution controls clustering granularity; neither is an interaction-hit threshold. The PAE domains and windows topic explains these settings inline, including how five domains can become two fragments.</p>
'''),
('pae', 'PAE 分域与窗口', 'PAE domains and windows', '''
<p>这里有两个不同的结果：PAE 结构域来自聚类；片段（window）是最终供 Scan 使用的连续序列范围。先识别结构域，再根据窗口参数组装或合并片段。因此，两种数量不必相等。</p>
<p class="route">模型 PAE → 聚类结构域 → 组装/合并窗口 → 最终片段与红色虚线</p>
<h2>用人工示意理解区别</h2>
<p>以下坐标为人工示意：链 A 得到 5 个结构域，D1 为 1–100，D2 为 101–200，D3 为 201–300，D4 和 D5 包含后半段的不连续残基。应用窗口规则后，D1+D2+D3 组成 1–300，D4+D5 组成 301–500。最终得到两个连续片段，图里的红色虚线也按这两个片段画。</p>
<pre>共 5 个PAE结构域；共切分为 2 个片段

片段 1：D1 + D2 + D3   1–300     300 aa
片段 2：D4 + D5        301–500   200 aa</pre>
<p>报告中 Domains 的 segments 记录聚类包含的实际残基；cut/span 是用于连续切片的跨度。不连续结构域可能存在相互交叠的跨度，需要结合最终 Fragments 阅读。不要把 Domains 中的行数直接当作下一步的任务数。</p>
<h2>分域参数：决定先找到哪些结构域</h2>
{pae_detection}
<h2>窗口参数：决定最终怎样送去扫描</h2>
{pae_windows}
<h2>每次调参后看三处</h2>
<p>先看应用按钮旁的数量摘要，再看图中的红色边界，最后看 Fragments 的起止坐标与长度。三处描述的是同一次分析；如果窗口变少，检查是否有短片段被合并，而不是仅根据数量认为分析失败。</p>
<p>“应用”只对当前模型重新进行分析，不会提交新的推理，也不会改动已运行的 Scan 计划。确定新窗口适合研究目的后，再在扫描提交页检查相应设置和执行预览。</p>
''', '''
<p>There are two outputs: PAE domains come from clustering, while fragments (windows) are the final contiguous sequence ranges used for Scan. Domains are identified first, then assembled or merged under the window rules. Their counts need not match.</p>
<p class="route">Model PAE → Domain clusters → Window assembly/merging → Final fragments and boundaries</p>
<h2>A schematic example</h2>
<p>These coordinates are schematic: chain A yields five domains, with D1 spanning 1–100, D2 101–200 and D3 201–300, while D4 and D5 contain discontinuous residues in the later region. Window rules can combine D1+D2+D3 into 1–300 and D4+D5 into 301–500. The two final fragments determine the red dashed boundaries.</p>
<pre>Total: 5 PAE domains; split into 2 fragments

Fragment 1: D1 + D2 + D3   1–300     300 aa
Fragment 2: D4 + D5        301–500   200 aa</pre>
<p>In Domains, segments lists actual clustered residues; cut/span describes the range used for contiguous slicing. Discontinuous domains can have overlapping spans, so read them alongside the final Fragments report. The number of domain rows is not the number of future prediction tasks.</p>
<h2>Detection settings: which domains are found</h2>
{pae_detection}
<h2>Window settings: what Scan will use</h2>
{pae_windows}
<h2>Check three things after adjusting parameters</h2>
<p>Read the count beside Apply, inspect the red boundaries, then check final Fragments ranges and lengths. These describe the same analysis. If the window count decreases, look for merged short fragments rather than assuming that analysis failed.</p>
<p>Apply reanalyses the current model. It does not submit inference or change an existing Scan plan. Once the windows suit your question, verify the corresponding settings and execution preview on the Scan submission page.</p>
'''),
('files', '取回文件与清理数据', 'Retrieving and cleaning files', '''
<p>GUI 运行在集群，所以文件选择器中的“本地”也是集群文件系统。想把模型下载到自己的电脑，需要通过 MobaXterm SFTP、其他 SFTP 客户端或 scp 传输。界面的复制路径功能帮助你找到正确位置。</p>
<h2>从界面走到模型文件</h2>
<p>在结果页确认任务，然后点击“复制路径”或模型旁的“复制目录路径”。把目录粘贴到 MobaXterm 的 SFTP 地址栏，回车进入，再下载所需文件。地址栏应使用目录，不是某个 .cif 文件的完整路径。</p>
<p>通常需要带走结构 .cif、相应置信度 JSON，以及筛选任务的排名 CSV。若要保留可复现记录，也保存批次的 spec.json、输入和报告。完整置信度 JSON 可能很大；界面里的“复制内容”只适合较小文本，大文件直接下载。</p>
<h2>为什么有时有模型，却没有排名表</h2>
<p>单体预测通常没有 A/B 筛选排名。筛选任务中若模型已完成、汇总结果尚未生成，可在任务监控的“更多”中重新汇总结果，并阅读反馈。不要仅因为缺少 CSV 就重新提交全部 GPU 计算。</p>
<h2>清理一个批次与清理共享池是两件事</h2>
<p>先确认要保留的文件已经下载或备份，再在任务监控选中准确的任务，通过“更多”进入清理。仔细核对将处理的目录，尤其是有同名任务时。运行中的任务仍需要计划、输入、日志和结果目录，不应边运行边删除。</p>
<p>批次清理不会顺便删除整个共享 MSA 池。其他批次可能仍引用同一份 MSA 或推理产物；JAX 缓存删除后也可能造成后续重新编译。清理空间时按实际用途判断，不要看到 cache 或 pool 就认为可以全部删除。</p>
<h2>需要别人帮助排查时</h2>
<p>在公开 Issue 中提供软件版本、任务阶段、错误信息和最小复现步骤。发布前将日志及计划中的真实蛋白、序列、课题标签、账号和目录脱敏；不要直接上传原始 spec.json 或截图。必要时用人工输入重新生成最小示例。</p>
''', '''
<p>The GUI runs on the cluster, so its file chooser also refers to the cluster filesystem. To bring models to your computer, use MobaXterm SFTP, another SFTP client or scp. Copy-path actions help locate the correct files.</p>
<h2>From the interface to your model</h2>
<p>Confirm the task in Results, then copy its path or the selected model's folder path. Paste that directory into MobaXterm's SFTP address bar, open it and download the files. The address bar expects a directory rather than a full .cif filename.</p>
<p>Typical downloads include the structure .cif, its confidence JSON and ranking CSV for screens. For reproducibility, also retain the batch spec.json, inputs and report. Full confidence JSON can be large; Copy content suits small text, while large files should be transferred directly.</p>
<h2>A model can exist without a ranking table</h2>
<p>Monomer predictions normally lack A/B screening rankings. If a screen has completed models but lacks aggregated outputs, use the aggregation action under Dashboard's More menu and inspect its feedback. A missing CSV alone is not a reason to resubmit all GPU work.</p>
<h2>Batch cleanup is different from pool cleanup</h2>
<p>Download or back up what you need, select the correct task in Dashboard and open cleanup through More. Verify the actual directory, especially for duplicate names. Running tasks still require their plans, inputs, logs and output directories; do not delete them during execution.</p>
<p>Cleaning a batch does not also erase the shared MSA pool. Other batches can still reference an alignment or inference product. Removing JAX caches can require later recompilation. Decide what to remove by its role rather than assuming everything named cache or pool is disposable.</p>
<h2>When asking for help</h2>
<p>For a public issue, provide the software version, failing stage, error and minimal reproduction steps. Remove real proteins, sequences, project labels, accounts and paths from logs or plans before posting. Avoid uploading original spec.json files or screenshots; regenerate a minimal example with synthetic inputs when needed.</p>
'''),
('faq', '常见问题与排查', 'Troubleshooting', '''
<h2>窗口打不开，出现 No X11 或 DISPLAY 错误</h2>
<p>这是图形转发没有建立。使用 MobaXterm 时确认 X server 已启动、SSH 会话启用了 X11 forwarding；或者使用支持 X11 的终端以 ssh -X 登录。重新建立会话后再启动 GUI。安装图形库不能代替建立图形转发。</p>
<h2>第一次启动慢，之后却快很多</h2>
<p>首次加载 Qt、字体和绘图库时可能建立缓存，网络盘也会放大启动开销。如果没有报错，先给首次启动一点时间。持续卡住则查看启动终端中的错误，而不是反复打开多个实例。字号和主题可以用右上角按钮调整。</p>
<h2>UniProt 编号获取失败</h2>
<p>先核对输入是 accession（例如 P12345），而不是常用基因名称。再看反馈区是否出现网络、DNS 或请求错误。已有缓存可能仍可用；你也可以使用可信的完整序列，以 p: 前缀直接输入。省略号不是合法序列，不要从示意文本中原样复制进去。</p>
<h2>刷新失败，但文件明明还在</h2>
<p>刷新涉及调度器查询和本地数据读取。Slurm 错误不代表结果丢失；先看提示条及上次成功查询时间，已有结果仍能浏览。若错误来自旧批次固定的运行代码，单独更新 GUI 不会把旧快照自动换新。</p>
<h2>MSA 完成了，为什么没有推理</h2>
<p>首先检查是否选择了“仅生成 MSA”。若是完整预测，再看控制器状态：它可能在确认产物、等待 GPU 提交，也可能已经失败或超时。MSA 离开队列不能证明产物有效。查看控制器及 MSA 日志可以区分这些情况，避免盲目重复提交。</p>
<h2>同一个名称出现两次，或者目录有一串后缀</h2>
<p>名称是你给的标签，后缀记录计算身份。不同种子、输入或设置可能使用同一个标签，但对应不同目录。选择时看时间和短标识，详情中核对完整路径。已确认同一产物的旧池条目会合并，但真实不同的计算仍分别保留。</p>
<h2>点击帮助里的操作后页面会不会再变空</h2>
<p>帮助正文直接展开说明，示例旁的“复制”只把对应表达式放进剪贴板，不会跳转页面。左侧搜索用于筛选相关章节，清空搜索可恢复全部目录。帮助在离线状态也可阅读。</p>
<h2>重试、改参数和关闭窗口会怎样</h2>
<p>重试沿用原计划与种子，--force 创建新尝试；两者用途不同。修改设置或编辑原始输入不会改变已提交的快照。关闭 GUI 也不会取消集群已受理的作业。如果确实需要停止计算，应根据准确作业编号单独处理。</p>
''', '''
<h2>No window: No X11 or DISPLAY error</h2>
<p>The graphical forwarding connection is missing. In MobaXterm, check that the X server is running and X11 forwarding is enabled for the SSH session, or log in using ssh -X from an X11-capable environment. Start the GUI after reconnecting. Installing graphics libraries cannot replace establishing forwarding.</p>
<h2>The first launch is slow</h2>
<p>Qt, fonts and plotting libraries may build caches on first use, with network filesystems adding latency. If there is no error, allow time for initialization. If it remains stuck, inspect the launch terminal rather than repeatedly opening more instances. Font size and theme controls are at the top right.</p>
<h2>A UniProt lookup fails</h2>
<p>Check that the input is an accession such as P12345, not a familiar gene name. Look for network, DNS or request errors in feedback. Existing cached sequences may still work; alternatively, enter a trusted complete sequence prefixed with p:. An ellipsis is not valid sequence and must not be copied from an abbreviated illustration.</p>
<h2>Refresh fails although result files exist</h2>
<p>Refresh reads both scheduler and local data. A Slurm failure does not mean results were lost. Inspect the notice and last successful query time; existing results remain browsable. Updating the GUI does not automatically replace runtime snapshots pinned by old batches.</p>
<h2>MSA finished but inference did not follow</h2>
<p>First check whether MSA-only was selected. For end-to-end work, inspect the controller: it may be validating products, arranging inference, or may have failed or timed out. An MSA job leaving the queue is not proof of a valid product. Controller and MSA logs distinguish these cases without blindly resubmitting work.</p>
<h2>Duplicate names or a long directory suffix</h2>
<p>The name is your label; the suffix distinguishes computation identity. Different seeds, inputs or settings can share a label but use separate directories. Check timestamps, short identifiers and full paths. Confirmed aliases of older pool products are combined, while genuinely different computations remain separate.</p>
<h2>Using help without losing the page</h2>
<p>Explanations appear directly in the article. Copy beside an example places only that expression on the clipboard and does not navigate away. Search narrows the topic list; clear it to restore all topics. The guide is available offline.</p>
<h2>Retrying, editing settings and closing the window</h2>
<p>Retry keeps the original plan and seeds; --force creates a new attempt. Editing settings or original inputs does not change a submitted snapshot. Closing the GUI does not cancel accepted cluster jobs. Stopping computation requires a separate action against the correct job IDs.</p>
'''),
]


def _expression_body(lang):
    zh = lang == 'zh'
    body = (_paragraphs('每一行是一项完整预测，+ 把多个实体放进同一复合物，xN 指定拷贝数，: 后面追加截短或修饰。下面的示例按用途排列。表格左边是输入，右边解释它会得到什么。\n\n示例编号和序列用于说明写法，请替换成自己的研究对象。残基编号、修饰位置及配体原子名必须与实际输入匹配。点击“复制”只复制表达式，不会带上右侧说明，也不会自动提交。') if zh else
            _paragraphs('Each line is a complete prediction. + joins entities into one complex, xN specifies copies, and : appends truncation or modification options. The examples below are grouped by purpose. Inputs are on the left and explanations on the right.\n\nExample accessions and sequences illustrate syntax; replace them with your research inputs. Residue numbers, modifications and ligand atom names must match the actual input. Copy places only the expression on the clipboard, without the explanation or automatic submission.'))
    reference = EXPRESSION_REFERENCE[0 if zh else 1]
    for paragraph in reference.split('\n\n')[1:]:
        lines = paragraph.splitlines()
        heading = lines[0].strip('【】[]')
        if heading in ('要点', 'Key points'): continue
        rows = []
        for line in lines[1:]:
            parts = re.split(r'\s{2,}', line.strip(), maxsplit=1)
            if len(parts) != 2: continue
            expression, explanation = parts
            # Raw-protein metasyntax is explained separately, never copied as input.
            if 'p:' in expression: continue
            key = 'ref_' + str(len(EXAMPLES))
            EXAMPLES[key] = expression
            rows.append(('<code>'+html.escape(expression)+'</code>',
                         html.escape(explanation.lstrip('# ').strip()),
                         '<a href="example:'+key+'">'+('复制' if zh else 'Copy')+'</a>'))
        body += '<h2>'+html.escape(heading)+'</h2>'+_table(('表达式','说明','') if zh else ('Expression','Meaning',''), rows)
    body += ('''<h2>多行提交和复合物，只有一个换行的区别</h2>
<pre># 两个独立预测
P12345
Q12345

# 一个复合物预测
P12345+Q12345</pre>
<p>在 Pulldown/Scan 中，每行则代表 A 或 B 中的一整侧，可以同样使用 + 和 xN。页面上的任务名称用于给计算命名；表达式里的 :name= 用于标记对应实体，不应把两者混淆。</p>
<h2>截短、修饰和配体要注意什么</h2>
<p>:trunc=1-300 表示包含两端的第 1 到第 300 位。普通截短会为片段准备匹配比对；Scan 的共享全长 MSA 是另一种裁剪复用方式。修饰与截短同时使用时，请检查处理后的编号。输入中的省略号必须换成完整序列。</p>
<p>l:NAG,FUC 描述配体成分，但仅列出成分并不能代替你需要的共价键描述。SMILES 中的原子和键符号也是表达式的一部分，复制后应先解析核对。链数、拷贝数和配体同时增加时，先看执行计划确认规模。</p>
<h2>共价键与更复杂的输入</h2>''' if zh else '''<h2>Separate predictions or one complex?</h2>
<pre># Two independent predictions
P12345
Q12345

# One complex prediction
P12345+Q12345</pre>
<p>In Pulldown/Scan each line describes a whole side in A or B, using the same + and xN syntax. The page's task name labels the computation; :name= inside an expression labels the entity.</p>
<h2>Truncation, modifications and ligands</h2>
<p>:trunc=1-300 selects residues 1 through 300, inclusive. Ordinary truncation prepares matching fragment alignments; Scan's shared full-length MSA uses a different cropped-reuse workflow. Inspect processed numbering when combining modifications and truncation. Replace any illustrative ellipsis with a complete sequence.</p>
<p>l:NAG,FUC specifies ligand components, but listing components does not replace the covalent-bond description you may need. SMILES atom and bond symbols are part of the input; parse the expression after copying it. Review the plan when chain counts, copy counts and ligands grow together.</p>
<h2>Covalent bonds and more elaborate inputs</h2>''')
    body += _parameter_body('bonds', lang)
    body += (_paragraphs('裸蛋白序列的语法是 p:<synthetic_sequence>，可追加 :name=your_label。尖括号中的内容是说明用占位符，必须替换成你有权使用的完整氨基酸序列；它不是可直接运行的示例。') if zh else
             _paragraphs('Raw protein syntax is p:<synthetic_sequence>, optionally followed by :name=your_label. The angle-bracketed text is metasyntax: replace it with a complete amino-acid sequence you are entitled to use. It is not an executable example.'))
    body += (_paragraphs('自定义 CCD 在高级选项中提供集群可读的 .cif 路径。若需要自定义 MSA、模板或表达式没有覆盖的组合，选择“导入 AF3 JSON”，直接提供符合当前 AF3 版本格式的完整输入。') if zh else
             _paragraphs('Provide custom CCD through a cluster-readable .cif path in Advanced options. For custom MSA, templates or combinations not represented by expressions, choose AF3 JSON and supply a complete input matching the installed AF3 version.'))
    return body


_expression_zh, _expression_en = _expression_body('zh'), _expression_body('en')
GUIDES.insert(3, ('expressions', '表达式与完整示例', 'Full expression reference', _expression_zh, _expression_en))

# Only these shipped files can be opened from a license: link.
LICENSE_FILES = {
    'project': ('AF3 Console — MIT', 'LICENSE'),
    'notices': ('Third-party notices', 'THIRD_PARTY_NOTICES.md'),
    'chimerax': ('ChimeraX PAE — upstream copyright', 'LICENSES/ChimeraX-PAE-COPYRIGHT.txt'),
    'lgpl': ('ChimeraX adaptation — LGPL-2.1-only', 'LICENSES/LGPL-2.1-only.txt'),
    'networkx': ('NetworkX — BSD-3-Clause', 'LICENSES/BSD-3-Clause-NetworkX.txt'),
    'apache': ('Apache License 2.0', 'LICENSES/Apache-2.0.txt'),
    'font-readme': ('WenQuanYi Micro Hei — README', 'LICENSES/WenQuanYi-MicroHei-README.txt'),
    'font-authors': ('WenQuanYi Micro Hei — authors', 'LICENSES/WenQuanYi-MicroHei-AUTHORS.txt'),
    'font-apache': ('WenQuanYi Micro Hei — Apache notice', 'LICENSES/WenQuanYi-MicroHei-LICENSE_Apache2.txt'),
    'font-gpl': ('WenQuanYi Micro Hei — upstream GPL notice', 'LICENSES/WenQuanYi-MicroHei-LICENSE_GPLv3.txt'),
}


def license_text(key):
    """Return the complete bundled text without network or arbitrary path access."""
    if key not in LICENSE_FILES:
        raise ValueError('Unknown license entry / 未知许可条目')
    title, relative = LICENSE_FILES[key]
    path = Path(__file__).resolve().parent / relative
    data = path.read_bytes()
    try:
        content = data.decode('utf-8')
    except UnicodeDecodeError:
        # Historical upstream notices contain Latin-1 copyright symbols.
        content = data.decode('latin-1')
    return title, content


def _license_links(lang):
    text = '查看离线全文' if lang == 'zh' else 'Read complete offline text'
    return '<ul>' + ''.join('<li>'+html.escape(title)+' — <a href="license:'+key+'">'+text+'</a></li>'
                           for key, (title, _) in LICENSE_FILES.items()) + '</ul>'


GUIDES.extend([
    ('about', '关于 UI / About', 'About the UI / 关于',
     '<p><b>AF3 Console '+html.escape(R.VERSION)+'</b></p>'
     '<p>Copyright © 2026 PKU-Gaolab, Ming-Ao Lu. 原创 UI 与工作流代码采用 MIT 许可；第三方组件保留其各自许可。'
     '<a href="license:project">查看 MIT 许可全文</a>。</p>'
     '<p>这是面向 Slurm 集群的独立桌面与命令行工具，用于准备、提交、监控和查看 AlphaFold 3 任务。'
     '本项目不是 Google DeepMind、UCSF、ChimeraX、ISOLDE 或 NetworkX 的官方产品，也不暗示它们的认可。</p>'
     '<p>AlphaFold 3 软件、模型权重和数据库需自行按上游条款获取；UI 的 MIT 许可不授予这些资源的再分发或商业使用权限。</p>'
     '<p>PAE 结构域分析包含对 ChimeraX PAE 分域实现的改编；上游说明将该方法追溯至 Tristan Croll 的 ISOLDE 工作。'
     '图聚类与映射队列实现包含 NetworkX 来源代码。详细来源与许可见 '
     '<a href="help:licenses">第三方许可与致谢</a>。</p>',
     '<p><b>AF3 Console '+html.escape(R.VERSION)+'</b></p>'
     '<p>Copyright © 2026 PKU-Gaolab, Ming-Ao Lu. Original UI and workflow code is licensed under MIT; '
     'third-party components retain their respective licenses. <a href="license:project">Read the MIT license</a>.</p>'
     '<p>An independent desktop and command-line workbench for preparing, submitting, monitoring and reviewing '
     'AlphaFold 3 jobs on Slurm clusters. This project is not affiliated with or endorsed by Google DeepMind, '
     'UCSF, ChimeraX, ISOLDE or NetworkX.</p>'
     '<p>Obtain AlphaFold 3 software, model weights and databases separately under their upstream terms. '
     'The UI MIT license does not grant redistribution or commercial-use rights for those resources.</p>'
     '<p>PAE domain analysis includes an adaptation of ChimeraX PAE domain code; its upstream documentation '
     'credits Tristan Croll and ISOLDE. Graph clustering and mapped queues include code from NetworkX. '
     'See <a href="help:licenses">Third-party licenses and acknowledgements</a> for provenance and terms.</p>'),
    ('licenses', '第三方许可与致谢 / Licenses', 'Third-party licenses / 许可',
     '<p>本页面和以下许可全文均可离线阅读。代码出处、修改范围和核验版本记录在 '
     '<a href="license:notices">THIRD_PARTY_NOTICES.md</a>。</p>'
     '<p><b>ChimeraX / UCSF RBVI：</b>PAE 分域改编代码按对应文件的 LGPL-2.1-only 许可分发。'
     '其算法来源链包括 Tristan Croll 的 ISOLDE；本项目不将 PAE 图分域方法宣称为原创。</p>'
     '<p><b>NetworkX：</b>社区聚类和映射队列来源代码采用 BSD-3-Clause。'
     '<b>文泉驿微米黑字体：</b>本发行版选用上游允许的 Apache 2.0 许可，并保留上游作者、说明与双许可文件。</p>'
     '<p>PAE 分域用于识别相对置信的残基组，结果受参数影响，不等同于经过实验验证的结构域注释。</p>' + _license_links('zh'),
     '<p>This page and the complete license texts below are available offline. Sources, modifications and '
     'verified revisions are recorded in <a href="license:notices">THIRD_PARTY_NOTICES.md</a>.</p>'
     '<p><b>ChimeraX / UCSF RBVI:</b> adapted PAE domain code is distributed under the LGPL-2.1-only '
     'license specified by the upstream file. Its provenance includes Tristan Croll and ISOLDE. '
     'This project does not claim to have invented PAE graph domain clustering.</p>'
     '<p><b>NetworkX:</b> community clustering and mapped queue source code is licensed under BSD-3-Clause. '
     '<b>WenQuanYi Micro Hei:</b> this distribution selects the permitted Apache 2.0 license and preserves '
     'the upstream authors, readme and dual-license files.</p>'
     '<p>PAE clustering identifies groups of relatively confident residues. Its boundaries depend on parameters '
     'and are not experimentally validated domain annotations.</p>' + _license_links('en')),
])
HELP_SECTIONS = []
for key, zh, en, zb, eb in GUIDES:
    if key == 'modes':
        zb += '<h2>从已完成的 MSA 继续推理</h2><p>在任务看板选择已完成的 MSA-only 任务，点击“继续推理”。也可运行 <code>python -B af3.py continue --spec /path/to/your/batch/spec.json</code>。程序复用原计划的输入和种子，验证 MSA 缓存，创建独立的推理执行记录；重复操作不会重复提交，原计划和运行快照保持不变。</p><p>预览不会固定随机种子。需要精确复现预览时，请填写明确的 seeds；使用随机种子时，正式提交才生成并保存最终种子。</p>'
        eb += '<h2>Continue a completed MSA plan</h2><p>Select a completed MSA-only task in Dashboard and click Continue inference, or run <code>python -B af3.py continue --spec /path/to/your/batch/spec.json</code>. The new inference execution reuses the original inputs and fixed seeds after validating MSA assets. Repeating the action does not submit another execution. The source plan and runtime snapshot remain intact.</p><p>A preview does not freeze random seeds. Enter explicit seeds to reproduce the preview exactly; otherwise final seeds are generated and saved on submission.</p>'
    if key == 'pae':
        zb += '<h2>后台计算与取消</h2><p>读取、分域和绘图在独立后台进程中运行。“取消分析”会停止当前请求；更换模型、再次应用参数或关闭窗口会取消过时工作。PAE 分域使用 float64 精度，稠密图的内存可能明显高于矩阵文件大小。</p>'
        eb += '<h2>Background work and cancellation</h2><p>Loading, domain analysis and plotting run in an isolated background process. Cancel analysis stops the current request; changing models, applying new parameters or closing the window cancels obsolete work. Analysis uses float64 precision. Dense graphs may require substantially more memory than the matrix file.</p>'
    rendered = []
    for lang, title, body in (('zh', zh, zb), ('en', en, eb)):
        # Only these known placeholders are replaced; ordinary text is left alone.
        for marker, keys in (
                ('shared_msa', ('shared_msa',)), ('seeds', ('seeds',)), ('force', ('force',)), ('topk', ('topk',)),
                ('pae_detection', ('pae_cutoff','pae_resolution','pae_min_domain')),
                ('pae_windows', ('pae_domains_per_window','pae_min_frag','pae_max_frag'))):
            if '{'+marker+'}' in body:
                body = body.replace('{'+marker+'}', _inline_parameters(keys,lang))
        rendered.append('<h1>'+html.escape(title)+'</h1>'+body)
    HELP_SECTIONS.append((key, zh, en, *rendered))

PARAM_HELP = {k:(v[0],v[2]) for k,v in PARAMETERS.items()}
PARAM_HELP['expressions'] = ('表达式与完整示例','')
EXPRESSION_HELP = EXPRESSION_REFERENCE[0]


def article_html(key, lang='zh', palette=None):
    sec = next((x for x in HELP_SECTIONS if x[0] == key), HELP_SECTIONS[0])
    return _document(sec[3 if lang == 'zh' else 4], palette)


def expression_reference_html(lang='zh', palette=None):
    return article_html('expressions', lang, palette)


def parameter_html(key, lang='zh', palette=None):
    aliases = {'page_setup':'setup', 'page_submit':'modes', 'page_scan':'screen', 'page_pulldown':'screen',
               'page_dashboard':'progress', 'page_results':'results', 'page_help':'quickstart',
               'expressions':'expressions'}
    if key in aliases:
        topic = aliases[key]
        sec = next(x for x in HELP_SECTIONS if x[0] == topic)
        return sec[1 if lang == 'zh' else 2], article_html(topic, lang, palette)
    if key not in PARAMETERS:
        return ('参数说明','Parameter help')[lang != 'zh'], _document('',palette)
    title = PARAMETERS[key][0 if lang == 'zh' else 1]
    return title, _document('<h1>'+html.escape(title)+'</h1>'+_parameter_body(key,lang), palette)
