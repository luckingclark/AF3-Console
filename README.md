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

Select the **main** branch, choose **Code → Download ZIP**, and unpack it into your cluster software directory. Keep the entire extracted folder, including fonts and licenses. Run these commands in a **Bash shell**, from the extracted directory containing `environment.yml`:

```bash
conda env create -f environment.yml
conda activate af3-console
cd AF3_Console
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

```bash
python -B af3_gui
```

Success at this stage means the main window opens and **Settings** and **Help → About** are accessible. AF3 resources can be configured afterward. If the display cannot open, check X11 forwarding; see [troubleshooting](docs/usage.en.md#troubleshooting).

## Configure and make your first prediction

1. In **Settings**, set the work directory (default `~/AF3`), AF3 container file, model-parameter directory, database directory, and your CPU/GPU partitions. Use actual cluster paths, not `/path/to/your/...` placeholders. [config.example.json](AF3_Console/config.example.json) describes the configuration fields; it is not a working cluster configuration.
2. Choose **Check resources**, resolve reported problems, and save. Personal settings normally go to `~/.config/af3_console/config.json`. `AF3_CONFIG` selects a different configuration file; `AF3_BASE` overrides the work directory. No `.env` file is required. See [configuration precedence](docs/usage.en.md#precedence-and-persistence).
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

## Validation and scope

[Linux CI](https://github.com/luckingclark/AF3-Console/actions/runs/35969165333) passed the deployment checks and **191 tests, with 0 skipped**, using Python 3.12 on Ubuntu 24.04. Checks include runtime/source consistency, isolated startup, offline licenses, artificial data and mocked scheduling. They do not establish successful execution of a real AF3 prediction on your cluster.

Real Slurm/AF3 execution, a fresh Linux Conda installation, interactive X11 use, GPU token limits and cross-cluster compatibility still require deployment validation. See the [validation record](https://github.com/luckingclark/AF3-Console/blob/03c29106fd23b20928c9591b35b22b2508c9adfd/docs/validation.md). No performance advantage over the [related tools](docs/comparison.md) is claimed.

## Author, licensing and citation

Project author: **PKU-Gaolab, Ming-Ao Lu**. This project's code was developed through AI-assisted **vibe coding using Kimi-K3 and GPT-6**. You can report issues after sanitizing logs and screenshots; readable source and development checks are on the [source branch](https://github.com/luckingclark/AF3-Console/tree/source).

- Original code and documentation: **MIT**; see [LICENSE](LICENSE).
- `af3_pae_domains.py`: **LGPL-2.1-only**, adapted from the UCSF ChimeraX **Color PAE Domains** implementation, retaining credit to Tristan Croll / ISOLDE.
- `af3_networkx_community.py`: **BSD-3-Clause**, adapted from NetworkX clustering and mapped-queue code.
- The bundled font retains its own terms. Full third-party notices and license texts are in [THIRD_PARTY_NOTICES.md](AF3_Console/THIRD_PARTY_NOTICES.md) and [LICENSES/](AF3_Console/LICENSES/), also accessible offline from Help → About.

See [algorithm provenance](https://github.com/luckingclark/AF3-Console/blob/03c29106fd23b20928c9591b35b22b2508c9adfd/docs/algorithm-provenance.md) for sources and modifications. AF3 Console has no official affiliation with Google DeepMind or UCSF ChimeraX. Separately obtained AF3 software, parameters and databases remain subject to their respective terms.

Use [CITATION.cff](CITATION.cff) to cite the software and record the commit/version used. No associated paper or DOI is claimed. Cite upstream methods as applicable; citation does not replace license obligations.
