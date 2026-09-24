# PAE domain algorithm provenance

AF3 Console provides a workflow and UI for running and inspecting structure
prediction jobs. It does not claim to have invented PAE graph clustering.
This document distinguishes the graph method, reused software, and application
logic. The release refactoring on 2026-09-13 preserves the previous numerical
implementation and makes its provenance and license boundaries explicit.

## Method and source chain

The function `_pae_domains_pure()` in `af3_pae_domains.py` adapts ChimeraX's
`pae_domains()` in [`pae.py`](https://github.com/RBVI/ChimeraX/blob/c1ed3a0c0c36cb880e7292cc429e6f74afd8b999/src/bundles/alphafold/src/pae.py).
For a residue pair it takes the smaller of the two directional PAE values,
applies a floor of 0.2, creates an undirected edge when that value is strictly
below `pae_cutoff`, and assigns inverse-PAE weights raised to `pae_power`.
It applies greedy modularity clustering with `graph_resolution`, then optionally
filters clusters by `min_size`. Isolated residues are included as graph nodes.

ChimeraX's source credits Tristan Croll, ChimeraX ticket #4966, and
[ISOLDE's domain-finding implementation](https://github.com/tristanic/isolde/blob/master/isolde/src/reference_model/alphafold/find_domains.py).
That attribution is retained. No additional ISOLDE source code is distributed
by this project, and no claim is made that the whole ISOLDE or ChimeraX
application has the license of this individual ChimeraX library file.

The priority queue and Clauset-Newman-Moore greedy modularity implementation
in `af3_networkx_community.py` are adapted from NetworkX's
[`mapped_queue.py`](https://github.com/networkx/networkx/blob/4e74880b0da01977da79915167c64e5c2af38b47/networkx/utils/mapped_queue.py)
and [`modularity_max.py`](https://github.com/networkx/networkx/blob/4e74880b0da01977da79915167c64e5c2af38b47/networkx/algorithms/community/modularity_max.py).
The implementation supports the undirected weighted case needed here without
requiring NetworkX at runtime. This is a software adaptation, not merely a
reference to a mathematical idea.

The historical upstream revisions used when these adaptations were first
written were not recorded. The permanent links above identify the upstream
source and licensing reviewed on 2026-09-13. They do not assert that the old port
was generated from those precise commits. Hashes and retrieval URLs are saved
in [the provenance record](../LICENSES/upstream-provenance.json).

## What this application adds

AF3 Console implements the surrounding workflow: streaming PAE loading and
disk-backed matrices, chain/residue mapping, isolated analysis workers,
discontinuous-domain segment reporting, conservative fragment ranges,
adjacent-domain windows, merging short windows, repeated clustering of
oversized domains, scheduler integration, and result presentation.
The application orchestrates the graph library through a normal Python import.
Its windowing and scheduling code remains separate from that library.

Differences from ChimeraX's implementation include Python loops in place of
NumPy-based graph construction and use of the dependency-free NetworkX adapter.
On 2026-09-13 the graph function and NetworkX helpers were extracted unchanged
from the pre-release application into their own source modules. No thresholds,
tie-breaking, clustering, or fragment-window rules were deliberately changed
by this release preparation.

PAE communities are confidence-based groupings. They should not be presented
as experimentally established domains or guaranteed biological domain
boundaries. The application's fragment ranges may also include linker or
unclustered residues according to its documented windowing rules.

## License boundaries and redistribution

- Original application code and documentation: [MIT, PKU-Gaolab, Ming-Ao Lu](../LICENSE).
- `af3_pae_domains.py`: [LGPL-2.1-only](../LICENSES/LGPL-2.1-only.txt), including
  the retained ChimeraX copyright and the application's adaptation notice.
- `af3_networkx_community.py`: [BSD-3-Clause](../LICENSES/BSD-3-Clause-NetworkX.txt),
  retaining NetworkX copyright, conditions, and disclaimer.

The ChimeraX source file explicitly offers LGPL version 2.1 for that library
file. AF3 Console uses that permission and supplies an independently
replaceable source module. The project's MIT license does not replace the
LGPL or BSD terms. Keep the applicable notices and complete license texts with
any redistribution, including copies of runtime snapshots. Mark further
changes and their dates, and provide the source and ability to use a modified
library as required by LGPL 2.1. Attribution in a README or paper alone does
not replace these licensing obligations.

For academic reporting, describe the PAE method and acknowledge the actual
software used, including ChimeraX/Tristan Croll's contribution and the NetworkX
clustering implementation. Describe AF3 Console's window-building rules
separately. A software citation is a scholarly acknowledgement; it does not
grant permission to redistribute source, engines, model parameters, or data.

See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) for included assets and
separately installed software. This record documents verified sources and
our distribution choices, without resolving every possible historical
authorship or institutional-rights question.
