"""Add-only MSA union/replication tests; all biological-looking data is artificial."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import af3_msa_sync as S
import af3_runtime as R


class MsaSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="af3-sync-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.pools = [self.base / name for name in ("primary pool", "second pool", "third pool")]
        for root in self.pools:
            root.mkdir()

    def data(self, name="P12345"):
        return {"name": name, "dialect": "alphafold3", "version": 4, "modelSeeds": [1],
                "sequences": [{"protein": {"id": "A", "sequence": "MAAAAA", "unpairedMsa": "", "pairedMsa": "", "templates": []}}]}

    def product(self, index=0, name="P12345", folder=None, flat=False, data=None):
        root = self.pools[index]
        path = root / (name + "_data.json") if flat else root / (folder or name) / (name + "_data.json")
        R.atomic_json(path, data if data is not None else self.data(name))
        return path

    def managed(self, index=0, name="P12345", fingerprint="synthetic-fingerprint"):
        root = self.pools[index]
        source = self.base / (str(index) + "-input.json")
        R.atomic_json(source, self.data(name))
        R.reserve_msa(root, name, fingerprint)
        R.msa_start(root, name, source)
        path = self.product(index, name)
        self.assertTrue(R.msa_finish(root, name, source, 0))
        return path

    def snapshot(self, root):
        return {path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*") if path.is_file() and not path.is_symlink()}

    def assertSuccessful(self, result, copies):
        self.assertEqual(result["copied"], copies, result)
        self.assertEqual(result["errors"], [], result)
        self.assertEqual(result["conflicts"], [], result)

    def test_read_only_three_pool_union_and_repeat_scan(self):
        self.product(0)
        self.product(1, "Q12345", flat=True)
        self.product(2, folder="P12345_20260602_140829")
        before = [self.snapshot(root) for root in self.pools]
        plan = S.scan_pools(self.pools)
        self.assertEqual([self.snapshot(root) for root in self.pools], before)
        self.assertEqual(len(plan["copies"]), 6)
        self.assertEqual(plan["invalid"], [])
        self.assertEqual(plan["errors"], [])
        json.dumps(plan)  # The GUI worker can hand a plan across process boundaries.
        self.assertSuccessful(S.execute_plan(plan), 6)
        for root in self.pools:
            self.assertTrue((root / "P12345" / "P12345_data.json").is_file())
            self.assertTrue((root / "Q12345_data.json").is_file())
            self.assertTrue((root / "P12345_20260602_140829" / "P12345_data.json").is_file())
        self.assertEqual(S.scan_pools(self.pools)["copies"], [])

    def test_timestamp_and_relative_companions_keep_whole_tree_and_bytes(self):
        data = self.data()
        body = data["sequences"][0]["protein"]
        body.pop("unpairedMsa")
        body["unpairedMsaPath"] = "assets/query.a3m"
        body["templates"] = [{"mmcifPath": "assets/template.cif", "queryIndices": [0], "templateIndices": [0]}]
        path = self.product(folder="P12345_20260602_140829", data=data)
        assets = path.parent / "assets"
        assets.mkdir()
        (assets / "query.a3m").write_text(">synthetic\nMAAAAA\n", encoding="utf-8")
        (assets / "template.cif").write_text("data_synthetic\n#\n", encoding="utf-8")
        (path.parent / "empty companion folder").mkdir()
        (path.parent / "notes.txt").write_text("synthetic companion", encoding="utf-8")
        original = self.snapshot(path.parent)
        plan = S.scan_pools(self.pools[:2])
        self.assertEqual(plan["invalid"], [], plan)
        self.assertSuccessful(S.execute_plan(plan), 1)
        destination = self.pools[1] / path.parent.name
        self.assertEqual(self.snapshot(destination), original)
        self.assertTrue((destination / "empty companion folder").is_dir())
        self.assertEqual(self.snapshot(path.parent), original)

    def test_different_whole_units_conflict_and_do_not_choose_for_empty_third(self):
        left = self.product(0)
        right = self.product(1)
        (left.parent / "notes.txt").write_text("first", encoding="utf-8")
        (right.parent / "notes.txt").write_text("second", encoding="utf-8")
        original = [self.snapshot(root) for root in self.pools]
        plan = S.scan_pools(self.pools)
        self.assertEqual(len(plan["conflicts"]), 1)
        self.assertEqual(plan["copies"], [])
        self.assertEqual(S.execute_plan(plan)["copied"], 0)
        self.assertEqual([self.snapshot(root) for root in self.pools], original)

    def test_existing_empty_or_incomplete_destination_is_never_filled(self):
        self.product()
        directory = self.pools[1] / "P12345"
        directory.mkdir()
        (directory / "keep.txt").write_text("peer working here", encoding="utf-8")
        before = self.snapshot(directory)
        plan = S.scan_pools(self.pools[:2])
        self.assertEqual(plan["copies"], [])
        self.assertTrue(plan["conflicts"])
        self.assertEqual(self.snapshot(directory), before)

    def test_invalid_json_or_missing_fields_prevent_whole_directory_copy(self):
        path = self.product()
        (path.parent / "Q12345_data.json").write_text("{", encoding="utf-8")
        plan = S.scan_pools(self.pools[:2])
        self.assertEqual(plan["copies"], [])
        self.assertTrue(plan["invalid"])
        bad = self.data("Q12345")
        del bad["sequences"][0]["protein"]["pairedMsa"]
        self.product(1, "Q12345", flat=True, data=bad)
        self.assertEqual(S.scan_pools(self.pools)["copies"], [])

    def test_pending_failed_and_complete_managed_records(self):
        path = self.managed()
        record_path = Path(R.msa_record_path(self.pools[0], "P12345"))
        record = R.read_json(record_path)
        for status in ("running", "failed", "reserved"):
            R.atomic_json(record_path, dict(record, status=status))
            plan = S.scan_pools(self.pools[:2])
            self.assertEqual(plan["copies"], [], status)
            self.assertTrue(plan["invalid"], status)
        R.atomic_json(record_path, record)
        original_record = record_path.read_bytes()
        self.assertSuccessful(S.execute_plan(S.scan_pools(self.pools[:2])), 1)
        self.assertEqual(Path(R.msa_record_path(self.pools[1], "P12345")).read_bytes(), original_record)
        self.assertTrue(R.msa_ready(self.pools[1], "P12345"))
        self.assertEqual((self.pools[1] / path.parent.name / path.name).read_bytes(), path.read_bytes())
        self.assertFalse((self.pools[1] / ".locks").exists())

    def test_destination_record_collision_preserves_record_and_product(self):
        self.managed()
        R.reserve_msa(self.pools[1], "P12345", "different-synthetic-fingerprint")
        before = self.snapshot(self.pools[1])
        plan = S.scan_pools(self.pools[:2])
        self.assertFalse(plan["copies"])
        self.assertTrue(plan["conflicts"])
        self.assertEqual(self.snapshot(self.pools[1]), before)

    def test_source_modified_after_scan_or_during_copy_is_not_published(self):
        path = self.product()
        plan = S.scan_pools(self.pools[:2])
        path.write_text(path.read_text() + "\n", encoding="utf-8")
        result = S.execute_plan(plan)
        self.assertEqual(result["copied"], 0)
        self.assertTrue(result["conflicts"])
        self.assertFalse((self.pools[1] / "P12345").exists())
        plan = S.scan_pools(self.pools[:2])
        original_copy = S._copy_verified

        def modify_after_copy(source, target, expected):
            original_copy(source, target, expected)
            source.write_text(source.read_text() + "\n", encoding="utf-8")

        with patch.object(S, "_copy_verified", side_effect=modify_after_copy):
            result = S.execute_plan(plan)
        self.assertEqual(result["copied"], 0)
        self.assertTrue(result["conflicts"])
        self.assertFalse((self.pools[1] / "P12345").exists())

    def test_destination_appearing_after_scan_or_during_stage_is_not_overwritten(self):
        self.product()
        plan = S.scan_pools(self.pools[:2])
        destination = self.pools[1] / "P12345"
        destination.mkdir()
        marker = destination / "peer.txt"
        marker.write_text("keep this", encoding="utf-8")
        result = S.execute_plan(plan)
        self.assertEqual(result["copied"], 0)
        self.assertTrue(result["conflicts"])
        self.assertEqual(marker.read_text(), "keep this")
        self.assertFalse((destination / "P12345_data.json").exists())
        # A second, independent target appears after staging has started.
        plan = S.scan_pools([self.pools[0], self.pools[2]])
        original_copy = S._copy_verified

        def peer_arrives(source, target, expected):
            original_copy(source, target, expected)
            peer = self.pools[2] / "P12345"
            peer.mkdir(exist_ok=True)
            (peer / "peer.txt").write_text("other peer", encoding="utf-8")

        with patch.object(S, "_copy_verified", side_effect=peer_arrives):
            result = S.execute_plan(plan)
        self.assertEqual(result["copied"], 0)
        self.assertTrue(result["conflicts"])
        self.assertFalse((self.pools[2] / "P12345" / "P12345_data.json").exists())

    def test_copy_permission_failure_keeps_source_and_publishes_no_ready_json(self):
        self.managed()
        before = self.snapshot(self.pools[0])
        plan = S.scan_pools(self.pools[:2])
        with patch.object(S, "_copy_verified", side_effect=PermissionError("synthetic access denied")):
            result = S.execute_plan(plan)
        self.assertEqual(result["copied"], 0)
        self.assertTrue(result["errors"])
        self.assertFalse((self.pools[1] / "P12345").exists())
        self.assertFalse(Path(R.msa_record_path(self.pools[1], "P12345")).exists())
        self.assertEqual(self.snapshot(self.pools[0]), before)

    def test_nested_duplicate_missing_roots_are_read_only_errors(self):
        before = self.snapshot(self.base)
        for values in ([self.pools[0], self.pools[0]], [self.pools[0], self.pools[0] / "inside"], [], self.pools + [self.base / "four"]):
            self.assertTrue(S.scan_pools(values)["errors"])
        missing = self.base / "missing"
        self.assertTrue(S.scan_pools([self.pools[0], missing])["errors"])
        self.assertFalse(missing.exists())
        self.assertEqual(self.snapshot(self.base), before)

    def test_symlink_units_companions_and_roots_are_rejected(self):
        path = self.product()
        link = self.pools[1] / "P12345"
        try:
            link.symlink_to(path.parent, target_is_directory=True)
        except OSError as exc:
            self.skipTest("This host cannot create symlinks: " + str(exc))
        plan = S.scan_pools(self.pools[:2])
        self.assertTrue(plan["invalid"])
        self.assertFalse(plan["copies"])
        self.assertTrue(S.scan_pools([link, self.pools[2]])["errors"])
        (path.parent / "linked.txt").symlink_to(path)
        plan = S.scan_pools([self.pools[0], self.pools[2]])
        self.assertTrue(plan["invalid"])
        self.assertFalse(plan["copies"])

    def test_external_absolute_and_flat_companion_references_are_reported(self):
        external = self.base / "synthetic.a3m"
        external.write_text(">synthetic\nMAAAAA\n", encoding="utf-8")
        data = self.data()
        body = data["sequences"][0]["protein"]
        body.pop("unpairedMsa")
        body["unpairedMsaPath"] = str(external)
        self.product(data=data)
        plan = S.scan_pools(self.pools[:2])
        self.assertTrue(plan["invalid"])
        self.assertIn("absolute", plan["invalid"][0]["reason"])
        self.assertFalse(plan["copies"])
        flat = copy.deepcopy(data)
        flat["name"] = "Q12345"
        flat["sequences"][0]["protein"]["unpairedMsaPath"] = "synthetic.a3m"
        self.product(1, "Q12345", flat=True, data=flat)
        (self.pools[1] / "synthetic.a3m").write_bytes(external.read_bytes())
        plan = S.scan_pools(self.pools)
        self.assertEqual(len(plan["invalid"]), 2)
        self.assertFalse(plan["copies"])

    def test_symlink_safety_branches_without_host_symlink_privilege(self):
        path = self.product()
        companion = path.parent / "details.txt"
        companion.write_text("synthetic companion", encoding="utf-8")
        real_linked = S._linked
        with patch.object(S, "_linked", side_effect=lambda value: Path(value) == companion or real_linked(value)):
            plan = S.scan_pools(self.pools[:2])
        self.assertTrue(plan["invalid"])
        self.assertFalse(plan["copies"])
        with patch.object(S, "_linked", side_effect=lambda value: Path(value) == self.pools[0] or real_linked(value)):
            plan = S.scan_pools(self.pools[:2])
        self.assertTrue(plan["errors"])
        self.assertFalse(plan["copies"])

    def test_inaccessible_companion_directory_is_not_silently_omitted(self):
        self.product()

        def inaccessible_walk(*args, **kwargs):
            kwargs["onerror"](PermissionError("synthetic inaccessible companion directory"))
            return iter(())

        with patch.object(S.os, "walk", side_effect=inaccessible_walk):
            plan = S.scan_pools(self.pools[:2])
        self.assertTrue(plan["invalid"])
        self.assertFalse(plan["copies"])

    def test_replicate_only_requested_product_and_skip_identical_backup(self):
        self.managed()
        self.product(0, "Q12345")
        self.assertSuccessful(S.replicate_product(self.pools[0], "P12345", self.pools[1:]), 2)
        for root in self.pools[1:]:
            self.assertTrue(R.msa_ready(root, "P12345"))
            self.assertFalse((root / "Q12345").exists())
        again = S.replicate_product(self.pools[0], "P12345", self.pools[1:])
        self.assertSuccessful(again, 0)
        self.assertEqual(again["skipped"], 2)

    def test_old_linux_fallback_publishes_json_only_after_all_companions(self):
        path = self.product()
        (path.parent / "details.txt").write_text("synthetic details", encoding="utf-8")
        plan = S.scan_pools(self.pools[:2])
        original_link = os.link
        observed = []

        def observed_link(source, destination, **kwargs):
            if str(destination).endswith("_data.json"):
                self.assertTrue((Path(destination).parent / "details.txt").is_file())
                self.assertFalse(Path(destination).exists())
                observed.append(str(destination))
            return original_link(source, destination, **kwargs)

        with patch.object(S, "_rename_directory_exclusive", return_value=False), patch.object(S.os, "link", side_effect=observed_link):
            self.assertSuccessful(S.execute_plan(plan), 1)
        self.assertEqual(len(observed), 1)

    def test_fallback_companion_failure_does_not_publish_data_or_delete_peer_files(self):
        path = self.managed()
        (path.parent / "details.txt").write_text("synthetic details", encoding="utf-8")
        plan = S.scan_pools(self.pools[:2])
        original_link = os.link

        def fail_companion(source, destination, **kwargs):
            if Path(destination).name == "details.txt":
                (Path(destination).parent / "peer-arrived.txt").write_text("keep peer file", encoding="utf-8")
                raise PermissionError("synthetic failure")
            return original_link(source, destination, **kwargs)

        with patch.object(S, "_rename_directory_exclusive", return_value=False), patch.object(S.os, "link", side_effect=fail_companion):
            result = S.execute_plan(plan)
        self.assertEqual(result["copied"], 0)
        self.assertTrue(result["errors"])
        destination = self.pools[1] / "P12345"
        self.assertFalse((destination / "P12345_data.json").exists())
        self.assertEqual((destination / "peer-arrived.txt").read_text(), "keep peer file")
        self.assertFalse(R.msa_ready(self.pools[1], "P12345"))

    def test_tampered_plan_cannot_escape_pool(self):
        self.product()
        plan = S.scan_pools(self.pools[:2])
        plan["copies"][0]["manifest"]["files"][0]["path"] = "../../outside.json"
        result = S.execute_plan(plan)
        self.assertEqual(result["copied"], 0)
        self.assertTrue(result["errors"])
        self.assertFalse((self.base / "outside.json").exists())


if __name__ == "__main__":
    unittest.main()
