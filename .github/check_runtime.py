"""Check the deployable layout against its matching, separately checked-out source."""
import argparse
import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'AF3_Console'
RUNTIME = ('af3_gui', 'af3.py', 'af3_runtime.py', 'af3_pae.py',
           'af3_msa_sync.py', 'af3_pae_domains.py', 'af3_networkx_community.py',
           'fonts/wqy-microhei.ttc')


def check(source):
    source = source.resolve()
    sys.path.insert(0, str(source / 'packaging'))
    import audit_release
    entries = [p.relative_to(ROOT).as_posix() for p in ROOT.rglob('*')
               if p.is_file() and not any(part.startswith('.') for part in p.relative_to(ROOT).parts)]
    # Scan the real deployment wrapper and its decoded modules as well as prose.
    app_entries = [p.relative_to(APP).as_posix() for p in APP.rglob('*') if p.is_file()]
    assert not audit_release.audit(APP, files=app_entries)
    assert not audit_release.audit(ROOT, files=[p for p in entries if not p.startswith('AF3_Console/')])
    for name in RUNTIME:
        assert (APP / name).read_bytes() == (source / name).read_bytes(), name
    for path in (source / 'LICENSES').iterdir():
        assert path.read_bytes() == (APP / 'LICENSES' / path.name).read_bytes(), path.name
    for name, text in audit_release.decoded_gui(APP / 'af3_gui').items():
        assert text == (source / name).read_text(encoding='utf-8'), name
    assert (APP / 'config.example.json').read_bytes() == (source / 'config.example.json').read_bytes()
    assert (ROOT / 'environment.yml').read_bytes() == (source / 'environment.yml').read_bytes()
    assert not (APP / 'config.json').exists()
    assert not (APP / 'site_config.json').exists()
    for path in ROOT.rglob('*.md'):
        if any(part.startswith('.') for part in path.relative_to(ROOT).parts):
            continue
        for target in re.findall(r'\]\(([^)]+)\)', path.read_text(encoding='utf-8')):
            if '://' not in target and not target.startswith(('#', 'mailto:')):
                assert (path.parent / target.split('#')[0]).exists(), (path, target)
    commands = []
    for lang in ('en', 'zh-CN'):
        text = (ROOT / f'docs/usage.{lang}.md').read_text(encoding='utf-8')
        commands.append([line.strip() for block in re.findall(r'```bash\n(.*?)```', text, re.S)
                         for line in block.splitlines() if line.strip()])
    assert commands[0] == commands[1], 'Bilingual commands differ'
    with tempfile.TemporaryDirectory(prefix='af3-deploy-check-') as tmp:
        home = Path(tmp)
        env = dict(os.environ, HOME=tmp, USERPROFILE=tmp, AF3_BASE=str(home/'work'),
                   AF3_CONFIG=str(home/'config.json'), AF3_PAE_CACHE=str(home/'pae'),
                   QT_QPA_PLATFORM='offscreen', PYTHONDONTWRITEBYTECODE='1',
                   PYTHONIOENCODING='utf-8', PYTHONPATH='')
        env.pop('AF3_SNAPSHOT', None)
        for args in (['af3.py', '--help'], ['af3.py', '--version']):
            p = subprocess.run([sys.executable, '-B', *args], cwd=APP, env=env,
                               capture_output=True, text=True, encoding='utf-8', timeout=40)
            assert p.returncode == 0, p.stdout + p.stderr
        script = '''import os, runpy
from pathlib import Path
from unittest.mock import patch
ns = runpy.run_path('af3_gui', run_name='deployment_smoke')
Q, H = ns['_qt'], ns['H']
from PySide6 import QtCore, QtWidgets
Settings = QtCore.QSettings
def isolated(*args, **kwargs):
    return Settings(str(Path(os.environ['HOME'])/'qt.ini'), Settings.IniFormat)
app = QtWidgets.QApplication([])
assert Q._register_cjk_font(app)
Q.qta = None
with patch.object(Q, 'enqueue', return_value=None), patch.object(QtCore, 'QSettings', side_effect=isolated):
    win = Q.App()
    win._timer.stop(); win._dash_timer.stop()
    for lang in ('zh', 'en'):
        win._set_lang(lang)
        win._help_topic_key = 'about'; win._help_filter()
        assert 'PKU-Gaolab, Ming-Ao Lu' in win.help_body.toPlainText()
    for key in H.LICENSE_FILES:
        assert len(H.license_text(key)[1]) > 100
    assert 'GNU LESSER GENERAL PUBLIC LICENSE' in H.license_text('lgpl')[1]
    assert Path(os.environ['HOME']).resolve() in Path(win._settings.fileName()).resolve().parents
    win.close()
print('Deployment GUI, font, bilingual About and offline licenses passed')
'''
        p = subprocess.run([sys.executable, '-B', '-c', script], cwd=APP, env=env,
                           capture_output=True, text=True, encoding='utf-8', timeout=40)
        assert p.returncode == 0, p.stdout + p.stderr
        print(p.stdout.strip())
    print('Runtime/source equality, decoded GUI, privacy, document links and bilingual commands passed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    check(parser.parse_args().source)
