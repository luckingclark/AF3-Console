"""Environment-scoped launcher, quoting, conflict protection and relocation."""
import contextlib
import io
import shutil
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import af3
import af3_runtime as installer


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='af3-launcher-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / "app space 'quoted' $literal"
        self.app.mkdir()
        for name in installer._GUI_COMMAND_REQUIRED:
            (self.app/name).write_text('# synthetic fixture\n', encoding='utf-8')
        self.prefix = self.root / "environment space 'quoted' $literal"
        (self.prefix/'bin').mkdir(parents=True)

    def install(self):
        return installer._install_gui_launcher(self.app, self.prefix, sys.executable)

    def test_active_environment_must_match_python(self):
        with mock.patch.object(sys, 'prefix', str(self.prefix)), mock.patch.object(sys, 'base_prefix', str(self.prefix)):
            with mock.patch.dict(os.environ, {'CONDA_PREFIX': ''}):
                with self.assertRaises(ValueError): installer.gui_environment_prefix()
            with mock.patch.dict(os.environ, {'CONDA_PREFIX': str(self.root/'other')}):
                with self.assertRaises(ValueError): installer.gui_environment_prefix()
            with mock.patch.dict(os.environ, {'CONDA_PREFIX': str(self.prefix)}):
                self.assertEqual(installer.gui_environment_prefix(), self.prefix.resolve())
            with mock.patch.object(sys, 'base_prefix', str(self.root/'base')), mock.patch.dict(os.environ, {'CONDA_PREFIX': ''}):
                self.assertEqual(installer.gui_environment_prefix(), self.prefix.resolve())

    def test_install_repeat_and_update_managed_command(self):
        command = self.install()
        self.assertEqual(command, self.prefix/'bin'/'af3_gui')
        original = command.read_bytes()
        self.assertEqual(self.install(), command)
        self.assertEqual(command.read_bytes(), original)
        # Existing installations made by the earlier local script can migrate.
        command.write_text(command.read_text().replace(installer._GUI_COMMAND_MARKER,
                           installer._GUI_COMMAND_LEGACY_MARKER), encoding='utf-8')
        self.install()
        self.assertIn(installer._GUI_COMMAND_MARKER, command.read_text())
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

    def test_cli_dispatch_and_clear_failure_status(self):
        args = af3.build_parser().parse_args(['install-gui'])
        self.assertEqual(args.func, af3.cmd_install_gui)
        with mock.patch.object(installer, 'install_gui_command', return_value=self.prefix/'bin'/'af3_gui') as call:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                args.func(args)
            call.assert_called_once_with(ROOT)
            self.assertIn('Installed: ', output.getvalue())
        with mock.patch.object(sys, 'argv', ['af3.py', 'install-gui']), \
             mock.patch.object(installer, 'install_gui_command', side_effect=ValueError('synthetic failure')):
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors), self.assertRaises(SystemExit) as stopped:
                af3.main()
            self.assertEqual(stopped.exception.code, 1)
            self.assertIn('synthetic failure', errors.getvalue())

    @unittest.skipUnless(os.name == 'posix', 'Actual POSIX launcher execution is covered by Linux CI')
    def test_command_runs_from_any_directory_using_environment_python(self):
        # A real venv verifies that resolving python symlinks does not lose its prefix.
        import venv
        venv.EnvBuilder(with_pip=False).create(self.prefix)
        python = self.prefix/'bin'/'python'
        (self.app/'af3_gui').write_text(
            'import sys,os,json\nprint(json.dumps({"prefix":sys.prefix,"cwd":os.getcwd(),'
            '"args":sys.argv[1:],"no_pyc":sys.dont_write_bytecode}))\n', encoding='utf-8')
        # Exercise the documented CLI, with a real venv and no Qt/AF3 resources.
        for name in installer._GUI_COMMAND_REQUIRED:
            if name != 'af3_gui':
                shutil.copy2(ROOT/name, self.app/name)
        install_env = dict(os.environ, CONDA_PREFIX='', AF3_CONFIG=str(self.root/'absent.json'),
                           AF3_BASE=str(self.root/'workspace'), AF3_SNAPSHOT='')
        installed = subprocess.run([str(python), '-B', str(self.app/'af3.py'), 'install-gui'],
                                   cwd=self.root, env=install_env, capture_output=True,
                                   encoding='utf-8', timeout=20)
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        command = self.prefix/'bin'/'af3_gui'
        self.assertIn('Installed: ', installed.stdout)
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
