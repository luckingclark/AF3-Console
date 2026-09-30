"""Environment-scoped launcher, quoting, conflict protection and relocation."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('af3_command_installer', ROOT/'install_command.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='af3-launcher-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / "app space 'quoted' $literal"
        self.app.mkdir()
        for name in installer.REQUIRED:
            (self.app/name).write_text('# synthetic fixture\n', encoding='utf-8')
        self.prefix = self.root / "environment space 'quoted' $literal"
        (self.prefix/'bin').mkdir(parents=True)

    def install(self):
        return installer.install_launcher(self.app, self.prefix, sys.executable)

    def test_active_environment_must_match_python(self):
        with mock.patch.object(sys, 'prefix', str(self.prefix)), mock.patch.object(sys, 'base_prefix', str(self.prefix)):
            with mock.patch.dict(os.environ, {'CONDA_PREFIX': ''}):
                with self.assertRaises(ValueError): installer.environment_prefix()
            with mock.patch.dict(os.environ, {'CONDA_PREFIX': str(self.root/'other')}):
                with self.assertRaises(ValueError): installer.environment_prefix()
            with mock.patch.dict(os.environ, {'CONDA_PREFIX': str(self.prefix)}):
                self.assertEqual(installer.environment_prefix(), self.prefix.resolve())
            with mock.patch.object(sys, 'base_prefix', str(self.root/'base')), mock.patch.dict(os.environ, {'CONDA_PREFIX': ''}):
                self.assertEqual(installer.environment_prefix(), self.prefix.resolve())

    def test_install_repeat_and_update_managed_command(self):
        command = self.install()
        self.assertEqual(command, self.prefix/'bin'/'af3_gui')
        original = command.read_bytes()
        self.assertEqual(self.install(), command)
        self.assertEqual(command.read_bytes(), original)
        moved = self.root/'new application'
        self.app.rename(moved)
        self.app = moved
        self.install()
        self.assertNotEqual(command.read_bytes(), original)
        self.assertIn(str(moved/'af3_gui'), command.read_text())
        self.assertEqual(sorted(p.name for p in command.parent.iterdir()), ['af3_gui'])

    def test_unrelated_command_and_incomplete_download_are_not_overwritten(self):
        command = self.prefix/'bin'/'af3_gui'
        command.write_text('existing command', encoding='utf-8')
        with self.assertRaises(ValueError): self.install()
        self.assertEqual(command.read_text(), 'existing command')
        command.unlink()
        (self.app/'af3.py').unlink()
        with self.assertRaises(ValueError): self.install()
        self.assertFalse(command.exists())

    def test_failed_write_preserves_managed_command(self):
        command = self.install()
        old = command.read_bytes()
        moved = self.root/'another application'
        self.app.rename(moved); self.app = moved
        with mock.patch.object(installer.os, 'replace', side_effect=OSError('synthetic write failure')):
            with self.assertRaises(OSError): self.install()
        self.assertEqual(command.read_bytes(), old)
        self.assertEqual(len(list(command.parent.iterdir())), 1)

    @unittest.skipUnless(os.name == 'posix', 'Actual POSIX launcher execution is covered by Linux CI')
    def test_command_runs_from_any_directory_using_environment_python(self):
        # A real venv verifies that resolving python symlinks does not lose its prefix.
        import venv
        venv.EnvBuilder(with_pip=False).create(self.prefix)
        python = self.prefix/'bin'/'python'
        (self.app/'af3_gui').write_text(
            'import sys,os,json\nprint(json.dumps({"prefix":sys.prefix,"cwd":os.getcwd(),'
            '"args":sys.argv[1:],"no_pyc":sys.dont_write_bytecode}))\n', encoding='utf-8')
        command = installer.install_launcher(self.app, self.prefix, python)
        cwd = self.root/'unrelated working directory'; cwd.mkdir()
        env = dict(os.environ, PATH=str(self.prefix/'bin')+os.pathsep+os.environ['PATH'])
        result = subprocess.run(['af3_gui', "argument with 'quotes' $literal"], cwd=cwd, env=env,
                                capture_output=True, encoding='utf-8', check=True, timeout=20)
        actual = json.loads(result.stdout)
        self.assertEqual(Path(actual['prefix']), self.prefix)
        self.assertEqual(Path(actual['cwd']), cwd)
        self.assertEqual(actual['args'], ["argument with 'quotes' $literal"])
        self.assertTrue(actual['no_pyc'])
        # The entry must fail clearly after the application is moved.
        self.app.rename(self.root/'moved after install')
        failure = subprocess.run([str(command)], cwd=cwd, env=env, capture_output=True, encoding='utf-8', timeout=20)
        self.assertNotEqual(failure.returncode, 0)
        self.assertIn('moved or removed', failure.stderr)


if __name__ == '__main__':
    unittest.main()
