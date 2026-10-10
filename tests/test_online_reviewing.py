import copy, json, sys, tempfile, unittest
from unittest.mock import patch
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'plugins/online-course-materials/scripts/vendor'),str(ROOT/'plugins/online-course-materials/scripts')]
from PIL import Image, ImageDraw
from ocm import reviewing, bundle, visuals
from ocm.common import atomic, digest, read_json, read_lines, write_lines, run, tools
from online_test_support import isolated_install


class BoundedReview(unittest.TestCase):
    def setUp(self):
        context=isolated_install();self.install=context.__enter__();self.addCleanup(context.__exit__,None,None,None)
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.job=Path(self.tmp.name)/'job'; self.b=self.job/'bundle'
        (self.b/'visuals/images').mkdir(parents=True); (self.b/'transcripts/raw').mkdir(parents=True)
        atomic(self.job/'state.json',{'id':'test','root':str(self.job.parent),'source_key':'test',
            'source':{'platform':'local','duration_ms':360000,'title':'Mixed learning video'},
            'course':{'name':'test','study_date':'2026-10-06','date_basis':'fixture'},'status':'ready_for_review'})
        atomic(self.b/'source-ref.json',{'fixture':True})
        write_lines(self.b/'transcripts/segments.jsonl',[{'id':'s1','begin_ms':0,'end_ms':360000,'text':'Fixture narration'}])
        self.rows=[]
        for i in range(18):
            p=self.b/f'visuals/images/f{i}.png'; img=Image.new('RGB',(800,450),(i*11,80,90))
            ImageDraw.Draw(img).text((5,5),f'Corner knowledge {i}',fill='white'); img.save(p)
            self.rows.append({'id':f'v{i}','path':str(p.relative_to(self.b)),'sha256':digest(p),
                'time_ms':i*20000,'occurrences_ms':[i*20000],'scene':'unreviewed','review_status':'not_viewed'})
        write_lines(self.b/'visuals/index.jsonl',self.rows)
        atomic(self.b/'visuals/scan.json',{'review_workflow':'temporal_previews_with_targeted_detail','sampling_windows':[]})
        bundle.publish_manifest(self.job)

    def checkpoint(self,batch,status='previewed',scene='animation'):
        p=self.job/'check.json'; atomic(p,{'batch_id':batch['id'],'sequence_note':'Synthetic time-sequence check only',
            'visuals':[{'id':vid,'scene':scene,'review_status':status,'note':'Synthetic fixture; no semantic completeness claim'}
                       for vid in dict.fromkeys(r['id'] for r in batch['occurrences'])]})
        return reviewing.save(self.job,p)

    def test_sequence_preserves_return_and_full_frame_preview(self):
        self.rows[0]['occurrences_ms'].append(350000)
        write_lines(self.b/'visuals/index.jsonl',self.rows)
        p=reviewing.plan(self.job)
        seq=[r['id'] for b in p['batches'] for r in b['occurrences']]
        self.assertEqual(seq[0],'v0'); self.assertEqual(seq[-1],'v0')
        self.assertTrue(all(len(b['occurrences'])<=8 for b in p['batches']))
        with Image.open(p['batches'][0]['image']) as img:
            self.assertEqual(img.width,1280)
        # A->B->A must not become A->B after exact SHA folding.
        self.assertEqual(len(seq),19)

    def test_resume_and_supplement_do_not_replay_unaffected_windows(self):
        p=reviewing.plan(self.job)
        for b in p['batches']:self.checkpoint(b)
        prior=copy.deepcopy(read_json(reviewing.paths(self.job)[3]))
        reviewing.detail(self.job,'v0','Read the actual mechanism label')
        v={**self.rows[0],'id':'new','time_ms':10000,'occurrences_ms':[10000]}
        write_lines(self.b/'visuals/index.jsonl',self.rows+[v])
        q=reviewing.plan(self.job); progress=read_json(reviewing.paths(self.job)[3])
        stable=set(prior['batches']) & {b['id'] for b in q['batches']}
        self.assertEqual(len(stable),2,'A supplement in first time window replayed later windows')
        self.assertEqual(set(progress['batches']),stable)
        self.assertEqual(len(progress['visuals']),18)
        self.assertEqual(reviewing.counts(progress)['detail_images'],1)

    def test_detail_idempotent_and_two_crops_then_stop(self):
        self.checkpoint(reviewing.plan(self.job)['batches'][0],'needs_detail','diagram')
        with self.assertRaisesRegex(ValueError,'FULL_IMAGE_BEFORE_CROP'):
            reviewing.detail(self.job,'v0','Read units',[0,0,100,100])
        r=reviewing.detail(self.job,'v0','Read units')
        again=reviewing.detail(self.job,'v0','Read units with changed wording')
        self.assertEqual(again['status'],'already_requested'); self.assertEqual(again['file'],r['file'])
        for box in ([0,0,100,100],[100,100,200,200]):reviewing.detail(self.job,'v0','Read important label',box)
        with self.assertRaisesRegex(ValueError,'CROP_LIMIT_REACHED'):
            reviewing.detail(self.job,'v0','Third enlargement',[200,200,300,300])
        self.assertEqual(reviewing.counts(read_json(reviewing.paths(self.job)[3]))['crops'],2)

    def test_budget_and_pending_detail_cannot_silently_pass(self):
        for b in reviewing.plan(self.job)['batches']:self.checkpoint(b,'needs_detail','ppt')
        p=read_json(reviewing.paths(self.job)[2]); p['limits']['detail_images']=1; atomic(reviewing.paths(self.job)[2],p)
        reviewing.detail(self.job,'v0','Read formula')
        with self.assertRaisesRegex(ValueError,'REVIEW_BUDGET_REACHED'):
            reviewing.detail(self.job,'v1','Read next formula')
        draft=self.job/'draft.md'; draft.write_text('# test')
        gaps=self.job/'gaps.json';atomic(gaps,[])
        with self.assertRaisesRegex(ValueError,'VISUAL_REVIEW_NOT_FINISHED'):
            reviewing.export(self.job,draft,gaps,True)

    def test_new_workflow_rejects_legacy_fake_review_and_accepts_checkpoints(self):
        review=self.job/'fake.json'; atomic(review,{'transcript_read_to_end':True,'visuals':[
            {'id':r['id'],'scene':'animation','review_status':'viewed','note':'Uncheckpointed'} for r in self.rows],
            'gap_reviews':[],'note_review_complete':True,'draft_sha256':'fake'})
        with self.assertRaisesRegex(ValueError,'REVIEW_PLAN_AND_CHECKPOINT_REQUIRED'):
            bundle.review(self.job,review)
        for b in reviewing.plan(self.job)['batches']:self.checkpoint(b)
        draft=self.job/'draft.md';draft.write_text('# 26.10.6 Animation process\n')
        gaps=self.job/'gaps.json';atomic(gaps,[])
        exported=reviewing.export(self.job,draft,gaps,True)
        self.assertEqual(bundle.review(self.job,exported['file'])['handoff_status'],'reviewed')
        self.assertTrue(bundle.validate(self.job)['valid'])

    def test_incomplete_sampling_window_and_unknown_are_disclosed(self):
        atomic(self.b/'visuals/scan.json',{'review_workflow':'temporal_previews_with_targeted_detail',
            'sampling_windows':[{'begin_ms':0,'end_ms':10000,'not_materialized':5}]})
        for b in reviewing.plan(self.job)['batches']:self.checkpoint(b)
        draft=self.job/'draft.md';draft.write_text('# 26.10.6 Animation mechanism\n\nSynthetic fixture.\n')
        gaps=self.job/'gaps.json';atomic(gaps,[])
        with self.assertRaisesRegex(ValueError,'EVERY_SAMPLING_WINDOW'):
            reviewing.export(self.job,draft,gaps,True)
        check=self.job/'sample.json';atomic(check,{'sampling_reviews':[{'begin_ms':0,'end_ms':10000,
            'status':'unresolved','basis':'Rapid labels not semantically verified'}]})
        reviewing.save(self.job,check)
        r=reviewing.export(self.job,draft,gaps,True)
        self.assertEqual(read_json(r['file'])['sampling_reviews'][0]['status'],'unresolved')
        bundle.publish_manifest(self.job)
        bundle.review(self.job,r['file'])
        cfg=self.job/'vault.json';vault=self.job/'vault';(vault/'Courses/test').mkdir(parents=True)
        atomic(cfg,{'vault':str(vault),'courses_dir':'Courses','attachments_dir':'Attachments','aliases':{}})
        with self.assertRaisesRegex(ValueError,'PARTIAL_NOTE_MUST_DISCLOSE_GAPS'):
            bundle.import_note(self.job,draft,'26.10.6 Animation mechanism',cfg)
        # A knowledge-first note may disclose unresolved coverage at the end.
        # The disclosure must still exist and the changed draft be re-reviewed.
        draft.write_text('# 26.10.6 Animation mechanism\n\n' + '教学机制与完整解释。\n\n' * 180 +
                         '部分摘录：快速动画中的个别标注未核实。自动测试素材。\n')
        r=reviewing.export(self.job,draft,gaps,True);bundle.review(self.job,r['file'])
        imported=bundle.import_note(self.job,draft,'26.10.6 Animation mechanism',cfg)
        self.assertTrue(Path(imported['note']).exists())

    def test_supplement_budget_is_durable_and_idempotent(self):
        budget=reviewing.plan(self.job)['limits']['supplements']
        for i in range(budget):reviewing.reserve_supplement(self.job,i*1000,'Check a meaningful motion transition')
        reviewing.reserve_supplement(self.job,0,'Retry the same snapshot after tool failure')
        self.assertEqual(reviewing.counts(read_json(reviewing.paths(self.job)[3]))['supplements'],budget)
        with self.assertRaisesRegex(ValueError,'REVIEW_BUDGET_REACHED'):
            reviewing.reserve_supplement(self.job,budget*1000,'Another transition')


class GeneralCapture(unittest.TestCase):
    def test_colour_only_diagram_change_is_not_ignored(self):
        a=Image.new('RGB',(320,180),(255,0,0))
        b=Image.new('RGB',(320,180),(0,130,0))
        # Same rounded luminance, different semantic colour legend / highlight.
        self.assertEqual(a.convert('L').getpixel((0,0)),b.convert('L').getpixel((0,0)))
        self.assertTrue(visuals.changed(a,b))
    def test_continuous_motion_full_timeline_bounded_and_silent_supported(self):
        import course_materials as cli
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); frames=root/'frames';frames.mkdir()
            # Animated diagram + footage-like whole-picture movement, not a board.
            for i in range(120):
                img=Image.new('RGB',(640,360),(i*13%255,i*29%255,i*47%255));d=ImageDraw.Draw(img)
                d.ellipse((i*5%500,90,i*5%500+60,150),fill='white')
                d.text((5,5),f'Process {i//20}',fill='black')
                if 50<=i<60:d.rectangle((0,280,200,359),fill='yellow')
                img.save(frames/f'{i:04d}.png')
            video=root/'animated.mp4';run([tools()['ffmpeg'],'-v','error','-y','-framerate','10','-i',frames/'%04d.png','-c:v','libx264','-pix_fmt','yuv420p',video])
            args=cli.parser().parse_args(['prepare','--local-video',str(video),'--root',str(root),'--course','science','--max-images','12'])
            job=Path(cli.prepare(args)['jobs'][0]['job']); cli.worker(job)
            scan=read_json(job/'bundle/visuals/scan.json');vs=list(read_lines(job/'bundle/visuals/index.jsonl'))
            self.assertEqual(scan['decoded_frames'],120)
            self.assertGreater(scan['detected_events'],12);self.assertEqual(scan['captured_events'],12)
            self.assertTrue(scan['sampling_windows']);self.assertLessEqual(len(vs),12)
            times=[t for v in vs for t in v['occurrences_ms']]
            self.assertGreaterEqual(max(times),11900)
            # A deliberately extreme 12-shot cap cannot preserve every state in
            # this fast-cut fixture. Omission must be explicit and recoverable,
            # rather than incorrectly labelled a duplicate or complete coverage.
            if not any(5000<=t<6000 for t in times):
                self.assertTrue(any(w['begin_ms']<6000 and w['end_ms']>=5000 for w in scan['sampling_windows']))
                with patch.object(sys,'argv',['course_materials.py','snapshot','--job',str(job),
                                              '--time','5.5','--purpose','Check the short animation label']):
                    supplemented=cli.main()
                self.assertTrue(Path(supplemented['file']).is_file())
                self.assertTrue(any(v['time_ms']==5500 for v in read_lines(job/'bundle/visuals/index.jsonl')))
                self.assertEqual(reviewing.counts(read_json(reviewing.paths(job)[3]))['supplements'],1)
            self.assertEqual(read_json(job/'bundle/manifest.json')['handoff_status'],'ready_for_review')
            self.assertEqual(read_json(job/'bundle/manifest.json')['quality']['unknown_text_ranges'],[])
            self.assertTrue(bundle.validate(job)['valid'])

    def test_temporal_allocation_not_first_n_and_omission_explicit(self):
        events={t:'visual_change' for t in range(0,100000,100)}
        events[99000]='settled_state'
        selected,windows=visuals.select_candidates(events,20,100000)
        self.assertEqual(len(selected),20); self.assertEqual(selected[-1],99900)
        self.assertTrue(any(45000<=t<=55000 for t in selected))
        self.assertGreater(len(windows),0)
        self.assertEqual(sum(w['not_materialized'] for w in windows),len(events)-20)

if __name__=='__main__': unittest.main(verbosity=2)
