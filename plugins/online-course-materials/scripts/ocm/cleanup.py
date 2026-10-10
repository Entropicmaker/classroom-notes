"""Remove only job-owned online media after durable, verified Vault import."""
from pathlib import Path
from .common import atomic, digest, now, read_json, safe_path, state

MEDIA = {'.mp4', '.mkv', '.webm', '.mov', '.m4v', '.m4a', '.mp3', '.wav', '.flac', '.aac', '.ogg', '.opus'}


def finish(job, repair_missing_media=False):
    from . import bundle
    job = Path(job).resolve()
    s = read_json(job / 'state.json')
    if s['source']['platform'] != 'bilibili':
        return {'status': 'skipped', 'reason': '用户提供的本地原件保留'}
    if s.get('import_status') != 'imported' or not Path(s.get('imported_note', '')).is_file():
        raise ValueError('CLEANUP_REQUIRES_SUCCESSFUL_IMPORT_AND_EXISTING_NOTE')
    if (job / 'asr.json').exists() and any(x['status'] != 'done' for x in read_json(job / 'asr.json')['blocks']):
        raise ValueError('CLEANUP_REQUIRES_FINISHED_ASR')
    archive_path = job / 'bundle/media-archive.json'
    if archive_path.exists():
        archive = read_json(archive_path)
        if archive['status'] == 'complete':
            m = read_json(job / 'bundle/manifest.json')
            if m.get('media_availability') != 'deleted_after_import':
                bundle.validate(job, allow_missing_media=s.get('archived_media') or s.get('media'))
                bundle.publish_manifest(job)
            bundle.validate(job)
            return {'status': 'complete', 'released_bytes': archive['released_bytes'], 'already_cleaned': True}
    else:
        m = read_json(job / 'bundle/manifest.json')
        missing = {a['path'] for a in m['source_artifacts'] if not safe_path(job, a['path']).is_file()}
        media_rel = s.get('media')
        if missing:
            # Only repair the registered media removed manually in the old workflow.
            if not repair_missing_media or missing != {media_rel} or not s.get('media_sha256'):
                raise ValueError('SOURCE_MISSING_BEFORE_CLEANUP')
            for a in m['source_artifacts']:
                if a['path'] == media_rel and a['sha256'] != s['media_sha256']:
                    raise ValueError('ARCHIVED_MEDIA_IDENTITY_MISMATCH')
            bundle.validate(job, allow_missing_media=media_rel)
        else:
            bundle.validate(job)
        files = []
        for scope in ('source', 'derived', '.dsh-work/classroom-audio'):
            folder = safe_path(job, scope)
            if not folder.exists():
                continue
            for p in sorted(folder.rglob('*')):
                if p.is_symlink():
                    raise ValueError('CLEANUP_REFUSES_SYMLINK')
                if p.is_file() and p.suffix.lower() in MEDIA:
                    p = safe_path(job, p.relative_to(job))
                    files.append({'path': str(p.relative_to(job)), 'sha256': digest(p), 'bytes': p.stat().st_size})
        if missing:
            files.append({'path': media_rel, 'sha256': s['media_sha256'], 'bytes': None,
                          'previously_removed': True})
        # Durable intent precedes deletion; interrupted cleanup resumes this exact list.
        archive = {'schema': 'media-cleanup/1', 'status': 'pending', 'created_at': now(),
                   'note': s['imported_note'], 'source_key': s['source_key'], 'source': s['source'],
                   'original_source_artifacts': m['source_artifacts'], 'files': files,
                   'released_bytes': sum(f['bytes'] or 0 for f in files),
                   'policy': '保留字幕、转写、全部候选截图、索引、审阅、草稿与来源指纹；原媒体已删除，不能本地播放或补图'}
        atomic(archive_path, archive)
    # Verify preserved evidence on every retry before regenerating its manifest.
    bundle.validate(job, allow_missing_media=s.get('archived_media') or s.get('media'))
    for item in archive['files']:
        parts = Path(item['path']).parts
        allowed_scope = parts[0] in ('source', 'derived') or parts[:2] == ('.dsh-work', 'classroom-audio')
        if not allowed_scope or Path(item['path']).suffix.lower() not in MEDIA:
            raise ValueError('CLEANUP_REFUSES_NON_MEDIA_OR_OUTSIDE_SCOPE')
        p = safe_path(job, item['path'])
        if p.exists():
            if not p.is_file() or digest(p) != item['sha256']:
                raise ValueError('CLEANUP_FILE_CHANGED: ' + item['path'])
            p.unlink()
    state(job, media_archived=True, archived_media=s.get('media') or s.get('archived_media'), media=None,
          cleanup_status='complete', status='imported', phase='imported', pid=None,
          storage={**s.get('storage', {}), 'retain_after_import': 'essential'})
    archive.update(status='complete', completed_at=now())
    atomic(archive_path, archive)
    bundle.publish_manifest(job)
    bundle.validate(job)
    return {'status': 'complete', 'released_bytes': archive['released_bytes'], 'archive': str(archive_path)}


def automatic(job):
    from .storage import settings
    if not settings().get('cleanup_online_media_after_import', True):
        return {'status': 'retained_by_configuration'}
    try:
        return finish(job)
    except Exception as e:
        # Imported note remains durable; expose incomplete cleanup for an explicit retry.
        state(job, cleanup_status='failed', cleanup_error=str(e))
        return {'status': 'failed', 'error': str(e), 'next': 'cleanup --job 原课次路径'}
