import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'plugins/online-course-materials/scripts/vendor'),str(ROOT/'plugins/online-course-materials/scripts')]
from ocm import bundle,cleanup
from ocm.common import atomic,digest,read_json,state,write_lines
from online_test_support import isolated_install

class CleanupAndNaming(unittest.TestCase):
 def setUp(self):
  context=isolated_install();self.install=context.__enter__();self.addCleanup(context.__exit__,None,None,None)
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name).resolve();self.job=self.root/'course/lesson';self.job.mkdir(parents=True)
  self.vault=self.root/'vault';(self.vault/'Courses/物理').mkdir(parents=True)
  self.cfg=self.root/'config.json';atomic(self.cfg,{'vault':str(self.vault),'courses_dir':'Courses','attachments_dir':'Attachments','aliases':{}})
  b=self.job/'bundle';(b/'transcripts/raw').mkdir(parents=True);(b/'visuals/images').mkdir(parents=True)
  atomic(b/'source-ref.json',{'platform':'bilibili','bvid':'test','cid':1})
  media=self.job/'source/video.mp4';media.parent.mkdir();media.write_bytes(b'original-fixture')
  cache=self.job/'.dsh-work/classroom-audio/test/audio1.wav';cache.parent.mkdir(parents=True);cache.write_bytes(b'cached-audio')
  derived=self.job/'derived/audio.wav';derived.parent.mkdir();derived.write_bytes(b'derived-audio')
  atomic(self.job/'state.json',{'id':'test','root':str(self.root),'source_key':'fixture','source':{'platform':'bilibili','title':'力的定律','duration_ms':8000,'canonical_url':'https://example.invalid'},'course':{'name':'物理','study_date':'2026-10-05','date_basis':'测试'},'status':'ready_for_review','import_status':'not_started','media':'source/video.mp4','media_sha256':digest(media)})
  write_lines(b/'transcripts/segments.jsonl',[{'id':'s1','begin_ms':0,'end_ms':8000,'text':'力等于质量乘加速度'}])
  image=b/'visuals/images/frame.png';image.write_bytes(b'fixture-image')
  write_lines(b/'visuals/index.jsonl',[{'id':'v1','path':'visuals/images/frame.png','time_ms':0,'occurrences_ms':[0],'sha256':digest(image)}])
  bundle.publish_manifest(self.job)
  self.draft=self.root/'draft.md';self.draft.write_text('# 26.10.5 牛顿第二定律\n\n力等于质量乘以加速度。\n')
  review=self.root/'review.json';atomic(review,{'transcript_read_to_end':True,'visuals':[{'id':'v1','scene':'ppt','review_status':'viewed','note':'Fixture only, not semantic validation'}],'gap_reviews':[],'note_review_complete':True,'draft_sha256':digest(self.draft)})
  bundle.review(self.job,review)
 def import_note(self):return bundle.import_note(self.job,self.draft,'26.10.5 牛顿第二定律',self.cfg)
 def test_success_cleanup_provenance_and_receipt_recovery(self):
  r=self.import_note();self.assertEqual(r['cleanup']['status'],'complete')
  self.assertFalse(list(self.job.rglob('*.wav')));self.assertFalse(list(self.job.rglob('*.mp4')))
  self.assertTrue(bundle.validate(self.job)['valid'])
  m=read_json(self.job/'bundle/manifest.json');self.assertEqual(m['media_availability'],'deleted_after_import')
  self.assertFalse(any('/source/video.mp4' in p for p in m['source_files']))
  note=Path(r['note']);note.write_text(note.read_text()+'\n用户小记。\n');before=digest(note)
  with bundle.database(self.root) as db:db.execute('DELETE FROM imports')
  again=self.import_note();self.assertEqual(again['status'],'already_imported');self.assertTrue(again['modified_since_import']);self.assertEqual(digest(note),before)
  image=self.job/'bundle/visuals/images/frame.png';image.write_bytes(b'tampered')
  with self.assertRaisesRegex(ValueError,'BUNDLE_ARTIFACT_CHANGED'):cleanup.finish(self.job)
 def test_failed_import_never_deletes_media(self):
  with self.assertRaises(Exception):bundle.import_note(self.job,self.draft,'2026-10-05 物理',self.cfg)
  self.assertTrue((self.job/'source/video.mp4').exists())
  with self.assertRaisesRegex(ValueError,'SUCCESSFUL_IMPORT'):cleanup.finish(self.job)
 def test_cleanup_interruption_is_resumable(self):
  original=Path.unlink
  def interrupt(p,*a,**kw):
   if p.name=='audio1.wav':raise OSError('fixture interruption')
   return original(p,*a,**kw)
  with patch.object(Path,'unlink',interrupt):r=self.import_note()
  self.assertEqual(r['cleanup']['status'],'failed');self.assertTrue(Path(r['note']).exists())
  self.assertEqual(cleanup.finish(self.job)['status'],'complete');self.assertTrue(bundle.validate(self.job)['valid'])
 def test_external_audio_is_protected(self):
  outside=self.root/'original.wav';outside.write_bytes(b'user original')
  link=self.job/'source/link.wav';link.symlink_to(outside)
  r=self.import_note();self.assertEqual(r['cleanup']['status'],'failed');self.assertEqual(outside.read_bytes(),b'user original')
  link.unlink();cleanup.finish(self.job);self.assertEqual(outside.read_bytes(),b'user original')
 def test_legacy_manually_deleted_media_repair(self):
  with patch.object(cleanup,'automatic',return_value={'status':'fixture-retained'}):self.import_note()
  (self.job/'source/video.mp4').unlink()
  with self.assertRaisesRegex(ValueError,'SOURCE_MISSING'):cleanup.finish(self.job)
  r=cleanup.finish(self.job,repair_missing_media=True);self.assertEqual(r['status'],'complete')
  self.assertTrue(bundle.validate(self.job)['valid'])
  self.assertTrue(any(x.get('previously_removed') for x in read_json(self.job/'bundle/media-archive.json')['files']))

 def test_missing_note_after_cleanup_requires_new_acquisition(self):
  r=self.import_note();Path(r['note']).unlink()
  with self.assertRaisesRegex(ValueError,'MEDIA_ALREADY_CLEANED'):self.import_note()
 def test_asr_starts_before_visual_capture(self):
  import course_materials as cli
  from ocm import transcripts,visuals
  b=self.job/'bundle';(b/'transcripts/segments.jsonl').unlink()
  visual_bytes=(b/'visuals/index.jsonl').read_bytes();(b/'visuals/index.jsonl').unlink()
  state(self.job,source={**read_json(self.job/'state.json')['source'],'platform':'local'},cookie_file=None,max_bytes=99999,max_images=100,asr_allowed=True)
  events=[]
  def chunks(*args):atomic(self.job/'asr.json',{'blocks':[]})
  def asr(*args):events.append('asr');return {'status':'asr_running'}
  def capture(*args,**kwargs):events.append('capture');(b/'visuals/index.jsonl').write_bytes(visual_bytes)
  with patch.object(cli,'probe',return_value={'duration_ms':8000,'streams':[{'codec_type':'video'},{'codec_type':'audio'}]}),patch.object(transcripts,'prepare_chunks',chunks),patch.object(transcripts,'step_asr',asr),patch.object(visuals,'capture',capture):cli.worker(self.job)
  self.assertEqual(events,['asr','capture']);self.assertEqual(read_json(self.job/'state.json')['status'],'waiting_asr')

if __name__=='__main__':unittest.main(verbosity=2)
