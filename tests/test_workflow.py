"""Synthetic offline workflow regressions; no research inputs or real Slurm jobs.

Short repeated amino-acid strings here are constructed test fixtures, not
biological examples. No external sequence service is used.
"""
import ast, copy, csv, json, os, pathlib, subprocess, sys, tempfile, time, unittest
from unittest import mock
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import af3 as A
import af3_runtime as R
import af3_pae as P

class WorkflowRegression(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='af3-public-workflow-')
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {
            'AF3_BASE': str(self.root), 'AF3_CONFIG': str(self.root/'config.json'),
            'AF3_PAE_CACHE': str(self.root/'pae'), 'PYTHONDONTWRITEBYTECODE': '1',
            'PYTHONIOENCODING': 'utf-8', 'AF3_SNAPSHOT': ''})
        self.env.start()
        self.addCleanup(self.env.stop)
        A.reload_config()
        review = mock.patch.object(A, '_MSA_REVIEW_CONTEXT', None)
        review.start(); self.addCleanup(review.stop)
        # These tests isolate orchestration from deployment validation, which
        # is covered separately with fake resources and scheduler executables.
        guard = mock.patch.object(A, 'ensure_deployment')
        guard.start(); self.addCleanup(guard.stop)

    def entity(self,expression='p:MAAAAA'):
        e=A.parse_expression(expression)[0];A.resolve_msa_key(e,str(self.root));return e

    def ready(self,e):
        key=e['_msa_key'];p=self.root/(key+'_data.json')
        R.atomic_json(p,self.complete_data(key,e['sequence']))
        return p

    def complete_data(self,name,sequence):
        # Complete synthetic hits let orchestration tests reach their intended
        # scheduling branches; empty-field policy is tested separately.
        return dict(name=name,sequences=[{'protein':{'id':'A','sequence':sequence,
                    'unpairedMsa':'>q\n'+sequence,'pairedMsa':'>q\n'+sequence,
                    'templates':[{'mmcif':'data_synthetic\n#\n','queryIndices':[0],'templateIndices':[0]}]}}],
                    modelSeeds=[999],version=4,dialect='alphafold3')

    def result(self,name='job',root=None):
        root=pathlib.Path(root or self.root);d=root/name;d.mkdir(parents=True,exist_ok=True)
        (d/(name+'_model.cif')).write_text('data_model')
        R.atomic_json(d/(name+'_summary_confidences.json'),{'iptm':.8,'ptm':.7,'ranking_score':.9})
        return d

    def test_msa_alias_shares_content(self):
        a=self.entity('p:MAAAAA:name=one');b=self.entity('p:MAAAAA:name=two')
        self.assertEqual(a['_msa_key'],b['_msa_key'])
        c=self.entity('p:MGGGGG:name=one');self.assertNotEqual(a['_msa_key'],c['_msa_key'])

    def test_custom_msa_content_identity(self):
        p=self.root/'a.a3m';p.write_text('>q\nMAAAAA\n')
        e=self.entity();e['msa_path']=str(p);key=A.resolve_msa_key(e,str(self.root))[0]
        p.write_text('>q\nMAAAAA\n>h\nMAAGAA\n');self.assertNotEqual(key,A.resolve_msa_key(e,str(self.root))[0])

    def test_unresolved_accessions_are_distinct(self):
        self.assertNotEqual(R.entity_identity({'type':'protein','uniprot':'P12345'}),R.entity_identity({'type':'protein','uniprot':'Q12345'}))

    def test_ptm_changes_task_and_pae_identity(self):
        e=self.entity();modified=copy.deepcopy(e);modified['modifications']=[{'ptmType':'SEP','ptmPosition':2}]
        self.assertNotEqual(A._side_sig({'entities':[e]}),A._side_sig({'entities':[modified]}))
        self.assertNotEqual(A.pae_identity(e),A.pae_identity(modified))
        self.assertNotEqual(A.pae_identity(e),A.pae_identity(e,True))

    def test_invalid_overlap_rejected_even_short_sequence(self):
        for overlap in (100,101,-1):
            with self.assertRaises(ValueError):A.fragment_windows(10,100,overlap,10,500)

    def test_infer_assembly_seeds_assets_and_cache_immutability(self):
        e=self.entity();p=self.ready(e);before=copy.deepcopy(A.read_data_json(str(p)))
        data=A.build_infer_json('job',[e],[11,22],str(self.root),template_free=True)
        self.assertEqual(data['modelSeeds'],[11,22])
        protein=data['sequences'][0]['protein']
        self.assertIn('unpairedMsaPath',protein);self.assertNotIn('unpairedMsa',protein)
        self.assertEqual(pathlib.Path(R.host_asset_path(protein['unpairedMsaPath'])).read_text(),'>q\nMAAAAA')
        self.assertEqual(before,A.read_data_json(str(p)))

    def test_msa_free_does_not_reuse_full_msa(self):
        e=self.entity();key=e['_msa_key'];e['_msa_free']=True
        self.assertNotEqual(key,A.resolve_msa_key(e,str(self.root))[0])

    def test_completion_requires_model_summary_and_receipt(self):
        R.record_submission(str(self.root),'job','12','input','tag')
        R.atomic_text(self.root/'.attempts/job/12.exit','0\n')
        self.assertEqual(R.task_state(str(self.root),'job',{})[0],'failed')
        self.result();self.assertEqual(R.task_state(str(self.root),'job',{})[0],'succeeded')
        R.atomic_text(self.root/'.attempts/job/12.exit','7\n');self.assertEqual(R.task_state(str(self.root),'job',{})[0],'failed')

    def test_retry_follows_exact_id_and_new_visibility_grace(self):
        R.record_submission(str(self.root),'job','12','input','tag')
        rec=R.read_json(R.task_path(str(self.root),'job'));rec['submitted_at']=time.time()-1000;R.atomic_json(R.task_path(str(self.root),'job'),rec)
        R.atomic_text(self.root/'.attempts/job/12.retry','22\n');R.atomic_text(self.root/'.attempts/job/12.exit','42\n')
        self.assertEqual(R.task_state(str(self.root),'job',{})[0],'confirming')
        self.assertEqual(R.task_state(str(self.root),'job',{'22':('same-name','RUNNING')})[0],'running')

    def test_scheduler_failure_is_unknown(self):
        R.record_submission(str(self.root),'job','12','input','tag')
        self.assertEqual(R.task_state(str(self.root),'job',None)[0],'unknown')
        with mock.patch.object(R.subprocess,'run',side_effect=OSError('offline')):
            with self.assertRaises(R.SchedulerUnavailable):R.queue_snapshot(force=True)

    def test_duplicate_submission_reuses_exact_id(self):
        p=self.root/'job_data.json';R.atomic_json(p,{'name':'job'})
        with mock.patch.object(R,'queue_snapshot',return_value={}),mock.patch.object(A,'submit_job',return_value='88') as submit:
            self.assertEqual(A.submit_infer_single(str(p),str(self.root),'tag'),'88')
            self.assertEqual(A.submit_infer_single(str(p),str(self.root),'tag'),'88')
            self.assertEqual(submit.call_count,1)

    def test_pae_stream_skip_metadata_precision_and_cache(self):
        import numpy as np
        path=self.root/'pae.json';raw={'unused':[[1]*400 for i in range(400)],'pae':[[.123456789123,2],[3,4]],'token_chain_ids':['A','B'],'token_res_ids':[1,1]}
        R.atomic_json(path,raw);matrix,meta=P.load(path,self.root/'cache')
        self.assertTrue(np.array_equal(matrix,raw['pae']));self.assertFalse(matrix.flags.writeable)
        self.assertEqual(meta['token_chain_ids'],['A','B']);del matrix
        matrix,meta=P.load(path,self.root/'cache');self.assertEqual(matrix.shape,(2,2));del matrix

    def test_pae_malformed_not_published(self):
        p=self.root/'bad.json';R.atomic_json(p,{'pae':[[1,2],[3]]})
        with self.assertRaises(ValueError):P.load(p,self.root/'cache')
        self.assertEqual(list((self.root/'cache').glob('*.npy')),[])

    def test_pae_chain_labels_and_ligand_mapping(self):
        inp={'sequences':[{'protein':{'id':['A','B'],'sequence':'MA'}},{'ligand':{'id':'C','ccdCodes':['ATP']}}]}
        result=P.chains({'n':7,'token_chain_ids':['A','A','B','B','C','C','C']},inp,['homo','homo','ATP'])
        self.assertEqual([x['label'] for x in result],['homo [A]','homo [B]','ATP [C]']);self.assertEqual(result[2]['type'],'ligand')
        self.assertEqual(P.chains({'n':7},inp),[])

    def test_pae_numeric_segmentation_equivalence(self):
        import numpy as np
        matrix=[[1 if i//4==j//4 else 20 for j in range(8)] for i in range(8)]
        args=dict(pae_cutoff=5,resolution=.5,min_domain=2,domains_per_window=1,min_window=1,max_span=8)
        self.assertEqual(A._pae_fragment_windows(matrix,list(range(1,9)),**args),A._pae_fragment_windows(np.array(matrix),list(range(1,9)),**args))

    def test_interface_score_and_seed_consistency(self):
        pair={'a':{'entities':[{'copies':2}]},'b':{'entities':[{'copies':1}]}}
        d=self.result();data={'iptm':.99,'ptm':.7,'chain_pair_iptm':[[1,.99,.2],[.99,1,.4],[.2,.4,1]],'chain_pair_pae_min':[[0,0,9],[0,0,8],[7,6,0]]}
        R.atomic_json(d/'job_summary_confidences.json',data)
        for i in range(2):R.atomic_json(d/('seed-1_sample-'+str(i))/'job_summary_confidences.json',data)
        score=A.extract_scores(str(d),pair);self.assertAlmostEqual(score[0],.3);self.assertEqual(score[2],6)
        self.assertAlmostEqual(A.extract_seed_stats(str(d),pair)[0],.3)

    def test_cli_dry_run_leaves_no_plan(self):
        result=subprocess.run([sys.executable,'-B',str(ROOT/'af3.py'),'run','p:MAAAAA','--seeds','11,22','--dry-run'],capture_output=True,encoding='utf8')
        self.assertEqual(result.returncode,0,result.stderr);self.assertIn('11',result.stdout)

    def test_controller_run_infer_msa_and_raw_modes(self):
        import argparse
        for mode in ('run','infer','msa','raw'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory(dir=self.root) as work:
                out=pathlib.Path(work)/'out';msa=pathlib.Path(work)/'msa';msa.mkdir()
                flags=['run' if mode=='raw' else mode,'--output-dir',str(out),'--msa-dir',str(msa),'--seeds','11,22']
                if mode=='raw':
                    raw=pathlib.Path(work)/'input.json';R.atomic_json(raw,{'name':'raw','modelSeeds':[11,22],'sequences':[{'protein':{'id':'A','sequence':'MAAAAA','unpairedMsa':'','pairedMsa':'','templates':[]}}],'version':4,'dialect':'alphafold3'})
                    flags+=['--json',str(raw),'--infer-only','--empty-msa-policy','reuse']
                else:flags+=['p:MAAAAA']
                args=A.build_parser().parse_args(flags)
                e=self.entity();source=self.ready(e)
                if mode=='infer':R.atomic_json(msa/source.name,R.read_json(source))
                with mock.patch.object(A,'submit_controller',return_value='100'):
                    A.cmd_run(args)
                specs=list(out.glob('*/spec.json'));self.assertEqual(len(specs),1)
                spec=specs[0];plan=spec.with_name('plan.json').read_bytes()
                def msa_submit(entities,tag,options,work_dir):
                    for entity in entities:
                        R.atomic_json(msa/(entity['_msa_key']+'_data.json'),self.complete_data(entity['_msa_key'],entity['sequence']))
                    return ['101']
                def infer_submit(path,output,tag,**kw):
                    name=R.read_json(path)['name'];self.result(name,output)
                    R.record_submission(output,name,'102',path,tag);R.atomic_text(pathlib.Path(output)/'.attempts'/name/'102.exit','0\n')
                    return '102'
                with mock.patch.object(R,'queue_snapshot',return_value={}),mock.patch.object(A,'submit_msa_stage',side_effect=msa_submit),mock.patch.object(A,'submit_infer_single',side_effect=infer_submit):
                    A.cmd_stage_infer(argparse.Namespace(spec=str(spec)))
                self.assertEqual(R.read_json(spec)['status'],'done')
                self.assertEqual(plan,spec.with_name('plan.json').read_bytes())

    def test_screen_partial_failure_then_retry_only_failed(self):
        import argparse
        out=self.root/'screen';out.mkdir()
        a=self.entity();b=self.entity('p:MGGGGG');c=self.entity('p:MSSSSS')
        for e in (a,b,c):self.ready(e)
        side=lambda e:{'entities':[e],'label':e['sequence'],'expr':'p:'+e['sequence']}
        pairs=A.build_pairs([side(a)],[side(b),side(c)],False)
        spec={'version':2,'type':'pulldown','name':'screen','tag':'screen','outdir':str(out),'output_dir':str(out),'msa_dir':str(self.root),'pairs':pairs,'msa_entities':[a,b,c],'max_concurrent':2,'resolved_seeds':[11,22],'seeds':'11,22','num_seeds':2,'status':'preparing'}
        sp=out/'spec.json';A.write_spec(spec,str(sp));calls=[];fail_name=pairs[1]['name']
        def submit(path,output,tag,**kw):
            name=R.read_json(path)['name'];calls.append(name);jid=str(200+len(calls));R.record_submission(output,name,jid,path,tag)
            if name==fail_name and calls.count(name)==1:code=7
            else:self.result(name,output);code=0
            R.atomic_text(pathlib.Path(output)/'.attempts'/name/(jid+'.exit'),str(code))
            return jid
        with mock.patch.object(R,'queue_snapshot',return_value={}),mock.patch.object(A,'submit_infer_single',side_effect=submit),mock.patch.object(A.time,'sleep'),mock.patch.object(A,'_try_plot_matrix'):
            A.cmd_pulldown_watcher(argparse.Namespace(spec=str(sp)))
            self.assertEqual(R.read_json(sp)['status'],'partial_failed')
            R.update_json(sp,retry_requested_at=time.time()+.01)
            A.cmd_pulldown_watcher(argparse.Namespace(spec=str(sp)))
            self.assertEqual(R.read_json(sp)['status'],'done')
        self.assertEqual(calls.count(pairs[0]['name']),1);self.assertEqual(calls.count(fail_name),2)
        self.assertTrue((out/pairs[0]['name']).is_dir())
        with (out/'ranking.csv').open(encoding='utf8') as stream: rows=list(csv.DictReader(stream))
        self.assertEqual(len(rows),2)

    def test_pae_scan_slow_path_completes_and_preserves_plan(self):
        import argparse
        out=self.root/'pae_out';msa=self.root/'msa';msa.mkdir()
        args=A.build_parser().parse_args(['scan','p:MAMAMAMAMAMA','p:MGGMGGMGGMGG','--mode','pae','--split-threshold','6','--pae-min-domain','2','--pae-min-frag','2','--pae-domains-per-window','1','--pae-max-frag','6','--shared-msa','--seeds','11,22','--output-dir',str(out),'--msa-dir',str(msa)])
        with mock.patch.object(A,'submit_watcher',return_value='300'):
            A.cmd_scan(args)
        sp=next(out.glob('*/spec.json'));plan=sp.with_name('plan.json').read_bytes()
        def msa_submit(entities,tag,options,work_dir):
            for entity in entities:
                R.atomic_json(msa/(entity['_msa_key']+'_data.json'),self.complete_data(entity['_msa_key'],entity['sequence']))
            return ['301']
        submitted=[]
        def submit(path,output,tag,**kw):
            data=R.read_json(path);name=data['name'];submitted.append(name);jid=str(400+len(submitted))
            folder=self.result(name,output);R.record_submission(output,name,jid,path,tag)
            if name.startswith('pae_'):
                n=len(data['sequences'][0]['protein']['sequence'])
                R.atomic_json(folder/(name+'_confidences.json'),{'pae':[[1 if i//3==j//3 else 20 for j in range(n)] for i in range(n)],'token_chain_ids':['A']*n,'token_res_ids':list(range(1,n+1))})
            R.atomic_text(pathlib.Path(output)/'.attempts'/name/(jid+'.exit'),'0');return jid
        with mock.patch.object(R,'queue_snapshot',return_value={}),mock.patch.object(A,'submit_msa_stage',side_effect=msa_submit),mock.patch.object(A,'submit_infer_single',side_effect=submit),mock.patch.object(A.time,'sleep'),mock.patch.object(A,'_try_plot_matrix'),mock.patch.object(A,'_write_scan_reports'):
            A.cmd_pulldown_watcher(argparse.Namespace(spec=str(sp)))
            # Shared-MSA slicing intentionally creates template-free fragments.
            # Simulate the user's explicit review of those newly created files
            # before retrying; never bypass the runtime guard.
            current = A.load_spec(str(sp))
            review = argparse.Namespace(msa_dir=str(msa), empty_msa_policy='reuse', dry_run=False)
            A._begin_msa_review(review)
            for cached in sp.parent.rglob('*_data.json'):
                if R.validate_msa(cached):
                    A._review_data_path(str(cached), review)
            opts = dict(current.get('submit_args', {}), empty_msa_approved=review.empty_msa_approved)
            A.update_spec(str(sp), submit_args=opts, retry_requested_at=time.time()+.01)
            A.cmd_pulldown_watcher(argparse.Namespace(spec=str(sp)))
        final=R.read_json(sp);self.assertEqual(final['status'],'done');self.assertGreater(len(final['pairs']),0)
        self.assertEqual(plan,sp.with_name('plan.json').read_bytes());self.assertTrue(sp.with_name('resolved_plan.json').exists())

    def test_repeated_fragment_sequence_keeps_coordinate_and_msa_context(self):
        e=self.entity();a=dict(e,_frag={'parent':'P','start':1,'end':6},_shared_source={'parent_key':'parent1','start':1,'end':6})
        b=dict(e,_frag={'parent':'P','start':7,'end':12},_shared_source={'parent_key':'parent1','start':7,'end':12})
        self.assertNotEqual(A._side_sig({'entities':[a]}),A._side_sig({'entities':[b]}))
        self.assertNotEqual(A.resolve_msa_key(a,str(self.root))[0],A.resolve_msa_key(b,str(self.root))[0])
        self.assertNotEqual(A._ent_label(a),A._ent_label(b))

    def test_help_section_schema_and_merged_sources(self):
        import hashlib,af3_ui_help as H
        self.assertTrue(all(len(section)==5 for section in H.HELP_SECTIONS))
        tree=ast.parse((ROOT/'af3_gui').read_text(encoding='utf8'))
        node=next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='SOURCE_HASHES' for t in n.targets))
        for file,digest in ast.literal_eval(node.value).items():self.assertEqual(hashlib.sha256((ROOT/file).read_text(encoding='utf8').encode()).hexdigest(),digest)

    def test_pin_assets_survive_source_edit(self):
        source=self.root/'template.cif';source.write_text('old content')
        data=R.pin_assets({'template':{'mmcifPath':str(source)}},str(self.root/'assets'))
        source.write_text('new content');self.assertEqual(pathlib.Path(data['template']['mmcifPath']).read_text(),'old content')

    def test_msa_publication_validation(self):
        p=self.root/'bad.json';p.write_text('{')
        self.assertFalse(R.validate_msa(p));R.atomic_json(p,{'sequences':[{'protein':{'sequence':'MA'}}]});self.assertFalse(R.validate_msa(p))
        self.assertTrue(R.validate_msa(self.ready(self.entity())))

    def test_backend_pagination_and_scan_prediction(self):
        import af3_ui_backend as B
        path=self.root/'ranking.csv'
        with path.open('w',newline='',encoding='utf8') as f:
            w=csv.writer(f);w.writerow(['rank','name','folder']);w.writerows((i,'label,with comma','folder') for i in range(850))
        cols,rows,total=B.csv_page(str(path),4,200);self.assertEqual(total,850);self.assertEqual(len(rows),50);self.assertEqual(rows[0][0],'800')
        a=[[self.entity('p:'+'MA'*300)]];b=[[self.entity('p:'+'MG'*300)]]
        got=B.preview_screen_plan(a,b,'scan',dict(mode='win',win=300,overlap=100,min_frag=100,threshold=500),{'seeds':'11,22'},False)
        self.assertGreater(got['pairs'],0);self.assertEqual(got['models'],got['pairs']*2*5)


if __name__ == "__main__": unittest.main()
