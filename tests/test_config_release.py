"""Public-release configuration and Slurm generation, without cluster jobs."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import af3
import af3_runtime as R


class ReleaseConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "user.json"
        env = mock.patch.dict(os.environ, {
            "AF3_CONFIG": str(self.config), "AF3_BASE": "", "AF3_SNAPSHOT": "",
        })
        env.start()
        self.addCleanup(env.stop)
        af3.reload_config()
        self.addCleanup(af3.reload_config)

    def configured_values(self):
        image = self.root / "image.sif"
        image.write_bytes(b"synthetic container fixture")
        for name in ("db", "models"):
            (self.root / name).mkdir(exist_ok=True)
        R.save_config({"HOST_BASE": str(self.root / "workspace"),
                       "HOST_SIF": str(image), "HOST_DB_SOURCE": str(self.root / "db"),
                       "HOST_MODELS": str(self.root / "models"),
                       "MSA_PARTITION": "example_cpu", "INF_PARTITION": "example_gpu"})
        af3.reload_config()
        return af3.config_snapshot()

    def test_fresh_defaults_and_version_need_no_deployment(self):
        self.assertEqual(af3.HOST_BASE, str(Path.home() / "AF3"))
        for key in ("HOST_SIF", "HOST_DB_SOURCE", "HOST_SSD_CACHE",
                    "MSA_PARTITION", "INF_PARTITION", "INF_FALLBACK_PARTITION"):
            self.assertEqual(getattr(af3, key), "")
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as result:
            af3.build_parser().parse_args(["--version"])
        self.assertEqual(result.exception.code, 0)
        self.assertIn("0.1.0", output.getvalue())
        with mock.patch.object(af3, "ensure_deployment") as guard:
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as result:
                af3.build_parser().parse_args(["--help"])
            self.assertEqual(result.exception.code, 0)
            guard.assert_not_called()

    def test_normalization_preserves_empty_and_rejects_bad_types(self):
        values = R.normalize_config({"HOST_BASE": "~/example_workspace", "HOST_SIF": "",
                                     "HOST_SSD_CACHE": "", "MSA_MAX_CONCURRENT": "3"})
        self.assertEqual(values["HOST_BASE"], str(Path.home() / "example_workspace"))
        self.assertEqual(values["HOST_SIF"], "")
        self.assertEqual(values["HOST_SSD_CACHE"], "")
        self.assertEqual(values["MSA_MAX_CONCURRENT"], 3)
        for bad in ({"MSA_MAX_CONCURRENT": True}, {"INF_MAX_TOKEN": 0},
                    {"MSA_NTASKS_SINGLE": 2.5}, {"HOST_BASE": None},
                    {"INF_PARTITION": "gpu\n#SBATCH --other"},
                    {"CONTAINER_MODULE": "name; echo example"},
                    {"CONTAINER_RUNTIME": "singularity --option"},
                    {"INF_BUCKETS": "512,256"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                R.normalize_config(bad)

    def test_empty_base_does_not_create_relative_derived_paths(self):
        R.save_config({"HOST_BASE": ""})
        af3.reload_config()
        for key in ("HOST_BASE", "HOST_OUTPUT", "HOST_CACHE", "HOST_MSA_DATA", "UNIPROT_CACHE"):
            self.assertEqual(getattr(af3, key), "")

    def test_aux_tracks_cpu_and_explicit_shared_paths_survive_base_changes(self):
        R.save_config({"HOST_BASE": str(self.root / "first"), "MSA_PARTITION": "cpu_a",
                       "HOST_MODELS": str(self.root / "shared_models")})
        af3.reload_config()
        self.assertEqual(af3.AUX_PARTITION, "cpu_a")
        R.save_config({"HOST_BASE": str(self.root / "second"), "MSA_PARTITION": "cpu_b"})
        af3.reload_config()
        self.assertEqual(af3.AUX_PARTITION, "cpu_b")
        self.assertEqual(af3.HOST_OUTPUT, str(self.root / "second" / "output"))
        self.assertEqual(af3.HOST_MODELS, str(self.root / "shared_models"))
        R.save_config({"AUX_PARTITION": "controller_cpu", "MSA_PARTITION": "cpu_c"})
        af3.reload_config()
        self.assertEqual(af3.AUX_PARTITION, "controller_cpu")

    def test_site_user_env_and_snapshot_precedence(self):
        source = self.root / "af3.py"
        site = self.root / "site_config.json"
        site.write_text(json.dumps({"HOST_BASE": str(self.root / "site"), "MSA_PARTITION": "site_cpu"}))
        R.save_config({"HOST_BASE": str(self.root / "user"), "MSA_PARTITION": "user_cpu"})
        defaults = dict(af3._CONFIG_DEFAULTS, __file__=str(source))
        R.configure(defaults, af3._CONFIG_DEFAULTS)
        self.assertEqual(defaults["HOST_BASE"], str(self.root / "user"))
        self.assertEqual(defaults["AUX_PARTITION"], "user_cpu")
        with mock.patch.dict(os.environ, {"AF3_BASE": str(self.root / "env")}):
            R.configure(defaults, af3._CONFIG_DEFAULTS)
            self.assertEqual(defaults["HOST_BASE"], str(self.root / "env"))
            with mock.patch.dict(os.environ, {"AF3_SNAPSHOT": "1"}):
                R.configure(defaults, af3._CONFIG_DEFAULTS)
                self.assertEqual(defaults["HOST_BASE"], str(self.root / "user"))

    def test_deployment_check_is_read_only_and_stage_specific(self):
        values = self.configured_values()
        values["INF_PARTITION"] = ""
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*"))
        with mock.patch.object(af3.sys, "platform", "linux"), mock.patch.object(af3.shutil, "which", return_value="/example/bin/tool"):
            checks = af3.deployment_checks(values, require_infer=False)
            self.assertTrue(all(item["ok"] for item in checks), checks)
            checks = af3.deployment_checks(values)
            self.assertEqual([item["key"] for item in checks if not item["ok"]], ["INF_PARTITION"])
        after = sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*"))
        self.assertEqual(before, after)
        self.assertFalse((self.root / "workspace").exists())

    def test_module_runtime_can_be_absent_on_login_node(self):
        values = self.configured_values()
        values["CONTAINER_MODULE"] = "singularity/3.11"
        with mock.patch.object(af3.sys, "platform", "linux"), mock.patch.object(af3.shutil, "which", side_effect=lambda name: None if name == "singularity" else "/example/bin/" + name):
            checks = af3.deployment_checks(values)
        self.assertTrue(all(item["ok"] for item in checks), checks)

    def test_partition_placeholders_can_be_saved_but_not_submitted(self):
        self.configured_values()
        R.save_config({"MSA_PARTITION": "your_cpu_partition", "INF_PARTITION": "your_gpu_partition"})
        af3.reload_config()
        with mock.patch.object(af3.sys, "platform", "linux"), mock.patch.object(af3.shutil, "which", return_value="/example/bin/tool"):
            checks = af3.deployment_checks()
        failures = {item["key"] for item in checks if not item["ok"]}
        self.assertEqual(failures, {"MSA_PARTITION", "INF_PARTITION", "AUX_PARTITION"})
        with mock.patch.object(af3, "_run") as run, self.assertRaisesRegex(R.BusinessError, "placeholder"):
            af3.submit_job("#!/bin/bash\n#SBATCH --partition=your_cpu_partition\ntrue\n")
        run.assert_not_called()

    def test_placeholder_paths_are_rejected_even_if_a_directory_exists(self):
        values = self.configured_values()
        values.update(R.normalize_config({"HOST_SIF": "/path/to/your/alphafold3.sif",
                                           "HOST_OUTPUT": "/path/to/your/output",
                                           "HOST_SSD_CACHE": "/path/to/your/node_cache"}))
        with mock.patch.object(af3.sys, "platform", "linux"), mock.patch.object(af3.shutil, "which", return_value="/example/bin/tool"), mock.patch.object(af3.os.path, "isfile", return_value=True), mock.patch.object(af3.os.path, "isdir", return_value=True), mock.patch.object(af3.os, "access", return_value=True):
            checks = af3.deployment_checks(values)
        failures = {item["key"]: item["message"] for item in checks if not item["ok"]}
        self.assertEqual(set(failures), {"HOST_SIF", "HOST_OUTPUT", "HOST_SSD_CACHE"})
        self.assertTrue(all("placeholder" in message for message in failures.values()))

    def test_submission_checks_happen_before_any_sbatch_or_script_write(self):
        destination = self.root / "not_created" / "job.sh"
        script = "#!/bin/bash\n#SBATCH --partition=example_cpu\nsingularity exec --norun_inference\n"
        with mock.patch.object(af3, "_run") as run, self.assertRaises(R.BusinessError):
            af3.submit_job(script, str(destination))
        run.assert_not_called()
        self.assertFalse(destination.exists())

    def test_submission_uses_effective_partition_and_passes_script_to_sbatch(self):
        self.configured_values()
        af3.MSA_PARTITION = ""
        script = "#!/bin/bash\n#SBATCH --partition=chosen_cpu\nsingularity exec --norun_inference\n"
        with mock.patch.object(af3.sys, "platform", "linux"), mock.patch.object(af3.shutil, "which", return_value="/example/bin/tool"), mock.patch.object(af3, "_run", return_value=subprocess.CompletedProcess([], 0, "Submitted batch job 123\n", "")) as run:
            self.assertEqual(af3.submit_job(script), "123")
        run.assert_called_once_with(["sbatch"], inp=script)

    def test_submission_uses_actual_output_override(self):
        self.configured_values()
        # A regular file cannot be the global output directory. The actual job
        # has a different writable destination, so this default is irrelevant.
        blocked = self.root / "not_a_directory"
        blocked.write_text("synthetic fixture")
        af3.HOST_OUTPUT = str(blocked)
        script = "#!/bin/bash\n#SBATCH --partition=example_gpu\nsingularity exec --norun_data_pipeline\n"
        with mock.patch.object(af3.sys, "platform", "linux"), mock.patch.object(af3.shutil, "which", return_value="/example/bin/tool"), mock.patch.object(af3, "_run", return_value=subprocess.CompletedProcess([], 0, "Submitted batch job 123\n", "")) as run:
            self.assertEqual(af3.submit_job(script, deployment_config={"HOST_OUTPUT": str(self.root / "actual_output")}), "123")
        run.assert_called_once()

    def test_cli_overrides_are_used_by_preflight(self):
        args = argparse.Namespace(partition="chosen_gpu", msa_partition="chosen_cpu",
                                  aux_partition=None, output_dir=str(self.root / "outputs"),
                                  msa_dir=str(self.root / "msa"))
        values = af3._submission_config(args)
        self.assertEqual(values["INF_PARTITION"], "chosen_gpu")
        self.assertEqual(values["AUX_PARTITION"], "chosen_cpu")
        self.assertEqual(values["HOST_OUTPUT"], str(self.root / "outputs"))
        args.command = "run"
        args.output_dir = "~/example_output"
        af3._validate_args(args)
        self.assertEqual(args.output_dir, str(Path.home() / "example_output"))
        af3.INF_FALLBACK_PARTITION = "configured_large_gpu"
        args.fallback_partition = ""
        self.assertEqual(af3._submission_config(args)["INF_FALLBACK_PARTITION"], "")

    def test_linux_host_paths_reject_container_bind_delimiters(self):
        with mock.patch.object(R.os, "name", "posix"):
            for path in ("/example/cache:other", "/example/cache,other"):
                with self.subTest(path=path), self.assertRaisesRegex(ValueError, "bind delimiters"):
                    R.normalize_config({"HOST_CACHE": path})

    def test_optional_fallback_and_ssd_are_disabled(self):
        af3.INF_PARTITION = "example_gpu"
        self.assertEqual(af3.infer_partition_for(af3.INF_BIG_TOKEN + 1), "example_gpu")
        self.assertFalse(af3.msa_use_ssd(100000, 1))
        script = af3._infer_script("example", "/example/in", "/example/out",
                                   ["P12345_data.json"], "example_gpu", True, None, False)
        self.assertNotIn("RETRY_ID", script)
        af3.INF_FALLBACK_PARTITION = "large_gpu"
        self.assertEqual(af3.infer_partition_for(af3.INF_BIG_TOKEN + 1), "large_gpu")
        script = af3._infer_script("example", "/example/in", "/example/out",
                                   ["P12345_data.json"], "example_gpu", True, None, False)
        self.assertIn("--partition=large_gpu", script)

    def test_shell_generation_quotes_user_paths_and_shares_module_setup(self):
        special = "/example/logs with 'quotes' and $value"
        af3.CONTAINER_MODULE = "singularity/3.11"
        msa = af3._msa_slurm_lines(job_name="example", cores=2, host_in="/example/in",
                                     host_out=special, array_spec="0-0%1", json_files=["P12345_input.json"],
                                     use_ssd=True, batch_log_dir=special, node_list_file=special + "/nodes",
                                     partition="example_cpu")
        infer = af3._infer_script("example", "/example/in", special,
                                  ["P12345_data.json"], "example_gpu", False, special, False)
        auxiliary = af3._aux_slurm_script("example", "01:00:00", special + "/%j.out", "true", partition="example_cpu")
        for line in af3._container_setup_lines():
            self.assertIn(line, msa)
            self.assertIn(line, infer)
        self.assertIn("LOG_DIR=" + shlex.quote(special), msa)
        self.assertIn('DATA_JSON="$MSA_ROOT/$TASK_NAME/${TASK_NAME}_data.json"', msa)
        self.assertNotIn('mv "$f"', msa)
        self.assertNotIn('rm -rf "$JOB_OUT_DIR"', msa)
        self.assertNotIn("cp -a", msa)
        self.assertIn("#SBATCH --error=" + shlex.quote(special + "/%j.out"), auxiliary)
        self.assertNotIn("{log_path}", auxiliary)
        bash = (str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git" / "bin" / "bash.exe") if os.name == "nt" else shutil.which("bash"))
        if bash and Path(bash).is_file():
            for script in (msa, infer, auxiliary):
                result = subprocess.run([bash, "-n"], input=script, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([bash, "-c", "LOG_DIR=" + shlex.quote(special) + '; printf "%s" "$LOG_DIR"'], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, special)

    def test_snapshot_includes_extracted_modules_and_every_license(self):
        work = self.root / "batch"
        source, config = R.snapshot(str(ROOT / "af3.py"), work, af3.config_snapshot())
        snapshot = Path(source).parent
        for name in ("af3_networkx_community.py", "af3_pae_domains.py", "LICENSE", "THIRD_PARTY_NOTICES.md"):
            self.assertEqual((snapshot / name).read_bytes(), (ROOT / name).read_bytes())
        expected = {p.relative_to(ROOT / "LICENSES") for p in (ROOT / "LICENSES").rglob("*") if p.is_file()}
        actual = {p.relative_to(snapshot / "LICENSES") for p in (snapshot / "LICENSES").rglob("*") if p.is_file()}
        self.assertEqual(actual, expected)
        self.assertTrue(Path(config).is_file())
        (snapshot / "af3_pae_domains.py").write_text("# different code\n")
        with self.assertRaises(ValueError):
            R.snapshot(str(ROOT / "af3.py"), work, af3.config_snapshot())


if __name__ == "__main__":
    unittest.main()
