"""Durable material contract, visual review and existing Vault import bridge."""
import json
import hashlib
from pathlib import Path
import sqlite3
import subprocess
from .common import SCHEMA, VERSION, atomic, digest, gaps, key, now, read_json, read_lines, run, safe_path, stamp, state, write_lines


def database(root):
    p = safe_path(root, '.dsh-data/online-course-materials/registry.sqlite')
    p.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(p, timeout=10)
    db.execute('CREATE TABLE IF NOT EXISTS imports (source_key TEXT, course TEXT, note TEXT, sha256 TEXT, imported_at TEXT, PRIMARY KEY(source_key, course))')
    return db


def find_import(root, source_key, course):
    with database(root) as db:
        row = db.execute('SELECT note,sha256,imported_at FROM imports WHERE source_key=? AND course=?', (source_key, course)).fetchone()
    if not row:
        return None
    p = Path(row[0])
    return {'note': str(p), 'exists': p.is_file(), 'modified_since_import': p.is_file() and digest(p) != row[1], 'imported_at': row[2]}


def publish_manifest(job):
    job = Path(job).resolve()
    s = read_json(job / 'state.json')
    b = job / 'bundle'
    transcript = b / 'transcripts/segments.jsonl'
    rows = list(read_lines(transcript)) if transcript.exists() else []
    visuals = list(read_lines(b / 'visuals/index.jsonl')) if (b / 'visuals/index.jsonl').exists() else []
    source_files = [b / 'source-ref.json']
    if s.get('media'):
        source_files.append(safe_path(job, s['media']))
    raw = b / 'transcripts/raw'
    if raw.exists():
        source_files.extend(sorted(p for p in raw.iterdir() if p.is_file()))
    artifacts = []
    for p in sorted(b.rglob('*')):
        if p.is_file() and p.name not in ('manifest.json', 'handoff.md'):
            artifacts.append({'path': str(p.relative_to(b)), 'sha256': digest(p), 'bytes': p.stat().st_size})
    no_audio = s.get('audio_track_present') is False and not rows
    unknown = [] if no_audio else gaps(rows, s['source']['duration_ms'])
    coverage = []
    cursor = 0
    for g in unknown:
        if g['begin_ms'] > cursor:
            coverage.append({'begin_ms': cursor, 'end_ms': g['begin_ms'], 'speech': 'text_available_automatic_only'})
        coverage.append({'begin_ms': g['begin_ms'], 'end_ms': g['end_ms'], 'speech': 'no_text_unverified'})
        cursor = g['end_ms']
    if cursor < s['source']['duration_ms']:
        coverage.append({'begin_ms': cursor, 'end_ms': s['source']['duration_ms'], 'speech': 'no_audio_track' if no_audio else 'text_available_automatic_only'})
    write_lines(b / 'coverage.jsonl', coverage)
    alignment = b / 'alignment.jsonl'
    # Regenerate after ASR or supplemental frames; these are time candidates only.
    links = []
    for x in rows:
        ids = [v['id'] for v in visuals if any(x['begin_ms'] <= t <= x['end_ms'] for t in v['occurrences_ms'])]
        links.append({'segment_id': x['id'], 'visual_candidates': ids, 'status': 'temporal_candidates_only'})
    write_lines(alignment, links)
    # Include files generated just above in the integrity manifest.
    for rel in ['coverage.jsonl', 'alignment.jsonl']:
        artifacts = [a for a in artifacts if a['path'] != rel]
        p = b / rel
        artifacts.append({'path': rel, 'sha256': digest(p), 'bytes': p.stat().st_size})
    status = 'ready_for_review' if visuals and (rows or no_audio) else 'waiting_asr' if visuals and s.get('status') == 'waiting_asr' else 'incomplete'
    reviewed = b / 'review.json'
    scan_file = b / 'visuals/scan.json'
    scan = read_json(scan_file) if scan_file.exists() else {}
    if reviewed.exists() and status == 'ready_for_review':
        status = 'reviewed'
    manifest = {'schema_version': SCHEMA, 'skill_version': VERSION, 'bundle_id': s['id'],
        'source': s['source'], 'source_key': s['source_key'], 'course': s['course'],
        'storage': s.get('storage', {'materials_root': s['root'], 'lesson_path': str(job), 'retain_after_import': True}),
        'root': s['root'], 'bundle_path': str(b), 'source_files': [str(p) for p in source_files],
        'source_artifacts': [{'path': str(p.relative_to(job)), 'sha256': s['media_sha256'] if s.get('media') == str(p.relative_to(job)) else digest(p)} for p in source_files],
        'materials': {'transcript': 'transcripts/segments.jsonl', 'visual_index': 'visuals/index.jsonl',
                      'alignment': 'alignment.jsonl', 'coverage': 'coverage.jsonl',
                      'review_entrypoint': 'review-next', 'visual_events': 'visuals/events.jsonl' if (b / 'visuals/events.jsonl').exists() else None},
        'counts': {'segments': len(rows), 'visuals': len(visuals)}, 'artifacts': artifacts,
        'quality': {'speech_source': s.get('speech_source'), 'unknown_text_ranges': unknown,
                    'audio_track_present': s.get('audio_track_present'),
                    'listening_review': 'not_performed', 'visual_review': 'recorded' if reviewed.exists() else 'pending',
                    'visual_review_method': read_json(reviewed).get('visual_review_method', 'legacy_individual_images') if reviewed.exists() else scan.get('review_workflow', 'legacy_individual_images'),
                    'visual_sampling_windows': scan.get('sampling_windows', []),
                    'visual_capture': 'native_frames_lowres', 'original_ppt_recovered': False},
        'handoff_status': status, 'import_status': s.get('import_status', 'not_started')}
    manifest['media_availability'] = 'deleted_after_import' if s.get('media_archived') else 'local_media_retained'
    manifest['cleanup_status'] = s.get('cleanup_status', 'not_started')
    atomic(b / 'manifest.json', manifest)
    source = s['source']
    atomic(b / 'source.md', f"# 来源\n\n标题：{source.get('title', '')}\n\n分段：{source.get('part_title', '')}\n\n链接：{source.get('canonical_url', '本地视频')}\n\n时长：{stamp(source['duration_ms'])}\n\n整理日期：{s['course']['study_date']}（{s['course']['date_basis']}）\n\n所有转写与截图时间均为本P内视频时间。ASR块起点已还原，不跨P拼成虚假连续时间。截图是采集编号，非原PPT页码。\n\n字幕/ASR未逐句听核；无文字区间不自动判作静音；完整解码检测不证明所有教学状态均已恢复。\n")
    atomic(b / 'handoff.md', f"# 视频素材交接\n\n课程：{s['course']['name']}\n\n入口：{b / 'manifest.json'}\n\n状态：{status}\n\n加载 classroom-notes 的 online-materials.md；本视频分支使用上游 references/visual-review.md。全部转写分页读完。review-next返回顺序画面组，实际看图并立即review-save；需精读才review-detail，不把候选图当作独立课件照片逐张全文抄写。不预设板书/固定ROI，动画和实拍按过程核对；无法确定的内容保留疑点与范围。检查采样窗口，不能声称逐帧语义完整。\n\n复用已有转写和审阅记录；默认由当前代理顺序完成，不自动分派子代理。来源集合在 manifest.source_files，lookup/plan/commit 必须一致。来源编号、图片出处与录音时间只记内部映射，成稿从知识正文开始；名词解释用普通引用 > 名词解释：……，老师重点/强调同类栏目用 > 老师强调：……，不使用提示框或折叠；完整知识原文、术语/主题及内部段落、列表与顺序保持，只去掉出处和录音时间，其他受保护栏目保持原结构。逐项核对完整内容，科普/动画无教师依据不强加老师强调。图片复制到草稿旁 assets/，最终通过本技能 import-note 包装入库程序并登记身份。\n")
    # Refresh hashes for source.md and exclude handoff, whose absolute paths are routing only.
    manifest['artifacts'] = [a for a in manifest['artifacts'] if a['path'] != 'source.md'] + [
        {'path': 'source.md', 'sha256': digest(b / 'source.md'), 'bytes': (b / 'source.md').stat().st_size}]
    atomic(b / 'manifest.json', manifest)
    return manifest


def validate(job, allow_missing_media=None):
    b = Path(job) / 'bundle'
    m = read_json(b / 'manifest.json')
    if m.get('schema_version') != SCHEMA:
        raise ValueError('UNSUPPORTED_BUNDLE_SCHEMA')
    for a in m['artifacts']:
        p = safe_path(b, a['path'])
        if not p.is_file() or digest(p) != a['sha256']:
            raise ValueError('BUNDLE_ARTIFACT_CHANGED: ' + a['path'])
    for p in m['source_files']:
        path = Path(p).resolve()
        if allow_missing_media and path == safe_path(job, allow_missing_media):
            continue
        if Path(job).resolve() not in path.parents or not path.is_file():
            raise ValueError('SOURCE_MISSING_OR_OUTSIDE_JOB')
    for a in m['source_artifacts']:
        if allow_missing_media and a['path'] == allow_missing_media:
            continue
        if digest(safe_path(job, a['path'])) != a['sha256']:
            raise ValueError('SOURCE_ARTIFACT_CHANGED: ' + a['path'])
    rows = list(read_lines(b / m['materials']['transcript'])) if (b / m['materials']['transcript']).exists() else []
    duration = m['source']['duration_ms']
    for x in rows:
        if not (0 <= x['begin_ms'] < x['end_ms'] <= duration):
            raise ValueError('TRANSCRIPT_INVALID_RANGE')
    visuals = list(read_lines(b / m['materials']['visual_index']))
    for v in visuals:
        p = safe_path(b, v['path'])
        if digest(p) != v['sha256'] or not all(0 <= t < duration for t in v['occurrences_ms']):
            raise ValueError('VISUAL_INVALID')
    return {'valid': True, 'handoff_status': m['handoff_status'], 'counts': m['counts'],
            'unknown_text_ranges': m['quality']['unknown_text_ranges'],
            'semantic_completeness_proven': False}


from .reviewing import SCENES


def review(job, file):
    validate(job)
    b = Path(job) / 'bundle'
    d = read_json(file)
    visuals = list(read_lines(b / 'visuals/index.jsonl'))
    supplied = d.get('visuals', [])
    indexed = {x['id']: x for x in supplied}
    if len(indexed) != len(supplied) or set(indexed) != {x['id'] for x in visuals}:
        raise ValueError('REVIEW_MUST_ACCOUNT_FOR_EVERY_VISUAL')
    scan_file = b / 'visuals/scan.json'
    scan = read_json(scan_file) if scan_file.exists() else {}
    bounded = d.get('visual_review_method') == 'temporal_previews_with_targeted_detail'
    if bounded or scan.get('review_workflow') == 'temporal_previews_with_targeted_detail':
        from .reviewing import verify_export
        verify_export(job, d)
        bounded = True
    expected_sampling = {(r['begin_ms'], r['end_ms']) for r in scan.get('sampling_windows', [])}
    if {(r['begin_ms'], r['end_ms']) for r in d.get('sampling_reviews', [])} != expected_sampling:
        raise ValueError('EVERY_SAMPLING_WINDOW_REQUIRES_REVIEW')
    statuses = ('viewed', 'previewed', 'duplicate', 'unreadable', 'unresolved') if bounded else ('viewed', 'unreadable')
    for v in visuals:
        r = indexed[v['id']]
        if r.get('scene') not in SCENES or r.get('review_status') not in statuses or not r.get('note'):
            raise ValueError('VISUAL_REVIEW_REQUIRES_SCENE_STATUS_AND_EVIDENCE')
        v.update(scene=r['scene'], review_status=r['review_status'])
    if d.get('transcript_read_to_end') is not True or not isinstance(d.get('gap_reviews'), list):
        raise ValueError('TRANSCRIPT_AND_GAPS_REVIEW_REQUIRED')
    current = read_json(b / 'manifest.json')['quality']['unknown_text_ranges']
    actual = {(x['begin_ms'], x['end_ms']) for x in d['gap_reviews']}
    if actual != {(x['begin_ms'], x['end_ms']) for x in current}:
        raise ValueError('EVERY_NO_TEXT_RANGE_NEEDS_REVIEW')
    for r in d['gap_reviews']:
        if r.get('status') not in ('no_teaching_content_verified', 'unresolved') or not r.get('basis'):
            raise ValueError('GAP_REVIEW_REQUIRES_EVIDENCE_OR_UNRESOLVED_STATUS')
    write_lines(b / 'visuals/index.jsonl', visuals)
    atomic(b / 'review.json', d)
    return publish_manifest(job)


def import_note(job, note, title, config=None, new_version=False, custom_title=False):
    from . import cleanup
    saved = read_json(Path(job) / 'state.json')
    old = find_import(saved['root'], saved['source_key'], saved['course']['name'])
    if not old and saved.get('import_status') == 'imported' and not new_version:
        skill = Path.home() / '.dsh/skills/classroom-notes'
        cfg = read_json(Path(config) if config else skill / 'references/vault.json')
        course = cfg.get('aliases', {}).get(saved['course']['name'], saved['course']['name'])
        folder = safe_path(cfg['vault'], str(Path(cfg['courses_dir']) / course))
        archive = Path(job) / 'bundle/media-archive.json'
        originals = read_json(archive)['original_source_artifacts'] if archive.exists() else read_json(Path(job) / 'bundle/manifest.json')['source_artifacts']
        source_key = hashlib.sha256(json.dumps([a['sha256'] for a in originals]).encode()).hexdigest()
        for receipt in (folder / '.classroom-imports').glob('*.json'):
            rec = read_json(receipt)
            if rec.get('source_key') != source_key:
                continue
            target = safe_path(folder, rec['note'])
            if not target.is_file():
                continue
            with database(saved['root']) as db:
                db.execute('INSERT OR REPLACE INTO imports VALUES (?,?,?,?,?)',
                    (saved['source_key'], saved['course']['name'], str(target), rec['note_sha256'], now()))
            old = find_import(saved['root'], saved['source_key'], saved['course']['name'])
            break
    if old and old['exists'] and not new_version:
        state(job, import_status='imported', imported_note=old['note'])
        return {'status': 'already_imported', **old, 'cleanup': cleanup.automatic(job)}
    if saved.get('media_archived'):
        raise ValueError('MEDIA_ALREADY_CLEANED: 无可恢复的导入记录或要求重整；请 prepare --new-version 重新获取媒体，不能用改变后的来源集合绕过重复检查')
    checked = validate(job)
    if checked['handoff_status'] != 'reviewed':
        raise ValueError('REVIEW_REQUIRED_BEFORE_IMPORT')
    b = Path(job) / 'bundle'
    m = read_json(b / 'manifest.json')
    skill = Path.home() / '.dsh/skills/classroom-notes'
    script = skill / 'scripts/vault_import.py'
    cfg = Path(config) if config else skill / 'references/vault.json'
    note = Path(note).resolve()
    if not note.is_file() or Path(m['root']).resolve() not in note.parents:
        raise ValueError('DRAFT_MUST_BE_IN_WORKSPACE')
    reviewed = read_json(b / 'review.json')
    if reviewed.get('note_review_complete') is not True or reviewed.get('draft_sha256') != digest(note):
        raise ValueError('FINAL_NOTE_REVIEW_AND_MATCHING_DRAFT_HASH_REQUIRED')
    unresolved = any(x['status'] == 'unresolved' for x in read_json(b / 'review.json')['gap_reviews'])
    uncertain_images = any(x['scene'] == 'unknown' or x['review_status'] in ('unreadable', 'unresolved') for x in read_json(b / 'review.json')['visuals'])
    unresolved_sampling = any(x['status'] == 'unresolved' for x in reviewed.get('sampling_reviews', []))
    if (unresolved or uncertain_images or unresolved_sampling) and '部分摘录' not in note.read_text(encoding='utf-8'):
        raise ValueError('PARTIAL_NOTE_MUST_DISCLOSE_GAPS')
    args = ['--config', str(cfg), '--course', m['course']['name'], '--title', title, '--note', str(note)]
    if custom_title:
        args += ['--custom-title']
    for p in m['source_files']:
        args += ['--source', p]
    if new_version:
        args += ['--new-version']
    # Resolve success from receipt if a previous import finished before registry update.
    lookup = json.loads(run(['python3', script, 'lookup'] + args))
    if lookup.get('existing') and not new_version:
        target = Path(lookup['existing'][0]['path'])
    else:
        plan = json.loads(run(['python3', script, 'plan'] + args))
        if plan.get('status') != 'ready':
            raise ValueError('VAULT_PLAN_NOT_READY')
        result = json.loads(run(['python3', script, 'commit'] + args))
        target = Path(result.get('note') or plan['note'])
    if not target.is_file():
        raise ValueError('VAULT_NOTE_NOT_CREATED')
    with database(m['root']) as db:
        db.execute('INSERT OR REPLACE INTO imports VALUES (?,?,?,?,?)',
                   (m['source_key'], m['course']['name'], str(target), digest(target), now()))
    state(job, import_status='imported', imported_note=str(target), status='imported', phase='imported', pid=None)
    publish_manifest(job)
    return {'status': 'imported', 'note': str(target), 'cleanup': cleanup.automatic(job)}
