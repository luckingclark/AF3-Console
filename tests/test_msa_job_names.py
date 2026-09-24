"""Per-entity MSA names across actual CLI planners and controller entry points.

Sequences are synthetic; UniProt lookup and scheduler submission are mocked.
"""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import af3 as A
import af3_runtime as R


class _SubmissionCaptured(Exception):
    """Stop at the scheduler boundary after real MSA inputs have been written."""


class MsaJobNamesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="af3-msa-job-names-")
        self.addCleanup(temporary.cleanup)
        self.addCleanup(A.reload_config)
        self.root = Path(temporary.name)
        environment = mock.patch.dict(os.environ, {
            "AF3_BASE": str(self.root / "workspace"),
            "AF3_CONFIG": str(self.root / "missing-config.json"),
            "AF3_SNAPSHOT": "",
        })
        environment.start()
        self.addCleanup(environment.stop)
        A.reload_config()
        patches = [
            mock.patch.object(A, "fetch_uniprot", side_effect={
                "P12345": "MAAAAAGGGGGG", "Q12345": "MGGGGGAAAAAA",
            }.__getitem__),
            mock.patch.object(A, "_environment_identity", return_value={"synthetic": 1}),
            mock.patch.object(A, "_prediction_environment", return_value={"synthetic": 1}),
            mock.patch.object(A, "_MSA_FREE_POLICY", False),
            mock.patch.object(A, "args_msa_dir_global", str(self.root / "workspace" / "msa_data")),
            mock.patch.object(A, "ensure_deployment"),
            mock.patch.object(A, "note"),
            mock.patch.object(A, "warn"),
            mock.patch.object(A, "submit_controller", return_value="100"),
            mock.patch.object(A, "submit_watcher", return_value="101"),
            mock.patch.object(A, "msa_use_ssd", return_value=False),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def submit_until_msa(self, command, label):
        """Exercise parser -> plan -> controller/watcher -> MSA JSON generation."""
        work = self.root / label
        pool = work / "msa_data"
        output = work / "output"
        args = A.build_parser().parse_args(command + [
            "--msa-dir", str(pool), "--output-dir", str(output), "--seeds", "11",
        ])
        A._validate_args(args)
        args.func(args)
        specs = list(output.glob("*/spec.json"))
        self.assertEqual(len(specs), 1)
        spec = A.load_spec(str(specs[0]))
        captured = {}

        def array_submit(json_dir, json_files, cores, max_concurrent, use_ssd,
                         slurm_name, log_dir, **kwargs):
            captured["slurm_name"] = slurm_name
            captured["inputs"] = {
                filename: R.read_json(Path(json_dir) / filename)
                for filename in json_files
            }
            raise _SubmissionCaptured()

        controller = A.cmd_stage_infer if args.command == "run" else A.cmd_pulldown_watcher
        with mock.patch.object(A, "submit_msa_array", side_effect=array_submit):
            with self.assertRaises(_SubmissionCaptured):
                controller(argparse.Namespace(spec=str(specs[0])))
        self.assertEqual(captured["slurm_name"], "af3m-" + spec["name"])
        for filename, data in captured["inputs"].items():
            self.assertEqual(filename, data["name"] + "_input.json")
            self.assertEqual(len(data["sequences"]), 1)
            expected = pool / data["name"] / (data["name"] + "_data.json")
            self.assertEqual(Path(A.msa_data_path(data["name"], str(pool))), expected)
        return spec, captured["inputs"]

    def test_run_plain_accession_uses_id_even_when_custom_job_name_contains_and(self):
        for job_name in ("P12345_and_Q12345", "example_job"):
            with self.subTest(job_name=job_name):
                spec, inputs = self.submit_until_msa(
                    ["run", "P12345", "--name", job_name], job_name)
                self.assertEqual(spec["name"], job_name)
                self.assertEqual(set(inputs), {"P12345_input.json"})
                self.assertEqual(inputs["P12345_input.json"]["name"], "P12345")
                self.assertEqual(len(spec["jobs"][0]["entities"]), 1)

    def test_pulldown_list_filename_and_batch_tag_never_replace_entity_ids(self):
        task_file = self.root / "example_batch.txt"
        task_file.write_text("P12345\nQ12345\n", encoding="utf-8")
        for tag_flags, label in (([], "automatic"), (["--name", "example_screen"], "custom")):
            with self.subTest(label=label):
                spec, inputs = self.submit_until_msa(
                    ["pulldown", str(task_file), "Q12345"] + tag_flags, label)
                self.assertEqual({e["_msa_key"] for e in spec["msa_entities"]}, {"P12345", "Q12345"})
                self.assertEqual(set(inputs), {"P12345_input.json", "Q12345_input.json"})

    def test_shared_scan_submits_full_length_ids_and_keeps_fragment_identity_separate(self):
        for threshold in (6, 50):
            with self.subTest(split_threshold=threshold):
                spec, inputs = self.submit_until_msa([
                    "scan", "P12345", "Q12345", "--name", "example_scan",
                    "--shared-msa", "--win", "6", "--overlap", "0",
                    "--min-frag", "3", "--split-threshold", str(threshold),
                ], "shared_" + str(threshold))
                self.assertEqual(set(inputs), {"P12345_input.json", "Q12345_input.json"})
                if threshold == 6:
                    self.assertEqual(len(spec["fragments"]), 4)
                    for fragment in spec["fragments"]:
                        parent = fragment["parent_key"]
                        self.assertIn(parent, {"P12345", "Q12345"})
                        self.assertTrue(fragment["key"].startswith(parent + "_t"))
                        self.assertNotEqual(fragment["key"], parent)
                else:
                    self.assertEqual(spec["fragments"], [])

    def test_independent_scan_submits_full_length_ids_and_coordinate_named_fragments(self):
        spec, inputs = self.submit_until_msa([
            "scan", "P12345", "Q12345", "--name", "example_scan",
            "--win", "6", "--overlap", "0", "--min-frag", "3", "--split-threshold", "6",
        ], "independent")
        expected = {accession + suffix + "_input.json"
                    for accession in ("P12345", "Q12345")
                    for suffix in ("", "_t1-6", "_t7-12")}
        self.assertEqual(set(inputs), expected)
        self.assertEqual(len(spec["fragments"]), 4)

    def test_pae_scan_preparation_submits_accessions_before_domain_jobs(self):
        spec, inputs = self.submit_until_msa([
            "scan", "P12345", "Q12345", "--name", "example_domains",
            "--mode", "pae", "--shared-msa", "--split-threshold", "6",
        ], "pae")
        self.assertEqual(set(inputs), {"P12345_input.json", "Q12345_input.json"})
        self.assertEqual(set(spec["pae_pending"]), {"P12345", "Q12345"})
        self.assertEqual(spec["pairs"], [])


if __name__ == "__main__":
    unittest.main()
