# AF3 Console

[Chinese](docs/README.zh-CN.md) · [Full usage guide](docs/usage.en.md) · [Related tools](docs/comparison.md)

AF3 Console is a bilingual desktop and command-line workbench for researchers using **AlphaFold 3 on Slurm clusters**. It prepares inputs, submits and monitors jobs, and organizes predicted structures and interaction-screening scores for review.

**Version 0.1.0 — pre-release.** `main` is the ready-to-configure deployment edition; [source](https://github.com/luckingclark/AF3-Console/tree/source) contains readable GUI modules, tests and build tools.

## Three workflow modes

### Run

Predict a specified protein or complex from an expression or AF3 input JSON, with options to run only MSA generation or inference.

### Pulldown

Screen combinations between two groups of proteins or complexes and summarize their prediction scores.

### Scan

Screen fragments against candidate partners using fixed sliding windows (**Window Scan**) or monomer PAE–guided windows (**PAE Domain Window**), and map the scores back to the original sequence coordinates.

Full-length predictions of long proteins may obscure local interaction signals, so excluding candidates solely on a low overall ipTM score can miss potential interactions. Fragment scanning aims to identify regions worth testing and guide truncation design and experimental validation.

Fixed windows can cut through a domain and produce high-scoring apparent interactions that depend on the truncation boundary. PAE Domain Window adapts ChimeraX **Color PAE Domains** to group residues using monomer PAE and construct fragments that better preserve candidate structural units, without requiring existing domain annotations. Whether this reduces false positives still requires systematic comparison and experimental validation.

![One overview of Run, Pulldown and Scan, including the two Scan modes](docs/images/workflow-en.svg)

PAE-guided scanning needs monomer PAE; missing data can require an additional monomer prediction. See [input formats](docs/usage.en.md#input-formats) for complexes, truncations and batch lists. Prediction scores and PAE-derived structural units are candidates for investigation, not experimental confirmation of binding or annotated domains.

## Before installing

For actual predictions, obtain access to:

- A **Linux + Slurm** cluster with CPU and GPU partitions. Submission checks require `sbatch`, `squeue` and `flock`.
- A compatible **AlphaFold 3 container**, its model parameters and databases, obtained separately. Generated jobs invoke `python /app/alphafold/run_alphafold.py` inside the container. The default container runtime is **Singularity**; runtime and optional `module load` settings are configurable.
- A writable work directory and resource paths accessible to the relevant compute nodes.
- **X11 forwarding** for the GUI. The command-line entry does not need a graphical display.

The repository contains the workbench, not the AF3 engine, model weights, databases or research datasets. Ask your cluster administrator for the deployed resources and partitions if you do not have them.

[environment.yml](environment.yml) specifies **Python 3.12**, PySide6, NumPy, pandas, Matplotlib, six, python-dateutil, zstandard and qtawesome, with version constraints. These are the workbench dependencies; installing this environment does not install the AF3 prediction engine.

## Download and install

Select the **main** branch, choose **Code → Download ZIP**, and unpack it.

**Only deploying the application?** Transfer the complete `AF3_Console/` folder plus **one** environment file (`environment.yml` or `environment_THUmirrors.yml`) to the cluster, keeping the environment file beside that folder. Keep everything inside `AF3_Console/`, including `fonts/`, `LICENSES/` and the third-party notices. `docs/`, `.github/`, `.gitattributes`, `.gitignore` and `CITATION.cff` are not needed at runtime; the guides and citation information remain available on GitHub. The root README and LICENSE are repository documentation; the application folder already contains its applicable licenses. Downloading the whole ZIP is also fine. No file needs to be deleted from an existing installation.

In a **Bash shell**, from the directory containing the environment files and `AF3_Console/`, create the environment with the default channels:

```bash
conda env create -f environment.yml
```

Or use [environment_THUmirrors.yml](environment_THUmirrors.yml) to download dependencies through the **Tsinghua University TUNA mirrors**, if that route works better for your network. Run **one** environment-creation command, not both:

```bash
conda env create -f environment_THUmirrors.yml
```

Both files create `af3-console` with the same dependency version constraints. The mirror file uses the TUNA conda-forge and bioconda channels with `nodefaults`; no global `.condarc` edit is required. See the [TUNA instructions](https://mirrors.tuna.tsinghua.edu.cn/help/anaconda/). Mirror availability and installation on your cluster still need verification; if the mirror is unavailable, use `environment.yml`. If your existing environment works, skip creation and activate it directly.

After creating the environment with either file, continue:

```bash
conda activate af3-console
cd AF3_Console
python -B af3.py install-gui
```

### Minimal offline check

From that same `AF3_Console/` directory and activated environment:

```bash
python -B af3.py --version
python -B -c "import af3; print(len(af3.parse_expression('P12345+Q12345', fetch=False)))"
```

Expected output:

```text
AF3 Console 0.1.0
2
```

`2` confirms that the expression was parsed into two protein entries. **P12345 and Q12345 are syntax placeholders.** This check fetches no sequences, submits no jobs and produces no prediction files. It verifies the CLI entry and expression parser; it does not validate AF3 resources or cluster execution.

### Open the GUI

The one-time registration above prints `Installed: .../bin/af3_gui`. On later launches, including new SSH sessions, activate the environment and start the GUI from **any directory**:

```bash
conda activate af3-console
af3_gui
```

Keep the application folder in place. If you move it, run `python -B af3.py install-gui` once from its new directory. No separate installer script or administrator access is needed.

Success at this stage means the main window opens and **Settings** and **Help → About** are accessible. AF3 resources can be configured afterward. If the display cannot open, check X11 forwarding; see [troubleshooting](docs/usage.en.md#troubleshooting).

## Offline sequence library for clusters without UniProt access

If sequence retrieval reports `Name or service not known` or `Resolving timed out`, install the optional [four-species offline library](https://github.com/luckingclark/AF3-Console/releases/tag/uniprot-4species-20260930-isoform-fix) using an internet-connected computer to download and transfer it. The corrected ZIP is about **58 MB**: **243,492 sequences** from E. coli K-12 MG1655, human, mouse and S. cerevisiae S288C, plus **15,904 official canonical isoform mappings** (UniProt **2026_03**).

Place its `uniprot/` folder under **Application cache** (default index: `~/AF3/cache/uniprot/uniprot.sqlite3`), preserving existing `.seq` files. A lab can share one read-only copy through Settings. Existing offline-capable installations need only the corrected data, with no Conda reinstall. See [installation, checksum verification, sharing and success checks](docs/usage.en.md#offline-uniprot-sequences-and-shared-libraries). This optional download is separate from the repository ZIP; it does not contain MSA results, AF3 search databases or weights. UniProt data retain **CC BY 4.0** attribution terms.

## Configure and make your first prediction

1. If you have a configuration JSON, choose **Import configuration JSON** in **Settings**, then adjust it for your account. Otherwise, enter the settings manually. Set the work directory (default `~/AF3`), AF3 container file, model-parameter directory, database directory, and your CPU/GPU partitions. Use actual cluster paths, not `/path/to/your/...` placeholders. [config.example.json](AF3_Console/config.example.json) describes the configuration fields; it is not a working cluster configuration.
2. Choose **Check resources**, resolve reported problems, and save. An imported configuration is saved as your personal default and read automatically on later launches; the template is not selected as the save destination. Personal settings normally go to `~/.config/af3_console/config.json`. `AF3_CONFIG` selects a different configuration file; `AF3_BASE` overrides the work directory. No `.env` file is required. See [configuration precedence](docs/usage.en.md#precedence-and-persistence).
3. Start with **Run** and a small input of your own. For a two-protein complex, join your two actual UniProt IDs with `+`; alternatively use an AF3 input JSON following the [input guide](docs/usage.en.md#input-formats). UniProt expressions can require network access to retrieve sequences.
4. **Preview** the plan and check the task count, stage and seeds before submitting. Preview / CLI `--dry-run` does not submit jobs, but may retrieve sequences and inspect caches; it is not necessarily offline.
5. **Submit**, then follow the job in the Dashboard. A Slurm job ID confirms submission only. For a full prediction, confirm successful completion and inspect the generated structure and confidence files described below.

For batch inputs, scan settings, CLI commands and the CPU/A40/A100 resource reference, follow the [usage guide](docs/usage.en.md). Resource limits are configuration choices, not verified guarantees that a given token count will fit in GPU memory.

### Reusing and backing up MSA data

Settings accept **1–3 MSA pool directories**. New MSA results are copied to the configured pools; at GUI startup, detected additions can be synchronized after confirmation. Synchronization adds missing files without deleting existing data or overwriting conflicting files. Permissions or unavailable destinations can prevent a backup, so check reported results.

Native AF3 output folders are retained, including timestamped folders such as `P12345_20260602_140829/P12345_data.json`. Incomplete MSA data are rejected; valid but empty `unpairedMsa`, `pairedMsa` or `templates: []` trigger a choice to reuse, recompute or cancel. See the [MSA guide](docs/usage.en.md#msa-pools) for the exact scope and limitations.

## Find and verify your results

Paths below use the default work directory. Configuration can change them; the submitted task's recorded path is authoritative.

| Location or file | Purpose and success check |
|---|---|
| `~/AF3/output/` | Task plans, status and logs, plus prediction outputs. Use the Dashboard's task directory; `spec.json` / `plan.json` describe the plan, not proof of completed inference. |
| `~/AF3/msa_data/` | Reusable MSA data, for example `P12345/P12345_data.json`. For an MSA-only run, check this stage's completion and data; no structure is expected. |
| `*_model.cif`, `*_summary_confidences.json` | AF3 structures and confidence summaries used by the viewer. For full predictions, check that expected tasks succeeded and these results can be opened. AF3 output nesting depends on the engine and task. |
| `ranking.csv`, `results_index.json` | Pulldown / Scan score summaries and result index. Check task statuses and missing results before interpreting a ranking. |
| `iptm_profile.csv`, `report.md` | Additional Scan summaries for reviewing scores along the original sequence coordinates. |

Missing results, failed tasks or a partial ranking are not a completed screen. See [results and upgrades](docs/usage.en.md#results-and-upgrades) for retrieval and follow-up commands.

## Author, licensing and citation

Project author: **PKU-Gaolab, Ming-Ao Lu**. This project's code was developed through AI-assisted **vibe coding using Kimi-K3 and GPT-6**. You can report issues after sanitizing logs and screenshots; readable source and development checks are on the [source branch](https://github.com/luckingclark/AF3-Console/tree/source).

- Original code and documentation: **MIT**; see [LICENSE](LICENSE).
- `af3_pae_domains.py`: **LGPL-2.1-only**, adapted from the UCSF ChimeraX **Color PAE Domains** implementation, retaining credit to Tristan Croll / ISOLDE.
- `af3_networkx_community.py`: **BSD-3-Clause**, adapted from NetworkX clustering and mapped-queue code.
- The bundled font retains its own terms. Full third-party notices and license texts are in [THIRD_PARTY_NOTICES.md](AF3_Console/THIRD_PARTY_NOTICES.md) and [LICENSES/](AF3_Console/LICENSES/), also accessible offline from Help → About.

See [algorithm provenance](https://github.com/luckingclark/AF3-Console/blob/03c29106fd23b20928c9591b35b22b2508c9adfd/docs/algorithm-provenance.md) for sources and modifications. AF3 Console has no official affiliation with Google DeepMind or UCSF ChimeraX. Separately obtained AF3 software, parameters and databases remain subject to their respective terms.

Use [CITATION.cff](CITATION.cff) to cite the software and record the commit/version used. No associated paper or DOI is claimed. Cite upstream methods as applicable; citation does not replace license obligations.
