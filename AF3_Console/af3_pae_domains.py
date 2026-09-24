# SPDX-License-Identifier: LGPL-2.1-only
# === UCSF ChimeraX Copyright ===
# Copyright 2022 Regents of the University of California. All rights reserved.
# The ChimeraX application is provided pursuant to the ChimeraX license
# agreement, which covers academic and commercial uses. For more details, see
# <https://www.rbvi.ucsf.edu/chimerax/docs/licensing.html>
#
# This particular file is part of the ChimeraX library. You can also
# redistribute and/or modify it under the terms of the GNU Lesser General
# Public License version 2.1 as published by the Free Software Foundation.
# For more details, see
# <https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html>
#
# THIS SOFTWARE IS PROVIDED "AS IS" WITHOUT WARRANTY OF ANY KIND, EITHER
# EXPRESSED OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES
# OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE. ADDITIONAL LIABILITY
# LIMITATIONS ARE DESCRIBED IN THE GNU LESSER GENERAL PUBLIC LICENSE
# VERSION 2.1
#
# This notice must be embedded in or attached to all copies, including partial
# copies, of the software or any revisions or derivations thereof.
# === UCSF ChimeraX Copyright ===
#
# AF3 Console adaptation: Copyright (c) 2026 PKU-Gaolab, Ming-Ao Lu.
# This adaptation is provided under LGPL-2.1-only, not the project's MIT license.
# Refactored into an independently replaceable module on 2026-09-13.
# Changes from upstream: Python loops instead of NumPy graph construction;
# dependency-free, BSD-licensed NetworkX community adapter. The pre-release
# AF3 Console numerical algorithm is unchanged by this module extraction.
# The historical upstream revision of the original adaptation was not recorded.
# Current reference reviewed at ChimeraX commit:
# c1ed3a0c0c36cb880e7292cc429e6f74afd8b999
# Source: src/bundles/alphafold/src/pae.py, pae_domains().
# Upstream credits Tristan Croll, ChimeraX ticket #4966, and ISOLDE:
# https://github.com/tristanic/isolde/blob/master/isolde/src/reference_model/alphafold/find_domains.py
# Complete license: LICENSES/LGPL-2.1-only.txt.

"""PAE graph clustering adapted from ChimeraX (LGPL-2.1-only).

This library accepts a matrix and returns residue-index communities. It has no
configuration, file-system, GUI, or scheduler dependency. A compatible modified
version can replace this file beside the AF3 Console application.
"""
from af3_networkx_community import _greedy_modularity_communities


def _pae_domains_pure(pae, pae_power=1, pae_cutoff=5, graph_resolution=0.5,
                      min_size=None):
    """ChimeraX pae_domains() 建图 + 纯 Python 聚类。pae: 方阵 list-of-lists。"""
    n = len(pae)
    adj = {}
    for i in range(n):
        row = pae[i]
        for j in range(i + 1, n):
            e = row[j]
            e2 = pae[j][i]
            if e2 < e:
                e = e2
            if e < 0.2:
                e = 0.2
            if e < pae_cutoff:
                w = float(1.0 / (e ** pae_power) if pae_power != 1 else 1.0 / e)
                adj.setdefault(i, {})[j] = w
                adj.setdefault(j, {})[i] = w
    clusters = _greedy_modularity_communities(range(n), adj,
                                              resolution=graph_resolution)
    clusters = [set(c) for c in clusters]
    if min_size:
        clusters = [c for c in clusters if len(c) >= min_size]
    return clusters
