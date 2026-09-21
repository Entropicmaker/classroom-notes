#!/usr/bin/env python3
"""Offline, resumable long-audio worker. One GPU owner, no HTTP server or API key."""
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
import uuid
import wave


def dump(path, value):
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temp.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    temp.replace(path)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for data in iter(lambda: f.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def safe(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('UNSAFE_INPUT_FILE')
    return path


def open_lock(path):
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600), 'a')


def timed_chunk(result):
    """Keep ASR punctuation/numbers intact; aligner tokens may strip decimal points."""
    items, offset, limit = result['segments'], result['offset'], result['duration']
    for item in items:
        start, end = float(item['start']), float(item['end'])
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start or end > limit + .5:
            raise ValueError('INVALID_LOCAL_ALIGNMENT')
    if not result['text'].strip():
        return []
    if not items:
        raise ValueError('LOCAL_ALIGNMENT_MISSING')
    return [{'begin_time': round((offset + min(float(s['start']) for s in items)) * 1000),
             'end_time': round((offset + min(limit, max(float(s['end']) for s in items))) * 1000),
             'text': result['text'], 'speaker_id': None}]


def checkpoint_chunk(path, result):
    """Validate before marking a chunk reusable; preserve failures separately."""
    try:
        sentences = timed_chunk(result)
    except ValueError as exc:
        dump(path.with_name(path.stem + '.failed-' + uuid.uuid4().hex + '.json'),
             {'error': str(exc), 'result': result})
        raise
    dump(path, result)
    return sentences


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--job', required=True, type=Path)
    ap.add_argument('--models', required=True, type=Path)
    args = ap.parse_args()
    directory = args.job.resolve()
    if directory.name != args.job.name or not directory.is_dir():
        raise ValueError('INVALID_JOB_DIRECTORY')
    os.umask(0o077)
    job = json.loads(safe(directory / 'job.json').read_text())
    if job.get('engine') != 'local' or job.get('model') != 'Qwen3-ASR-1.7B-MLX':
        raise ValueError('NOT_A_LOCAL_JOB')
    state = {'pid': os.getpid(), 'status': 'QUEUED', 'audios': {}, 'started_at': time.time()}
    def update():
        state['updated_at'] = time.time()
        dump(directory / 'local-state.json', state)
    # A concurrent duplicate launcher exits without overwriting the active worker's state.
    job_lock = open_lock(directory / '.local-worker.lock')
    try:
        fcntl.flock(job_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    update()
    try:
        if job.get('vocabulary'):
            raise ValueError('LOCAL_CONTEXT_DISABLED: prepare a new local job without vocabulary; keep the old evidence')
        lockdir = args.models.parent / 'local-asr'
        if lockdir.is_symlink():
            raise ValueError('UNSAFE_LOCK_DIRECTORY')
        lockdir.mkdir(mode=0o700, exist_ok=True)
        with open_lock(lockdir / 'gpu.lock') as gpu_lock:
            fcntl.flock(gpu_lock, fcntl.LOCK_EX)
            state['status'] = 'LOADING_MODEL'
            update()
            # Model downloads are completed during installation. Runtime never contacts HF.
            os.environ['HF_HUB_OFFLINE'] = '1'
            import numpy as np
            import mlx.core as mx
            from mlx_qwen3_asr import Session
            from mlx_qwen3_asr.forced_aligner import ForcedAligner
            from mlx_qwen3_asr.chunking import split_audio_into_chunks
            session = Session(model=str(args.models / 'Qwen3-ASR-1.7B'))
            aligner = ForcedAligner(str(args.models / 'Qwen3-ForcedAligner-0.6B'))
            for audio in job['audios']:
                aid = audio['id']
                if aid not in ('audio1', 'audio2', 'audio3', 'audio4'):
                    raise ValueError('INVALID_AUDIO_ID')
                output = directory / (aid + '.local.raw.json')
                if output.exists():
                    safe(output)
                    state['audios'][aid] = {'status': 'DONE', 'progress': 1}
                    update()
                    continue
                source = safe(directory / (aid + '.wav'))
                if digest(source) != audio['prepared_sha256']:
                    raise ValueError('PREPARED_AUDIO_CHANGED')
                with wave.open(str(source), 'rb') as wav:
                    if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16000:
                        raise ValueError('INVALID_PREPARED_WAV')
                    samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2').astype(np.float32) / 32768.0
                chunks = split_audio_into_chunks(samples, sr=16000, max_chunk_sec=25)
                state['status'] = 'RUNNING'
                state['audios'][aid] = {'status': 'RUNNING', 'completed_chunks': 0, 'total_chunks': len(chunks), 'progress': 0}
                update()
                cache = directory / (aid + '.chunks')
                if cache.is_symlink():
                    raise ValueError('UNSAFE_CHUNK_DIRECTORY')
                cache.mkdir(mode=0o700, exist_ok=True)
                sentences, chunk_manifest = [], []
                for index, (chunk, offset) in enumerate(chunks):
                    checkpoint = cache / f'{index:05d}.json'
                    offset = float(offset)
                    duration = len(chunk) / 16000
                    if checkpoint.exists():
                        result = json.loads(safe(checkpoint).read_text())
                        if abs(result['offset'] - offset) > .001 or abs(result['duration'] - duration) > .001:
                            raise ValueError('CHUNK_CACHE_MISMATCH')
                    else:
                        # All time is retained. Only exact digital silence bypasses recognition.
                        if not np.any(chunk):
                            result = {'offset': offset, 'duration': duration, 'text': '', 'segments': [], 'digital_silence': True}
                        else:
                            transcribed = session.transcribe((chunk, 16000), context='', language='Chinese',
                                                            return_timestamps=True, forced_aligner=aligner,
                                                            max_new_tokens=1024, return_chunks=True)
                            if transcribed.truncated:
                                raise ValueError('LOCAL_TRANSCRIPT_TRUNCATED')
                            result = {'offset': offset, 'duration': duration, 'text': transcribed.text,
                                      'segments': transcribed.segments or [], 'digital_silence': False}
                            if transcribed.text.strip() and not result['segments']:
                                raise ValueError('LOCAL_ALIGNMENT_MISSING')
                        checkpoint_chunk(checkpoint, result)
                    sentences.extend(timed_chunk(result))
                    chunk_manifest.append({'index': index, 'offset': offset, 'duration': duration,
                                           'text': result['text'], 'digital_silence': result['digital_silence']})
                    state['audios'][aid].update(completed_chunks=index + 1, progress=(index + 1) / len(chunks))
                    update()
                    mx.clear_cache()
                raw = {'properties': {'original_duration_in_milliseconds': audio['duration_ms'],
                                      'engine': job['model'], 'offline': True, 'speaker_diarization': False},
                       'transcripts': [{'channel_id': 0, 'sentences': sentences}], 'chunks': chunk_manifest}
                dump(output, raw)
                output.chmod(0o400)
                state['audios'][aid] = {'status': 'DONE', 'progress': 1, 'completed_chunks': len(chunks), 'total_chunks': len(chunks)}
                update()
                del samples, chunks
                mx.clear_cache()
            state['status'] = 'DONE'
            update()
    except BaseException as exc:
        state['status'] = 'FAILED'
        state['error'] = f'{type(exc).__name__}: {str(exc)[:400]}'
        update()
        raise
    finally:
        job_lock.close()


if __name__ == '__main__':
    main()
