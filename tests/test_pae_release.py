"""Synthetic, offline checks for the separately licensed PAE libraries.

NetworkX is an optional test reference, not an application dependency. Install
it to run the independent randomized reference comparisons as well.
"""
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from af3_pae_domains import _pae_domains_pure
from af3_networkx_community import _MappedQueue

try:
    import networkx as nx
except ImportError:
    nx = None


def canonical(communities):
    return sorted(tuple(sorted(group)) for group in communities)


def block_matrix(lengths):
    n = sum(lengths)
    matrix = [[20.0] * n for _ in range(n)]
    start = 0
    for length in lengths:
        for i in range(start, start + length):
            for j in range(start, start + length):
                matrix[i][j] = 0.0 if i == j else 1.0
        start += length
    return matrix


class PAEGraphTests(unittest.TestCase):
    def test_disconnected_confident_blocks(self):
        self.assertEqual(
            canonical(_pae_domains_pure(block_matrix([3, 4]))),
            [(0, 1, 2), (3, 4, 5, 6)],
        )

    def test_asymmetric_error_uses_smaller_direction(self):
        matrix = [[0, 12, 20], [1, 0, 20], [20, 20, 0]]
        self.assertEqual(canonical(_pae_domains_pure(matrix)), [(0, 1), (2,)])

    def test_floor_and_strict_cutoff(self):
        for value in (0.0, -1.0, 0.2):
            matrix = [[0, value], [value, 0]]
            with self.subTest(value=value):
                self.assertEqual(
                    canonical(_pae_domains_pure(matrix, pae_cutoff=0.2)),
                    [(0,), (1,)],
                )
                self.assertEqual(
                    canonical(_pae_domains_pure(matrix, pae_cutoff=0.2001)),
                    [(0, 1)],
                )

    def test_cutoff_equal_is_not_an_edge(self):
        self.assertEqual(
            canonical(_pae_domains_pure([[0, 5], [5, 0]], pae_cutoff=5)),
            [(0,), (1,)],
        )

    def test_isolated_nodes_and_minimum_cluster_size(self):
        self.assertEqual(canonical(_pae_domains_pure([])), [])
        self.assertEqual(canonical(_pae_domains_pure([[0]])), [(0,)])
        self.assertEqual(
            canonical(_pae_domains_pure(block_matrix([1, 2, 3]), min_size=3)),
            [(3, 4, 5)],
        )

    def test_input_matrix_is_not_modified(self):
        matrix = [[0, 0, 8], [3, 0, 1], [12, 6, 0]]
        before = [row[:] for row in matrix]
        _pae_domains_pure(matrix, pae_power=2)
        self.assertEqual(matrix, before)

    def test_queue_ties_updates_and_removal(self):
        queue = _MappedQueue({(1, 2): 3.0, (0, 2): 3.0, (2, 3): 4.0})
        self.assertEqual(queue.pop().element, (0, 2))
        queue.update((2, 3), (2, 3), priority=1.0)
        self.assertEqual(queue.pop().element, (2, 3))
        queue.remove((1, 2))
        self.assertEqual(len(queue), 0)
        self.assertTrue(queue.push((3, 4), priority=2.0))
        self.assertFalse(queue.push((3, 4), priority=1.0))

    @unittest.skipIf(nx is None, "Install networkx for independent reference checks")
    def test_matches_independent_networkx_reference(self):
        rng = random.Random(20260913)
        for n in (8, 17, 31):
            matrix = [[rng.uniform(0.0, 18.0) for _ in range(n)] for _ in range(n)]
            for power in (1, 2):
                for cutoff in (3.0, 7.0):
                    for resolution in (0.5, 1.25):
                        with self.subTest(n=n, power=power, cutoff=cutoff, resolution=resolution):
                            graph = nx.Graph()
                            graph.add_nodes_from(range(n))
                            for i in range(n):
                                for j in range(i + 1, n):
                                    error = max(0.2, min(matrix[i][j], matrix[j][i]))
                                    if error < cutoff:
                                        graph.add_edge(i, j, weight=error ** (-power))
                            expected = nx.community.greedy_modularity_communities(
                                graph, weight="weight", resolution=resolution
                            )
                            actual = _pae_domains_pure(
                                matrix, pae_power=power, pae_cutoff=cutoff,
                                graph_resolution=resolution,
                            )
                            self.assertEqual(canonical(actual), canonical(expected))

    def test_library_import_needs_only_standard_library(self):
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from af3_pae_domains import _pae_domains_pure; "
            "assert _pae_domains_pure([[0, 1], [1, 0]]) == [{0, 1}]; "
            "assert 'numpy' not in sys.modules; "
            "assert 'networkx' not in sys.modules; "
            "assert 'af3' not in sys.modules"
        )
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-B", "-c", code, str(ROOT)],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class PAEWorkerTests(unittest.TestCase):
    def worker(self, matrix, params):
        with tempfile.TemporaryDirectory(prefix="af3-release-pae-") as directory:
            directory = Path(directory)
            path = directory / "synthetic_pae.json"
            n = len(matrix)
            path.write_text(json.dumps({
                "pae": matrix, "token_chain_ids": ["A"] * n,
                "token_res_ids": list(range(1, n + 1)),
            }), encoding="utf-8")
            request = dict(
                path=str(path),
                chains=[dict(label="A", type="protein", start=0, end=n)],
                params=params,
            )
            env = dict(
                os.environ, AF3_BASE=str(directory / "workspace"),
                AF3_CONFIG=str(directory / "config.json"),
                AF3_PAE_CACHE=str(directory / "cache"),
                PYTHONIOENCODING="utf-8", PYTHONPATH="",
                PYTHONDONTWRITEBYTECODE="1",
            )
            result = subprocess.run(
                [sys.executable, "-B", str(ROOT / "af3_pae.py")],
                input=json.dumps(request), capture_output=True, encoding="utf-8",
                cwd=directory, env=env, timeout=60,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result, json.loads(result.stdout)

    def test_worker_returns_expected_block_windows(self):
        params = dict(pae_cutoff=5, resolution=0.5, min_domain=1,
                      domains_per_window=1, min_window=1, max_span=None)
        _, result = self.worker(block_matrix([3, 3]), params)
        self.assertEqual(result["A"]["windows"], [
            dict(domains=["D1"], start=1, end=3),
            dict(domains=["D2"], start=4, end=6),
        ])
        self.assertEqual(result["A"]["domains"], [
            dict(name="D1", segments=[[1, 3]], cut_range=[1, 3]),
            dict(name="D2", segments=[[4, 6]], cut_range=[4, 6]),
        ])

    def test_oversized_diagnostics_do_not_corrupt_json(self):
        params = dict(pae_cutoff=5, resolution=0.5, min_domain=10,
                      domains_per_window=1, min_window=1, max_span=20)
        process, result = self.worker(block_matrix([24]), params)
        self.assertIn("WARNING:", process.stderr)
        self.assertNotIn("WARNING:", process.stdout)
        self.assertEqual(result["A"]["windows"], [dict(domains=["D1"], start=1, end=24)])
        self.assertTrue(result["A"]["notes"])


if __name__ == "__main__":
    unittest.main()
