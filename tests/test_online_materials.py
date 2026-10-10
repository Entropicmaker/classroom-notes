import json, os, shutil, subprocess, sys, time, unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT/'plugins/online-course-materials/scripts'
sys.path[:0]=[str(SCRIPTS/'vendor'),str(SCRIPTS)]
from PIL import Image, ImageDraw
from ocm import acquire,bundle,transcripts,reviewing
from ocm.common import atomic,digest,read_json,read_lines,run,tools,write_lines
import course_materials as cli
from online_test_support import isolated_install

class Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        context=isolated_install();cls.install=context.__enter__();cls.addClassCleanup(context.__exit__,None,None,None)
        cls.root=cls.install.root/'fixtures'
        cls.root.mkdir(parents=True,exist_ok=True)
        # Fixture has a 1-second teaching state and returns to the first page.
        colours=['white']*20+['#9bc8fa']*10+['#9afab8']*20+['white']*30
        frames=cls.root/'fixture-frames';frames.mkdir(exist_ok=True)
        for i,c in enumerate(colours):
            image=Image.new('RGB',(640,360),c);d=ImageDraw.Draw(image)
            d.rectangle((30,30,590,310),outline='black',width=3)
            label={'white':'A: force = mass * acceleration','#9bc8fa':'B: conditions and units','#9afab8':'C: additional example'}[c]
            d.text((50,65),label,fill='black');image.save(frames/f'{i:04d}.png')
        cls.video=cls.root/'lesson.mp4'
        run([tools()['ffmpeg'],'-nostdin','-v','error','-y','-framerate','10','-i',frames/'%04d.png','-c:v','libx264','-pix_fmt','yuv420p',cls.video])
        cls.subtitle=cls.root/'lesson.srt'
        cls.subtitle.write_text('1\n00:00:00,000 --> 00:00:02,000\nForce equals mass times acceleration.\n\n2\n00:00:02,000 --> 00:00:03,000\nUnits and conditions.\n\n3\n00:00:03,000 --> 00:00:08,000\nAn example and repeated explanation.\n',encoding='utf-8')
        cls.job=Path(cls.call('prepare','--local-video',str(cls.video),'--subtitle',str(cls.subtitle),'--root',str(cls.root),'--course','物理测试','--asr')['jobs'][0]['job'])
        cls.call('start','--job',str(cls.job),'--foreground')
    @classmethod
    def call(cls,*args,fail=False):
        p=subprocess.run([sys.executable,str(cls.install.scripts/'course_materials.py'),*args],capture_output=True,text=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
        if fail:
            assert p.returncode, p.stdout
            return json.loads(p.stderr)
        assert p.returncode==0, p.stderr
        return json.loads(p.stdout)
    def test_01_all_native_frames_and_short_page(self):
        b=self.job/'bundle';scan=read_json(b/'visuals/scan.json')
        self.assertEqual(scan['decoded_frames'],80)
        rows=list(read_lines(b/'visuals/index.jsonl'))
        occurrences=[t for v in rows for t in v['occurrences_ms']]
        self.assertTrue(any(2000<=t<3000 for t in occurrences),'1 second page was lost')
        self.assertTrue(any(t>=5000 for t in occurrences),'return to first page was lost')
        self.assertTrue(any(len(v['occurrences_ms'])>1 for v in rows),'exact duplicate occurrences not retained')
        self.assertEqual(self.call('validate','--job',str(self.job))['valid'],True)
    def test_02_full_pagination_and_tampering(self):
        a=self.call('read','--job',str(self.job),'--limit','2')
        self.assertEqual(a['next_offset'],2)
        b=self.call('read','--job',str(self.job),'--offset','2','--limit','2')
        self.assertIsNone(b['next_offset'])
        media=self.job/'source/video.mp4';raw=media.read_bytes()
        try:
            media.write_bytes(raw+b'changed')
            self.assertIn('SOURCE_ARTIFACT_CHANGED',self.call('validate','--job',str(self.job),fail=True)['error'])
        finally:media.write_bytes(raw)
    def test_03_review_import_and_preserve_personal_edits(self):
        root=self.root;draft=root/'draft.md';assets=root/'assets';assets.mkdir(exist_ok=True)
        vs=list(read_lines(self.job/'bundle/visuals/index.jsonl'))
        shutil.copyfile(self.job/'bundle'/vs[0]['path'],assets/'figure.png')
        draft.write_text('# 26.10.5 牛顿第二定律\n\n自动字幕测试。\n\n力等于质量乘以加速度。\n\n![Figure](assets/figure.png)\n',encoding='utf-8')
        plan=reviewing.plan(self.job)
        for batch in plan['batches']:
            check=root/'checkpoint.json';atomic(check,{'batch_id':batch['id'],'sequence_note':'Synthetic fixture only; no semantic review claim','visuals':[{'id':vid,'scene':'ppt','review_status':'previewed','note':'Automated fixture contract test; not a real semantic review'} for vid in dict.fromkeys(v['id'] for v in batch['occurrences'])]})
            reviewing.save(self.job,check)
        gaps=root/'gap-reviews.json';atomic(gaps,[])
        path=Path(reviewing.export(self.job,draft,gaps,True)['file'])
        self.call('review','--job',str(self.job),'--file',str(path))
        vault=root/'isolated-vault';(vault/'Courses/物理测试').mkdir(parents=True,exist_ok=True)
        config=root/'test-vault.json';atomic(config,{'vault':str(vault),'courses_dir':'Courses','attachments_dir':'Attachments','aliases':{}})
        # Changed draft must not be imported under an old review.
        raw=draft.read_bytes();draft.write_bytes(raw+b'changed')
        self.assertIn('FINAL_NOTE_REVIEW',self.call('import-note','--job',str(self.job),'--note',str(draft),'--title','26.10.5 牛顿第二定律','--config',str(config),fail=True)['error']);draft.write_bytes(raw)
        result=self.call('import-note','--job',str(self.job),'--note',str(draft),'--title','26.10.5 牛顿第二定律','--config',str(config))
        note=Path(result['note']);self.assertTrue(note.exists());self.assertTrue(list((vault/'Attachments').rglob('*.png')))
        note.write_text(note.read_text()+'\nPersonal edits.\n')
        previous=digest(note)
        repeated=self.call('import-note','--job',str(self.job),'--note',str(draft),'--title','Another title','--config',str(config))
        self.assertEqual(repeated['status'],'already_imported');self.assertEqual(digest(note),previous)
        prepared=self.call('prepare','--local-video',str(self.video),'--root',str(root),'--course','物理测试')
        self.assertEqual(prepared['jobs'][0]['status'],'already_imported')
        # Clear registry simulating crash after Vault commit, recover from existing receipt.
        with bundle.database(root) as db:db.execute('DELETE FROM imports')
        recovered=self.call('import-note','--job',str(self.job),'--note',str(draft),'--title','Recovery title','--config',str(config))
        self.assertEqual(recovered['note'],str(note));self.assertEqual(digest(note),previous)
    def test_04_native_budget_is_failure_not_truncation(self):
        job=Path(self.call('prepare','--local-video',str(self.video),'--subtitle',str(self.subtitle),'--root',str(self.root),'--course','物理测试','--new-version','--max-images','1')['jobs'][0]['job'])
        e=self.call('start','--job',str(job),'--foreground',fail=True)
        self.assertIn('VISUAL_BUDGET_EXCEEDED',e['error'])
        self.assertFalse((job/'bundle/visuals/index.jsonl').exists())
        media=job/'source/video.mp4';media.write_bytes(media.read_bytes()+b'changed while paused')
        e=self.call('start','--job',str(job),'--foreground',fail=True)
        self.assertIn('MEDIA_BYTES_CHANGED',e['error'])
    def test_05_background_no_lock_race(self):
        job=Path(self.call('prepare','--local-video',str(self.video),'--subtitle',str(self.subtitle),'--root',str(self.root),'--course','物理测试','--new-version')['jobs'][0]['job'])
        self.call('start','--job',str(job))
        end=time.monotonic()+20
        while time.monotonic()<end:
            result=self.call('status','--job',str(job))
            if result['status']!='running':break
            time.sleep(.2)
        self.assertEqual(result['status'],'ready_for_review',result)
    def test_06_asr_absolute_time_and_alignment_refresh(self):
        job=self.root/'fake-asr';(job/'bundle/transcripts/raw').mkdir(parents=True,exist_ok=True);(job/'bundle/visuals').mkdir(parents=True,exist_ok=True)
        source={'platform':'local','duration_ms':5405000,'title':'fixture'}
        atomic(job/'state.json',{'id':'fixture','root':str(self.root),'source':source,'source_key':'fake','course':{'name':'test','study_date':'2026-10-05','date_basis':'test'},'status':'waiting_asr'})
        atomic(job/'bundle/source-ref.json',{'fixture':True});write_lines(job/'bundle/visuals/index.jsonl',[])
        transcripts.publish(job,[],'empty');bundle.publish_manifest(job)
        atomic(job/'asr.json',{'blocks':[{'index':0,'begin_ms':0,'end_ms':5400000,'status':'done'},{'index':1,'begin_ms':5400000,'end_ms':5405000,'status':'done'}]})
        atomic(job/'bundle/transcripts/raw/asr-block-000.json',[{'begin_ms':0,'end_ms':1000,'text':'one'}])
        atomic(job/'bundle/transcripts/raw/asr-block-001.json',[{'begin_ms':0,'end_ms':5000,'text':'two'}])
        transcripts.step_asr(job);bundle.publish_manifest(job)
        rows=list(read_lines(job/'bundle/transcripts/segments.jsonl'))
        self.assertEqual(rows[1]['begin_ms'],5400000);self.assertEqual(rows[1]['end_ms'],5405000)
        self.assertEqual(len(list(read_lines(job/'bundle/alignment.jsonl'))),2)
    def test_08_course_lesson_storage_and_resume(self):
        root=self.root/'layout-test';course=root/'水文学';course.mkdir(parents=True)
        marker=course/'9.10/existing.txt';marker.parent.mkdir();marker.write_text('original course files')
        first=self.call('prepare','--local-video',str(self.video),'--root',str(root),'--course','水文学','--date','2026-10-05')
        job=Path(first['jobs'][0]['job'])
        self.assertEqual(job.parent,course)
        self.assertTrue(job.name.startswith('2026-10-05 第01节'))
        again=self.call('prepare','--local-video',str(self.video),'--root',str(root),'--course','水文学','--date','2026-10-06')
        self.assertEqual(again['jobs'][0]['job'],str(job))
        # Different source gets its own lesson, same top-level course.
        second_video=root/'lesson-two.mp4';second_video.write_bytes(self.video.read_bytes()+b'another source fixture')
        second=self.call('prepare','--local-video',str(second_video),'--root',str(root),'--course','水文学')
        job2=Path(second['jobs'][0]['job']);self.assertEqual(job2.parent,course);self.assertNotEqual(job,job2)
        self.assertEqual(marker.read_text(),'original course files')
        with self.assertRaisesRegex(ValueError,'INVALID_COURSE_FOLDER'):
            cli.storage.component('../escape')
        # An independent online series can have a folder distinct from Vault subject.
        series=self.call('prepare','--local-video',str(self.video),'--root',str(root),'--course','水文学','--series','水文学-某老师课程')
        self.assertEqual(Path(series['jobs'][0]['job']).parent.name,'水文学-某老师课程')
        self.assertEqual(cli.storage.settings()['materials_root'],str(self.install.materials))

    def test_07_scope_and_network_failure_not_empty_content(self):
        source={'code':0,'data':{'bvid':'BV1kybV6DE47','aid':1,'pages':[{'page':1,'cid':2,'duration':8},{'page':2,'cid':3,'duration':9}]}}
        with patch.object(acquire,'fetch_json',return_value=source):
            self.assertEqual(acquire.resolve('https://www.bilibili.com/video/BV1kybV6DE47?spm_id=x')[0]['cid'],2)
            self.assertEqual(len(acquire.resolve('BV1kybV6DE47',pages='all')),2)
            self.assertEqual(acquire.resolve('https://www.bilibili.com/video/BV1kybV6DE47?p=2')[0]['cid'],3)
        with patch.object(acquire,'fetch_json',side_effect=ValueError('HTTP Error 412')):
            with self.assertRaisesRegex(ValueError,'412'):acquire.resolve('BV1kybV6DE47')

if __name__=='__main__':unittest.main(verbosity=2)
