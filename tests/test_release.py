"""Exercise the actual release archives, isolated imports, and privacy audit."""
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))
import audit_release as audit
import build_release as release


class ReleaseTests(unittest.TestCase):
    def test_public_manifest_and_decoded_payload_pass_audit(self):
        self.assertEqual(audit.audit(ROOT), [])
        entries = audit.manifest(ROOT)
        self.assertTrue(set(entries['runtime']).issubset(entries['source']))
        for name in ('af3.py', 'af3_runtime.py', 'af3_pae.py',
                     'af3_pae_domains.py', 'af3_networkx_community.py', 'af3_gui',
                     'LICENSE', 'THIRD_PARTY_NOTICES.md'):
            self.assertIn(name, entries['runtime'])

    def test_private_material_and_outer_gui_wrapper_are_detected(self):
        artificial_id = 'P' + '0' * 5
        self.assertTrue(audit.audit_text('example.txt', artificial_id))
        self.assertTrue(audit.audit_text('example.txt', 'ghp_' + 'A' * 36))
        self.assertTrue(audit.audit_text('example.txt', 'confidential_marker', ['confidential_marker']))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'af3_gui'
            path.write_text((ROOT/'af3_gui').read_text(encoding='utf-8') +
                            '\n# ' + artificial_id + '\n', encoding='utf-8')
            self.assertTrue(audit.audit(Path(temporary), files=['af3_gui']))
        self.assertEqual(audit.audit_text('example.txt', 'P12345x2+Q12345'), [])

    def test_archives_are_reproducible_complete_and_run_in_isolation(self):
        with tempfile.TemporaryDirectory(prefix='af3-release-test-') as temporary:
            folder = Path(temporary)
            with mock.patch.dict(os.environ, {
                    'AF3_BASE': str(folder/'never-package-this-workspace'),
                    'AF3_CONFIG': str(folder/'never-package-this-config.json')}):
                release.build(ROOT, folder/'first')
                release.build(ROOT, folder/'second')
            for first in (folder/'first').glob('*'):
                self.assertEqual(first.read_bytes(), (folder/'second'/first.name).read_bytes())
            for kind in ('source', 'runtime'):
                archive = next((folder/'first').glob('*-' + kind + '.zip'))
                unpacked = folder/kind
                with zipfile.ZipFile(archive) as z:
                    names = z.namelist()
                    prefix = names[0].split('/')[0]
                    expected = {prefix+'/'+p for p in audit.manifest(ROOT)[kind]} | {prefix+'/manifest.json'}
                    self.assertEqual(set(names), expected)
                    z.extractall(unpacked)
                tree = unpacked/prefix
                metadata = json.loads((tree/'manifest.json').read_text(encoding='utf-8'))
                for name, expected_hash in metadata['files'].items():
                    self.assertEqual(hashlib.sha256((tree/name).read_bytes()).hexdigest(), expected_hash)
                env = dict(os.environ, AF3_BASE=str(folder/'isolated-work'),
                           AF3_CONFIG=str(folder/'isolated-config.json'),
                           AF3_PAE_CACHE=str(folder/'pae'), PYTHONPATH='',
                           PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
                env.pop('AF3_SNAPSHOT', None)
                for args in (['af3.py','--help'], ['af3.py','--version']):
                    proc = subprocess.run([sys.executable, '-B', *args], cwd=tree, env=env,
                                          capture_output=True, text=True, encoding='utf-8', timeout=40)
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                if kind == 'runtime':
                    import importlib.util
                    if importlib.util.find_spec('PySide6'):
                        env.update(QT_QPA_PLATFORM='offscreen', HOME=str(folder/'qt-home'),
                                   USERPROFILE=str(folder/'qt-home'))
                        script = '''import os, runpy
from pathlib import Path
from unittest.mock import patch
ns = runpy.run_path('af3_gui', run_name='runtime_smoke')
Q = ns['_qt']
from PySide6 import QtCore, QtWidgets
Settings = QtCore.QSettings
def isolated(*args, **kwargs):
    return Settings(str(Path(os.environ['HOME'])/'qt.ini'), Settings.IniFormat)
app = QtWidgets.QApplication([])
Q.qta = None
with patch.object(Q, 'enqueue', return_value=None), patch.object(QtCore, 'QSettings', side_effect=isolated):
    win = Q.App()
    win._timer.stop(); win._dash_timer.stop()
    win._set_lang('en')
    win._help_topic_key = 'about'; win._help_filter()
    assert 'PKU-Gaolab, Ming-Ao Lu' in win.help_body.toPlainText()
    assert 'GNU LESSER GENERAL PUBLIC LICENSE' in ns['H'].license_text('lgpl')[1]
    assert Path(os.environ['HOME']).resolve() in Path(win._settings.fileName()).resolve().parents
    win.close()
print('runtime GUI and offline licenses passed without readable GUI source modules')
'''
                        proc = subprocess.run([sys.executable, '-B', '-c', script], cwd=tree, env=env,
                                              capture_output=True, text=True, encoding='utf-8', timeout=40)
                        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                # LGPL recipients can change and import the shipped source.
                with (tree/'af3_pae_domains.py').open('a', encoding='utf-8') as stream:
                    stream.write('\nLOCAL_MODIFICATION_ALLOWED = True\n')
                proc = subprocess.run([sys.executable, '-B', '-c',
                    'import af3_pae_domains as p; assert p.LOCAL_MODIFICATION_ALLOWED'],
                    cwd=tree, env=env, capture_output=True, text=True, timeout=15)
                self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_public_local_document_links_exist(self):
        import re
        for relative in audit.manifest(ROOT)['source']:
            path = ROOT/relative
            if path.suffix != '.md':
                continue
            for target in re.findall(r'\]\(([^)]+)\)', path.read_text(encoding='utf-8')):
                if '://' in target or target.startswith(('#', 'mailto:')):
                    continue
                clean = target.split('#', 1)[0]
                self.assertTrue((path.parent/clean).exists(), relative + ': ' + target)


if __name__ == '__main__':
    unittest.main()
