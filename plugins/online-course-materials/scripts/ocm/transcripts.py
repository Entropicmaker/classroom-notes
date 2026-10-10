"""Subtitle normalization and bounded calls to the existing local ASR."""
import json
from pathlib import Path
import re
from .common import atomic, gaps, probe, read_json, run, safe_path, stamp, tools, write_lines

BRIDGE = Path(__file__).resolve().parents[1] / 'audio_bridge.mjs'


def audio_call(job, args):
    return json.loads(run(['node', BRIDGE, json.dumps(args)], cwd=job, timeout=600))


def parse_subtitle(path, duration_ms):
    text = Path(path).read_text(encoding='utf-8-sig')
    if Path(path).suffix.lower() == '.json':
        d = json.loads(text)
        if isinstance(d, dict) and 'body' in d:
            rows = [{'begin_ms': round(float(x['from']) * 1000), 'end_ms': round(float(x['to']) * 1000), 'text': x['content']} for x in d['body']]
        elif isinstance(d, dict) and 'segments' in d:
            rows = d['segments']
        elif isinstance(d, list):
            rows = d
        else:
            raise ValueError('UNSUPPORTED_SUBTITLE_JSON')
    else:
        rows = []
        pattern = r'(\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3})[^\n]*\n(.*?)(?=\n\s*\n|\Z)'
        def millis(s):
            pieces = s.replace(',', '.').split(':')
            return round(sum(float(x) * 60**i for i, x in enumerate(reversed(pieces))) * 1000)
        for m in re.finditer(pattern, text, re.S):
            rows.append({'begin_ms': millis(m[1]), 'end_ms': millis(m[2]),
                         'text': re.sub(r'<[^>]+>', '', m[3]).strip()})
    normalized = []
    for i, row in enumerate(rows):
        b, e = row.get('begin_ms'), row.get('end_ms')
        if b is None:
            b, e = round(float(row['start']) * 1000), round(float(row['end']) * 1000)
        if not isinstance(b, (int, float)) or not isinstance(e, (int, float)) or b < 0 or e <= b or e > duration_ms + 2000:
            raise ValueError('INVALID_SUBTITLE_TIMESTAMPS')
        if not isinstance(row.get('text'), str):
            raise ValueError('INVALID_SUBTITLE_TEXT')
        if row['text'].strip():
            normalized.append({'id': f'provided-{i:06d}', 'begin_ms': round(b),
                'end_ms': min(round(e), duration_ms), 'text': row['text'],
                'source_type': 'provided_subtitle', 'speaker_id': row.get('speaker_id'),
                'review_status': 'automatic_only', 'raw_reference': 'raw/provided-subtitle' + Path(path).suffix})
    return sorted(normalized, key=lambda x: (x['begin_ms'], x['end_ms']))


def publish(job, segments, kind):
    s = read_json(Path(job) / 'state.json')
    bundle = Path(job) / 'bundle'
    duration = s['source']['duration_ms']
    for x in segments:
        if x['begin_ms'] < 0 or x['end_ms'] <= x['begin_ms'] or x['end_ms'] > duration:
            raise ValueError('TRANSCRIPT_OUTSIDE_VIDEO')
    write_lines(bundle / 'transcripts/segments.jsonl', segments)
    atomic(bundle / 'transcripts/transcript.md', '\n\n'.join(
        f"[{stamp(x['begin_ms'])}–{stamp(x['end_ms'])}] ({x['id']}) {x['text']}" for x in segments) + '\n')
    atomic(bundle / 'transcripts/coverage.json', {'source_type': kind, 'duration_ms': duration,
           'segments': len(segments), 'gaps': gaps(segments, duration),
           'listening_review': 'not_performed'})
    return gaps(segments, duration)


def prepare_chunks(job, video):
    state = read_json(Path(job) / 'state.json')
    duration = state['source']['duration_ms']
    audio = Path(job) / 'derived/video-audio-aligned-v1.m4a'
    audio.parent.mkdir(parents=True, exist_ok=True)
    if not audio.exists():
        run([tools()['ffmpeg'], '-nostdin', '-v', 'error', '-y', '-i', video,
             '-map', '0:a:0', '-vn', '-af', 'aresample=async=1:first_pts=0,apad',
             '-t', f'{duration / 1000:.3f}', '-c:a', 'aac', '-b:a', '128k', audio], timeout=max(120, duration / 1000))
    if abs(probe(audio)['duration_ms'] - duration) > 2000:
        raise ValueError('EXTRACTED_AUDIO_DURATION_MISMATCH')
    blocks = []
    for i, begin in enumerate(range(0, duration, 90 * 60000)):
        end = min(duration, begin + 90 * 60000)
        dest = Path(job) / f'derived/audio-{i:03d}.wav'
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            run([tools()['ffmpeg'], '-nostdin', '-v', 'error', '-y', '-i', audio,
                 '-ss', f'{begin / 1000:.3f}', '-t', f'{(end - begin) / 1000:.3f}',
                 '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', dest], timeout=max(120, (end - begin) / 1000))
        if abs(probe(dest)['duration_ms'] - (end - begin)) > 1000:
            raise ValueError('AUDIO_CHUNK_DURATION_MISMATCH')
        blocks.append({'index': i, 'begin_ms': begin, 'end_ms': end,
                       'path': str(dest.relative_to(job)), 'status': 'pending', 'job_id': None})
    atomic(Path(job) / 'asr.json', {'blocks': blocks, 'engine': 'local', 'model': 'Qwen3-ASR-1.7B-MLX',
           'audio_is_video_derived': True, 'boundary_review': 'required'})
    return blocks


def step_asr(job):
    p = Path(job) / 'asr.json'
    data = read_json(p)
    for block in data['blocks']:
        if block['status'] == 'done':
            continue
        if not block['job_id']:
            result = audio_call(job, {'action': 'prepare', 'files': [str(safe_path(job, block['path']))]})
            block['job_id'] = result['job_id']
            block['plugin_directory'] = result['directory']
            block['status'] = 'prepared'
            atomic(p, data)
        # Poll first: an INTERRUPTED job is the only already-started state to resume.
        result = audio_call(job, {'action': 'poll', 'job_id': block['job_id']})
        a = result['audios'][0]
        if a['status'] in ('PREPARED', 'INTERRUPTED'):
            audio_call(job, {'action': 'start', 'job_id': block['job_id']})
            block['status'] = 'running'
            atomic(p, data)
            return {'status': 'asr_running', 'block': block['index'], 'audio': a}
        if a['status'] == 'FAILED':
            block['status'], block['error'] = 'failed', a.get('error')
            atomic(p, data)
            raise ValueError('LOCAL_ASR_FAILED: ' + str(a.get('error')))
        if a['status'] != 'DONE':
            return {'status': 'asr_running', 'block': block['index'], 'audio': a}
        rows, offset = [], 0
        while offset is not None:
            page = audio_call(job, {'action': 'read', 'job_id': block['job_id'], 'audio_id': 'audio1', 'offset': offset, 'limit': 60})
            rows.extend(page['segments'])
            offset = page['next_offset']
        atomic(Path(job) / f"bundle/transcripts/raw/asr-block-{block['index']:03d}.json", rows)
        block['status'] = 'done'
        atomic(p, data)
        # Finish one block per invocation so the Agent can process files in between calls.
        return {'status': 'asr_block_done', 'block': block['index'], 'next': '再次调用 asr 处理下一块或发布全部转写'}
    segments = []
    for block in data['blocks']:
        rows = read_json(Path(job) / f"bundle/transcripts/raw/asr-block-{block['index']:03d}.json")
        for i, x in enumerate(rows):
            segments.append({'id': f"asr-{block['index']:03d}-{i:06d}",
                'begin_ms': block['begin_ms'] + x['begin_ms'],
                'end_ms': min(block['end_ms'], block['begin_ms'] + x['end_ms']),
                'text': x['text'], 'speaker_id': None, 'source_type': 'local_asr',
                'review_status': 'automatic_only', 'raw_reference': f"raw/asr-block-{block['index']:03d}.json#{i}"})
    publish(job, segments, 'local_asr')
    return {'status': 'asr_complete', 'segments': len(segments)}
