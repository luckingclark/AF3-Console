# Third-party notices

AF3 Console's original application code and documentation are copyright (c)
2026 PKU-Gaolab, Ming-Ao Lu and licensed under [MIT](LICENSE), except for the components
identified below. MIT does not replace these components' licenses. Upstream
copyrights, attribution, and disclaimers remain applicable.

## Code and assets included in this distribution

| Component | Local files | License and notices |
| --- | --- | --- |
| ChimeraX PAE graph adaptation | `af3_pae_domains.py` | [LGPL-2.1-only](LICENSES/LGPL-2.1-only.txt); [original Regents of the University of California copyright header](LICENSES/ChimeraX-PAE-COPYRIGHT.txt), also retained in the module |
| NetworkX community and priority-queue adaptation | `af3_networkx_community.py` | [BSD-3-Clause, including NetworkX copyright and disclaimer](LICENSES/BSD-3-Clause-NetworkX.txt) |
| WenQuanYi Micro Hei 0.2.0-beta | `fonts/wqy-microhei.ttc` | Distributed using the Apache-2.0 option; [original Apache license](LICENSES/WenQuanYi-MicroHei-LICENSE_Apache2.txt), [upstream README and additional notices](LICENSES/WenQuanYi-MicroHei-README.txt), and [upstream authors](LICENSES/WenQuanYi-MicroHei-AUTHORS.txt) |
| Project cat artwork | Image embedded in `af3_ui_brand.py` | Supplied by PKU-Gaolab, Ming-Ao Lu with permission for public distribution as part of AF3 Console |

The font was checked byte-for-byte against the publisher's
[0.2.0-beta SourceForge archive](https://sourceforge.net/projects/wqy/files/wqy-microhei/0.2.0-beta/).
It includes Google, WenQuanYi, Qianqian Fang, and other upstream contributors'
work; the full original notices are supplied, including the original
[alternative GPLv3 text](LICENSES/WenQuanYi-MicroHei-LICENSE_GPLv3.txt).
Including that alternative text does not change our choice to distribute the
unmodified font under its Apache-2.0 option. The [standard Apache-2.0 text](LICENSES/Apache-2.0.txt)
is also provided.

The original historical upstream revisions used for the PAE and NetworkX ports
were not recorded. Source and license references reviewed for this release are:

- [ChimeraX `pae.py`, revision c1ed3a0c0c36cb880e7292cc429e6f74afd8b999](https://github.com/RBVI/ChimeraX/blob/c1ed3a0c0c36cb880e7292cc429e6f74afd8b999/src/bundles/alphafold/src/pae.py).
- [NetworkX priority queue, revision 4e74880b0da01977da79915167c64e5c2af38b47](https://github.com/networkx/networkx/blob/4e74880b0da01977da79915167c64e5c2af38b47/networkx/utils/mapped_queue.py).
- [NetworkX modularity clustering, the same revision](https://github.com/networkx/networkx/blob/4e74880b0da01977da79915167c64e5c2af38b47/networkx/algorithms/community/modularity_max.py).

These are verified review references, not claims about the historical port's
exact starting commit. Verification date, upstream URLs, and SHA-256 hashes are
recorded in [upstream-provenance.json](LICENSES/upstream-provenance.json).

The PAE adaptation is an independently importable library. Its source is
provided in this distribution. Users can edit or replace
`af3_pae_domains.py` with a compatible implementation; the application imports
that file at runtime. When redistributing it, retain its notices and comply
with LGPL 2.1, including its source and modification-notice requirements.
If you redistribute the portable GUI, also distribute the application source,
the independent algorithm modules, notices, and the GUI build script.
See [algorithm provenance](docs/algorithm-provenance.md) for details.

## External software installed separately

The source and portable application distributions do not bundle Python, Qt,
PySide6, Shiboken6, NumPy, pandas, Matplotlib, six, python-dateutil, qtawesome,
NetworkX's complete package, or a prediction engine. Those projects retain
their own licenses. Installing dependencies does not relicense them under MIT.
For Qt for Python, consult its [official licensing information](https://doc.qt.io/qtforpython-6/licenses.html).
If a downstream distributor builds an environment or executable that bundles
dependencies, that distributor must include the applicable licenses and meet
the requirements of the actual versions and Qt modules bundled.

AlphaFold 3, its containers, model parameters, databases, and prediction outputs
are not included. Obtain them separately through their authorized channels.
AlphaFold 3's source-code license and its model/output terms are separate:
[source repository](https://github.com/google-deepmind/alphafold3),
[model parameter terms](https://github.com/google-deepmind/alphafold3/blob/main/WEIGHTS_TERMS_OF_USE.md),
[output terms](https://github.com/google-deepmind/alphafold3/blob/main/OUTPUT_TERMS_OF_USE.md).
Check the license of the precise engine version you use. AF3 Console's MIT
license does not grant rights to distribute model parameters or alter the
engine's permitted uses.

ChimeraX, ISOLDE, NetworkX, Qt, and AlphaFold are credited for their respective
work. Their inclusion in these notices does not imply affiliation with or
endorsement of AF3 Console by their developers or institutions.
