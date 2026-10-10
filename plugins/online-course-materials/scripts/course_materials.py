#!/usr/bin/env python3
"""DSH course acquisition entrypoint. No model keys or cloud calls here."""
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
if sys.version_info < (3, 10):
    candidate = Path.home() / '.dsh/vendor/dsh-classroom-audio/.venv/bin/python'
    if not candidate.is_file():
        sys.exit('PYTHON_310_REQUIRED: 请使用已有 DSH 本地模型 Python 运行环境。')
    os.execv(str(candidate), [str(candidate), str(Path(__file__).resolve()), *sys.argv[1:]])
sys.path.insert(0, str(HERE / 'vendor'))

import argparse
import importlib.util
import json
import signal
import shutil
import subprocess
import time
from ocm import acquire, bundle, transcripts, storage, cleanup, reviewing
from ocm.common import VERSION, atomic, digest, key, lock, now, probe, read_json, read_lines, redact, safe_path, state, tools


def doctor():
    try:
        audio = transcripts.audio_call(Path.cwd(), {'action': 'doctor'})
    except Exception as e:
        audio = {'error': redact(e), 'local': {'ready': False}}
    return {'version': VERSION, 'python': sys.executable, 'tools': tools(),
            'yt_dlp': bool(importlib.util.find_spec('yt_dlp')), 'pillow': bool(importlib.util.find_spec('PIL')),
            'local_audio': audio, 'global_model': 'user_configured_primary_model', 'model_api_calls': False,
            'image_transport_verified': False, 'storage': storage.settings()}


def prepare(args):
    root = Path(args.root or storage.settings()['materials_root']).expanduser().resolve()
    if not root.is_dir() or root == Path.home() or root == Path('/'):
        raise ValueError('USE_A_COURSE_WORKSPACE')
    series = storage.component(args.series or args.course)
    study_date = args.date or now()[:10]
    if args.local_video:
        path = Path(args.local_video).resolve()
        p = probe(path)
        if not any(x.get('codec_type') == 'video' for x in p['streams']):
            raise ValueError('VIDEO_STREAM_REQUIRED')
        sources = [{'platform': 'local', 'sha256': digest(path), 'title': path.stem,
                    'part_title': path.stem, 'page_number': 1, 'duration_ms': p['duration_ms'],
                    'canonical_url': None}]
    else:
        sources = acquire.resolve(args.url, args.pages, args.cookies)
    if len(sources) > args.max_parts or sum(x['duration_ms'] for x in sources) > args.max_minutes * 60000:
        raise ValueError('COURSE_BATCH_LIMIT: 超出明确批次上限，增加限额或指定分P范围。')
    results = []
    for source in sources:
        identity = {k: source[k] for k in ('platform', 'bvid', 'cid', 'sha256') if k in source}
        identity['selected_range_ms'] = [0, source['duration_ms']]
        identity['schema'] = 'online-source/1'
        source_key = key(identity)
        existing = bundle.find_import(root, source_key, args.course)
        if existing and existing['exists'] and not args.new_version:
            results.append({'status': 'already_imported', **existing})
            continue
        job, job_id, series = storage.allocate(root, source, source_key, args.course, series, study_date, args.new_version)
        with lock(job):
            if (job / 'state.json').exists():
                old = read_json(job / 'state.json')
                if old['course']['name'] != args.course:
                    raise ValueError('SOURCE_ALREADY_ASSIGNED_TO_OTHER_COURSE: 使用 --new-version 明确另建。')
                results.append({'status': old['status'], 'job': str(job), 'source': source})
                continue
            b = job / 'bundle'
            (b / 'transcripts/raw').mkdir(parents=True, exist_ok=True)
            (b / 'visuals').mkdir(parents=True, exist_ok=True)
            atomic(b / 'source-ref.json', identity)
            original = None
            if args.local_video:
                original = job / ('source/video' + path.suffix.lower())
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, original)
            subtitle = None
            if args.subtitle:
                sp = Path(args.subtitle).resolve()
                subtitle = b / ('transcripts/raw/provided-subtitle' + sp.suffix.lower())
                shutil.copyfile(sp, subtitle)
            atomic(job / 'state.json', {
                'id': job_id, 'source_key': source_key, 'root': str(root), 'source': source,
                'storage': {'materials_root': str(root), 'series': series, 'lesson_path': str(job), 'retain_after_import': 'essential'},
                'course': {'name': args.course, 'study_date': study_date,
                           'date_basis': '用户指定' if args.date else '未提供上课日期，采用整理日期'},
                'status': 'prepared', 'phase': 'prepared', 'created_at': now(), 'updated_at': now(),
                'media': str(original.relative_to(job)) if original else None,
                'provided_subtitle': str(subtitle.relative_to(job)) if subtitle else None,
                'cookie_file': str(Path(args.cookies).resolve()) if args.cookies else None,
                'max_images': args.max_images, 'max_bytes': args.max_gib * 1024**3,
                'event_cooldown_ms': args.event_cooldown_ms,
                'import_status': 'not_started', 'pid': None, 'asr_allowed': args.asr,
            })
        results.append({'status': 'prepared', 'job': str(job), 'source': source})
    return {'jobs': results, 'next': 'start --job 路径；status 查看进度'}


def worker(job):
    from ocm.visuals import capture
    with lock(job, wait=True):
        s = read_json(job / 'state.json')
        try:
            state(job, status='running', pid=os.getpid(), phase='acquire', error=None)
            media = safe_path(job, s['media']) if s.get('media') else None
            if media is None or not media.is_file():
                media = acquire.download(s['source'], job, s['cookie_file'], s['max_bytes'])
                state(job, media=str(media.relative_to(job)))
            info = probe(media)
            audio_present = any(x.get('codec_type') == 'audio' for x in info['streams'])
            state(job, audio_track_present=audio_present)
            if abs(info['duration_ms'] - s['source']['duration_ms']) > max(2000, s['source']['duration_ms'] * .005):
                raise ValueError('MEDIA_DURATION_MISMATCH')
            media_hash = digest(media)
            expected_hash = s.get('media_sha256') or (s['source'].get('sha256') if s['source']['platform'] == 'local' else None)
            if expected_hash and media_hash != expected_hash:
                raise ValueError('MEDIA_BYTES_CHANGED: 原视频变化，不能复用同一素材身份。')
            state(job, media_sha256=media_hash)
            # Start speech work first; the independent ASR worker runs during image capture.
            if not (job / 'bundle/transcripts/segments.jsonl').is_file():
                if s.get('provided_subtitle'):
                    rows = transcripts.parse_subtitle(safe_path(job, s['provided_subtitle']), s['source']['duration_ms'])
                    kind = 'provided_subtitle'
                elif s['source']['platform'] == 'bilibili':
                    try:
                        rows, kind = acquire.subtitles(s['source'], job, s['cookie_file'])
                    except ValueError as e:
                        rows, kind = [], 'subtitle_acquisition_failed'
                        state(job, subtitle_error=redact(e))
                else:
                    rows, kind = [], 'no_subtitle'
                transcripts.publish(job, rows, kind)
                state(job, speech_source=kind)
            else:
                rows = list(read_lines(job / 'bundle/transcripts/segments.jsonl'))
            if not rows and audio_present:
                if not (job / 'asr.json').exists():
                    transcripts.prepare_chunks(job, media)
                if s.get('asr_allowed'):
                    transcripts.step_asr(job)
            if not (job / 'bundle/visuals/index.jsonl').is_file():
                capture(media, job, s['source']['duration_ms'], s['max_images'],
                        event_cooldown_ms=s.get('event_cooldown_ms', 350))
            if rows or not audio_present:
                state(job, status='ready_for_review', phase='review')
            elif (job / 'asr.json').exists():
                state(job, status='waiting_asr', phase='waiting_asr')
            else:
                state(job, status='partial', phase='transcript_missing', error='AUDIO_TRANSCRIPT_MISSING')
            bundle.publish_manifest(job)
        except Exception as e:
            state(job, status='blocked' if '412' in str(e) else 'failed', phase='acquire_blocked' if '412' in str(e) else 'failed', error=redact(e))
            raise


def alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def start(job, foreground=False):
    with lock(job):
        s = read_json(job / 'state.json')
        if s['status'] in ('ready_for_review', 'reviewed', 'waiting_asr', 'imported'):
            return {'status': s['status'], 'job': str(job)}
        if alive(s.get('pid')):
            return {'status': 'running', 'pid': s['pid']}
        if not foreground:
            log = safe_path(job, 'worker.log')
            with log.open('ab') as f:
                p = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'worker', '--job', str(job)],
                    cwd=job, stdout=f, stderr=f, start_new_session=True)
            state(job, status='running', pid=p.pid, phase='starting')
            return {'status': 'running', 'pid': p.pid, 'job': str(job)}
    worker(job)
    return status(job)


def status(job):
    s = read_json(job / 'state.json')
    result = {k: v for k, v in s.items() if k not in ('cookie_file',)}
    if s['status'] == 'running' and not alive(s.get('pid')):
        result['status'] = 'interrupted'
        result['next'] = 'start 复用已保存媒体与完整阶段，重新执行未完成阶段'
    m = job / 'bundle/manifest.json'
    if m.exists():
        data = read_json(m)
        result.update(bundle=str(m.parent), counts=data['counts'], handoff_status=data['handoff_status'])
    return result


def page(job, kind, offset, limit):
    allowed = {'transcript': 'transcripts/segments.jsonl', 'visuals': 'visuals/index.jsonl', 'coverage': 'coverage.jsonl', 'alignment': 'alignment.jsonl'}
    rows = list(read_lines(job / 'bundle' / allowed[kind]))
    if offset < 0 or not 1 <= limit <= 200:
        raise ValueError('INVALID_PAGE')
    end = min(len(rows), offset + limit)
    return {'kind': kind, 'total': len(rows), 'offset': offset,
            'next_offset': end if end < len(rows) else None, 'records': rows[offset:end]}


def cancel(job):
    s = read_json(job / 'state.json')
    pid = s.get('pid')
    if alive(pid):
        # Verify ownership to avoid signalling a reused unrelated PID.
        command = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True).stdout
        if str(Path(__file__).resolve()) in command and str(job) in command:
            os.killpg(pid, signal.SIGTERM)
    state(job, status='cancelled', phase='cancelled')
    return {'status': 'cancelled', 'existing_asr_not_killed': (job / 'asr.json').exists(),
            'note': '采集任务停止；已启动的独立课堂ASR不误杀，保留任务与素材。'}

def parser():
    p = argparse.ArgumentParser(description='B站课程素材采集与现有课堂笔记交接（本地优先）')
    sub = p.add_subparsers(dest='action', required=True)
    sub.add_parser('doctor')
    prepare_p = sub.add_parser('prepare')
    src = prepare_p.add_mutually_exclusive_group(required=True)
    src.add_argument('--url'); src.add_argument('--local-video')
    prepare_p.add_argument('--root', help='覆盖素材根目录，仅隔离测试或用户明确改路径时使用'); prepare_p.add_argument('--course', required=True)
    prepare_p.add_argument('--series', help='单门视频课程的大文件夹名；默认使用course，已有文件夹可复用')
    prepare_p.add_argument('--pages'); prepare_p.add_argument('--date')
    prepare_p.add_argument('--cookies'); prepare_p.add_argument('--subtitle')
    prepare_p.add_argument('--asr', action='store_true', help='授权由此CLI衔接已有本地ASR，仍不调用云端')
    prepare_p.add_argument('--new-version', action='store_true')
    prepare_p.add_argument('--max-parts', type=int, default=20)
    prepare_p.add_argument('--max-minutes', type=int, default=240)
    prepare_p.add_argument('--max-gib', type=int, default=4)
    prepare_p.add_argument('--max-images', type=int, default=2000)
    prepare_p.add_argument('--event-cooldown-ms', type=int, default=350, help='全幅事件间隔，记录采样限制；不按视频类型固定ROI')
    for name in ['start', 'worker', 'status', 'asr', 'validate', 'read', 'review', 'import-note', 'snapshot', 'cancel', 'cleanup',
                 'review-plan', 'review-next', 'review-save', 'review-detail', 'review-export']:
        a = sub.add_parser(name); a.add_argument('--job', type=Path, required=True)
        if name == 'start':
            a.add_argument('--foreground', action='store_true')
            a.add_argument('--max-images', type=int, help='显式覆盖本地截图额度；超额事件仍留全片索引，不自动扩额')
            a.add_argument('--event-cooldown-ms', type=int, help='显式覆盖采集间隔，默认350ms')
        elif name == 'asr':
            a.add_argument('--force', action='store_true', help='有字幕仍转写全程，保留原字幕')
        elif name == 'read':
            a.add_argument('--kind', choices=['transcript', 'visuals', 'coverage', 'alignment'], default='transcript')
            a.add_argument('--offset', type=int, default=0); a.add_argument('--limit', type=int, default=60)
        elif name == 'review':
            a.add_argument('--file', type=Path, required=True)
        elif name == 'import-note':
            a.add_argument('--note', type=Path, required=True); a.add_argument('--title', required=True)
            a.add_argument('--config', type=Path); a.add_argument('--new-version', action='store_true')
            a.add_argument('--custom-title', action='store_true', help='仅用于用户明确指定的其他文件名')
        elif name == 'cleanup':
            a.add_argument('--repair-missing-media', action='store_true', help='修复旧流程在成功入库后手动删除原视频的记录')
        elif name == 'snapshot':
            a.add_argument('--time', type=float, required=True)
            a.add_argument('--purpose', required=True, help='需要核实的内容或动画转折')
        elif name == 'review-save':
            a.add_argument('--file', type=Path, required=True)
        elif name == 'review-detail':
            a.add_argument('--id', required=True); a.add_argument('--purpose', required=True)
            a.add_argument('--box', help='原图像素坐标x1,y1,x2,y2；先读原图，再有界裁切')
        elif name == 'review-export':
            a.add_argument('--note', type=Path, required=True)
            a.add_argument('--gap-reviews', type=Path, required=True)
            a.add_argument('--note-reviewed', action='store_true')
    return p


def main():
    args = parser().parse_args()
    if args.action == 'doctor':
        return doctor()
    if args.action == 'prepare':
        if args.date:
            from datetime import date
            date.fromisoformat(args.date)
        if min(args.max_parts, args.max_minutes, args.max_gib, args.max_images, args.event_cooldown_ms) <= 0:
            raise ValueError('BUDGET_MUST_BE_POSITIVE')
        return prepare(args)
    job = args.job.resolve()
    if not (job / 'state.json').is_file():
        raise ValueError('JOB_NOT_FOUND')
    if args.action == 'status':
        return status(job)
    if args.action == 'start':
        with lock(job):
            changes = {k: getattr(args, k) for k in ('max_images', 'event_cooldown_ms') if getattr(args, k) is not None}
            if any(v <= 0 for v in changes.values()):
                raise ValueError('BUDGET_MUST_BE_POSITIVE')
            if changes:
                state(job, **changes)
        return start(job, args.foreground)
    if args.action == 'worker':
        return worker(job) or status(job)
    if args.action == 'read':
        return page(job, args.kind, args.offset, args.limit)
    if args.action == 'cancel':
        return cancel(job)
    with lock(job):
        s = read_json(job / 'state.json')
        if args.action == 'cleanup':
            return cleanup.finish(job, args.repair_missing_media)
        if args.action == 'review-plan':
            p = reviewing.plan(job)
            return {'status': 'planned', 'plan': str(reviewing.paths(job)[2]), 'visuals': p['visual_count'],
                    'preview_batches': len(p['batches']), 'limits': p['limits'],
                    'sampling_windows': p['sampling_windows'], 'next': 'review-next'}
        if args.action == 'review-next':
            return reviewing.next_item(job)
        if args.action == 'review-save':
            return reviewing.save(job, args.file)
        if args.action == 'review-detail':
            box = [int(x) for x in args.box.split(',')] if args.box else None
            if box and len(box) != 4:
                raise ValueError('INVALID_CROP_BOX')
            return reviewing.detail(job, args.id, args.purpose, box)
        if args.action == 'review-export':
            return reviewing.export(job, args.note, args.gap_reviews, args.note_reviewed)
        if s.get('media_archived') and args.action in ('asr', 'snapshot', 'review'):
            raise ValueError('MEDIA_ALREADY_CLEANED: 原媒体已删除；补图、重转写或重新入库需 prepare --new-version')
        if args.action == 'asr':
            if not s['asr_allowed']:
                raise ValueError('LOCAL_ASR_NOT_ENABLED: prepare 时使用 --asr，或使用 DSH 已授权的 classroom_audio 工具')
            if args.force and not (job / 'asr.json').exists():
                prior = job / 'bundle/review.json'
                if prior.exists():
                    prior.replace(job / f'review-before-asr-{time.time_ns()}.json')
                state(job, status='waiting_asr', phase='waiting_asr')
                transcripts.prepare_chunks(job, safe_path(job, s['media']))
            if not (job / 'asr.json').exists():
                return {'status': 'subtitle_available', 'next': '仅疑难缺段复核时使用 --force'}
            result = transcripts.step_asr(job)
            if result['status'] == 'asr_complete':
                # All generated ASR blocks now share the original video's time base.
                state(job, status='ready_for_review', speech_source='local_asr', phase='review')
                bundle.publish_manifest(job)
            return result
        if args.action == 'validate':
            return bundle.validate(job)
        if args.action == 'review':
            result = bundle.review(job, args.file)
            state(job, status='reviewed')
            return {'status': result['handoff_status'], 'counts': result['counts']}
        if args.action == 'import-note':
            return bundle.import_note(job, args.note, args.title, args.config, args.new_version, args.custom_title)
        if args.action == 'snapshot':
            from ocm.visuals import screenshot
            ms = round(args.time * 1000)
            if not 0 <= ms < s['source']['duration_ms']:
                raise ValueError('SNAPSHOT_TIME_OUTSIDE_VIDEO')
            reviewing.reserve_supplement(job, ms, args.purpose)
            path = job / f'bundle/visuals/images/supplement-{ms:010d}.png'
            if not path.exists():
                screenshot(safe_path(job, s['media']), ms, path)
            index = list(read_lines(job / 'bundle/visuals/index.jsonl'))
            vid = f'supplement-{ms:010d}'
            if not any(v['id'] == vid for v in index):
                index.append({'id': vid, 'path': str(path.relative_to(job / 'bundle')), 'sha256': digest(path),
                              'time_ms': ms, 'occurrences_ms': [ms], 'reason': 'supplement',
                              'scene': 'unreviewed', 'review_status': 'not_viewed', 'original_frame': True})
                from ocm.common import write_lines
                write_lines(job / 'bundle/visuals/index.jsonl', index)
            # Reopen review after the material set changes.
            review = job / 'bundle/review.json'
            if review.exists():
                review.replace(job / f'review-before-supplement-{time.time_ns()}.json')
            bundle.publish_manifest(job)
            state(job, status='ready_for_review')
            return {'file': str(path), 'next': '复核新增图并重新提交 review'}



if __name__ == '__main__':
    try:
        print(json.dumps(main(), ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({'status': 'error', 'error': redact(e)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
