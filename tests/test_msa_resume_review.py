"""Continuation/retry cannot bypass review of empty but legal AF3 MSA fields."""
import argparse
import copy
from contextlib import redirect_stderr, redirect_stdout
import io
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


class MsaResumeReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='af3-resume-review-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = mock.patch.dict(os.environ, AF3_BASE=str(self.root / 'workspace'),
                                      AF3_CONFIG=str(self.root / 'missing.json'), AF3_SNAPSHOT='')
        environment.start(); self.addCleanup(environment.stop)
        A.reload_config(); self.addCleanup(A.reload_config)
        context = mock.patch.object(A, '_MSA_REVIEW_CONTEXT', None)
        context.start(); self.addCleanup(context.stop)
        self.pool = self.root / 'pool'
        self.pool.mkdir()
        deployment = mock.patch.object(A, 'ensure_deployment')
        self.deployment = deployment.start(); self.addCleanup(deployment.stop)
        queue = mock.patch.object(R, 'queue_snapshot', return_value={})
        self.queue = queue.start(); self.addCleanup(queue.stop)
        self.console = io.StringIO()
        out = redirect_stdout(self.console); out.__enter__(); self.addCleanup(out.__exit__, None, None, None)
        err = redirect_stderr(self.console); err.__enter__(); self.addCleanup(err.__exit__, None, None, None)

    def entity_product(self, accession='P12345'):
        entity = A.parse_expression('p:MAAAAA')[0]
        entity['uniprot'] = accession
        A.resolve_msa_key(entity, str(self.pool))
        data = A.generate_msa_input_json(entity, entity['_msa_key'])
        data['sequences'][0]['protein'].update(unpairedMsa='', pairedMsa='', templates=[])
        path = Path(R.msa_native_path(self.pool, entity['_msa_key']))
        R.atomic_json(path, data)
        return entity, path

    def run_spec(self, msa_only=True):
        entity, product = self.entity_product()
        work = self.root / 'fixed plan'
        path = work / 'spec.json'
        submit_args = vars(A.build_parser().parse_args(['run', 'P12345', '--msa-dir', str(self.pool),
                                                        '--output-dir', str(self.root / 'results')]))
        submit_args.pop('func', None)
        submit_args.update(msa_only=msa_only, infer_only=not msa_only)
        job = dict(name='P12345_fixed', label='P12345', entities=[entity], seeds=[17, 29],
                   template_free=False, bonds=[], user_ccd_path=None)
        spec = dict(version=2, type='run', name='P12345', jobs=[job], msa_entities=[],
                    msa_dir=str(self.pool), output_dir=str(self.root / 'results'), batch_dir=None,
                    msa_only=msa_only, infer_only=not msa_only, submit_args=submit_args,
                    msa_jids=[], infer_jids=[], status='done' if msa_only else 'failed')
        A.write_spec(spec, str(path))
        return path, product

    def raw_spec(self, msa_only=True):
        path, product = self.run_spec(msa_only=msa_only)
        spec = A.load_spec(str(path))
        spec.update(type='raw_json', raw_input=str(product))
        spec.pop('jobs'); spec.pop('msa_entities')
        # Build a second genuine snapshot instead of modifying the fixed plan.
        target = self.root / 'raw fixed plan' / 'spec.json'
        for key in ('storage_version', 'plan_file', 'detail_files'):
            spec.pop(key, None)
        A.write_spec(spec, str(target))
        return target, product

    def args(self, path, policy='review', dry=False):
        return argparse.Namespace(spec=str(path), empty_msa_policy=policy, dry_run=dry)

    def test_continue_requires_explicit_choice_before_new_plan_or_submission(self):
        path, product = self.run_spec()
        before = path.read_bytes(), product.read_bytes()
        with mock.patch.object(A, 'submit_controller') as submit, self.assertRaises(SystemExit) as error:
            A.cmd_continue(self.args(path))
        self.assertEqual(error.exception.code, 42)
        submit.assert_not_called()
        self.assertEqual((path.read_bytes(), product.read_bytes()), before)
        self.assertFalse(Path(str(path.parent) + '__infer').exists())
        self.assertIn('MSA_REUSE_REVIEW_JSON=', self.console.getvalue())

    def test_continue_reuse_pins_current_content_but_not_future_or_changed_files(self):
        path, product = self.run_spec()
        with mock.patch.object(A, 'submit_controller', return_value='101'):
            child = A.cmd_continue(self.args(path, 'reuse'))
        spec = A.load_spec(child)
        self.assertTrue(spec['infer_only'])
        self.assertEqual(spec['jobs'][0]['seeds'], [17, 29])
        approvals = spec['submit_args']['empty_msa_approved']
        self.assertEqual(approvals[str(product)], R.msa_reuse_fingerprint(product))
        remote = argparse.Namespace(**spec['submit_args'])
        A._begin_msa_review(remote, remote=True)
        self.assertIsNone(A._review_data_path(product, remote))
        changed = R.read_json(product)
        changed['modelSeeds'] = [31]
        R.atomic_json(product, changed)
        with self.assertRaises(R.BusinessError):
            A._review_data_path(product, remote)
        another = self.pool / 'Q12345' / 'Q12345_data.json'
        R.atomic_json(another, dict(changed, name='Q12345'))
        with self.assertRaises(R.BusinessError):
            A._review_data_path(another, remote)

    def test_continue_recompute_allocates_new_cache_and_keeps_old_plan_data_and_seeds(self):
        path, product = self.run_spec()
        before = path.read_bytes(), product.read_bytes()
        with mock.patch.object(A, 'submit_controller', return_value='101'):
            child = A.cmd_continue(self.args(path, 'recompute'))
        spec = A.load_spec(child)
        self.assertFalse(spec['infer_only'])
        self.assertFalse(spec['msa_only'])
        self.assertIn('__recalc_', child)
        self.assertNotEqual(spec['jobs'][0]['entities'][0]['_msa_key'], 'P12345')
        self.assertEqual(spec['jobs'][0]['seeds'], [17, 29])
        self.assertEqual((path.read_bytes(), product.read_bytes()), before)
        self.assertEqual(spec['msa_entities'][0]['_msa_key'], spec['jobs'][0]['entities'][0]['_msa_key'])
        self.deployment.assert_called_once()
        self.assertTrue(self.deployment.call_args.kwargs['require_msa'])

    def test_raw_continue_recompute_removes_empty_fields_in_new_input_only(self):
        path, product = self.raw_spec()
        before = path.read_bytes(), product.read_bytes()
        with mock.patch.object(A, 'submit_controller', return_value='101'):
            child = A.cmd_continue(self.args(path, 'recompute'))
        spec = A.load_spec(child)
        data = R.read_json(spec['raw_input'])
        self.assertNotEqual(spec['name'], 'P12345')
        self.assertEqual(data['name'], spec['name'])
        self.assertFalse(spec['infer_only'])
        for key in ('unpairedMsa', 'pairedMsa', 'templates'):
            self.assertNotIn(key, data['sequences'][0]['protein'])
        self.assertEqual((path.read_bytes(), product.read_bytes()), before)

    def test_retry_review_does_not_change_state_or_submit(self):
        path, product = self.run_spec(msa_only=False)
        before = path.read_bytes(), product.read_bytes()
        with mock.patch.object(A, '_submit_control') as submit, self.assertRaises(SystemExit) as error:
            A._cmd_retry_locked(self.args(path))
        self.assertEqual(error.exception.code, 42)
        submit.assert_not_called()
        self.assertEqual((path.read_bytes(), product.read_bytes()), before)

    def test_retry_reuse_saves_approval_and_runs_guarded_pinned_controller(self):
        path, product = self.run_spec(msa_only=False)
        with mock.patch.object(A, '_submit_control', return_value='102') as submit:
            A._cmd_retry_locked(self.args(path, 'reuse'))
        spec = A.load_spec(str(path))
        self.assertEqual(spec['submit_args']['empty_msa_approved'][str(product)], R.msa_reuse_fingerprint(product))
        self.assertEqual(spec['controller_jid'], '102')
        script = submit.call_args.args[1]
        self.assertIn('.runtime', script)
        self.assertIn('_stage_infer', script)
        # Old saved choice "reuse" is not blanket authorization after content changes.
        changed = R.read_json(product); changed['modelSeeds'] = [53]
        R.atomic_json(product, changed)
        with mock.patch.object(A, '_submit_control') as submit, self.assertRaises(SystemExit) as error:
            A._cmd_retry_locked(self.args(path))
        self.assertEqual(error.exception.code, 42)
        submit.assert_not_called()

    def test_retry_rejects_old_pinned_controller_without_review_protocol(self):
        path, _ = self.run_spec(msa_only=False)
        runtime = path.parent / '.runtime' / 'af3.py'
        runtime.write_text('# synthetic old controller\n', encoding='utf-8')
        before = path.read_bytes()
        with mock.patch.object(A, '_submit_control') as submit, self.assertRaises(R.BusinessError):
            A._cmd_retry_locked(self.args(path, 'reuse'))
        self.assertEqual(path.read_bytes(), before)
        submit.assert_not_called()

    def test_retry_scan_recompute_uses_one_new_key_for_repeated_entities(self):
        run_path, product = self.run_spec(msa_only=False)
        spec = A.load_spec(str(run_path))
        entity = spec['jobs'][0]['entities'][0]
        spec.update(type='scan', tag='P12345', outdir=str(self.root / 'scan original'), shared_msa=False,
                    pairs=[dict(name='P12345_fixed', a=dict(entities=[copy.deepcopy(entity)]),
                                b=dict(entities=[copy.deepcopy(entity)]), seeds=[17, 29])],
                    msa_entities=[copy.deepcopy(entity)], resolved_seeds=[17, 29])
        spec.pop('jobs')
        for key in ('storage_version', 'plan_file', 'detail_files'): spec.pop(key, None)
        path = self.root / 'scan original' / 'spec.json'
        A.write_spec(spec, str(path))
        before = path.read_bytes(), product.read_bytes()
        with mock.patch.object(A, 'submit_watcher', return_value='103') as watcher:
            child = A._cmd_retry_locked(self.args(path, 'recompute'))
        result = A.load_spec(child)
        keys = [result['pairs'][0][side]['entities'][0]['_msa_key'] for side in ('a', 'b')]
        self.assertEqual(keys[0], keys[1])
        self.assertNotEqual(keys[0], 'P12345')
        self.assertEqual(result['msa_entities'][0]['_msa_key'], keys[0])
        self.assertEqual(result['pairs'][0]['seeds'], [17, 29])
        self.assertEqual(result['resolved_seeds'], [17, 29])
        self.assertEqual(result['outdir'], str(Path(child).parent))
        self.assertEqual((path.read_bytes(), product.read_bytes()), before)
        watcher.assert_called_once()

    def test_retry_raw_recompute_switches_infer_only_to_new_pipeline_input(self):
        path, product = self.raw_spec(msa_only=False)
        original = path.read_bytes(), product.read_bytes()
        with mock.patch.object(A, 'submit_controller', return_value='104'):
            child = A._cmd_retry_locked(self.args(path, 'recompute'))
        spec = A.load_spec(child)
        self.assertFalse(spec['infer_only'])
        self.assertNotEqual(spec['name'], 'P12345')
        body = R.read_json(spec['raw_input'])['sequences'][0]['protein']
        self.assertEqual(body['sequence'], 'MAAAAA')
        self.assertNotIn('unpairedMsa', body)
        self.assertNotIn('pairedMsa', body)
        self.assertNotIn('templates', body)
        self.assertEqual((path.read_bytes(), product.read_bytes()), original)

    def test_shared_scan_recompute_uses_independent_msa_and_preserves_local_cache(self):
        run_path, product = self.run_spec(msa_only=False)
        spec = A.load_spec(str(run_path))
        parent = spec['jobs'][0]['entities'][0]
        fragment = copy.deepcopy(parent)
        fragment.update(sequence='MAA', trunc='1-3', _shared_source={'parent_key': 'P12345', 'start': 1, 'end': 3},
                        _shared_parent_key='P12345')
        A.resolve_msa_key(fragment, str(self.pool))
        work = self.root / 'shared scan'
        local = Path(A.batch_paths(str(work))['msa_local'])
        fragment_path = Path(R.msa_native_path(local, fragment['_msa_key']))
        data = A.generate_msa_input_json(fragment, fragment['_msa_key'])
        data['sequences'][0]['protein'].update(unpairedMsa='', pairedMsa='', templates=[])
        R.atomic_json(fragment_path, data)
        original = product.read_bytes(), fragment_path.read_bytes()
        spec.update(type='scan', tag='P12345', outdir=str(work), shared_msa=True,
                    pairs=[dict(name='P12345_fixed', a=dict(entities=[fragment]),
                                b=dict(entities=[copy.deepcopy(parent)]), seeds=[17, 29])],
                    msa_entities=[copy.deepcopy(parent)], resolved_seeds=[17, 29],
                    fragments=[dict(key=fragment['_msa_key'], parent_key='P12345', start=1, end=3)],
                    scan_args={'shared_msa': True})
        spec.pop('jobs')
        for key in ('storage_version', 'plan_file', 'detail_files'): spec.pop(key, None)
        path = work / 'spec.json'
        A.write_spec(spec, str(path))
        with mock.patch.object(A, 'submit_watcher', return_value='105'):
            child = A._cmd_retry_locked(self.args(path, 'recompute'))
        renewed = A.load_spec(child)
        self.assertFalse(renewed['shared_msa'])
        self.assertFalse(renewed['scan_args']['shared_msa'])
        self.assertFalse(renewed['submit_args']['shared_msa'])
        recomputed_fragment = renewed['pairs'][0]['a']['entities'][0]
        self.assertNotEqual(recomputed_fragment['_msa_key'], fragment['_msa_key'])
        self.assertFalse(recomputed_fragment.get('_shared_source'))
        self.assertFalse(recomputed_fragment.get('_shared_parent_key'))
        self.assertIn(recomputed_fragment['_msa_key'], [entity['_msa_key'] for entity in renewed['msa_entities']])
        self.assertEqual((product.read_bytes(), fragment_path.read_bytes()), original)

    def test_dry_retry_reports_without_changing_plan_or_approving_content(self):
        path, product = self.run_spec(msa_only=False)
        before = path.read_bytes(), product.read_bytes()
        with mock.patch.object(A, '_submit_control') as submit:
            A._cmd_retry_locked(self.args(path, 'reuse', dry=True))
        submit.assert_not_called()
        self.queue.assert_not_called()
        self.assertEqual((path.read_bytes(), product.read_bytes()), before)
        self.assertIn('MSA_REUSE_REVIEW_JSON=', self.console.getvalue())
        self.assertFalse(A.load_spec(str(path))['submit_args'].get('empty_msa_approved'))

    def test_incoming_approval_survives_continue_and_retry(self):
        path, product = self.run_spec()
        for mode in ('continue', 'retry'):
            with self.subTest(mode=mode):
                args = self.args(path)
                args.empty_msa_approved = {str(product): R.msa_reuse_fingerprint(product)}
                with mock.patch.object(A, 'submit_controller', return_value='110'), mock.patch.object(A, '_submit_control', return_value='111'):
                    target = A.cmd_continue(args) if mode == 'continue' else A._cmd_retry_locked(args)
                self.assertEqual(A.load_spec(target)['submit_args']['empty_msa_approved'], args.empty_msa_approved)
                path = Path(target)

    def test_raw_review_uses_pinned_asset_root_and_restores_current_settings(self):
        path, product = self.raw_spec(msa_only=False)
        old_cache = Path(A.HOST_CACHE)
        assets = old_cache / 'assets'
        assets.mkdir(parents=True, exist_ok=True)
        asset = assets / 'synthetic.a3m'
        asset.write_text('>synthetic\nMAAAAA\n', encoding='utf-8')
        data = R.read_json(product)
        body = data['sequences'][0]['protein']
        body.pop('unpairedMsa')
        body['unpairedMsaPath'] = '/root/af_assets/synthetic.a3m'
        R.atomic_json(product, data)
        approved = R.msa_reuse_fingerprint(product)
        current_cache = str(self.root / 'unrelated current cache')
        current_assets = str(Path(current_cache) / 'assets')
        args = self.args(path)
        args.empty_msa_approved = {str(product): approved}
        with mock.patch.object(A, 'HOST_CACHE', current_cache), mock.patch.object(R, 'ASSET_ROOT', current_assets), mock.patch.object(A, '_submit_control', return_value='112'):
            A._cmd_retry_locked(args)
            self.assertEqual(A.HOST_CACHE, current_cache)
            self.assertEqual(R.ASSET_ROOT, current_assets)
        saved = A.load_spec(str(path))
        self.assertEqual(saved['submit_args']['empty_msa_approved'][str(product)], approved)

    def test_invalid_occupied_continue_repairs_new_key_without_clearing_custom_recipe(self):
        path, product = self.run_spec()
        spec = A.load_spec(str(path))
        entity = spec['jobs'][0]['entities'][0]
        asset = self.root / 'synthetic.a3m'
        asset.write_text('>synthetic\nMAAAAA\n', encoding='utf-8')
        entity['msa_path'] = str(asset)
        entity['paired_msa'] = ''
        entity['template'] = None
        for key in ('storage_version', 'plan_file', 'detail_files'): spec.pop(key, None)
        repair_path = self.root / 'fixed custom recipe' / 'spec.json'
        A.write_spec(spec, str(repair_path))
        product.write_text('{', encoding='utf-8')
        original = repair_path.read_bytes(), product.read_bytes()
        with mock.patch.object(A, 'submit_controller', return_value='113'):
            target = A.cmd_continue(self.args(repair_path))
        repaired = A.load_spec(target)
        output_entity = repaired['jobs'][0]['entities'][0]
        self.assertIn('__repair_', output_entity['_msa_key'])
        self.assertTrue(output_entity['msa_path'])
        self.assertEqual(Path(output_entity['msa_path']).read_bytes(), asset.read_bytes())
        self.assertEqual(output_entity['paired_msa'], '')
        self.assertFalse(repaired['infer_only'])
        self.assertEqual((repair_path.read_bytes(), product.read_bytes()), original)

    def test_raw_invalid_output_retry_preserves_fixed_original_recipe_in_new_input(self):
        path, product = self.raw_spec(msa_only=True)
        recipe = self.root / 'original raw recipe.json'
        data = R.read_json(product)
        data['sequences'][0]['protein']['unpairedMsa'] = '>synthetic\nMAAAAA\n'
        R.atomic_json(recipe, data)
        A.update_spec(str(path), raw_input=str(recipe), status='failed')
        product.write_text('{', encoding='utf-8')
        original = path.read_bytes(), product.read_bytes(), recipe.read_bytes()
        with mock.patch.object(A, 'submit_controller', return_value='114'):
            target = A._cmd_retry_locked(self.args(path))
        repaired = A.load_spec(target)
        generated = R.read_json(repaired['raw_input'])
        body = generated['sequences'][0]['protein']
        self.assertNotEqual(repaired['name'], 'P12345')
        self.assertIn('unpairedMsaPath', body)
        self.assertEqual(Path(R.host_asset_path(body['unpairedMsaPath'])).read_text(encoding='utf-8'), '>synthetic\nMAAAAA\n')
        self.assertEqual(body['pairedMsa'], '')
        self.assertEqual(body['templates'], [])
        self.assertEqual((path.read_bytes(), product.read_bytes(), recipe.read_bytes()), original)


if __name__ == '__main__':
    unittest.main()
