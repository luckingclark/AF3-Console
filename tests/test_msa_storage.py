"""Offline MSA storage lifecycle tests with constructed, nonbiological inputs."""
import copy
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import af3_runtime as R


class MsaStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="af3-msa-storage-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "msa pool"
        self.name = "P12345"
        self.input = Path(self.tmp.name) / "input.json"
        self.data = {
            "name": self.name, "modelSeeds": [1], "dialect": "alphafold3", "version": 4,
            "sequences": [{"protein": {"id": "A", "sequence": "MAAAAA"}}],
        }
        R.atomic_json(self.input, self.data)

    def output(self, flat=False, data=None):
        value = copy.deepcopy(data or self.data)
        for entry in value["sequences"]:
            for kind, body in entry.items():
                if kind in ("protein", "rna"):
                    body.update(unpairedMsa="", pairedMsa="", templates=[])
        path = self.root / (self.name + "_data.json") if flat else Path(R.msa_native_path(self.root, self.name))
        R.atomic_json(path, value)
        return path

    def record(self):
        return R.read_json(R.msa_record_path(self.root, self.name))

    def reserve_start(self):
        R.reserve_msa(self.root, self.name, "synthetic-fingerprint")
        self.assertTrue(R.msa_start(self.root, self.name, self.input))

    def cli(self, command, *args):
        return subprocess.run(
            [sys.executable, "-B", str(ROOT / "af3_runtime.py"), command,
             str(self.root), self.name, *(str(a) for a in args)],
            capture_output=True, text=True, env=dict(os.environ, PYTHONIOENCODING="utf-8"),
        )

    def test_native_lifecycle_preserves_companions_and_skips_complete_cache(self):
        self.reserve_start()
        data_path = self.output()
        companion = data_path.parent / "pipeline-details.txt"
        companion.write_text("synthetic pipeline metadata", encoding="utf-8")
        before = data_path.read_bytes()
        self.assertFalse(R.msa_ready(self.root, self.name))
        self.assertTrue(R.msa_finish(self.root, self.name, self.input, 0))
        self.assertTrue(R.msa_ready(self.root, self.name))
        self.assertFalse(R.msa_start(self.root, self.name, self.input))
        self.assertEqual(data_path.read_bytes(), before)
        self.assertEqual(companion.read_text(encoding="utf-8"), "synthetic pipeline metadata")
        self.assertFalse((self.root / (self.name + "_data.json")).exists())

    def test_failed_process_cannot_publish_even_complete_json(self):
        self.reserve_start()
        path = self.output()
        self.assertFalse(R.msa_finish(self.root, self.name, self.input, 9))
        self.assertEqual(self.record()["status"], "failed")
        self.assertTrue(path.is_file())
        self.assertFalse(R.msa_ready(self.root, self.name))
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'refusing to overwrite'):
            R.msa_start(self.root, self.name, self.input)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.record()['status'], 'failed')

    def test_complete_record_rechecks_sequence_without_entity_argument(self):
        self.reserve_start()
        self.output()
        self.assertTrue(R.msa_finish(self.root, self.name, self.input, 0))
        self.assertTrue(R.msa_ready(self.root, self.name))
        replacement = copy.deepcopy(self.data)
        replacement["sequences"][0]["protein"]["sequence"] = "MGGGGG"
        self.output(data=replacement)
        self.assertFalse(R.msa_ready(self.root, self.name))
        self.assertEqual(self.cli("msa-check").returncode, 1)
        self.assertEqual(self.record()["status"], "complete")

    def test_missing_truncated_and_wrong_sequence_outputs_fail(self):
        self.reserve_start()
        self.assertFalse(R.msa_finish(self.root, self.name, self.input, 0))
        path = Path(R.msa_native_path(self.root, self.name))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, 'refusing to overwrite'):
            R.msa_start(self.root, self.name, self.input)
        self.assertEqual(path.read_text(encoding='utf-8'), '{')
        wrong = copy.deepcopy(self.data)
        wrong["sequences"][0]["protein"]["sequence"] = "MGGGGG"
        self.output(data=wrong)
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'refusing to overwrite'):
            R.msa_start(self.root, self.name, self.input)
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(path.is_file())
        self.assertFalse(R.msa_ready(self.root, self.name))

    def test_output_name_must_match_input(self):
        self.reserve_start()
        wrong = dict(self.data, name="Q12345")
        self.output(data=wrong)
        self.assertFalse(R.msa_finish(self.root, self.name, self.input, 0))

    def test_equivalent_chain_id_encodings_are_accepted(self):
        self.reserve_start()
        value = copy.deepcopy(self.data)
        value["sequences"][0]["protein"]["id"] = ["A"]
        self.output(data=value)
        self.assertTrue(R.msa_finish(self.root, self.name, self.input, 0))

    def test_all_polymer_chains_are_checked(self):
        self.data["sequences"].append({"rna": {"id": "B", "sequence": "AAAAAA"}})
        R.atomic_json(self.input, self.data)
        self.reserve_start()
        wrong = copy.deepcopy(self.data)
        wrong["sequences"][1]["rna"]["sequence"] = "GGGGGG"
        self.output(data=wrong)
        self.assertFalse(R.msa_finish(self.root, self.name, self.input, 0))

    def test_reservation_is_idempotent_and_collision_keeps_original_record(self):
        first = R.reserve_msa(self.root, self.name, "synthetic-fingerprint")
        self.assertEqual(R.reserve_msa(self.root, self.name, "synthetic-fingerprint"), first)
        original = Path(R.msa_record_path(self.root, self.name)).read_bytes()
        with self.assertRaises(ValueError):
            R.reserve_msa(self.root, self.name, "other-inputs")
        self.assertEqual(Path(R.msa_record_path(self.root, self.name)).read_bytes(), original)

    def test_concurrent_reservations_have_exactly_one_owner(self):
        barrier = threading.Barrier(2)

        def reserve(fingerprint):
            barrier.wait(timeout=5)
            try:
                return R.reserve_msa(self.root, self.name, fingerprint)["fingerprint"]
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(reserve, ["synthetic-first", "synthetic-second"]))
        owners = [value for value in outcomes if value is not None]
        self.assertEqual(len(owners), 1)
        self.assertEqual(self.record()["fingerprint"], owners[0])

    def test_legacy_flat_and_native_are_readable_but_never_claimed_or_moved(self):
        flat = self.output(flat=True)
        original = flat.read_bytes()
        self.assertEqual(R.msa_data_path(self.root, self.name), str(flat))
        self.assertTrue(R.msa_ready(self.root, self.name))
        with self.assertRaises(ValueError):
            R.reserve_msa(self.root, self.name, "synthetic-fingerprint")
        self.assertFalse(Path(R.msa_record_path(self.root, self.name)).exists())
        native = self.output()
        self.assertEqual(R.msa_data_path(self.root, self.name), str(native))
        self.assertTrue(R.msa_ready(self.root, self.name))
        self.assertEqual(flat.read_bytes(), original)
        with self.assertRaises(ValueError):
            R.reserve_msa(self.root, self.name, "synthetic-fingerprint")

    def test_managed_pending_record_blocks_flat_fallback(self):
        R.reserve_msa(self.root, self.name, "synthetic-fingerprint")
        self.output(flat=True)
        self.assertEqual(R.msa_data_path(self.root, self.name), R.msa_native_path(self.root, self.name))
        self.assertFalse(R.msa_ready(self.root, self.name))
        record_path = Path(R.msa_record_path(self.root, self.name))
        record_path.write_text("{", encoding="utf-8")
        self.assertFalse(R.msa_ready(self.root, self.name))
        with self.assertRaises(ValueError):
            R.reserve_msa(self.root, self.name, "synthetic-fingerprint")

    def test_native_corrupt_file_is_not_hidden_by_flat_cache(self):
        self.output(flat=True)
        native = Path(R.msa_native_path(self.root, self.name))
        R.atomic_text(native, "{")
        self.assertEqual(R.msa_data_path(self.root, self.name), str(native))
        self.assertFalse(R.msa_ready(self.root, self.name))

    def timestamp_output(self, timestamp, data=None):
        value = copy.deepcopy(data or self.data)
        value["sequences"][0]["protein"].update(unpairedMsa="", pairedMsa="", templates=[])
        path = self.root / (self.name + "_" + timestamp) / (self.name + "_data.json")
        R.atomic_json(path, value)
        path.with_name("companion.txt").write_text("synthetic native metadata", encoding="utf-8")
        return path

    def test_latest_valid_timestamp_precedes_canonical_and_flat_without_changes(self):
        canonical = self.output()
        flat = self.output(flat=True)
        older = self.timestamp_output("20260101_120000")
        newest = self.timestamp_output("20260202_130000")
        # Folder mtime cannot make an old prediction supersede a newer one.
        os.utime(older.parent, (2000000000, 2000000000))
        before = {str(path): path.read_bytes() for path in (canonical, flat, older, newest)}
        self.assertEqual(R.msa_data_path(self.root, self.name), str(newest))
        self.assertTrue(R.msa_ready(self.root, self.name))
        self.assertEqual(self.cli("msa-check").returncode, 0)
        self.assertEqual({str(path): path.read_bytes() for path in (canonical, flat, older, newest)}, before)
        self.assertTrue(newest.with_name("companion.txt").is_file())
        self.assertFalse(Path(R.msa_record_path(self.root, self.name)).exists())

    def test_invalid_newer_timestamp_is_skipped_in_favor_of_complete_history(self):
        older = self.timestamp_output("20260101_120000")
        newest = self.timestamp_output("20260202_130000")
        newest.write_text("{", encoding="utf-8")
        self.assertEqual(R.msa_data_path(self.root, self.name), str(older))
        self.assertTrue(R.msa_ready(self.root, self.name))
        older.write_text("{", encoding="utf-8")
        self.assertEqual(R.msa_data_path(self.root, self.name), R.msa_native_path(self.root, self.name))
        self.assertFalse(R.msa_ready(self.root, self.name))
        flat = self.output(flat=True)
        self.assertEqual(R.msa_data_path(self.root, self.name), str(flat))
        self.assertTrue(R.msa_ready(self.root, self.name))

    def test_timestamp_only_layout_keeps_single_accession_name_and_directory(self):
        path = self.timestamp_output("20260602_140829")
        self.assertEqual(path.relative_to(self.root).as_posix(), "P12345_20260602_140829/P12345_data.json")
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*"))
        self.assertEqual(R.msa_data_path(self.root, self.name), str(path))
        self.assertTrue(R.msa_ready(self.root, self.name))
        self.assertEqual(sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*")), before)
        self.assertFalse((self.root / self.name).exists())

    def test_managed_states_never_fall_back_to_timestamp_history(self):
        history = self.timestamp_output("20260602_140829")
        R.reserve_msa(self.root, self.name, "synthetic-fingerprint")
        native = R.msa_native_path(self.root, self.name)
        self.assertEqual(R.msa_data_path(self.root, self.name), native)
        self.assertFalse(R.msa_ready(self.root, self.name))
        R.msa_start(self.root, self.name, self.input)
        self.assertFalse(R.msa_ready(self.root, self.name))
        self.assertFalse(R.msa_finish(self.root, self.name, self.input, 1))
        self.assertFalse(R.msa_ready(self.root, self.name))
        R.msa_start(self.root, self.name, self.input)
        self.output()
        self.assertTrue(R.msa_finish(self.root, self.name, self.input, 0))
        self.assertEqual(R.msa_data_path(self.root, self.name), native)
        Path(native).write_text("{", encoding="utf-8")
        self.assertFalse(R.msa_ready(self.root, self.name))
        self.assertTrue(history.is_file())
        self.assertTrue(history.with_name("companion.txt").is_file())

    def test_input_changes_and_unreserved_runs_are_rejected(self):
        with self.assertRaises(ValueError):
            R.msa_start(self.root, self.name, self.input)
        self.reserve_start()
        changed = copy.deepcopy(self.data)
        changed["sequences"][0]["protein"]["sequence"] = "MGGGGG"
        R.atomic_json(self.input, changed)
        with self.assertRaises(ValueError):
            R.msa_start(self.root, self.name, self.input)
        self.output()
        self.assertFalse(R.msa_finish(self.root, self.name, self.input, 0))

    def test_names_cannot_escape_pool(self):
        for name in (None, 1, "", ".", "..", "../P12345", "P12345/Q12345", "P12345\\Q12345", "/tmp/P12345", "P12345\n", "x" * 201):
            with self.subTest(name=name), self.assertRaises(ValueError):
                R.reserve_msa(self.root, name, "synthetic-fingerprint")
        self.assertFalse(self.root.exists())

    def test_cli_status_codes_and_stderr_errors(self):
        check = self.cli("msa-check")
        self.assertEqual(check.returncode, 1)
        start = self.cli("msa-start", self.input)
        self.assertEqual(start.returncode, 1)
        self.assertTrue(start.stderr.strip())
        R.reserve_msa(self.root, self.name, "synthetic-fingerprint")
        self.assertEqual(self.cli("msa-start", self.input).returncode, 0)
        self.output()
        self.assertEqual(self.cli("msa-check").returncode, 1)
        self.assertEqual(self.cli("msa-finish", self.input, 0).returncode, 0)
        self.assertEqual(self.cli("msa-check").returncode, 0)
        cached = self.cli("msa-start", self.input)
        self.assertEqual(cached.returncode, 3)
        self.assertEqual(cached.stdout, "")

    def test_cli_assets_map_container_msa_and_template_paths_to_host(self):
        assets = Path(self.tmp.name) / "host assets"
        assets.mkdir()
        (assets / "synthetic.a3m").write_text(">query\nMAAAAA\n", encoding="utf-8")
        (assets / "synthetic.cif").write_text("data_synthetic\n", encoding="utf-8")
        R.reserve_msa(self.root, self.name, "synthetic-fingerprint")
        self.assertEqual(self.cli("msa-start", self.input, "--assets", assets).returncode, 0)
        output = self.output()
        value = R.read_json(output)
        body = value["sequences"][0]["protein"]
        body.pop("unpairedMsa")
        body["unpairedMsaPath"] = "/root/af_assets/synthetic.a3m"
        body["templates"] = [{"mmcifPath": "/root/af_assets/synthetic.cif",
                              "queryIndices": [], "templateIndices": []}]
        R.atomic_json(output, value)
        before = output.read_bytes()
        self.assertEqual(self.cli("msa-finish", self.input, 0, "--assets", assets).returncode, 0)
        self.assertEqual(self.cli("msa-check").returncode, 1)
        self.assertEqual(self.cli("msa-check", "--assets", assets).returncode, 0)
        self.assertEqual(self.cli("msa-start", self.input, "--assets", assets).returncode, 3)
        self.assertEqual(output.read_bytes(), before)

    def test_start_refuses_companion_only_directory_and_preserves_record(self):
        R.reserve_msa(self.root, self.name, 'synthetic-fingerprint')
        folder = Path(R.msa_native_path(self.root, self.name)).parent
        folder.mkdir()
        companion = folder / 'notes.txt'
        companion.write_text('synthetic unfinished output', encoding='utf-8')
        before = Path(R.msa_record_path(self.root, self.name)).read_bytes()
        with self.assertRaisesRegex(ValueError, 'refusing to overwrite'):
            R.msa_start(self.root, self.name, self.input)
        self.assertEqual(companion.read_text(encoding='utf-8'), 'synthetic unfinished output')
        self.assertEqual(Path(R.msa_record_path(self.root, self.name)).read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
