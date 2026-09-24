"""Native MSA directory workflows using synthetic inputs and a fake AF3 command."""
import argparse
import copy
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import af3 as A
import af3_runtime as R


class MsaLayoutTests(unittest.TestCase):
    def setUp(self):
        review_context = mock.patch.object(A, '_MSA_REVIEW_CONTEXT', None)
        review_context.start(); self.addCleanup(review_context.stop)
        temporary = tempfile.TemporaryDirectory(prefix='af3-msa-layout-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = mock.patch.dict(os.environ, AF3_BASE=str(self.root/'workspace'),
                              AF3_CONFIG=str(self.root/'missing.json'), AF3_SNAPSHOT='')
        env.start(); self.addCleanup(env.stop)
        A.reload_config(); self.addCleanup(A.reload_config)
        identity = mock.patch.object(A, '_environment_identity', return_value={'synthetic_environment': 1})
        identity.start(); self.addCleanup(identity.stop)
        self.pool = self.root/'pool'

    def entity(self, sequence='MAAAAA', accession='P12345'):
        ent = A.parse_expression('p:' + sequence)[0]
        ent['uniprot'] = accession
        A.resolve_msa_key(ent, str(self.pool))
        return ent

    def product(self, ent, root=None, native=True, managed=False):
        root = root or self.pool
        key = ent['_msa_key']
        data = A.generate_msa_input_json(ent, key)
        request = self.root/(key + '_input.json')
        R.atomic_json(request, data)
        if managed:
            R.reserve_msa(root, key, ent['_msa_fingerprint'])
            R.msa_start(root, key, request)
        data['sequences'][0]['protein'].update(unpairedMsa='>q\n' + ent['sequence'] + '\n', pairedMsa='', templates=[])
        path = Path(R.msa_native_path(root, key)) if native else Path(root)/(key+'_data.json')
        R.atomic_json(path, data)
        if managed: self.assertTrue(R.msa_finish(root, key, request, 0))
        return path

    def test_new_accession_is_readable_and_planning_does_not_reserve(self):
        ent = self.entity()
        self.assertEqual(ent['_msa_key'], 'P12345')
        self.assertEqual(A.generate_msa_input_json(ent, ent['_msa_key'])['name'], 'P12345')
        self.assertEqual(Path(A.msa_data_path('P12345', str(self.pool))), self.pool/'P12345/P12345_data.json')
        self.assertFalse(self.pool.exists())

    def test_same_id_revisions_and_environment_changes_are_disambiguated(self):
        ent = self.entity(); self.product(ent, managed=True)
        self.assertEqual(self.entity()['_msa_key'], 'P12345')
        changed = self.entity('MGGGGG')
        self.assertTrue(changed['_msa_key'].startswith('P12345__'))
        with mock.patch.object(A, '_environment_identity', return_value={'synthetic_environment': 2}):
            self.assertTrue(self.entity()['_msa_key'].startswith('P12345__'))
        self.assertEqual(A.check_msa_ready('P12345', changed, str(self.pool)), 'conflict')

    def test_custom_and_msa_free_inputs_do_not_take_the_plain_id(self):
        ent = self.entity()
        ent['_msa_free'] = True
        self.assertTrue(A.resolve_msa_key(ent, str(self.pool))[0].startswith('P12345__'))
        ent = self.entity()
        path = self.root/'custom.a3m'; path.write_text('>q\nMAAAAA\n')
        ent['msa_path'] = str(path)
        first = A.resolve_msa_key(ent, str(self.pool))[0]
        self.assertTrue(first.startswith('P12345__'))
        path.write_text('>q\nMAAAAA\n>h\nMAGAAA\n')
        self.assertNotEqual(first, A.resolve_msa_key(ent, str(self.pool))[0])

    def test_old_exact_hash_is_reused_without_moving_or_renaming(self):
        ent = self.entity()
        ent['_msa_key'] = 'msa_' + ent['_msa_fingerprint']
        path = self.product(ent, native=False)
        before = path.read_bytes()
        fresh = self.entity()
        self.assertEqual(fresh['_msa_key'], ent['_msa_key'])
        self.assertEqual(A.check_msa_ready(fresh['_msa_key'], fresh, str(self.pool)), 'ready')
        self.assertEqual(A.find_data_json(fresh['_msa_key'], str(self.pool)), str(path))
        A.build_infer_json('example', [fresh], [11], str(self.pool))
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((self.pool/ent['_msa_key']).exists())

    def test_untracked_id_reuses_matching_sequence_without_claiming_or_moving(self):
        ent = self.entity(); path = self.product(ent, native=False)
        before = path.read_bytes()
        self.assertEqual(A.find_data_json('P12345', str(self.pool)), str(path))
        self.assertEqual(A.check_msa_ready('P12345', ent, str(self.pool)), 'ready')
        self.assertEqual(self.entity()['_msa_key'], 'P12345')
        self.assertTrue(self.entity('MGGGGG')['_msa_key'].startswith('P12345__'))
        self.assertFalse(Path(R.msa_record_path(self.pool, 'P12345')).exists())
        self.assertEqual(path.read_bytes(), before)

    def test_pending_product_is_not_consumed_by_any_managed_lookup(self):
        ent = self.entity(); key = ent['_msa_key']
        R.reserve_msa(self.pool, key, ent['_msa_fingerprint'])
        self.product(ent)
        self.assertEqual(A.check_msa_ready(key, ent, str(self.pool)), 'missing')
        self.assertIsNone(A.find_data_json(key, str(self.pool), strict=False))
        self.assertIsNone(A.find_data_json(str(self.pool/key), str(self.pool), strict=False))
        self.assertIsNone(A.find_data_json(R.msa_native_path(self.pool, key), str(self.pool), strict=False))
        self.assertFalse(A._pair_msa_ready({'a': {'entities': [ent]}, 'b': {'entities': []}}, str(self.pool)))
        self.assertEqual(A.count_msa_products(str(self.pool)), 0)

    def test_native_relative_assets_and_local_fragment_synthesis(self):
        ent = self.entity(); path = self.product(ent)
        data = R.read_json(path)
        body = data['sequences'][0]['protein']
        (path.parent/'alignment.a3m').write_text(body.pop('unpairedMsa'))
        body['unpairedMsaPath'] = 'alignment.a3m'
        R.atomic_json(path, data)
        before = path.read_bytes()
        assembled = A.build_infer_json('example', [ent], [11], str(self.pool))
        asset = R.host_asset_path(assembled['sequences'][0]['protein']['unpairedMsaPath'])
        self.assertEqual(Path(asset).read_text(), (path.parent/'alignment.a3m').read_text())
        local = self.root/'local'
        frag = {'key': 'P12345_t2-4', 'parent_key': 'P12345', 'start': 2, 'end': 4,
                'sequence': 'AAA', 'parent_sequence': 'MAAAAA'}
        self.assertTrue(A.synthesize_fragment_data(frag, str(self.pool), str(local)))
        local_path = local/'P12345_t2-4/P12345_t2-4_data.json'
        self.assertTrue(local_path.is_file())
        self.assertEqual(A.find_data_json(frag['key'], str(self.pool), local_dir=str(local)), str(local_path))
        self.assertEqual(path.read_bytes(), before)

    def test_count_both_layouts_once_and_exclude_hidden_attempts(self):
        first = self.entity(); self.product(first)
        self.product(first, native=False)
        second = self.entity('MGGGGG', 'Q12345'); self.product(second, native=False)
        self.product(first, root=self.pool/'.msa_attempts', native=True)
        (self.pool/'backup copy').mkdir()
        (self.pool/'bad name_data.json').write_text('{}')
        self.assertEqual(A.count_msa_products(str(self.pool)), 2)

    def test_timestamp_directory_and_custom_job_name_are_reused_in_place(self):
        ent = self.entity()
        path = self.pool/'P12345_20260602_140829/P12345_data.json'
        data = A.generate_msa_input_json(ent, 'P12345')
        data['sequences'][0]['protein'].update(unpairedMsa='>q\nMAAAAA\n', pairedMsa='', templates=[])
        R.atomic_json(path, data)
        companion = path.with_name('companion.txt'); companion.write_text('synthetic companion')
        before = {p.name:p.read_bytes() for p in path.parent.iterdir()}
        self.assertEqual(self.entity()['_msa_key'], 'P12345')
        self.assertEqual(A.find_data_json('P12345', str(self.pool)), str(path))
        self.assertEqual(A.find_data_json(str(path.parent), str(self.pool)), str(path))
        self.assertEqual(A.find_data_json(path.parent.name, str(self.pool)), str(path))
        A.build_infer_json('example', [ent], [11], str(self.pool))
        self.assertEqual(A.count_msa_products(str(self.pool)), 1)
        self.assertEqual({p.name:p.read_bytes() for p in path.parent.iterdir()}, before)
        # A custom job label containing "and" still contains only one protein.
        custom_path = self.pool/'example_job_and_label_20260602_140829/example_job_and_label_data.json'
        data['name'] = 'example_job_and_label'
        R.atomic_json(custom_path, data)
        self.assertEqual(A.find_data_json('example_job_and_label', str(self.pool)), str(custom_path))
        self.assertEqual(A.find_data_json(str(custom_path.parent), str(self.pool)), str(custom_path))
        self.assertEqual(len(A.read_data_json(str(custom_path))['sequences']), 1)

    def test_different_directory_prefix_requires_explicit_selection(self):
        ent = self.entity()
        path = self.pool/'Q12345_20260602_140829/P12345_data.json'
        data = A.generate_msa_input_json(ent, 'P12345')
        data['sequences'][0]['protein'].update(unpairedMsa='', pairedMsa='', templates=[])
        R.atomic_json(path, data)
        before = path.read_bytes()
        self.assertIsNone(A.find_data_json('P12345', str(self.pool), strict=False))
        self.assertEqual(A.find_data_json(str(path), str(self.pool)), str(path))
        self.assertEqual(A.find_data_json(str(path.parent), str(self.pool)), str(path))
        self.assertEqual(path.read_bytes(), before)

    def test_long_mutation_labels_keep_distinct_input_identities(self):
        first = self.entity('M' + 'A'*100)
        second = copy.deepcopy(first); second['sequence'] = 'M' + 'A'*99 + 'G'
        prefix = ','.join('A'+str(i)+'G' for i in range(1,26))
        first['mut'] = prefix + ',A90G'
        second['mut'] = prefix + ',A90V'
        self.assertNotEqual(A.resolve_msa_key(first, str(self.pool))[0], A.resolve_msa_key(second, str(self.pool))[0])

    def test_raw_msa_controller_status_and_continuation_use_completion_records(self):
        source = self.root/'raw.json'
        R.atomic_json(source, {'name': 'P12345', 'modelSeeds': [11], 'dialect': 'alphafold3', 'version': 4,
                               'sequences': [{'protein': {'id': 'A', 'sequence': 'MAAAAA'}}]})
        out = self.root/'output'
        args = A.build_parser().parse_args(['run', '--json', str(source), '--msa-only',
                                            '--msa-dir', str(self.pool), '--output-dir', str(out)])
        def complete_array(json_dir, json_files, *positional, **options):
            for filename in json_files:
                request = Path(json_dir)/filename
                data = R.read_json(request); key = data['name']
                self.assertTrue(R.msa_start(self.pool, key, request))
                data['sequences'][0]['protein'].update(unpairedMsa='>q\nMAAAAA\n', pairedMsa='', templates=[])
                R.atomic_json(R.msa_native_path(self.pool, key), data)
                self.assertFalse(R.msa_ready(self.pool, key))
                self.assertTrue(R.msa_finish(self.pool, key, request, 0))
            return '101'
        with mock.patch.object(A, 'ensure_deployment'), mock.patch.object(A, 'submit_controller', return_value='100'), mock.patch.object(R, 'queue_snapshot', return_value={}):
            A.cmd_run(args)
            spec_path = next(out.glob('*/spec.json'))
            with mock.patch.object(A, 'submit_msa_array', side_effect=complete_array):
                A.cmd_stage_infer(argparse.Namespace(spec=str(spec_path)))
            spec = A.load_spec(str(spec_path)); key = spec['name']
            self.assertEqual(spec['status'], 'done')
            self.assertTrue(Path(R.msa_native_path(self.pool, key)).is_file())
            self.assertEqual(A._status_spec(str(spec_path), [])['task_states'][key][0], 'msa_ready')
            record_path = R.msa_record_path(self.pool, key)
            completed = R.read_json(record_path)
            R.atomic_json(record_path, dict(completed, status='failed'))
            self.assertEqual(A._status_spec(str(spec_path), [])['task_states'][key][0], 'waiting')
            preview = A.cmd_continue(argparse.Namespace(spec=str(spec_path), dry_run=True))
            self.assertIn('__recalc_', preview)
            self.assertFalse(Path(preview).exists())
            self.assertEqual(R.read_json(record_path)['status'], 'failed')
            R.atomic_json(record_path, completed)
            child = A.cmd_continue(argparse.Namespace(spec=str(spec_path), dry_run=False,
                                                     empty_msa_policy='reuse'))
            self.assertEqual(A.load_spec(child)['raw_input'], R.msa_native_path(self.pool, key))

    def test_generated_script_keeps_native_directory_on_success_and_failure(self):
        bash = (str(Path(os.environ.get('ProgramFiles', 'C:/Program Files'))/'Git/bin/bash.exe')
                if os.name == 'nt' else shutil.which('bash'))
        if not bash or not Path(bash).is_file(): self.skipTest('Bash is needed for generated-script execution')
        fake = self.root/'fake_af3.py'
        fake.write_text('''import json, os, pathlib, sys
args=sys.argv[1:]; binds={}
for i,arg in enumerate(args):
    if arg=='--bind':
        host,inside=args[i+1].rsplit(':',1); binds[inside]=host
def mapped(value):
    for inside,host in binds.items():
        if value==inside or value.startswith(inside+'/'):
            return pathlib.Path(host)/value[len(inside):].lstrip('/')
    raise ValueError('unmapped container path')
opts=dict(a[2:].split('=',1) for a in args if a.startswith('--') and '=' in a)
data=json.loads(mapped(opts['json_path']).read_text())
folder=mapped(opts['output_dir'])/data['name']; folder.mkdir(parents=True,exist_ok=True)
for entry in data['sequences']:
    body=entry['protein']; body.update(unpairedMsa='>q\\n'+body['sequence']+'\\n',pairedMsa='',templates=[])
(folder/(data['name']+'_data.json')).write_text(json.dumps(data))
(folder/'companion.txt').write_text('preserve this artificial AF3 companion')
sys.exit(int(os.environ.get('MOCK_AF3_EXIT','0')))
''', encoding='utf-8')
        for status in (0, 42):
            with self.subTest(status=status):
                pool = self.root/("pool with 'quote' and $value " + str(status))
                source = self.root/('inputs '+str(status)); source.mkdir()
                ent = self.entity()
                request = source/'P12345_input.json'
                R.atomic_json(request, A.generate_msa_input_json(ent, 'P12345'))
                R.reserve_msa(pool, 'P12345', ent['_msa_fingerprint'])
                with mock.patch.object(A, 'CONTAINER_RUNTIME', 'fake_container'), mock.patch.object(A, 'CONTAINER_MODULE', ''), mock.patch.object(A.sys, 'executable', sys.executable.replace('\\','/')):
                    script = A._msa_slurm_lines('example', 8, source.as_posix(), pool.as_posix(), '0-0%1',
                                               ['P12345_input.json'], False, None, partition='example_cpu')
                self.assertNotIn('MSA_ATTEMPT=', script)
                self.assertNotIn('rm -rf "$JOB_OUT_DIR"', script)
                helper = ('export PATH=/usr/bin:/bin:$PATH\n' +
                          ('flock() { return 0; }\n' if os.name == 'nt' else '') +
                          'fake_container() { ' + shlex.quote(sys.executable.replace('\\','/')) + ' ' +
                          shlex.quote(fake.as_posix()) + ' "$@"; }\n')
                env = dict(os.environ, SLURM_ARRAY_TASK_ID='0', SLURM_NTASKS='8',
                           MOCK_AF3_EXIT=str(status), MSYS2_ARG_CONV_EXCL='*', PYTHONDONTWRITEBYTECODE='1')
                proc = subprocess.run([bash], input=helper+script, text=True, encoding='utf-8', errors='replace', capture_output=True, env=env, timeout=40)
                self.assertEqual(proc.returncode, status, proc.stdout+proc.stderr)
                target = pool/'P12345/P12345_data.json'
                self.assertTrue(target.is_file())
                self.assertTrue(target.with_name('companion.txt').is_file())
                self.assertFalse((pool/'P12345_data.json').exists())
                self.assertEqual(R.msa_ready(pool, 'P12345', ent), status==0)


if __name__ == '__main__':
    unittest.main()
