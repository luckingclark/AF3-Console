"""Import templates without changing their source; persist across fresh launches."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec('PySide6'), 'Install GUI dependencies')
class ConfigImportTests(unittest.TestCase):
    def run_ui(self, body):
        prelude = '''
import json, os, subprocess, sys
from pathlib import Path
from unittest.mock import patch
home = Path(os.environ['HOME'])
import af3_qt_app as Q
import af3_ui_backend as B
QtCore, QtWidgets = Q.QtCore, Q.QtWidgets
app = QtWidgets.QApplication([])
Q.qta = None
settings_class = QtCore.QSettings
def isolated_settings(*args, **kwargs):
    return settings_class(str(home/'qt.ini'), settings_class.IniFormat)
with patch.object(Q, 'enqueue', return_value=None), patch.object(QtCore, 'QSettings', side_effect=isolated_settings):
    win = Q.App()
win._timer.stop(); win._dash_timer.stop()
win._set_lang('en')
source = home/'shared template.json'
personal = Path(B.personal_config_path())
template = {'HOST_BASE': str(home/'template-work'), 'HOST_MODELS': str(home/'shared models'),
            'HOST_UNIPROT_SHARED': str(home/'shared sequences'),
            'MSA_PARTITION': 'example_cpu', 'INF_PARTITION': 'example_gpu',
            'MSA_NTASKS_SINGLE': 7, 'MSA_NTASKS_BATCH': 3, 'INF_UM_TOKEN': 4096,
            'INF_BUCKETS': '256,512,1024', 'WATCHER_POLL_SEC': 43,
            'MSA_BACKUP_DIRS': [str(home/'backup one'), str(home/'backup two')]}
source.write_text(json.dumps(template), encoding='utf-8-sig')
original = source.read_bytes()
def import_file():
    with patch.object(QtWidgets.QFileDialog, 'getOpenFileName', return_value=(str(source), 'JSON (*.json)')):
        win.setup_import_button.click()
'''
        with tempfile.TemporaryDirectory(prefix='af3-config-import-') as folder:
            env = dict(os.environ, HOME=folder, USERPROFILE=folder, XDG_CONFIG_HOME=folder,
                       AF3_CONFIG=str(Path(folder)/'old-selected.json'),
                       AF3_BASE=str(Path(folder)/'old-env-work'), AF3_SNAPSHOT='',
                       QT_QPA_PLATFORM='offscreen', PYTHONDONTWRITEBYTECODE='1',
                       PYTHONIOENCODING='utf-8', AF3_PAE_CACHE=str(Path(folder)/'pae'))
            result = subprocess.run([sys.executable, '-B', '-c', prelude+'\n'+textwrap.dedent(body)],
                                    cwd=ROOT, env=env, capture_output=True, encoding='utf-8', timeout=45)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_review_save_and_fresh_process_retain_all_parameters(self):
        self.run_ui('''
            personal.parent.mkdir(parents=True)
            old_personal = json.dumps({'MSA_PARTITION': 'old_cpu'}).encode()
            personal.write_bytes(old_personal)
            import_file()
            assert source.read_bytes() == original and personal.read_bytes() == old_personal
            assert B.HOST_BASE == str(home/'old-env-work')
            assert not win.setup_edits['HOST_BASE'].isReadOnly()
            assert len(win.setup_msa_backup_edits) == 2
            assert win.setup_derived['HOST_MODELS'].text() == template['HOST_MODELS']
            assert win.setup_derived['HOST_UNIPROT_SHARED'].text() == template['HOST_UNIPROT_SHARED']
            win.setup_edits['HOST_BASE'].setText(str(home/'my work'))
            win.setup_edits['MSA_MAX_CONCURRENT'].setText('2')
            assert win.setup_derived['HOST_OUTPUT'].text() == str(home/'my work'/'output')
            assert win.setup_derived['HOST_MODELS'].text() == template['HOST_MODELS']
            exported = json.loads(win._setup_config_lines())
            assert exported['MSA_NTASKS_SINGLE'] == 7 and exported['INF_UM_TOKEN'] == 4096
            def immediate(queue, target, done): done(None, target())
            with patch.object(Q, 'enqueue', side_effect=immediate), patch.object(B.af3, 'deployment_checks', return_value=[]) as check:
                win._check_resources()
                assert check.call_args.args[0]['MSA_NTASKS_BATCH'] == 3
                assert check.call_args.args[0]['HOST_INFER_DATA'] == str(home/'my work'/'infer_data')
            with patch.object(QtWidgets.QMessageBox, 'critical') as error:
                win._setup_apply()
                assert not error.called, error.call_args
            assert source.read_bytes() == original
            assert 'AF3_CONFIG' not in os.environ and 'AF3_BASE' not in os.environ
            assert next(personal.parent.glob('config.json.before-import-*')).read_bytes() == old_personal
            assert not (home/'old-selected.json').exists()
            assert B.HOST_BASE == str(home/'my work') and str(personal) == B.R.config_path()
            assert 'automatically' in win.setup_import_note.text()
            win._set_lang('zh')
            assert win.setup_import_button.text() == '导入配置 JSON'
            assert '自动读取' in win.setup_import_note.text()
            child = subprocess.run([sys.executable, '-B', '-c', 'import af3,json; print(json.dumps(af3.config_snapshot()))'],
                                   capture_output=True, encoding='utf-8', check=True, env=dict(os.environ))
            saved = json.loads(child.stdout)
            for key in ('MSA_NTASKS_SINGLE', 'MSA_NTASKS_BATCH', 'INF_UM_TOKEN', 'INF_BUCKETS', 'WATCHER_POLL_SEC', 'MSA_BACKUP_DIRS', 'HOST_UNIPROT_SHARED'):
                assert saved[key] == template[key], key
            assert saved['HOST_BASE'] == str(home/'my work')
            assert saved['MSA_MAX_CONCURRENT'] == 2
            assert saved['HOST_INFER_DATA'] == str(home/'my work'/'infer_data')
            assert 'HOST_INFER_DATA' not in json.loads(personal.read_text())
            win.setup_edits['HOST_BASE'].setText(str(home/'later work'))
            with patch.object(QtWidgets.QMessageBox, 'critical') as error:
                win._setup_apply()
                assert not error.called
            assert B.HOST_INFER_DATA == str(home/'later work'/'infer_data')
        ''')

    def test_cancel_and_invalid_import_leave_form_files_and_environment_alone(self):
        self.run_ui('''
            before = win._setup_values(); env = dict(os.environ)
            with patch.object(QtWidgets.QFileDialog, 'getOpenFileName', return_value=('', '')):
                win._setup_import()
            assert before == win._setup_values()
            invalids = ['{', '[]', '{}', '{"HOST_BASE": null}', '{"MSA_NTASKS_SINGLE":0}',
                        '{"AF3_PY":"not-a-config-field"}', '{"INF_BUCKETS":"512,256"}',
                        '{"WATCHER_TIME":"invalid"}',
                        json.dumps({'HOST_BASE': str(home/'work'), 'HOST_MSA_DATA': str(home/'pool'),
                                    'MSA_BACKUP_DIRS': [str(home/'pool'/'child')]})]
            for text in invalids:
                source.write_text(text, encoding='utf-8')
                with patch.object(QtWidgets.QMessageBox, 'critical') as error:
                    import_file()
                    assert error.called, text
                assert before == win._setup_values()
                assert dict(os.environ) == env
                assert not personal.exists()
            assert win._setup_import_config is None
        ''')

    def test_save_failure_preserves_original_and_can_be_retried(self):
        self.run_ui('''
            import_file()
            env = dict(os.environ)
            with patch.object(B.R, 'atomic_json', side_effect=OSError('synthetic write failure')), patch.object(QtWidgets.QMessageBox, 'critical') as error:
                win._setup_apply()
                assert error.called
            assert dict(os.environ) == env and not personal.exists()
            assert source.read_bytes() == original and win._setup_import_config is not None
            with patch.object(QtWidgets.QMessageBox, 'critical') as error:
                win._setup_apply()
                assert not error.called
            assert personal.exists() and source.read_bytes() == original
        ''')

    def test_explicit_shared_paths_and_unchanged_import_are_saved(self):
        self.run_ui('''
            template['HOST_MODELS'] = str(home/'template-work'/'models')
            template['HOST_INFER_DATA'] = str(home/'shared-infer')
            source.write_text(json.dumps(template), encoding='utf-8')
            import_file()
            win.setup_edits['HOST_BASE'].setText(str(home/'new-work'))
            assert win.setup_derived['HOST_MODELS'].text() == template['HOST_MODELS']
            assert win._setup_values()['HOST_INFER_DATA'] == template['HOST_INFER_DATA']
            with patch.object(QtWidgets.QMessageBox, 'critical') as error:
                win._setup_apply()
                assert not error.called
            # A second import identical to the active form still writes personal defaults.
            source.write_bytes(personal.read_bytes())
            import_file()
            with patch.object(QtWidgets.QMessageBox, 'critical') as error:
                win._setup_apply()
                assert not error.called and win._setup_import_config is None
        ''')


if __name__ == '__main__':
    unittest.main()
