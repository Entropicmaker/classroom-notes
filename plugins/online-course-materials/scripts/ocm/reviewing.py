"""Bounded, resumable visual review. No model, OCR or semantic classification."""
import math
from pathlib import Path
from PIL import Image, ImageDraw
from .common import atomic, digest, key, now, read_json, read_lines, safe_path

SCENES = {'ppt', 'board', 'demo', 'animation', 'diagram', 'footage',
          'talking_head', 'mixed', 'nonteaching', 'unknown'}
STATUSES = {'previewed', 'viewed', 'duplicate', 'unreadable', 'unresolved', 'needs_detail'}


def limits(duration_ms):
    minutes = duration_ms / 60000
    return {'detail_images': min(180, max(24, math.ceil(minutes * 1.2))),
            'crops': min(60, max(12, math.ceil(minutes / 2))),
            'crops_per_visual': 2,
            'supplements': min(48, max(8, math.ceil(minutes / 3))),
            'tiles_per_batch': 8, 'batch_span_ms': 120000}


def paths(job):
    job = Path(job)
    work = safe_path(job, 'draft/visual-review')
    return job / 'bundle', work, work / 'plan.json', work / 'progress.json'


def source_rows(job):
    return list(read_lines(Path(job) / 'bundle/visuals/index.jsonl'))


def fingerprint(rows):
    return key([{'id': r['id'], 'sha256': r['sha256'],
                 'occurrences_ms': r['occurrences_ms']} for r in rows])


def plan(job):
    b, work, plan_file, progress_file = paths(job)
    rows = source_rows(job)
    fp = fingerprint(rows)
    if plan_file.exists() and read_json(plan_file)['source_fingerprint'] == fp:
        return read_json(plan_file)
    work.mkdir(parents=True, exist_ok=True)
    s = read_json(Path(job) / 'state.json')
    budget = limits(s['source']['duration_ms'])
    # Occurrences, not unique SHA order: A -> B -> A must remain a sequence.
    occurrences = sorted((t, r['id']) for r in rows for t in r['occurrences_ms'])
    indexed = {r['id']: r for r in rows}
    groups, group = [], []
    for t, vid in occurrences:
        if group and (len(group) >= budget['tiles_per_batch'] or t // budget['batch_span_ms'] != group[0]['time_ms'] // budget['batch_span_ms']):
            groups.append(group); group = []
        group.append({'id': vid, 'time_ms': t})
    if group:
        groups.append(group)
    batches = []
    for number, group in enumerate(groups):
        batch_id = 'batch-' + key([(r['id'], r['time_ms'], indexed[r['id']]['sha256']) for r in group])[:16]
        dest = work / f'preview-{batch_id}.jpg'
        batch = {'id': batch_id, 'image': str(dest),
                 'begin_ms': group[0]['time_ms'], 'end_ms': group[-1]['time_ms'], 'occurrences': group}
        batches.append(batch)
        if dest.exists():
            continue
        # 640px tiles are useful for scene/trajectory review. Dense text still
        # requires the full image; do not label these as full-resolution review.
        sheet = Image.new('RGB', (1280, math.ceil(len(group) / 2) * 390), 'white')
        draw = ImageDraw.Draw(sheet)
        for i, r in enumerate(group):
            v = indexed[r['id']]
            original = safe_path(b, v['path'])
            if digest(original) != v['sha256']:
                raise ValueError('REVIEW_SOURCE_CHANGED: ' + v['id'])
            with Image.open(original) as img:
                img = img.convert('RGB'); img.thumbnail((636, 356))
                x, y = (i % 2) * 640, (i // 2) * 390
                sheet.paste(img, (x + (640 - img.width) // 2, y))
            draw.text((x + 4, y + 360), f"{r['id']}  {r['time_ms'] / 1000:.3f}s", fill='black')
        sheet.save(dest, quality=90)
    scan_file = b / 'visuals/scan.json'
    scan = read_json(scan_file) if scan_file.exists() else {}
    result = {'schema': 'visual-review/1', 'source_fingerprint': fp, 'limits': budget,
              'visual_count': len(rows), 'occurrence_count': len(occurrences), 'batches': batches,
              'sampling_windows': scan.get('sampling_windows', []),
              'capture_warning': scan.get('warning'), 'created_at': now()}
    old = read_json(progress_file) if progress_file.exists() else {}
    # Stable time-window groups prevent one supplemental frame from invalidating
    # every later batch. Recheck only groups with a changed sequence.
    progress = {'source_fingerprint': fp, 'visuals': {}, 'batches': {},
                'requests': old.get('requests', {}), 'supplements': old.get('supplements', {}),
                'sampling_reviews': old.get('sampling_reviews', [])}
    for batch in batches:
        if batch['id'] in old.get('batches', {}):
            progress['batches'][batch['id']] = old['batches'][batch['id']]
    for r in rows:
        previous = old.get('visuals', {}).get(r['id'])
        if previous and previous.get('source_sha256') == r['sha256']:
            progress['visuals'][r['id']] = previous
    atomic(plan_file, result); atomic(progress_file, progress)
    return result


def load(job):
    p = plan(job)
    return p, read_json(paths(job)[3])


def counts(progress):
    reqs = progress.get('requests', {}).values()
    return {'detail_images': sum(r['kind'] == 'full' for r in reqs),
            'crops': sum(r['kind'] == 'crop' for r in reqs),
            'supplements': len(progress.get('supplements', {}))}


def next_item(job):
    p, progress = load(job)
    result = {'limits': p['limits'], 'used': counts(progress),
              'visuals_completed': sum(r['review_status'] != 'needs_detail' for r in progress['visuals'].values()),
              'visuals_total': p['visual_count'], 'batches_total': len(p['batches'])}
    for batch in p['batches']:
        if batch['id'] not in progress['batches']:
            result.update(status='preview_pending', batch=batch,
                          next='实际看这张顺序画面组，提交各ID的scene/status/note和sequence_note；细字或关键关系用needs_detail')
            # Only local corresponding text, not the entire cumulative transcript.
            result['transcript'] = [r for r in read_lines(Path(job) / 'bundle/transcripts/segments.jsonl')
                if r['begin_ms'] <= batch['end_ms'] + 3000 and r['end_ms'] >= batch['begin_ms'] - 3000]
            result['previously_reviewed'] = {r['id']: progress['visuals'][r['id']]
                for r in batch['occurrences'] if r['id'] in progress['visuals']}
            result['sampling_windows'] = [w for w in p['sampling_windows']
                if w['begin_ms'] <= batch['end_ms'] + 3000 and w['end_ms'] >= batch['begin_ms'] - 3000]
            return result
    # See the whole video before spending the scarce full-image allowance.
    for batch in p['batches']:
        for r in batch['occurrences']:
            v = progress['visuals'].get(r['id'])
            if v and v['review_status'] == 'needs_detail':
                return {**result, 'status': 'detail_pending', 'visual': v,
                        'next': 'review-detail --id ID --purpose 具体疑点；查看返回原图后review-save更新该ID'}
    missing = [r['id'] for r in source_rows(job) if r['id'] not in progress['visuals']]
    if missing:
        return {**result, 'status': 'incomplete', 'missing': missing}
    return {**result, 'status': 'visual_review_checkpointed',
            'sampling_windows': p['sampling_windows'], 'sampling_reviews': progress['sampling_reviews'],
            'next': '完成成稿/缺口复核后review-export；不能把previewed称为逐张原图精读'}


def save(job, file):
    p, progress = load(job)
    d = read_json(file)
    rows = {r['id']: r for r in source_rows(job)}
    batch_id = d.get('batch_id')
    batch = next((b for b in p['batches'] if b['id'] == batch_id), None) if batch_id else None
    supplied = d.get('visuals', [])
    if not isinstance(supplied, list) or len({r['id'] for r in supplied}) != len(supplied):
        raise ValueError('INVALID_REVIEW_CHECKPOINT')
    if batch_id and (not batch or {r['id'] for r in supplied} != {r['id'] for r in batch['occurrences']} or not d.get('sequence_note')):
        raise ValueError('BATCH_REQUIRES_ALL_IDS_AND_SEQUENCE_NOTE')
    if not batch_id and not supplied and 'sampling_reviews' not in d:
        raise ValueError('EMPTY_REVIEW_CHECKPOINT')
    for r in supplied:
        vid = r['id']
        if vid not in rows or r.get('scene') not in SCENES or r.get('review_status') not in STATUSES or not r.get('note'):
            raise ValueError('VISUAL_REVIEW_REQUIRES_SCENE_STATUS_AND_EVIDENCE')
        if not batch_id and not any(vid in {v['id'] for v in b['occurrences']} and b['id'] in progress['batches'] for b in p['batches']):
            raise ValueError('PREVIEW_REQUIRED_BEFORE_DETAIL_CHECKPOINT')
        if r['review_status'] == 'viewed' and not any(x['id'] == vid and x['kind'] == 'full' for x in progress['requests'].values()):
            raise ValueError('FULL_IMAGE_REQUEST_REQUIRED_FOR_VIEWED_STATUS')
        if r['review_status'] == 'duplicate':
            other = r.get('duplicate_of')
            prior = progress['visuals'].get(other, {})
            if other == vid or prior.get('review_status') not in ('previewed', 'viewed'):
                raise ValueError('DUPLICATE_REQUIRES_A_REVIEWED_SOURCE_AND_CONTENT_BASIS')
        progress['visuals'][vid] = {**r, 'source_sha256': rows[vid]['sha256'], 'updated_at': now()}
    if batch:
        progress['batches'][batch_id] = {'sequence_note': d['sequence_note'], 'updated_at': now()}
    if 'sampling_reviews' in d:
        expected = {(r['begin_ms'], r['end_ms']) for r in p['sampling_windows']}
        supplied_ranges = d['sampling_reviews']
        if {(r['begin_ms'], r['end_ms']) for r in supplied_ranges} != expected or len(supplied_ranges) != len(expected):
            raise ValueError('EVERY_SAMPLING_WINDOW_REQUIRES_REVIEW')
        for r in supplied_ranges:
            if r.get('status') not in ('sequence_reviewed', 'unresolved') or not r.get('basis'):
                raise ValueError('SAMPLING_REVIEW_REQUIRES_EVIDENCE')
        progress['sampling_reviews'] = supplied_ranges
    atomic(paths(job)[3], progress)
    return {'status': 'checkpoint_saved', 'saved_ids': [r['id'] for r in supplied], 'used': counts(progress)}


def detail(job, vid, purpose, box=None):
    p, progress = load(job)
    rows = {r['id']: r for r in source_rows(job)}
    if vid not in rows or not purpose.strip():
        raise ValueError('DETAIL_REQUIRES_ID_AND_CONTENT_QUESTION')
    if vid not in progress['visuals']:
        raise ValueError('PREVIEW_REQUIRED_BEFORE_DETAIL')
    v = rows[vid]
    original = safe_path(paths(job)[0], v['path'])
    if digest(original) != v['sha256']:
        raise ValueError('REVIEW_SOURCE_CHANGED')
    kind = 'crop' if box else 'full'
    token = key([vid, v['sha256'], kind, box])
    cached = progress['requests'].get(token)
    if cached:
        return {**cached, 'status': 'already_requested', 'checkpoint': progress['visuals'][vid],
                'next': '已核对则复用记录；前次工具未返回图像时才读取同一路径一次'}
    used = counts(progress)
    budget_key = 'crops' if box else 'detail_images'
    if used[budget_key] >= p['limits'][budget_key]:
        raise ValueError('REVIEW_BUDGET_REACHED: ' + budget_key + '；保存具体未核实内容，不自动加额、开子代理或修改源码。')
    dest = original
    if box:
        if not any(r['id'] == vid and r['kind'] == 'full' for r in progress['requests'].values()):
            raise ValueError('FULL_IMAGE_BEFORE_CROP')
        n = sum(r['id'] == vid and r['kind'] == 'crop' for r in progress['requests'].values())
        if n >= p['limits']['crops_per_visual']:
            raise ValueError('CROP_LIMIT_REACHED: 无新信息时标无法辨认，不能无限放大。')
        with Image.open(original) as img:
            x1, y1, x2, y2 = box
            if not (0 <= x1 < x2 <= img.width and 0 <= y1 < y2 <= img.height):
                raise ValueError('INVALID_CROP_BOX')
            crop = img.crop(box).convert('RGB')
            crop.thumbnail((2048, 2048))
            dest = paths(job)[1] / f'crop-{token[:16]}.png'
            crop.save(dest)
    request = {'id': vid, 'kind': kind, 'file': str(dest), 'purpose': purpose,
               'box': box, 'source_sha256': v['sha256'], 'requested_at': now()}
    progress['requests'][token] = request
    atomic(paths(job)[3], progress)
    return {**request, 'status': 'read_once', 'used': counts(progress)}


def reserve_supplement(job, ms, purpose):
    p, progress = load(job)
    if not purpose or not purpose.strip():
        raise ValueError('SUPPLEMENT_REQUIRES_CONTENT_QUESTION')
    token = str(ms)
    if token not in progress['supplements']:
        if len(progress['supplements']) >= p['limits']['supplements']:
            raise ValueError('REVIEW_BUDGET_REACHED: supplements')
        progress['supplements'][token] = {'time_ms': ms, 'purpose': purpose}
        atomic(paths(job)[3], progress)


def export(job, draft, gap_file, note_reviewed=False):
    p, progress = load(job)
    if not note_reviewed or not Path(draft).is_file():
        raise ValueError('FINAL_NOTE_REVIEW_REQUIRED')
    if len(progress['batches']) != len(p['batches']) or len(progress['visuals']) != p['visual_count'] or any(r['review_status'] == 'needs_detail' for r in progress['visuals'].values()):
        raise ValueError('VISUAL_REVIEW_NOT_FINISHED')
    expected = {(r['begin_ms'], r['end_ms']) for r in p['sampling_windows']}
    if {(r['begin_ms'], r['end_ms']) for r in progress['sampling_reviews']} != expected:
        raise ValueError('EVERY_SAMPLING_WINDOW_REQUIRES_REVIEW')
    gaps = read_json(gap_file)
    if not isinstance(gaps, list):
        raise ValueError('GAP_REVIEWS_MUST_BE_ARRAY')
    result = {'transcript_read_to_end': True, 'visuals': list(progress['visuals'].values()),
              'gap_reviews': gaps, 'sampling_reviews': progress['sampling_reviews'],
              'sequence_reviews': [{'batch_id': k, **v} for k, v in progress['batches'].items()],
              'note_review_complete': True, 'draft_sha256': digest(draft),
              'visual_review_method': 'temporal_previews_with_targeted_detail',
              'source_fingerprint': p['source_fingerprint'], 'review_limits': p['limits'],
              'review_operations': counts(progress)}
    target = Path(job) / 'draft/review.json'
    atomic(target, result)
    return {'file': str(target), 'counts': {'visuals': p['visual_count'], 'batches': len(p['batches'])},
            'operations': counts(progress), 'next': 'review --file 此文件，再import-note'}


def verify_export(job, d):
    p, progress = load(job)
    if d.get('source_fingerprint') != p['source_fingerprint'] or d.get('visual_review_method') != 'temporal_previews_with_targeted_detail':
        raise ValueError('REVIEW_PLAN_AND_CHECKPOINT_REQUIRED')
    if len(progress['batches']) != len(p['batches']) or any(r['review_status'] == 'needs_detail' for r in progress['visuals'].values()):
        raise ValueError('VISUAL_REVIEW_NOT_FINISHED')
    if d.get('visuals') != list(progress['visuals'].values()) or d.get('sampling_reviews') != progress['sampling_reviews']:
        raise ValueError('REVIEW_MUST_MATCH_SAVED_CHECKPOINTS')
    if d.get('review_operations') != counts(progress) or any(counts(progress)[k] > p['limits'][k] for k in counts(progress)):
        raise ValueError('REVIEW_OPERATION_BUDGET_INVALID')
