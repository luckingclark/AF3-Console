"""Public help, configuration and offline Qt checks with isolated user state."""
import ast
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import af3_ui_help as H
import af3_runtime as R


class PublicHelpTests(unittest.TestCase):
    def test_examples_contain_only_public_accession_placeholders(self):
        for name in ('af3_ui_help.py', 'af3_ui_backend.py', 'af3_ui_text.py',
                     'af3_ui_widgets.py', 'af3_qt_app.py'):
            source = (ROOT / name).read_text(encoding='utf-8')
            ast.parse(source)
            self.assertEqual(set(re.findall(r'[OPQ][0-9][A-Z0-9]{3}[0-9]', source)) -
                             {'P12345', 'Q12345'}, set(), name)
            self.assertIsNone(re.search(r'\b[A-Z0-9]+_HUMAN\b', source), name)
            self.assertIsNone(re.search(r'p:[ACDEFGHIKLMNPQRSTVWY]{6,}', source), name)
        for example in H.EXAMPLES.values():
            self.assertNotIn('p:', example)
            self.assertNotIn('<', example)
            self.assertNotIn('...', example)

    def test_bilingual_attribution_and_complete_offline_licenses(self):
        for lang in ('zh', 'en'):
            about = H.article_html('about', lang)
            for expected in (R.VERSION, 'PKU-Gaolab, Ming-Ao Lu', 'MIT', 'ChimeraX', 'Tristan Croll', 'ISOLDE'):
                self.assertIn(expected, about)
            licenses = H.article_html('licenses', lang)
            for expected in ('LGPL-2.1-only', 'BSD-3-Clause', 'Apache 2.0'):
                self.assertIn(expected, licenses)
        for key, (_, relative) in H.LICENSE_FILES.items():
            title, content = H.license_text(key)
            self.assertTrue(title)
            data = (ROOT / relative).read_bytes()
            self.assertIn(data, (content.encode('utf-8'), content.encode('latin-1', errors='replace')))
            self.assertGreater(len(content), 100)
        with self.assertRaises(ValueError):
            H.license_text('../../config.json')


@unittest.skipUnless(importlib.util.find_spec('PySide6'), 'Install the GUI dependencies for the offscreen check')
class PublicQtTests(unittest.TestCase):
    def test_setup_help_and_dynamic_input_in_an_isolated_process(self):
        script = textwrap.dedent(r'''
            import json, os
            from pathlib import Path
            from unittest.mock import patch
            import af3_qt_app as Q
            QtCore, QtWidgets = Q.QtCore, Q.QtWidgets
            home = Path(os.environ['HOME'])
            settings_class = QtCore.QSettings
            def isolated_settings(*args, **kwargs):
                # The (organization, application) overload uses NativeFormat on Windows.
                return settings_class(str(home / 'qt.ini'), settings_class.IniFormat)
            import af3_ui_help as H
            import af3_ui_backend as B
            app = QtWidgets.QApplication([])
            Q.qta = None
            with patch.object(Q, 'enqueue', return_value=None), patch.object(QtCore, 'QSettings', side_effect=isolated_settings):
                win = Q.App()
            win._timer.stop()
            win._dash_timer.stop()
            assert Path(win._settings.fileName()).is_relative_to(home), win._settings.fileName()
            assert not win.setup_resource_toggle.isChecked()
            for key in ('MSA_PARTITION', 'INF_PARTITION', 'CONTAINER_RUNTIME',
                        'CONTAINER_MODULE', 'HOST_SSD_CACHE', 'AUX_PARTITION', 'INF_FALLBACK_PARTITION'):
                assert key in win.setup_edits
                field = win.setup_edits[key]
                node = field
                while node is not None and node is not win:
                    assert node.isVisible() or not node.isHidden() or node is win.tabs.widget(0)
                    node = node.parentWidget()
            win.setup_edits['HOST_BASE'].setText(str(home / 'workspace'))
            assert win.setup_derived['HOST_OUTPUT'].text() == str(home / 'workspace' / 'output')
            win.setup_edits['MSA_PARTITION'].setText('cpu')
            win.setup_edits['INF_PARTITION'].setText('gpu')
            win.setup_edits['CONTAINER_RUNTIME'].setText('apptainer')
            for key in ('CONTAINER_MODULE', 'HOST_SSD_CACHE', 'INF_FALLBACK_PARTITION', 'AUX_PARTITION'):
                win.setup_edits[key].setText('')
            def immediate(queue, target, done):
                done(None, target())
            checks = [{'key':'CONTAINER_RUNTIME', 'label':'Container runtime', 'ok':True, 'message':'test preflight'}]
            with patch.object(Q, 'enqueue', side_effect=immediate), patch.object(B.af3, 'deployment_checks', return_value=checks) as checked:
                win._check_resources()
                assert checked.call_args.args[0]['CONTAINER_RUNTIME'] == 'apptainer'
                assert checked.call_args.args[0]['INF_FALLBACK_PARTITION'] == ''
            assert 'test preflight' in win.setup_tree_view.toPlainText()
            with patch.object(QtWidgets.QMessageBox, 'critical') as error:
                win._setup_apply()
                assert not error.called
            saved = json.loads(Path(B.R.config_path()).read_text())
            assert saved['CONTAINER_RUNTIME'] == 'apptainer'
            assert B.af3.INF_FALLBACK_PARTITION == ''
            assert B.af3.HOST_SSD_CACHE == ''
            win._set_lang('zh')
            win.help_search.setText('About')
            assert any(win.help_topics.item(i).data(QtCore.Qt.UserRole) == 'about'
                       for i in range(win.help_topics.count()))
            win.help_search.setText('ChimeraX')
            assert win.help_topics.count() >= 2
            win._set_lang('en')
            win.help_search.setText('许可')
            assert any(win.help_topics.item(i).data(QtCore.Qt.UserRole) == 'licenses'
                       for i in range(win.help_topics.count()))
            win.help_search.clear()
            with patch.object(QtWidgets.QDialog, 'exec', return_value=0):
                win._help_link(QtCore.QUrl('license:lgpl'))
            viewers = win.findChildren(QtWidgets.QPlainTextEdit)
            assert any('GNU LESSER GENERAL PUBLIC LICENSE' in viewer.toPlainText() for viewer in viewers)
            with patch.object(B.urllib.request, 'urlopen', side_effect=AssertionError('offline test')):
                entities, error = B.capture_af3_parse('p:ACDEFGHIKLMNPQRSTVWY:name=synthetic', fetch=False)
                assert not error and entities[0]['sequence'] == 'ACDEFGHIKLMNPQRSTVWY'
                assert B.entity_to_expr(entities[0]).startswith('p:ACDEFGHIKLMNPQRSTVWY')
            os.environ['AF3_BASE'] = str(home / 'override')
            B.af3.reload_config()
            with patch.object(Q, 'enqueue', return_value=None), patch.object(QtCore, 'QSettings', side_effect=isolated_settings):
                overridden = Q.App()
            assert overridden.setup_edits['HOST_BASE'].isReadOnly()
            assert 'AF3_BASE' in overridden.setup_tree_view.toPlainText()
            assert str(home / 'override') in overridden.setup_edits['HOST_BASE'].text()
            win._settings.sync()
            assert Path(win._settings.fileName()).is_file()
            # Exit without running timers or background scheduler refreshes.
            print('offscreen public UI checks passed')
        ''')
        with tempfile.TemporaryDirectory(prefix='af3-ui-test-') as folder:
            env = dict(os.environ)
            for key in ('AF3_BASE', 'AF3_SNAPSHOT'):
                env.pop(key, None)
            env.update(HOME=folder, USERPROFILE=folder, XDG_CONFIG_HOME=folder,
                       AF3_CONFIG=str(Path(folder) / 'config.json'),
                       QT_QPA_PLATFORM='offscreen', PYTHONDONTWRITEBYTECODE='1',
                       PYTHONIOENCODING='utf-8')
            result = subprocess.run([sys.executable, '-B', '-c', script], cwd=ROOT,
                                    env=env, capture_output=True, text=True, encoding='utf-8', timeout=45)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
