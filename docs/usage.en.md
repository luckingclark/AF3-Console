# AF3 Console usage

[Chinese guide](usage.zh-CN.md) · [Home](../README.md) · [Input formats](inputs.en.md)

Run commands below from the unpacked software directory unless a step says otherwise. Installation begins with Conda environment creation; cluster resources are configured afterward.

## Before installing

You need access to a **Linux Slurm cluster**, an AlphaFold 3 **Singularity container**, its separately obtained **model parameters and databases**, and a Python environment visible to the compute nodes. The GUI uses **X11 forwarding**. AF3 Console supplies the UI and orchestration scripts; it does not include the prediction engine, GPU drivers, parameters, databases, or a Python environment.

Desktop preview and synthetic tests can run without those cluster resources. Real prediction jobs require a working cluster deployment. This is an early release: the exact checks performed and untested deployment boundaries are listed in [validation](validation.md).

## What it does

- Prepare expressions or native AF3 JSON for MSA, inference, or a complete run.
- Screen two groups of proteins or complexes, and scan fixed or PAE-guided fragments.
- Reuse compatible MSA assets and preserve task inputs, seeds, code, and configuration snapshots.
- Monitor tasks, continue completed MSA plans, and retry failed work.
- Review ranking, interface confidence, PAE plots, and generated reports in Chinese or English.

![AF3 Console public setup preview](images/setup-en-light.png)

This project focuses on workflow integration and desktop usability. It does not claim a new domain-segmentation algorithm, universal hardware performance, or experimental proof of interaction. PAE-derived domains and confidence scores are computational aids that need scientific interpretation.

## Download and install

Download the repository using **Code → Download ZIP**, or download the lightweight runtime ZIP from **Releases** after a release is published. Unpack it into a directory visible from the cluster's login and compute nodes. Both distributions include readable, replaceable PAE modules and licenses. The runtime ZIP contains fewer development files.

From the unpacked directory, create an isolated environment:

```bash
conda env create -f environment.yml
```

Or use [environment_THUmirrors.yml](../environment_THUmirrors.yml) to download dependencies through the **Tsinghua University TUNA mirrors**, if that route works better for your network. Run **one** environment-creation command, not both:

```bash
conda env create -f environment_THUmirrors.yml
```

Both files create `af3-console` with the same dependency version constraints. The mirror file uses the TUNA conda-forge and bioconda channels with `nodefaults`; no global `.condarc` edit is required. See the [TUNA instructions](https://mirrors.tuna.tsinghua.edu.cn/help/anaconda/). Mirror availability and installation on your cluster still need verification; if the mirror is unavailable, use `environment.yml`. If your existing environment works, skip creation and activate it directly.

After creating the environment with either file, continue:

```bash
conda activate af3-console
python -B af3.py --version
python -B af3.py install-gui
```

Alternatively, on a supported system with Python 3.12:

```bash
python -m venv /path/to/your/af3-console-env
source /path/to/your/af3-console-env/bin/activate
python -m pip install -r requirements.txt
python -B af3.py install-gui
```

Conda is the recommended route for cluster installations. Dependency ranges are declared, rather than exporting a developer's environment or private package-channel URLs. Older Linux/glibc installations may require a separately validated environment; no blanket compatibility claim is made.

The one-time `python -B af3.py install-gui` step registers `af3_gui` in the active Conda environment (or venv). The installation succeeds when it prints `Installed: .../bin/af3_gui`. After activating this environment, run `af3_gui` from any directory. No separate installer file, administrator access or `.bashrc` edit is needed. The command uses the environment's Python and preserves the calling directory. It refuses to replace an unrelated command. After moving the application, rerun this step from its new directory; `command -v af3_gui` should point into the active environment.

Keep the application directory and Python environment accessible to compute nodes. Do not remove an installation while queued or running jobs still reference it.

## Configure your resources

### Import an existing configuration in the GUI (recommended)

If a colleague supplies a configuration JSON, upload it to your cluster account and launch the GUI normally. In Setup, choose **Import configuration JSON**, select the file, adjust your own workspace and other fields, choose **Check resources**, then **Save user settings**.

Import fills the settings without writing files. Saving writes the personal default `~/.config/af3_console/config.json`; an existing file is backed up with a `before-import` suffix. The template is not selected as the save destination. Imported MSA thread counts, compilation buckets and other parameters absent from the form are retained. Unknown fields or invalid values are reported rather than silently discarded.

After this one-time setup, ordinary GUI launches read your personal settings automatically; no repeated import or `AF3_CONFIG` is needed. Saving an import switches this GUI and its future child processes to personal defaults; submitted jobs retain their snapshots. If `.bashrc` or a launch script sets `AF3_CONFIG` / `AF3_BASE`, remove those overrides once. The application does not edit shell startup files.

### Manual configuration (optional)

Either fill out **Setup** in the GUI or copy the example to your user configuration:

```bash
mkdir -p ~/.config/af3_console
cp config.example.json ~/.config/af3_console/config.json
```

Replace every `your_...` and `/path/to/your/...` placeholder. This template is intentionally not a runnable cluster configuration. The required locations are the container file, model parameter directory, and database directory; enter your actual CPU and GPU partition names. The optional SSD cache and fallback GPU partition default to disabled.

`~/AF3` is the default personal work directory, independent of the software location. Setup saves user JSON, not Python source. See [configuration](configuration.md) for precedence, environment overrides, shared paths, and module loading. Never commit your live configuration.

**MSA pools and backups:** use **＋** beside the MSA path to configure 1–3 independent directories. Completed new products are copied to the backups; a startup check asks before synchronizing existing additions between pools. Conflicting products remain untouched, with no overwrite or deletion. Before reuse, incomplete data is rejected and valid empty `unpairedMsa`, `pairedMsa` or `templates` prompts a reuse/recompute/cancel decision. See [MSA backup, synchronization and empty-field handling](msa-pools.md).

### Offline UniProt sequences and shared libraries

If UniProt sequence retrieval fails with `Name or service not known`, `Could not resolve host` or `Resolving timed out`, the reported failure is a network/DNS problem. It does not by itself indicate a shared-directory permission problem. When the cluster cannot reach `rest.uniprot.org`, install this optional library to resolve covered IDs locally. It also avoids repeated online sequence downloads. It does not repair the cluster network or provide other online services.

**Download on an internet-connected computer:** [corrected four-species library ZIP](https://github.com/luckingclark/AF3-Console/releases/download/uniprot-4species-20260930-isoform-fix/AF3_UniProt_4species_20260930_isoform_fix.zip) and its [SHA-256 file](https://github.com/luckingclark/AF3-Console/releases/download/uniprot-4species-20260930-isoform-fix/AF3_UniProt_4species_20260930_isoform_fix.zip.sha256), also available on the [data release page](https://github.com/luckingclark/AF3-Console/releases/tag/uniprot-4species-20260930-isoform-fix). Transfer both files to the cluster with SFTP or your usual file-transfer tool. Under Release assets, choose the named library ZIP; GitHub's automatic **Source code** archives contain the application, not this sequence library. The library is optional and separate from the normal repository download.

This snapshot uses **UniProt 2026_03**, downloaded on **2026-09-30**. The ZIP is about **58 MB**, with about **210 MB** needed for the extracted library; allow additional space for backups.

| Reference proteome | Canonical entries | Additional isoform sequences | Total sequences |
|---|---:|---:|---:|
| E. coli K-12 MG1655 (`UP000000625`) | 4,403 | 12 | 4,415 |
| Human (`UP000005640`) | 147,520 | 22,131 | 169,651 |
| Mouse (`UP000000589`) | 54,855 | 8,473 | 63,328 |
| S. cerevisiae S288C (`UP000002311`) | 6,067 | 31 | 6,098 |
| **Total** | **212,845** | **30,647** | **243,492** |

Counts are UniProt sequence entries, not genes; reviewed and unreviewed entries are included. The corrected index adds **15,904 official canonical isoform aliases**, while all original sequence strings remain unchanged. UniProt may export a canonical sequence under its unsuffixed accession only. Official `ALTERNATIVE PRODUCTS` annotations with sequence status `Displayed` supply its explicit isoform ID. Canonical does **not** always mean `-1`; this library never strips that suffix by guesswork. Five ambiguous historical isoform IDs were omitted and recorded in the manifest. Coverage does not include every strain, variant, secondary or historical accession.

#### Install or replace the library

Use the AF3 Console offline-library update dated **2026-09-30** or later. If already installed, this is a **data-only update**: no GUI, Conda or global-command reinstall is needed. Older installations must first update `af3.py`, `af3_runtime.py` and `af3_gui` together from the current deployment edition, or use a fresh complete `AF3_Console/` folder.

1. Close the GUI and ensure no other process is reading the index being replaced. For a shared library, coordinate the replacement with its users.
2. In the folder containing the transferred ZIP and its `.sha256` file, verify the archive:

   ```bash
   sha256sum -c AF3_UniProt_4species_20260930_isoform_fix.zip.sha256
   ```

   Success is the archive filename followed by `OK`. If verification fails, transfer the file again before installing it.
3. Extract into a temporary folder. The ZIP already contains `uniprot/`. Inside that extracted folder, verify its contents:

   ```bash
   cd /path/to/your/extracted/uniprot
   sha256sum -c SHA256SUMS
   ```

   All listed files should report `OK`.
4. Back up any existing `uniprot.sqlite3` and accompanying metadata outside the active library folder. Merge the extracted `uniprot/` into **Settings → Application cache** only after transfer and verification have finished. Replace the index and bundled metadata, **preserving every existing `.seq` file**. Do not delete the cache folder or extract another `uniprot/` inside it.

   With the default application cache, the final layout is:

   ```text
   ~/AF3/cache/uniprot/
   ├── uniprot.sqlite3
   ├── manifest.json
   ├── NOTICE.txt
   ├── README.zh-CN.md
   ├── SHA256SUMS
   └── existing .seq files, if any
   ```

   A custom application cache uses `<application cache>/uniprot/uniprot.sqlite3`. Keep the manifest and notice beside the database. The application reads the SQLite index directly without a database service, additional Python dependencies or thousands of new small files.
5. Reopen the GUI, enter an actual ID covered by the library, and choose **Parse input**. Seeing its sequence and length without a UniProt retrieval error is the success check; it does not submit a prediction. `P12345`, `Q12345` and `P12345-2` in this guide are syntax placeholders, not guaranteed library test entries. An index hit does not create another `.seq` file.

#### Share one copy within a lab

In **Shared UniProt sequences (optional, read-only)**, enter the directory directly containing `uniprot.sqlite3`, for example `/path/to/your/shared/uniprot`, then save user settings. Keep each user's **Application cache** pointing to their own directory. Shared files must be readable and all parent directories traversable; users need no write permission. Saved settings are reused on later launches.

Run, Pulldown and both Scan modes query **personal `.seq` → personal index → shared `.seq` → shared index → online UniProt**. The application does not synchronize, overwrite or delete the shared library; both indexes are read-only. Online downloads go only to personal `.seq` files. Existing personal sequences take precedence over a newer index. If an ID is absent, prepare its sequence separately or restore network access; the package is not all of UniProt. A checksum failure, unreadable file or incorrect directory nesting must be corrected before offline lookup can work.

This is a **sequence lookup library**, not MSA results, AF3 search databases or model weights. Data are attributed to **The UniProt Consortium** under [CC BY 4.0](https://www.uniprot.org/help/license), independently of the application's MIT license. Retain `NOTICE.txt` and `manifest.json` when sharing it.

**Rebuilding:** the current `source`-branch `packaging/build_uniprot_library.py` builds the base FASTA index but does not yet add the official canonical isoform aliases included in this release. Its output is not equivalent to this corrected package. Use the released ZIP when you need these mappings.

### Hardware reference for Resource limits

These are the development environment's hardware specifications, supplied by the project author on **2026-09-14**, and its existing configuration. They do not constitute a new cluster validation of the public release. The CPU model was not supplied; only scheduler-reported topology and memory are listed. Account, node, partition and deployment-path identifiers are omitted.

| Resource | Node specification | Existing configuration |
|---|---|---|
| CPU node | 2 sockets × 24 cores × 1 thread, 48 CPUs; Slurm `RealMemory=510000 MB` | Small MSA batches (< 6 tasks): `MSA_NTASKS_SINGLE=16`; batches of ≥ 6: `MSA_NTASKS_BATCH=8`. |
| A40 node | 4 × NVIDIA A40 per node; **46,068 MiB** total memory per card reported by `nvidia-smi` | Configure its partition as the primary GPU partition; route total tokens **≤ 3,584** here. |
| A100 node | 8 × NVIDIA A100 80GB PCIe per node; **81,920 MiB** total memory per card | Configure its partition as the fallback GPU partition; route total tokens **> 3,584 and ≤ 7,168** here. |

This describes automatic partition selection. A manual partition override takes precedence; an empty fallback disables automatic routing to A100. The application submits to partitions without detecting GPU models, so verify allocation if a partition contains mixed hardware. Each inference job uses **1 GPU and 12 CPUs** (`INF_GPUS=1`, `INF_NTASKS=12`); the application does not combine the memory of a node's 4 or 8 cards for one task.

| Total tokens | Existing inference policy |
|---|---|
| ≤ 3,584 | A40, regular device-memory settings. |
| 3,585–5,120 | A100 80GB, regular device-memory settings. |
| 5,121–7,168 | A100 80GB with unified memory; sufficient GPU-node host RAM and job memory allowance are needed. |
| > 7,168 | Reject before submission under the configured protective limit. |

The settings are `INF_BIG_TOKEN=3584`, `INF_UM_TOKEN=5120`, `INF_MAX_TOKEN=7168`, with compilation buckets ending at 7,168. Count all entities and copies in a task: for standard proteins this is approximately the sum of chain residue counts; mixed entities require actual tokenization. Preview estimates and bucket padding also affect capacity assessment.

**OOM evidence:** historical development-code comments report an **A100 80GB compilation OOM in the 7,680-token bucket with unified memory enabled**; the existing configuration therefore stops at 7,168. The original log was not available for this review and the test was not rerun. This does not establish that every input above 7,168 fails or every input below it fits. Likewise, 3,584 is the A40-to-A100 routing threshold, not a measured physical A40 limit. See the [AF3 v3.0.2 GPU-memory documentation](https://github.com/google-deepmind/alphafold3/blob/v3.0.2/docs/performance.md#gpu-memory) for the regular 5,120-token settings and unified-memory mechanism. Actual AF3/JAX/CUDA versions, buckets and GPU-node host memory matter; the CPU node's RAM does not establish the GPU nodes' RAM.

**MSA threads and concurrency:** the current script uses 16/8 both as the Slurm CPU request and the per-process Jackhmmer/Nhmmer thread setting. Protein MSA searches can run against four databases concurrently, so 16 threads per search is not a 16-thread total job limit; this configuration can oversubscribe CPUs. These values document the development setup, not an optimum for a 48-core node. Strictly matching total CPU allocation to per-search threads requires separating those settings and changing the script. See the [AF3 data-pipeline documentation](https://github.com/google-deepmind/alphafold3/blob/v3.0.2/docs/performance.md#data-pipeline).

The existing `MSA_MAX_CONCURRENT=12` and `INF_MAX_CONCURRENT=16` are batch scheduling caps, not per-node thread/card counts or promises of free resources; Slurm queues work when resources are unavailable. On another cluster, start validation with one small job at a time and adjust for CPU, RAM, GPUs and quotas. MSA threads, the unified-memory threshold and buckets can be set in user JSON even when absent from the Resource limits panel. Keep the public `config.example.json` as a placeholder template and store actual lab paths and partitions outside the repository.

## Launch the GUI

Connect with X11 forwarding enabled in your SSH client, for example:

```bash
ssh -X your_username@your_login_host
conda activate af3-console
cd /path/to/your/af3-console
python -B af3_gui
```

MobaXterm users can enable its X server and X11 forwarding. In Setup, choose **Check resources**, correct the reported issues, and save. No X11 display is required for CLI help.

In Settings, the import/save/copy/check buttons sit directly below the page title. Drag the divider between panels to adjust their relative sizes. Each page remembers your proportions automatically for the next launch, with separate values for horizontal and vertical layouts. These personal display preferences are separate from the AF3 configuration JSON; no Save button is needed.

## First preview and run

The installed `af3_gui` command works from any directory. Run the `python -B af3.py ...` examples below from the application directory, with the environment activated.

The identifiers below are **syntax placeholders**, not a biological example. Substitute your own identifiers before using the prediction commands. Even `--dry-run` may resolve sequences over the network.

```bash
python -B af3.py run 'P12345+Q12345' --seeds 11 --dry-run
```

The command shows the planning interface without scheduling jobs. After configuring resources and replacing the identifiers, remove `--dry-run` to submit. In the GUI, use Submit → Preview, review the plan, and then submit.

Further syntax examples:

```bash
python -B af3.py run 'P12345' --msa-only
python -B af3.py pulldown 'P12345' 'Q12345' --seeds 11
python -B af3.py scan 'P12345' 'Q12345' --mode win --win 300 --overlap 100
python -B af3.py scan 'P12345' 'Q12345' --mode pae
python -B af3.py status --jobs /path/to/your/batch/spec.json --json
python -B af3.py continue --spec /path/to/your/batch/spec.json
python -B af3.py retry --spec /path/to/your/batch/spec.json
```

PAE-guided scans can first schedule full-length monomer predictions when their required PAE data is missing. Check the plan and resource limits before submitting a large screen. [Input formats](inputs.en.md) provide more details.

## Mode workflows

**Run** predicts specified proteins or complexes; **Pulldown** screens combinations of two input groups; **Scan** generates fixed or PAE-guided fragment windows and screens their combinations.

![Run, Pulldown and Scan converge into MSA, GPU inference and results; Scan offers fixed and PAE-guided windows.](images/workflow-en.svg)

The diagram shows complete prediction workflows. If required full-length monomer PAE is missing, the PAE mode first predicts those monomers. See UI Help for stage controls and MSA strategies.

## Results and upgrades

By default, each batch lives under `~/AF3/output/`; MSA and full-length inference pools live under `~/AF3/msa_data/` and `~/AF3/infer_data/`. Results remain on the cluster. Use the displayed folder paths with your SFTP client to retrieve them. Closing the GUI does not cancel submitted Slurm jobs.

New ordinary UniProt MSA jobs use each protein's ID, independently of the user-supplied run job name or pulldown/scan batch name, and retain AF3's native layout (the ID is a syntax placeholder):

```text
msa_data/
└── P12345/
    └── P12345_data.json
```

The application no longer moves JSON files into the pool root or deletes AF3 output directories and companion files. Different sequences, external MSAs or recorded environments for one ID, or labels that require shortening, use names such as `P12345__<hash>/P12345__<hash>_data.json` to prevent accidental reuse. Truncations and mutations also appear in names; anonymous sequences retain content-based names. Old flat JSON and hash products are left in place. Exact old hash identities remain reusable. Ordinary inputs may also reuse untracked ID products after schema and sequence validation, but their historical container/database environment cannot be determined from filenames. External MSA and MSA-free inputs remain separate.

Pulldown prepares a separate MSA for each ordinary UniProt input. Scan with shared full-length MSA also names each original full-length MSA by ID; derived fragment data includes its residue range and other identity information. Without shared full-length MSA, independently computed fragment MSAs need range identifiers too, rather than all using the same full-length ID.

Timestamped AF3 directories for a single UniProt ID are also supported: only the directory adds a timestamp; the JSON retains the ID:

```text
msa_data/
└── P12345_20260602_140829/
    └── P12345_data.json
```

For untracked results, valid timestamped outputs are tried newest first by directory timestamp, then the plain native directory and old flat file. Automatic lookup by ID expects the directory's ID prefix to match the JSON's ID. Select a particular directory or JSON explicitly when several runs exist. An existing MSA JSON can be used for inference directly:

```bash
python -B af3.py run --infer-only --json /path/to/your/msa_data/P12345_20260602_140829/P12345_data.json
```

Historical outputs can also use custom job names. Text such as `_and_` in a name does not determine the number of inputs; the JSON contents do. Select the JSON explicitly for a custom name or a directory prefix that differs from the filename. Keep or copy the complete output directory together with companion files and relative references.

The pool's `.msa_records/` stores identity and completion state; `.locks/` coordinates writes. New jobs become ready only after successful exit and output validation. Failed files remain for diagnosis but are not completed products. Preserve these records when copying a pool; deleting them is not a way to force reuse. Existing task snapshots keep their original storage behavior; the new layout applies to tasks created with this version.

Install a new release in a new directory and keep your user configuration. Do not move or delete an installation, Python environment, or task snapshot that queued/running jobs still reference. Retry and continue use task-specific state; do not manually edit its fixed plan. Shared cache changes and a newer application version can require recomputation.

## Troubleshooting

| Symptom | Check |
|---|---|
| No GUI / empty `DISPLAY` | Enable X11 forwarding and the local X server. |
| Qt xcb or platform plugin error | Check the Linux libraries required by your installed Qt build; changing Python code will not install system libraries. |
| Resource check fails | Replace template paths and partitions; check file permissions and compute-node visibility. |
| Container is available only via a module | Set `CONTAINER_MODULE`; the job loads it before checking the runtime. |
| Setup changes appear overridden | Inspect `AF3_CONFIG` and `AF3_BASE` in the launch environment. |
| OOM or rejected token count | Inspect GPU capacity and configured thresholds; there is no universal safe maximum. |
| A result directory exists but the task failed | Read the exit receipt and logs; a directory alone does not prove success. |

Before opening an issue, remove research targets, sequences, unpublished results, hosts, usernames, private paths, and credentials from logs/screenshots.

## Development

Bug reports and focused pull requests are welcome. For development, download the source archive or clone the repository: the runtime ZIP omits tests and build tools. Use artificial data or a public, shareable reproducer. Remove protein names, sequences, unpublished results, usernames, hosts, account information, and paths from screenshots and logs before posting.

Install the environment described in the README, then run:

```bash
python -m pip install -r requirements-dev.txt
python -B merge_gui.py
python -B -m unittest discover -s tests -v
python -B packaging/audit_release.py
python -B packaging/build_workflow_diagram.py --check
```

Edit the readable GUI modules and regenerate `af3_gui`; do not edit its compressed payload. A release must contain matching sources and payload hashes. Update both help languages when changing behavior. Use temporary directories and mock Slurm/network access in tests; never submit real jobs in CI.

The README overview is a self-contained SVG with editable Mermaid sources at `docs/images/workflow-en.mmd` and `docs/images/workflow-zh-CN.mmd`. Edit both languages, then run `python -B packaging/build_workflow_diagram.py` to regenerate the presentation layout. Changes to the graph's structure also require updating the layout in that script. The generator uses the Python standard library and system font fallbacks; it bundles no proprietary fonts.

Original contributions use MIT unless the file states otherwise. Keep the LGPL-2.1-only PAE implementation and BSD-3-Clause NetworkX implementation under their file-specific licenses, preserve notices, and document changes. See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

Do not change the PAE numerical algorithm in a packaging or UI patch. Algorithm changes need independent expected results and an explanation of the scientific impact. Avoid claims of universal speed or hardware compatibility without reproducible measurements.

For suspected secrets or unpublished research accidentally committed, do not repeat them in a public issue. Contact the project author through a private channel already available to you, or use GitHub private vulnerability reporting if enabled.
