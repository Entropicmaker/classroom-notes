"""Bilibili DASH fallback download.

The site currently answers browser-like User-Agents with HTTP 412 and yt-dlp's
Bilibili extractor returns no formats, while the public web-interface API answers
a plain `curl` UA normally. The media CDN is the mirror image: it rejects a curl
UA and serves a browser UA. So this path splits the two.

No login, no signature cracking: only the public endpoints the site itself calls,
with the two User-Agents it happens to accept on each side.
"""
from pathlib import Path
import json
import urllib.error
import urllib.parse
import urllib.request

from .common import atomic, probe, redact, run, tools

API_UA = 'curl/8.4.0'
CDN_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
          '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
CDN_HEADERS = {'User-Agent': CDN_UA, 'Referer': 'https://www.bilibili.com/',
               'Origin': 'https://www.bilibili.com'}
CHUNK = 1 << 20


def api_json(url, cookie_file=None):
    headers = {'User-Agent': API_UA, 'Referer': 'https://www.bilibili.com/'}
    request = urllib.request.Request(url, headers=headers)
    try:
        from .acquire import client
        with client(cookie_file).open(request, timeout=30) as response:
            body = response.read()
    except (urllib.error.URLError, OSError) as e:
        raise ValueError('BILIBILI_API_FAILED: ' + redact(e))
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError('BILIBILI_API_NOT_JSON: ' + redact(e))


def playurl(source, cookie_file=None):
    query = urllib.parse.urlencode({
        'bvid': source['bvid'], 'cid': source['cid'], 'qn': '80', 'fnval': '4048',
        'fnver': '0', 'fourk': '1', 'platform': 'pc', 'high_quality': '1'})
    data = api_json('https://api.bilibili.com/x/player/playurl?' + query, cookie_file)
    if data.get('code') != 0:
        raise ValueError('BILIBILI_PLAYURL_CODE: ' + str(data.get('code')))
    payload = data.get('data') or {}
    dash = payload.get('dash') or {}
    videos = [v for v in (dash.get('video') or []) if v.get('baseUrl') or v.get('base_url')]
    audios = [a for a in (dash.get('audio') or []) if a.get('baseUrl') or a.get('base_url')]
    if not videos:
        raise ValueError('BILIBILI_NO_DASH_VIDEO')
    # Only H.264 is reliably decodable end to end: the bundled ffmpeg/ffprobe choke
    # on these AV1 tracks ("Failed to read unit 1"), which would break frame
    # scanning and audio extraction. Prefer H.264, then the largest picture.
    def rank(v):
        codec = v.get('codecs') or ''
        return (1 if codec.startswith('avc') else 0, v.get('height') or 0,
                v.get('bandwidth') or 0)
    videos.sort(key=rank, reverse=True)
    if not (videos[0].get('codecs') or '').startswith('avc'):
        raise ValueError('BILIBILI_NO_AVC_TRACK: 无 H.264 轨，现有解码器无法可靠处理')
    audios.sort(key=lambda a: a.get('bandwidth') or 0)
    quality = payload.get('quality')
    return {'video': videos[0], 'audio': audios[0] if audios else None,
            'duration_ms': dash.get('duration'), 'quality': quality,
            'accept_quality': payload.get('accept_quality')}


def stream_url(track):
    url = track.get('baseUrl') or track.get('base_url')
    backups = track.get('backupUrl') or track.get('backup_url') or []
    return [url] + [b for b in backups if b]


def fetch(urls, dest, max_bytes, cookie_file=None):
    """Download one track, trying each mirror before giving up."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_name('.' + dest.name + '.part')
    last = None
    for url in urls:
        try:
            request = urllib.request.Request(url, headers=CDN_HEADERS)
            written = 0
            from .acquire import client
            with client(cookie_file).open(request, timeout=60) as response, temp.open('wb') as out:
                expected = response.headers.get('Content-Length')
                if response.status != 200:
                    raise ValueError('BILIBILI_CDN_PARTIAL_RESPONSE')
                if expected and int(expected) > max_bytes:
                    raise ValueError('DOWNLOAD_SPACE_BUDGET_EXCEEDED')
                while True:
                    block = response.read(CHUNK)
                    if not block:
                        break
                    written += len(block)
                    if written > max_bytes:
                        raise ValueError('DOWNLOAD_SPACE_BUDGET_EXCEEDED')
                    out.write(block)
            if written < 1024:
                raise ValueError('BILIBILI_CDN_EMPTY')
            if expected and written != int(expected):
                raise ValueError('BILIBILI_CDN_TRUNCATED')
            temp.replace(dest)
            return written
        except Exception as e:  # try the next mirror
            last = e
            if temp.exists():
                temp.unlink()
    raise ValueError('BILIBILI_CDN_FAILED: ' + redact(last))


def download(source, job, cookie_file=None, max_bytes=4 * 1024**3):
    """Return a playable video path, mirroring the yt-dlp download() contract."""
    from .common import state
    job = Path(job)
    media_dir = job / 'source'
    media_dir.mkdir(parents=True, exist_ok=True)

    plan = playurl(source, cookie_file)
    atomic(job / 'bundle/transcripts/raw/playurl-plan.json',
           {k: plan[k] for k in ('video', 'audio', 'quality', 'accept_quality')})
    # Reserve space for both source tracks plus the merged copy.
    budget = max_bytes // 2
    track = plan['video']
    kind = track.get('codecs', 'video')
    video_file = media_dir / f'dash-video-{track.get("height") or 0}.mp4'
    audio_file = media_dir / 'dash-audio.m4a'

    # Audio first: local ASR only needs this, and it is the small side.
    if plan['audio'] is not None:
        size = fetch(stream_url(plan['audio']), audio_file, budget, cookie_file)
        budget -= size
        state(job, dash_audio_bytes=size, dash_audio_codec=plan['audio'].get('codecs'))
    size = fetch(stream_url(track), video_file, budget, cookie_file)
    state(job, dash_video_bytes=size, dash_video_codec=kind,
          dash_video_height=track.get('height'), dash_quality=plan['quality'])

    merged = media_dir / 'video.mp4'
    inputs = ['-i', str(video_file)]
    if plan['audio'] is not None:
        inputs += ['-i', str(audio_file)]
    run([tools()['ffmpeg'], '-nostdin', '-v', 'error', '-y', *inputs,
         '-c', 'copy', '-movflags', '+faststart', str(merged)], timeout=1800)
    if not merged.is_file():
        raise ValueError('DASH_MERGE_FAILED')
    info = probe(merged)
    expected = source['duration_ms']
    if abs(info['duration_ms'] - expected) > max(2000, expected * .005):
        raise ValueError('MEDIA_TRUNCATED_OR_DURATION_MISMATCH')
    return merged
