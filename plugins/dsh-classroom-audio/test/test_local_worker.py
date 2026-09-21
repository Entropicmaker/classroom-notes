import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('local_worker', Path(__file__).resolve().parents[1] / 'bin/local-worker.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class LocalWorkerTests(unittest.TestCase):
    def test_preserves_numbers_punctuation_and_offset(self):
        value = {'offset': 25.0, 'duration': 10.0, 'text': '0.5克，不是5克。',
                 'segments': [{'start': .2, 'end': 4.0, 'text': '05克不是5克'}]}
        result = worker.timed_chunk(value)[0]
        self.assertEqual(result['text'], value['text'])
        self.assertEqual(result['begin_time'], 25200)
        self.assertEqual(result['end_time'], 29000)
        self.assertIsNone(result['speaker_id'])

    def test_rejects_missing_alignment(self):
        with self.assertRaisesRegex(ValueError, 'LOCAL_ALIGNMENT_MISSING'):
            worker.timed_chunk({'offset': 0, 'duration': 10, 'text': '注意', 'segments': []})

    def test_lock_does_not_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'original').write_text('untouched')
            (root / 'lock').symlink_to(root / 'original')
            with self.assertRaises(OSError):
                worker.open_lock(root / 'lock')
            self.assertEqual((root / 'original').read_text(), 'untouched')

    def test_bad_alignment_is_evidence_not_reusable_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / '00025.json'
            value = {'offset': 403.4, 'duration': 13, 'text': '岩盘、岩盖',
                     'segments': [{'start': 0, 'end': 95.44}]}
            with self.assertRaisesRegex(ValueError, 'INVALID_LOCAL_ALIGNMENT'):
                worker.checkpoint_chunk(checkpoint, value)
            self.assertFalse(checkpoint.exists())
            self.assertEqual(len(list(Path(directory).glob('00025.failed-*.json'))), 1)
            value['segments'] = [{'start': 0.1, 'end': 3}]
            result = worker.checkpoint_chunk(checkpoint, value)
            self.assertTrue(checkpoint.exists())
            self.assertEqual(result[0]['begin_time'], 403500)

    def test_nonfinite_alignment_is_rejected(self):
        for end in [float('nan'), float('inf')]:
            with self.assertRaisesRegex(ValueError, 'INVALID_LOCAL_ALIGNMENT'):
                worker.timed_chunk({'offset': 0, 'duration': 13, 'text': '岩盘',
                                    'segments': [{'start': 0, 'end': end}]})


if __name__ == '__main__':
    unittest.main()
