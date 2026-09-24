"""MSA backup controls and confirmation boundaries with isolated, synthetic Qt state."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec('PySide6'), 'Install GUI dependencies for offscreen checks')
class MsaPoolUiTests(unittest.TestCase):
    def run_ui(self, body, backups=0):
        prelude = r'''
            import json, os
            from pathlib import Path
            from unittest.mock import patch
            home = Path(os.environ['HOME'])
            roots = [str(home / 'primary'), str(home / 'backup one'), str(home / 'backup two')]
            Path(os.environ['AF3_CONFIG']).write_text(json.dumps({
                'HOST_BASE': str(home / 'workspace'), 'HOST_MSA_DATA': roots[0],
                'MSA_BACKUP_DIRS': roots[1:BACKUP_COUNT + 1]
            }), encoding='utf-8')
            import af3_qt_app as Q
            import af3_ui_backend as B
            QtCore, QtWidgets = Q.QtCore, Q.QtWidgets
            app = QtWidgets.QApplication([])
            Q.qta = None
            settings_class = QtCore.QSettings
            def isolated_settings(*args, **kwargs):
                return settings_class(str(home / 'qt.ini'), settings_class.IniFormat)
            with patch.object(Q, 'enqueue', return_value=None), patch.object(QtCore, 'QSettings', side_effect=isolated_settings):
                win = Q.App()
            win._timer.stop()
            win._dash_timer.stop()
            win._set_lang('en')
            def immediate(queue, target, done, **kwargs):
                done(None, target())
            plan = {'roots': B.msa_pool_paths(), 'copies': [{
                'source': roots[0], 'destination': roots[1],
                'relative': 'P12345_20260602_140829', 'files': 2, 'bytes': 2097152
            }], 'conflicts': [], 'invalid': [], 'errors': []}
        '''.replace('BACKUP_COUNT', str(backups))
        script = textwrap.dedent(prelude) + '\n' + textwrap.dedent(body)
        with tempfile.TemporaryDirectory(prefix='af3-msa-ui-') as folder:
            env = dict(os.environ)
            for key in ('AF3_BASE', 'AF3_SNAPSHOT'):
                env.pop(key, None)
            env.update(HOME=folder, USERPROFILE=folder, XDG_CONFIG_HOME=folder,
                       AF3_CONFIG=str(Path(folder) / 'config.json'), QT_QPA_PLATFORM='offscreen',
                       PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
            result = subprocess.run([sys.executable, '-B', '-c', script], cwd=ROOT, env=env,
                                    capture_output=True, text=True, encoding='utf-8', timeout=45)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_add_remove_save_export_and_resource_checks(self):
        self.run_ui(r'''
            assert not win.setup_msa_backup_edits
            assert win._setup_msa_paths_layout.itemAt(0).layout().itemAt(0).widget() is win.setup_msa_add
            win.setup_msa_add.click()
            win.setup_msa_backup_edits[0].setText(roots[1])
            win.setup_msa_add.click()
            win.setup_msa_backup_edits[1].setText(roots[2])
            win._setup_add_msa_backup('unused fourth path')
            assert len(win.setup_msa_backup_edits) == 2
            assert not win.setup_msa_add.isEnabled()
            assert json.loads(win._setup_config_lines())['MSA_BACKUP_DIRS'] == roots[1:]
            assert roots[1] in win.setup_directory_map.toPlainText()
            assert roots[2] in win.setup_directory_map.toPlainText()
            checks = [{'key': 'MSA_BACKUP_DIRS[1]', 'ok': True, 'message': 'synthetic backup check'}]
            with patch.object(Q, 'enqueue', side_effect=immediate), patch.object(B.af3, 'deployment_checks', return_value=checks) as checked:
                win._check_resources()
                assert checked.call_args.args[0]['MSA_BACKUP_DIRS'] == roots[1:]
            with patch.object(QtWidgets.QMessageBox, 'critical') as error:
                win._setup_apply()
                assert not error.called
            assert B.msa_pool_paths() == roots
            saved = json.loads(Path(B.R.config_path()).read_text())
            assert saved['MSA_BACKUP_DIRS'] == roots[1:]
            for path in roots:
                Path(path).mkdir(exist_ok=True)
                (Path(path) / 'sentinel.txt').write_text('leave existing files alone')
            row = win._setup_msa_backup_rows[0]
            remove = row.layout().itemAt(0).widget()
            assert 'no files are deleted' in remove.toolTip()
            with patch.object(Q.os, 'remove', side_effect=AssertionError('no deletion')),
                 patch.object(Q.shutil, 'rmtree', side_effect=AssertionError('no deletion')):
                remove.click()
            assert len(win.setup_msa_backup_edits) == 1 and win.setup_msa_add.isEnabled()
            with patch.object(QtWidgets.QMessageBox, 'critical') as error:
                win._setup_apply()
                assert not error.called
            assert B.msa_pool_paths() == [roots[0], roots[2]]
            for path in roots:
                assert (Path(path) / 'sentinel.txt').read_text() == 'leave existing files alone'
            win._set_lang('zh')
            assert '备份' in win.setup_msa_add.toolTip()
            assert '同步' in win.setup_msa_sync_button.text()
        '''.replace("with patch.object(Q.os, 'remove', side_effect=AssertionError('no deletion')),\n", "with patch.object(Q.os, 'remove', side_effect=AssertionError('no deletion')), \\\n"))

    def test_startup_scan_is_once_and_default_no_does_not_copy(self):
        self.run_ui(r'''
            dialogs = []
            def decline(box):
                dialogs.append(box.text())
                assert box.defaultButton() is box.button(QtWidgets.QMessageBox.No)
                assert box.escapeButton() is box.button(QtWidgets.QMessageBox.No)
                assert box.textFormat() == QtCore.Qt.PlainText
                assert '2.0 MiB' in box.text()
                assert roots[1] in box.detailedText()
                assert 'P12345_20260602_140829' in box.detailedText()
                return QtWidgets.QMessageBox.No
            with patch.object(Q, 'enqueue', side_effect=immediate), patch.object(B, 'scan_msa_pools', return_value=plan) as scan,
                 patch.object(B, 'sync_msa_pools') as sync, patch.object(QtWidgets.QMessageBox, 'exec', new=decline):
                win._startup_msa_sync()
                win._startup_msa_sync()
                win._set_lang('zh')
                win._set_lang('en')
                win._setup_tree()
                assert scan.call_count == 1 and not sync.called
                assert len(dialogs) == 1
                assert not win._msa_sync_busy and win.setup_msa_sync_button.isEnabled()
        '''.replace("as scan,\n", "as scan, \\\n"), backups=1)

    def test_acceptance_runs_sync_in_worker_and_reports_failures(self):
        self.run_ui(r'''
            calls = []
            pending = []
            def queued(queue, target, done, **kwargs):
                pending.append((target, done))
            def respond(text, details='', question=False, warning=False):
                calls.append((text, question, warning))
                return QtWidgets.QMessageBox.Yes if question else QtWidgets.QMessageBox.Ok
            result = {'copied': 1, 'skipped': 0, 'conflicts': [], 'errors': [{'path': roots[2], 'reason': 'permission denied'}]}
            plan['invalid'] = [{'path': str(home / 'primary' / 'Q12345_data.json'), 'reason': 'missing templates'}]
            with patch.object(Q, 'enqueue', side_effect=queued), patch.object(B, 'scan_msa_pools', return_value=plan) as scan,
                 patch.object(B, 'sync_msa_pools', return_value=result) as sync, patch.object(win, '_msa_sync_message', side_effect=respond):
                win._startup_msa_sync()
                assert not scan.called and not sync.called and win._msa_sync_busy
                target, done = pending.pop(0)
                done(None, target())
                assert scan.call_count == 1 and not sync.called
                assert len(pending) == 1 and win._msa_sync_busy
                target, done = pending.pop(0)
                done(None, target())
                assert sync.call_count == 1 and not win._msa_sync_busy
                assert len(calls) == 2 and calls[0][1] and calls[1][2]
                assert '1 errors' in calls[1][0]
                assert '1 incomplete/unsupported' in calls[1][0]
        '''.replace("as scan,\n", "as scan, \\\n"), backups=2)

    def test_stale_scan_and_close_never_prompt_or_sync(self):
        self.run_ui(r'''
            pending = []
            def queued(queue, target, done, **kwargs):
                pending.append((target, done))
            with patch.object(Q, 'enqueue', side_effect=queued), patch.object(B, 'scan_msa_pools', return_value=plan),
                 patch.object(B, 'sync_msa_pools') as sync, patch.object(win, '_msa_sync_message') as message:
                win._startup_msa_sync()
                target, done = pending.pop(0)
                with patch.object(B, 'msa_pool_paths', return_value=[roots[0], roots[2]]):
                    done(None, target())
                assert not message.called and not sync.called and not win._msa_sync_busy
                win._check_msa_sync(manual=True)
                target, done = pending.pop(0)
                win._closing = True
                done(None, target())
                assert not message.called and not sync.called
        '''.replace("return_value=plan),\n", "return_value=plan), \\\n"), backups=1)

    def test_diagnostics_without_copies_and_manual_unsaved_path_guard(self):
        self.run_ui(r'''
            plan.update(copies=[], conflicts=[{'relative': 'P12345', 'reason': 'different existing product'}],
                        invalid=[{'relative': 'Q12345_data.json', 'reason': 'missing templates'}], errors=[])
            with patch.object(Q, 'enqueue', side_effect=immediate), patch.object(B, 'scan_msa_pools', return_value=plan) as scan,
                 patch.object(B, 'sync_msa_pools') as sync, patch.object(win, '_msa_sync_message') as message:
                win._startup_msa_sync()
                assert message.call_count == 1
                text = message.call_args.args[0]
                assert 'Conflicts: 1' in text and 'incomplete/unsupported: 1' in text
                assert message.call_args.kwargs['warning']
                assert not sync.called
                win.setup_msa_backup_edits[0].setText(roots[2])
                win._check_msa_sync(manual=True)
                assert scan.call_count == 1
                assert 'Save the MSA paths' in message.call_args.args[0]
        '''.replace("as scan,\n", "as scan, \\\n"), backups=1)

    def test_no_backup_and_changed_configuration_while_confirming(self):
        self.run_ui(r'''
            with patch.object(Q, 'enqueue', side_effect=immediate), patch.object(B, 'scan_msa_pools') as scan:
                win._startup_msa_sync()
                assert not scan.called
            win.setup_msa_add.click()
            win.setup_msa_backup_edits[0].setText(roots[1])
            win._setup_apply()
            plan['roots'] = B.msa_pool_paths()
            def stale_yes(*args, **kwargs):
                B.update_af3_config({'MSA_BACKUP_DIRS': [roots[2]]})
                return QtWidgets.QMessageBox.Yes
            with patch.object(Q, 'enqueue', side_effect=immediate), patch.object(B, 'scan_msa_pools', return_value=plan),
                 patch.object(B, 'sync_msa_pools') as sync, patch.object(win, '_msa_sync_message', side_effect=stale_yes):
                win._check_msa_sync(manual=True)
                assert not sync.called
        '''.replace("return_value=plan),\n", "return_value=plan), \\\n"))

    def test_empty_cache_dialog_lists_fields_and_defaults_to_cancel(self):
        self.run_ui(r'''
            review = {'empty': [{'path': str(home / 'primary' / 'P12345_data.json'),
                                 'fields': ['protein[A].pairedMsa', 'protein[A].templates']}]}
            inspected = []
            def inspect_dialog(box):
                inspected.append(box)
                assert box.defaultButton().text() == 'Cancel'
                assert box.escapeButton().text() == 'Cancel'
                assert {'Reuse these inputs', 'Generate MSA again', 'Cancel'} <= {button.text() for button in box.buttons()}
                assert 'P12345_data.json' in box.detailedText()
                assert 'pairedMsa' in box.detailedText() and 'templates' in box.detailedText()
                assert box.textFormat() == QtCore.Qt.PlainText
                return 0
            with patch.object(QtWidgets.QMessageBox, 'exec', new=inspect_dialog):
                assert win._choose_empty_msa_policy(review) is None
            assert len(inspected) == 1
        ''')

    def test_empty_cache_review_restarts_each_command_only_after_choice(self):
        self.run_ui(r'''
            review = {'empty': [{'path': str(home / 'primary' / 'P12345_data.json'), 'fields': ['templates']}]}
            marker = 'MSA_REUSE_REVIEW_JSON=' + json.dumps(review)
            for mode in ['run', 'pulldown', 'scan', 'pae', 'continue', 'retry']:
                for policy in ['reuse', 'recompute', None]:
                    results = [(42, marker, ''), (0, 'accepted synthetic operation', '')]
                    args = [mode, '--name', 'synthetic']
                    with patch.object(Q, 'enqueue', side_effect=immediate), \
                         patch.object(B, 'run_af3_stream', side_effect=results) as executed, \
                         patch.object(win, '_choose_empty_msa_policy', return_value=policy) as chose:
                        win._exec(args, False, [win.sub_go], win.sub_out)
                        assert chose.call_args.args[0] == review
                        assert executed.call_count == (1 if policy is None else 2)
                        if policy:
                            assert executed.call_args.args[0][-2:] == ['--empty-msa-policy', policy]
                        else:
                            assert '--empty-msa-policy' not in executed.call_args.args[0]
                        assert win.sub_go.isEnabled()
            assert win._args_with_empty_msa_policy(['run', '--empty-msa-policy', 'reuse'], 'recompute') == [
                'run', '--empty-msa-policy', 'recompute']
            assert win._args_with_empty_msa_policy(['run', '--empty-msa-policy=review'], 'reuse') == [
                'run', '--empty-msa-policy', 'reuse']
        ''')

    def test_empty_cache_preview_and_malformed_reviews_never_authorize(self):
        self.run_ui(r'''
            review = {'empty': [{'path': str(home / 'primary' / 'P12345_data.json'), 'fields': ['templates']}]}
            marker = 'MSA_REUSE_REVIEW_JSON=' + json.dumps(review)
            for dry, rc, output in [(True, 42, marker), (False, 1, marker),
                                    (False, 42, 'MSA_REUSE_REVIEW_JSON=malformed'),
                                    (False, 42, 'MSA_REUSE_REVIEW_JSON={"empty":["bad"]}')]:
                with patch.object(Q, 'enqueue', side_effect=immediate), \
                     patch.object(B, 'run_af3_stream', return_value=(rc, output, '')) as executed, \
                     patch.object(win, '_choose_empty_msa_policy') as chose:
                    win._exec(['run', '--name', 'synthetic'], dry, [win.sub_go], win.sub_out)
                    assert executed.call_count == 1 and not chose.called
            assert win._empty_msa_review('MSA_REUSE_REVIEW_JSON=[]') is None
            assert win._empty_msa_review('MSA_REUSE_REVIEW_JSON={"empty":[]}') is None
        ''')

    def test_exec_review_uses_real_dialog_buttons_before_rerunning(self):
        self.run_ui(r'''
            review = {'empty': [{'path': str(home / 'primary' / 'P12345_data.json'), 'fields': ['templates']}]}
            marker = 'MSA_REUSE_REVIEW_JSON=' + json.dumps(review)
            for choice, policy in [('Reuse these inputs', 'reuse'), ('Generate MSA again', 'recompute'), ('Cancel', None)]:
                dialogs = []
                def interact(box):
                    dialogs.append(box)
                    assert box.defaultButton().text() == 'Cancel'
                    assert 'P12345_data.json' in box.detailedText()
                    button = next(button for button in box.buttons() if button.text() == choice)
                    button.click()
                    return 0
                with patch.object(Q, 'enqueue', side_effect=immediate), \
                     patch.object(B, 'run_af3_stream', side_effect=[(42, marker, ''), (0, 'synthetic accepted', '')]) as executed, \
                     patch.object(QtWidgets.QMessageBox, 'exec', new=interact):
                    win._exec(['run', '--name', 'synthetic'], False, [win.sub_go], win.sub_out)
                    assert len(dialogs) == 1
                    assert executed.call_count == (2 if policy else 1)
                    if policy:
                        assert executed.call_args.args[0][-2:] == ['--empty-msa-policy', policy]
        ''')


if __name__ == '__main__':
    unittest.main()
