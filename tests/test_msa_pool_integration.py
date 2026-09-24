"""Temporary, synthetic MSA pools: settings, job replication and cleanup barriers."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import af3
import af3_runtime as R
import af3_ui_backend as B


class PoolIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="af3-pool-integration-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "config.json"
        self.addCleanup(af3.reload_config)
        env = mock.patch.dict(os.environ, AF3_CONFIG=str(self.config), AF3_BASE="", AF3_SNAPSHOT="")
        env.start()
        self.addCleanup(env.stop)
        R.save_config({"HOST_BASE": str(self.root / "work")})
        af3.reload_config()
        self.primary = Path(af3.HOST_MSA_DATA)
        self.backups = [self.root / "backup one", self.root / "backup two"]
        for path in [self.primary, *self.backups]:
            path.mkdir(parents=True)
        R.save_config({"MSA_BACKUP_DIRS": [str(p) for p in self.backups]})
        af3.reload_config()

    def product(self):
        name = "P12345"
        value = {"name": name, "modelSeeds": [1], "dialect": "alphafold3", "version": 4,
                 "sequences": [{"protein": {"id": "A", "sequence": "MAAAAA"}}]}
        inp = self.root / "input.json"
        R.atomic_json(inp, value)
        R.reserve_msa(self.primary, name, "synthetic-fingerprint")
        R.msa_start(self.primary, name, inp)
        value["sequences"][0]["protein"].update(unpairedMsa="", pairedMsa="", templates=[])
        path = Path(R.msa_native_path(self.primary, name))
        R.atomic_json(path, value)
        (path.parent / "metadata.txt").write_text("synthetic companion", encoding="utf-8")
        self.assertTrue(R.msa_finish(self.primary, name, inp, 0))
        return path

    def test_backup_lists_roundtrip_and_snapshot_contains_module(self):
        self.assertEqual(B.msa_pool_paths(), [str(self.primary), *map(str, self.backups)])
        target = self.root / "batch"
        R.snapshot(ROOT / "af3.py", target, af3.config_snapshot())
        self.assertEqual((target / ".runtime/af3_msa_sync.py").read_bytes(),
                         (ROOT / "af3_msa_sync.py").read_bytes())
        cfg = R.read_json(target / ".runtime/config.json")
        self.assertEqual(cfg["MSA_BACKUP_DIRS"], list(map(str, self.backups)))
        with mock.patch.dict(os.environ, AF3_BASE=str(self.root / "other")):
            af3.reload_config()
            self.assertEqual(af3.MSA_BACKUP_DIRS, list(map(str, self.backups)))

    def test_invalid_lists_and_nested_roots_rejected(self):
        for invalid in ("/example", None, [1], ["a", "b", "c"], ["a", "a"], ["a", "a/child"]):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                R.normalize_config({"MSA_BACKUP_DIRS": invalid})
        for secondary in (str(self.primary), str(self.primary / "child"), str(self.primary.parent)):
            saved = self.config.read_bytes()
            count, path, error = B.update_af3_config({"MSA_BACKUP_DIRS": [secondary]})
            self.assertEqual(count, 0)
            self.assertIsNotNone(error)
            self.assertEqual(self.config.read_bytes(), saved)

    def test_missing_backup_is_preflight_error_only_for_msa(self):
        values = af3.config_snapshot()
        missing = self.root / "never-created"
        values["MSA_BACKUP_DIRS"] = [str(missing)]
        msa = af3.deployment_checks(values, require_msa=True, require_infer=False)
        self.assertTrue(any(c["key"].startswith("MSA_BACKUP_DIRS") and not c["ok"] for c in msa))
        infer = af3.deployment_checks(values, require_msa=False, require_infer=True)
        self.assertFalse(any(c["key"].startswith("MSA_BACKUP_DIRS") for c in infer))
        self.assertFalse(missing.exists())

    def test_changing_base_cannot_save_a_new_primary_inside_a_backup(self):
        before = self.config.read_bytes()
        count, _, error = B.update_af3_config({"HOST_BASE": str(self.backups[0])})
        self.assertEqual(count, 0)
        self.assertIn("non-overlapping", error)
        self.assertEqual(self.config.read_bytes(), before)

    def test_pin_assets_keeps_identity_digests_and_copies_real_recipe(self):
        source = self.root / 'alignment.a3m'
        source.write_text('>query\nMAAAAA\n', encoding='utf-8')
        entity = {'type': 'protein', 'sequence': 'MAAAAA', 'msa_path': str(source)}
        identity = R.entity_identity(entity)
        spec = {'identity': {'entities': [identity]}, 'entities': [entity],
                '_parent_identity': identity, 'parent_identity': identity}
        pinned = R.pin_assets(spec, self.root / 'assets')
        self.assertEqual(pinned['identity'], spec['identity'])
        self.assertEqual(pinned['_parent_identity'], identity)
        self.assertEqual(pinned['parent_identity'], identity)
        self.assertNotEqual(pinned['entities'][0]['msa_path'], str(source))
        source.write_text('changed source', encoding='utf-8')
        self.assertEqual(Path(pinned['entities'][0]['msa_path']).read_text(), '>query\nMAAAAA\n')

    def test_cli_replicates_whole_product_to_both_roots(self):
        path = self.product()
        result = subprocess.run([sys.executable, "-B", str(ROOT / "af3_runtime.py"),
                                 "msa-replicate", str(self.primary), "P12345",
                                 json.dumps(list(map(str, self.backups)))],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout)["copied"], 2)
        for backup in self.backups:
            copied = backup / "P12345/P12345_data.json"
            self.assertEqual(copied.read_bytes(), path.read_bytes())
            self.assertEqual((copied.parent / "metadata.txt").read_text(), "synthetic companion")
            self.assertTrue(R.msa_ready(backup, "P12345"))
        self.assertEqual(R.read_json(self.primary / ".msa_backup_receipts/P12345.json")["status"], "complete")

    def test_compressed_alignment_is_materialized_as_plain_immutable_asset(self):
        import gzip
        import lzma
        encoders = {"gzip": gzip.compress, "xz": lzma.compress}
        try:
            import zstandard
            encoders["zstd"] = zstandard.ZstdCompressor().compress
        except ImportError:
            pass
        content = b">query\nMAAAAA\n"
        for kind, compress in encoders.items():
            with self.subTest(kind=kind):
                source = self.root / (kind + ".a3m")
                source.write_bytes(compress(content))
                result = R.externalize_input({"unpairedMsaPath": str(source)}, self.root / "assets")
                target = self.root / "assets" / Path(result["unpairedMsaPath"]).name
                self.assertEqual(target.read_bytes(), content)
                self.assertNotEqual(source.read_bytes(), content)

    def test_backup_error_keeps_primary_ready_and_peer_untouched(self):
        path = self.product()
        before = path.read_bytes()
        occupied = self.backups[0] / "P12345"
        occupied.mkdir()
        (occupied / "colleague.txt").write_text("synthetic colleague fixture")
        result = R.msa_replicate(self.primary, "P12345", list(map(str, self.backups)))
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(result["conflicts"])
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(R.msa_ready(self.primary, "P12345"))
        self.assertEqual([p.name for p in occupied.iterdir()], ["colleague.txt"])

    def test_script_carries_snapshot_backup_paths_with_shell_quoting(self):
        backups = ["/example/one 'quoted' $name", "/example/two"]
        with mock.patch.object(af3, "MSA_BACKUP_DIRS", backups):
            script = af3._msa_slurm_lines("example", 1, "/example/in", "/example/msa", "0-0%1",
                                          ["P12345_input.json"], False, None)
        self.assertIn(shlex.quote(json.dumps(backups)), script)
        self.assertLess(script.index('FINISH_STATUS" -eq 0'), script.index(" msa-replicate "))
        self.assertIn("WARNING: MSA backup incomplete", script)
        self.assertNotIn("--force_output_dir", script)

    def test_cleanup_rejects_pool_content_pool_itself_and_ancestors(self):
        for pool in [self.primary, *self.backups]:
            for target in (pool, pool / "P12345", pool.parent):
                with self.subTest(target=target), self.assertRaisesRegex(ValueError, "MSA pool protected"):
                    B._protect_msa_pools(str(target), {"dir": str(self.root / "batch")})
        B._protect_msa_pools(str(self.root / "independent-output"), {"dir": str(self.root / "batch")})
        with mock.patch.object(B.shutil, "rmtree") as remove:
            result, error = B.clean_batch({"dir": str(self.primary), "status": "done"}, ["output"])
        self.assertIn("MSA pool protected", error)
        remove.assert_not_called()

    def test_cleanup_uses_old_task_snapshot_even_after_settings_change(self):
        batch = self.root / "old_batch"
        old = self.root / "old_pool"
        R.atomic_json(batch / ".runtime/config.json", {"MSA_BACKUP_DIRS": [str(old)]})
        with self.assertRaisesRegex(ValueError, "MSA pool protected"):
            B._protect_msa_pools(str(old / "P12345"), {"dir": str(batch)})
        (batch / ".runtime/config.json").write_text("{")
        with self.assertRaisesRegex(ValueError, "Cannot verify"):
            B._protect_msa_pools(str(self.root / "independent-output"), {"dir": str(batch)})


if __name__ == "__main__":
    unittest.main()
