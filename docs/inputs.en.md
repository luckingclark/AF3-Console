# Input formats

[中文](inputs.zh-CN.md) · [Usage](usage.en.md)

Prepare your own input; no research or downloadable synthetic dataset is supplied. `P12345` and `Q12345` are syntax placeholders, not biological examples. Replace them before a real run. Even `--dry-run` may resolve sequences over the network.

| Input | Contents and purpose |
|---|---|
| UniProt expression | An identifier such as `P12345`, or a complex such as `P12345+Q12345`; Run predicts the specified input. |
| Pulldown groups | Two groups of identifiers/expressions; their combinations form the screen. See UI Help for list syntax. |
| Scan inputs | Long protein and candidate partner inputs; fixed-window scans use window length and overlap, PAE scans use monomer confidence data. |
| Native AF3 input JSON | AF3 input schema containing entities and sequences; select explicitly in the GUI or with `--json /path/to/your/input.json`. |
| Existing MSA product | AF3 `*_data.json` plus its complete directory and referenced companions. Preserve native or timestamped directories; schema, sequence and required fields are checked before reuse. Valid empty MSA/template fields require a reuse/recompute decision. |
| Unpaired MSA | `P12345:msa=/path/to/your/alignment.a3m`, compatible with the selected sequence. |
| Monomer PAE | `P12345:pae=/path/to/your/confidences.json`, an AF3 confidence JSON containing the PAE matrix for that sequence. |
| Template | `P12345:tpl=/path/to/your/template.cif:0,1:0,1`, a template mmCIF with zero-based query/template residue mappings. |

Container (`/path/to/your/alphafold3.sif`), model-parameter directory (`/path/to/your/model_parameters`), database directory (`/path/to/your/databases`), and work directory (`/path/to/your/workspace`) are deployment resources, not interchangeable input files. Configure them as described in [configuration](configuration.md). Keep local paths and real sequences out of issues and version control.
