"""Empty cache consent is checked at real CLI planning/controller boundaries."""
import argparse
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import af3 as A
import af3_runtime as R


class MsaReuseReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='af3-empty-review-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.pool = self.root / 'msa_data'
        self.output = self.root / 'output'
        self.addCleanup(A.reload_config)
        patches = [
            mock.patch.dict(os.environ, AF3_BASE=str(self.root / 'workspace'),
                            AF3_CONFIG=str(self.root / 'missing.json'), AF3_SNAPSHOT=''),
            mock.patch.object(A, '_MSA_REVIEW_CONTEXT', None),
            mock.patch.object(A, '_MSA_FREE_POLICY', False),
            mock.patch.object(A, 'fetch_uniprot', side_effect={
                'P12345': 'MAAAAAGGGGGG', 'Q12345': 'MGGGGGAAAAAA'}.__getitem__),
            mock.patch.object(A, '_environment_identity', return_value={'synthetic': 1}),
            mock.patch.object(A, '_prediction_environment', return_value={'synthetic': 1}),
            mock.patch.object(A, 'ensure_deployment'),
            mock.patch.object(A, 'note'), mock.patch.object(A, 'warn'),
            mock.patch.object(A, 'submit_controller', return_value='100'),
            mock.patch.object(A, 'submit_watcher', return_value='101'),
        ]
        for patch in patches:
            value = patch.start()
            self.addCleanup(patch.stop)
            if getattr(patch, 'attribute', None) == 'submit_controller': self.controller = value
            if getattr(patch, 'attribute', None) == 'submit_watcher': self.watcher = value
        A.reload_config()

    def product(self, accession='P12345', empty=True):
        sequence = {'P12345': 'MAAAAAGGGGGG', 'Q12345': 'MGGGGGAAAAAA'}[accession]
        body = {'id': 'A', 'sequence': sequence, 'unpairedMsa': '>q\n' + sequence + '\n',
                'pairedMsa': '' if empty else '>q\n' + sequence + '\n',
                'templates': [] if empty else [{'mmcif': 'data_synthetic\n',
                    'queryIndices': [0], 'templateIndices': [0]}]}
        value = {'name': accession, 'dialect': 'alphafold3', 'version': 4,
                 'modelSeeds': [11], 'sequences': [{'protein': body}]}
        path = self.pool / accession / (accession + '_data.json')
        R.atomic_json(path, value)
        return path

    def args(self, command, policy=None, dry=False):
        flags = ['--msa-dir', str(self.pool), '--output-dir', str(self.output), '--seeds', '11']
        if policy: flags += ['--empty-msa-policy', policy]
        if dry: flags += ['--dry-run']
        result = A.build_parser().parse_args(command + flags)
        A._validate_args(result)
        return result

    def assert_review(self, args, count=1):
        text = io.StringIO()
        with contextlib.redirect_stdout(text), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                args.func(args)
        self.assertEqual(error.exception.code, 42)
        records = [json.loads(line.split('=', 1)[1]) for line in text.getvalue().splitlines()
                   if line.startswith('MSA_REUSE_REVIEW_JSON=')]
        self.assertEqual(len(records), 1)
        self.assertEqual(len(records[0]['empty']), count)
        self.controller.assert_not_called()
        self.watcher.assert_not_called()
        self.assertFalse(list(self.output.glob('**/spec.json')))
        return records[0]

    def test_run_requires_choice_before_spec_or_scheduler(self):
        path = self.product()
        before = path.read_bytes()
        report = self.assert_review(self.args(['run', 'P12345']))
        self.assertEqual(report['empty'][0]['path'], str(path.resolve()))
        self.assertEqual(path.read_bytes(), before)

    def test_dry_run_reports_without_authorizing(self):
        self.product()
        args = self.args(['run', 'P12345'], policy='reuse', dry=True)
        output = io.StringIO()
        with contextlib.redirect_stdout(output): args.func(args)
        self.assertIn('MSA_REUSE_REVIEW_JSON=', output.getvalue())
        self.assertEqual(args.empty_msa_approved, {})
        self.controller.assert_not_called()

    def test_reuse_records_file_content_and_rejects_new_empty_results(self):
        path = self.product()
        args = self.args(['run', 'P12345'], policy='reuse')
        args.func(args)
        spec_path = next(self.output.glob('*/spec.json'))
        spec = A.load_spec(str(spec_path))
        saved = argparse.Namespace(**spec['submit_args'])
        A._begin_msa_review(saved, remote=True)
        self.assertIsNone(A._review_data_path(str(path), saved))
        changed = R.read_json(path)
        changed['sequences'][0]['protein']['unpairedMsa'] = ''
        R.atomic_json(path, changed)
        with self.assertRaisesRegex(R.BusinessError, '确认'):
            A._review_data_path(str(path), saved)

    def test_recompute_preserves_original_and_shares_key_across_batch(self):
        path = self.product()
        before = path.read_bytes()
        jobs = self.root / 'examples.txt'
        jobs.write_text('P12345\nP12345x2\n', encoding='utf-8')
        args = self.args(['run', str(jobs)], policy='recompute')
        args.func(args)
        spec = A.load_spec(str(next(self.output.glob('*/spec.json'))))
        keys = {e['_msa_key'] for job in spec['jobs'] for e in job['entities']}
        self.assertEqual(len(keys), 1)
        self.assertTrue(next(iter(keys)).startswith('P12345__recalc_'))
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((self.pool / next(iter(keys))).exists())
        self.assertEqual(len(spec['msa_entities']), 1)

    def test_infer_only_recompute_explicitly_enables_new_msa_stage(self):
        self.product()
        args = self.args(['run', 'P12345', '--infer-only'], policy='recompute')
        args.func(args)
        self.assertTrue(A.ensure_deployment.call_args.kwargs['require_msa'])
        spec = A.load_spec(str(next(self.output.glob('*/spec.json'))))
        self.assertFalse(spec['infer_only'])
        self.assertEqual(len(spec['msa_entities']), 1)

    def test_pulldown_and_both_scan_modes_collect_all_parent_caches(self):
        self.product('P12345'); self.product('Q12345')
        for command in (['pulldown', 'P12345', 'Q12345'],
                        ['scan', 'P12345', 'Q12345', '--shared-msa', '--win', '6',
                         '--overlap', '0', '--min-frag', '3', '--split-threshold', '6'],
                        ['scan', 'P12345', 'Q12345', '--mode', 'pae', '--shared-msa', '--split-threshold', '6']):
            with self.subTest(command=command):
                self.assert_review(self.args(command), count=2)

    def test_shared_scan_recompute_parent_keys_remain_consistent(self):
        paths = [self.product('P12345'), self.product('Q12345')]
        before = [path.read_bytes() for path in paths]
        args = self.args(['scan', 'P12345', 'Q12345', '--shared-msa', '--win', '6',
                          '--overlap', '0', '--min-frag', '3', '--split-threshold', '6'], policy='recompute')
        args.func(args)
        spec = A.load_spec(str(next(self.output.glob('*/spec.json'))))
        parents = {ent['_msa_key'] for ent in spec['msa_entities']}
        self.assertEqual(len(parents), 2)
        self.assertTrue(all('__recalc_' in key for key in parents))
        self.assertTrue(all(fragment['parent_key'] in parents for fragment in spec['fragments']))
        self.assertEqual([path.read_bytes() for path in paths], before)

    def test_raw_infer_incomplete_reports_exact_missing_fields(self):
        path = self.product()
        value = R.read_json(path)
        value['sequences'][0]['protein'].pop('templates')
        R.atomic_json(path, value)
        args = self.args(['run', '--json', str(path), '--infer-only'])
        with self.assertRaisesRegex(R.BusinessError, 'templates'): args.func(args)
        self.controller.assert_not_called()

    def test_raw_infer_empty_review_and_recompute_preserve_source(self):
        path = self.product()
        before = path.read_bytes()
        report = self.assert_review(self.args(['run', '--json', str(path), '--infer-only']))
        self.assertEqual(report['empty'][0]['path'], str(path.resolve()))
        args = self.args(['run', '--json', str(path), '--infer-only'], policy='recompute')
        args.func(args)
        spec = A.load_spec(str(next(self.output.glob('*/spec.json'))))
        self.assertFalse(spec['infer_only'])
        self.assertTrue(A.ensure_deployment.call_args.kwargs['require_msa'])
        body = R.read_json(spec['raw_input'])['sequences'][0]['protein']
        self.assertNotIn('pairedMsa', body)
        self.assertNotIn('templates', body)
        self.assertEqual(path.read_bytes(), before)

    def test_raw_infer_reuse_pins_approval_to_frozen_input(self):
        path = self.product()
        args = self.args(['run', '--json', str(path), '--infer-only'], policy='reuse')
        args.func(args)
        spec = A.load_spec(str(next(self.output.glob('*/spec.json'))))
        saved = argparse.Namespace(**spec['submit_args'])
        A._begin_msa_review(saved, remote=True)
        self.assertIsNone(A._review_data_path(spec['raw_input'], saved))

    def test_pae_requires_choice_before_mononer_prediction_submission(self):
        self.product()
        with mock.patch.object(A, 'find_confidences_json', return_value=None):
            self.assert_review(self.args(['pae', 'P12345']))

    def test_fully_populated_cache_needs_no_dialog(self):
        self.product(empty=False)
        args = self.args(['run', 'P12345'])
        args.func(args)
        self.controller.assert_called_once()

    def test_controller_stops_before_inference_when_new_pipeline_output_is_empty(self):
        args = self.args(['run', 'P12345'])
        args.func(args)
        spec_path = next(self.output.glob('*/spec.json'))
        self.product()
        with mock.patch.object(R, 'queue_snapshot', return_value={}), \
                mock.patch.object(A, 'submit_infer_single') as inference:
            with self.assertRaisesRegex(R.BusinessError, '确认'):
                A.cmd_stage_infer(argparse.Namespace(spec=str(spec_path)))
        inference.assert_not_called()

    def test_controller_accepts_only_the_previously_reviewed_cache(self):
        self.product()
        args = self.args(['run', 'P12345'], policy='reuse')
        args.func(args)
        spec_path = next(self.output.glob('*/spec.json'))
        with mock.patch.object(R, 'queue_snapshot', return_value={}), \
                mock.patch.object(A, 'submit_infer_single', return_value='200') as inference, \
                mock.patch.object(A, '_monitor_jobs'):
            A.cmd_stage_infer(argparse.Namespace(spec=str(spec_path)))
        inference.assert_called_once()

    def test_watcher_blocks_new_empty_cache_without_submitting_inference(self):
        args = self.args(['pulldown', 'P12345', 'Q12345'])
        args.func(args)
        spec_path = next(self.output.glob('*/spec.json'))
        self.product('P12345'); self.product('Q12345')
        with mock.patch.object(R, 'queue_snapshot', return_value={}), \
                mock.patch.object(A, 'submit_infer_single') as inference, \
                mock.patch.object(A, 'do_screen_rank', return_value={}):
            A.cmd_pulldown_watcher(argparse.Namespace(spec=str(spec_path)))
        inference.assert_not_called()
        self.assertEqual(A.load_spec(str(spec_path))['counts']['blocked'], 1)

    def test_damaged_managed_cache_is_repaired_to_new_name_without_changing_recipe(self):
        external = self.root / 'synthetic.a3m'
        external.write_text('>q\nMAAAAAGGGGGG\n', encoding='utf-8')
        expression = 'P12345:msa=' + str(external)
        # Build the same custom identity the CLI will select, then simulate a
        # completed managed cache becoming corrupted outside this application.
        entity = A.parse_expression(expression)[0]
        key, _ = A.resolve_msa_key(entity, str(self.pool))
        R.reserve_msa(self.pool, key, entity['_msa_fingerprint'])
        path = Path(R.msa_native_path(self.pool, key))
        R.atomic_text(path, '{')
        record_path = Path(R.msa_record_path(self.pool, key))
        record = R.read_json(record_path); record['status'] = 'complete'
        R.atomic_json(record_path, record)
        record_before = record_path.read_bytes()
        args = self.args(['run', expression, '--infer-only'])
        args.func(args)
        spec = A.load_spec(str(next(self.output.glob('*/spec.json'))))
        repaired = spec['jobs'][0]['entities'][0]
        self.assertIn('__repair_', repaired['_msa_key'])
        self.assertEqual(Path(repaired['msa_path']).read_text(encoding='utf-8'), external.read_text(encoding='utf-8'))
        self.assertFalse(spec['infer_only'])
        self.assertTrue(A.ensure_deployment.call_args.kwargs['require_msa'])
        self.assertEqual(path.read_text(encoding='utf-8'), '{')
        self.assertEqual(record_path.read_bytes(), record_before)

    def test_planned_cache_that_becomes_partial_stops_before_any_new_msa_submission(self):
        args = self.args(['run', 'P12345'])
        args.func(args)
        spec_path = next(self.output.glob('*/spec.json'))
        spec = A.load_spec(str(spec_path))
        entity = spec['jobs'][0]['entities'][0]
        R.reserve_msa(self.pool, entity['_msa_key'], entity['_msa_fingerprint'])
        path = Path(R.msa_native_path(self.pool, entity['_msa_key']))
        R.atomic_text(path, '{')
        with mock.patch.object(A, 'submit_msa_array') as msa_submit:
            with self.assertRaisesRegex(R.BusinessError, '禁止覆盖'):
                A.cmd_stage_infer(argparse.Namespace(spec=str(spec_path)))
        msa_submit.assert_not_called()
        self.assertEqual(path.read_text(encoding='utf-8'), '{')

    def test_raw_pipeline_occupied_output_uses_a_fresh_repair_directory(self):
        original = self.root / 'synthetic_input.json'
        data = {'name': 'P12345', 'dialect': 'alphafold3', 'version': 4, 'modelSeeds': [11],
                'sequences': [{'protein': {'id': 'A', 'sequence': 'MAAAAAGGGGGG'}}]}
        R.atomic_json(original, data)
        base = 'P12345__' + R.digest({'input': R.asset_identity(data), 'environment': {'synthetic': 1}}, 16)
        partial = Path(R.msa_native_path(self.pool, base))
        R.atomic_text(partial, '{')
        args = self.args(['run', '--json', str(original)])
        args.func(args)
        spec = A.load_spec(str(next(self.output.glob('*/spec.json'))))
        self.assertTrue(spec['name'].startswith(base + '__repair_'))
        self.assertEqual(partial.read_text(encoding='utf-8'), '{')


if __name__ == '__main__':
    unittest.main()
