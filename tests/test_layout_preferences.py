"""Real Qt layouts across processes, without personal preferences or cluster activity."""
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
class LayoutPreferenceTests(unittest.TestCase):
    def run_ui(self, folder, body):
        prelude = '''
import json, os
from pathlib import Path
from unittest.mock import patch
import af3_qt_app as Q
import af3_ui_widgets as UI
from PySide6 import QtCore, QtWidgets, QtTest
home = Path(os.environ['HOME'])
app = QtWidgets.QApplication([])
Q.qta = None
settings_class = QtCore.QSettings
def isolated_settings(*args, **kwargs):
    return settings_class(str(home/'qt.ini'), settings_class.IniFormat)
patch.object(QtCore, 'QSettings', side_effect=isolated_settings).start()
patch.object(Q, 'enqueue', return_value=None).start()
patch.object(Q.App, '_startup_msa_sync', return_value=None).start()
win = Q.App()
win._timer.stop(); win._dash_timer.stop()
def settle():
    for _ in range(4): app.processEvents()
def splitter(key, parent=win):
    return next(x for x in parent.findChildren(UI.PersistentSplitter)
                if x._layout_key == 'layout/splitters/v1/' + key)
def proportions(sp):
    sizes = sp.sizes()
    return sizes[0] / sum(sizes)
def structure_view():
    win._loaded_struct_entries = [{'path':str(home/'P12345_model.cif'),
                                   'a':'P12345', 'b':'', 'label':'P12345'}]
    view = win._res_struct_tab(str(home))
    view.resize(1850, 800); view.show(); settle()
    return view
'''
        env = dict(os.environ, HOME=folder, USERPROFILE=folder, XDG_CONFIG_HOME=folder,
                   AF3_CONFIG=str(Path(folder)/'config.json'), AF3_BASE=str(Path(folder)/'work'),
                   AF3_SNAPSHOT='', AF3_PAE_CACHE=str(Path(folder)/'pae'),
                   QT_QPA_PLATFORM='offscreen', PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
        result = subprocess.run([sys.executable, '-B', '-c', textwrap.dedent(prelude)+'\n'+textwrap.dedent(body)],
                                cwd=ROOT, env=env, capture_output=True, encoding='utf-8', timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_toolbar_is_immediately_below_setup_title_in_both_languages(self):
        with tempfile.TemporaryDirectory(prefix='af3-layout-toolbar-') as home:
            self.run_ui(home, '''
                win.resize(1800, 1000); win.tabs.setCurrentIndex(0); win.show(); settle()
                page = win.tabs.widget(0).widget()
                layout = page.layout()
                assert layout.itemAt(0).layout().itemAt(0).widget().objectName() == 'pagehead'
                actions = layout.itemAt(1).layout()
                assert actions.itemAt(0).widget() is win.setup_import_button
                assert sum(isinstance(actions.itemAt(i).widget(), QtWidgets.QPushButton)
                           for i in range(actions.count())) == 4
                for language in ('zh', 'en'):
                    win._set_lang(language); settle()
                    button_bottom = win.setup_import_button.mapTo(page, QtCore.QPoint(0,win.setup_import_button.height())).y()
                    pane_top = splitter('setup').mapTo(page,QtCore.QPoint(0,0)).y()
                    assert button_bottom <= pane_top, (button_bottom,pane_top)
                    assert win.setup_import_button.isVisible()
                assert not (home/'config.json').exists()
                win.close()
            ''')

    def test_all_pages_restore_in_fresh_process_and_result_rebuild(self):
        with tempfile.TemporaryDirectory(prefix='af3-layout-restart-') as home:
            self.run_ui(home, '''
                win.resize(1900, 1050); win.show(); settle()
                expected = {}
                for tab,key,ratio in [(0,'setup',.57),(2,'pulldown/inputs',.72),(3,'scan/inputs',.36),(6,'help',.13)]:
                    win.tabs.setCurrentIndex(tab); settle()
                    sp = splitter(key)
                    sp.moveSplitter(round(sum(sp.sizes())*ratio),1); settle()
                    expected[key] = proportions(sp)
                    assert sp._settings.value(sp._preference_key()) == sp.sizes()
                view=structure_view(); sp=splitter('results/structure',view)
                sp.moveSplitter(round(sum(sp.sizes())*.65),1); settle()
                expected['results/structure']=proportions(sp)
                QtTest.QTest.qWait(300)
                assert (home/'qt.ini').exists()   # Auto-save without closing the window.
                (home/'expected.json').write_text(json.dumps(expected))
            ''')
            self.run_ui(home, '''
                expected=json.loads((home/'expected.json').read_text())
                before={key:win._settings.value(key) for key in win._settings.allKeys() if key.startswith('layout/')}
                win.resize(1800, 1000); win.show(); settle()
                for tab,key in [(0,'setup'),(2,'pulldown/inputs'),(3,'scan/inputs'),(6,'help')]:
                    win.tabs.setCurrentIndex(tab); settle()
                    actual=proportions(splitter(key))
                    assert abs(actual-expected[key]) < .035, (key,actual,expected[key])
                view=structure_view()
                assert abs(proportions(splitter('results/structure',view))-expected['results/structure']) < .02
                view.close(); view.deleteLater(); settle()
                rebuilt=structure_view()
                assert abs(proportions(splitter('results/structure',rebuilt))-expected['results/structure']) < .02
                win._set_lang('en'); win._set_font_size(12, quiet=True); settle()
                after={key:win._settings.value(key) for key in win._settings.allKeys() if key.startswith('layout/')}
                assert before==after, (before,after)   # Hidden/layout events do not overwrite preferences.
                assert not (home/'config.json').exists()
                rebuilt.close(); win.close()
            ''')

    def test_responsive_orientations_are_independent_and_invalid_settings_are_ignored(self):
        with tempfile.TemporaryDirectory(prefix='af3-layout-directions-') as home:
            self.run_ui(home, '''
                key='test-orientations'
                columns=UI.ResponsiveColumns(threshold=700,settings=win._settings,key=key)
                for _ in range(2):
                    pane=QtWidgets.QWidget(); pane.setMinimumSize(30,30); columns.addPanel(pane)
                columns.resize(1200,700); columns.show(); settle()
                sp=columns.splitter
                assert sp.orientation()==QtCore.Qt.Horizontal
                sp.moveSplitter(850,1); settle(); horizontal=proportions(sp)
                columns.resize(600,850); settle()
                assert sp.orientation()==QtCore.Qt.Vertical
                sp.moveSplitter(300,1); settle(); vertical=proportions(sp)
                columns.resize(1200,700); settle()
                assert abs(proportions(sp)-horizontal)<.01
                columns.resize(600,850); settle()
                assert abs(proportions(sp)-vertical)<.01
                old=sp.sizes()
                for invalid in ([0,0],[-5,10],['bad',20],[2,3,4],{'a':2},'invalid'):
                    win._settings.setValue(sp._preference_key(),invalid)
                    sp.restore_proportions()
                    assert sp.sizes()==old
                columns.close(); win.close()
            ''')


if __name__=='__main__':
    unittest.main()
