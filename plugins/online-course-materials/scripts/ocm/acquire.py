"""Bilibili public metadata and optional explicitly supplied cookie file."""
import http.cookiejar
import json
from pathlib import Path
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from . import bili
from .common import atomic, probe, redact, tools

SUBTITLE_NEEDS_LOGIN = 'login_required'

# Bilibili serves several codecs for the same quality. The bundled ffmpeg lists an
# AV1 decoder but cannot actually parse the site's AV1 tracks ("[av1] Failed to read
# unit 1 (type 5)"), which would kill both frame scanning and audio extraction, and
# HEVC is no safer. So prefer H.264 and keep the H.264-less selectors as fallbacks
# (ocm/bili.py enforces the same rule on the DASH path).
VIDEO_FORMAT = ('bv[height<=1080][vcodec^=avc1]+ba/b[height<=1080][vcodec^=avc1]/'
                'bv*[height<=1080]+ba/b[height<=1080]/b')

# The public web-interface API answers a plain client UA normally but replies
# HTTP 412 to browser-like UAs; the media CDN is the opposite (see ocm/bili.py).
HEADERS = {'User-Agent': 'curl/8.4.0',
           'Referer': 'https://www.bilibili.com/'}

# Player/subtitle endpoints sit behind WBI signing and reject the plain UA.
WBI_HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                             'AppleWebKit/537.36 (KHTML, like Gecko) '
                             'Chrome/131.0.0.0 Safari/537.36',
               'Referer': 'https://www.bilibili.com/'}


def client(cookie_file=None):
    jar = http.cookiejar.MozillaCookieJar()
    if cookie_file:
        jar.load(str(cookie_file), ignore_discard=True, ignore_expires=False)
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def fetch_json(opener, url, headers=None):
    try:
        with opener.open(urllib.request.Request(url, headers=headers or HEADERS), timeout=30) as r:
            return json.load(r)
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        raise ValueError('BILIBILI_NETWORK: ' + redact(e))


def normalize(value, opener=None):
    value = value.strip()
    m = re.fullmatch(r'BV[0-9A-Za-z]{10}', value)
    if m:
        return value, 1
    u = urllib.parse.urlsplit(value)
    if u.scheme not in ('http', 'https') or u.username or u.password:
        raise ValueError('INVALID_BILIBILI_URL')
    if u.hostname in ('b23.tv', 'www.b23.tv'):
        with (opener or client()).open(urllib.request.Request(value, headers=HEADERS), timeout=30) as r:
            value = r.geturl()
        u = urllib.parse.urlsplit(value)
    if u.hostname not in ('bilibili.com', 'www.bilibili.com', 'm.bilibili.com'):
        raise ValueError('NOT_BILIBILI_VIDEO')
    m = re.search(r'/video/(BV[0-9A-Za-z]{10}|av\d+)(?:/|$)', u.path)
    if not m:
        raise ValueError('ONLY_BV_AV_COURSE_VIDEOS_SUPPORTED')
    try:
        page = int(urllib.parse.parse_qs(u.query).get('p', ['1'])[0])
    except ValueError:
        raise ValueError('INVALID_PAGE')
    if page < 1:
        raise ValueError('INVALID_PAGE')
    return m.group(1), page


def resolve(value, pages=None, cookie_file=None):
    opener = client(cookie_file)
    video, default = normalize(value, opener)
    param = {'bvid': video} if video.startswith('BV') else {'aid': video[2:]}
    data = fetch_json(opener, 'https://api.bilibili.com/x/web-interface/view?' + urllib.parse.urlencode(param))
    if data.get('code') != 0 or not isinstance(data.get('data'), dict):
        raise ValueError('BILIBILI_METADATA_CODE: ' + str(data.get('code')))
    d = data['data']
    available = {int(x['page']): x for x in d.get('pages', [])}
    if not available:
        raise ValueError('NO_VIDEO_PARTS')
    if pages == 'all':
        chosen = sorted(available)
    elif pages:
        chosen = []
        for part in pages.split(','):
            ends = part.split('-')
            if len(ends) == 1:
                chosen.append(int(part))
            elif len(ends) == 2:
                lo, hi = map(int, ends)
                if hi < lo or hi - lo > 100:
                    raise ValueError('INVALID_PAGE_RANGE')
                chosen.extend(range(lo, hi + 1))
            else:
                raise ValueError('INVALID_PAGE_RANGE')
        chosen = list(dict.fromkeys(chosen))
    else:
        chosen = [default]
    if any(p not in available for p in chosen):
        raise ValueError('PAGE_NOT_FOUND')
    return [{
        'platform': 'bilibili', 'bvid': d['bvid'], 'aid': d['aid'], 'cid': available[p]['cid'],
        'page_number': p, 'part_title': available[p].get('part', ''), 'title': d.get('title', ''),
        'uploader': d.get('owner', {}).get('name', ''), 'published_unix': d.get('pubdate'),
        'duration_ms': round(float(available[p]['duration']) * 1000),
        'canonical_url': f"https://www.bilibili.com/video/{d['bvid']}/?p={p}",
    } for p in chosen]


def subtitles(source, job, cookie_file=None):
    opener = client(cookie_file)
    query = urllib.parse.urlencode({'bvid': source['bvid'], 'cid': source['cid']})
    data = fetch_json(opener, 'https://api.bilibili.com/x/player/wbi/v2?' + query, WBI_HEADERS)
    if data.get('code') != 0:
        return [], 'subtitle_api_error_' + str(data.get('code'))
    d = data.get('data', {})
    tracks = d.get('subtitle', {}).get('subtitles', [])
    tracks = [x for x in tracks if x.get('subtitle_url')]
    def priority(x):
        lang = x.get('lan', '')
        ai = bool(x.get('ai_status') or x.get('ai_type') or lang.startswith('ai-'))
        zh = lang in ('zh', 'zh-CN', 'zh-Hans', 'ai-zh') or lang.startswith('zh-')
        return (not zh, ai)
    for t in sorted(tracks, key=priority):
        url = t['subtitle_url']
        if url.startswith('//'):
            url = 'https:' + url
        host = urllib.parse.urlsplit(url).hostname or ''
        if not host.endswith(('.hdslb.com', '.bilibili.com')):
            continue
        raw = fetch_json(opener, url)
        body = raw.get('body', [])
        if not body:
            continue
        ai = bool(t.get('ai_status') or t.get('ai_type') or t.get('lan', '').startswith('ai-'))
        kind = 'platform_ai_subtitle' if ai else 'platform_subtitle_type_unverified'
        segments = []
        for i, s in enumerate(body):
            begin, end = round(float(s['from']) * 1000), round(float(s['to']) * 1000)
            if begin < 0 or end < begin or end > source['duration_ms'] + 2000 or not isinstance(s['content'], str):
                raise ValueError('INVALID_SUBTITLE_TIMESTAMPS')
            if s['content'].strip():
                segments.append({'id': f'sub-{i:06d}', 'begin_ms': begin,
                                 'end_ms': min(end, source['duration_ms']), 'text': s['content'],
                                 'source_type': kind, 'speaker_id': None, 'review_status': 'automatic_only',
                                 'raw_reference': f'raw/subtitle.json#body/{i}'})
        atomic(Path(job) / 'bundle/transcripts/raw/subtitle.json', raw)
        atomic(Path(job) / 'subtitle-track.json', {'language': t.get('lan'), 'source_type': kind})
        return sorted(segments, key=lambda x: (x['begin_ms'], x['end_ms'])), kind
    return [], 'login_required' if d.get('need_login_subtitle') else 'no_available_subtitle'


class Logger:
    def debug(self, message):
        pass
    def warning(self, message):
        pass
    def error(self, message):
        pass


def video_codec(info):
    """Codec fourcc of the video side as served, e.g. 'avc1.64001f' or 'av01.0.08M.08'."""
    if not isinstance(info, dict):
        return ''
    codec = str(info.get('vcodec') or '')
    for requested in info.get('requested_formats') or []:
        if isinstance(requested, dict) and str(requested.get('vcodec') or '') not in ('', 'none'):
            return str(requested['vcodec'])
    return codec


def download(source, job, cookie_file=None, max_bytes=4 * 1024**3):
    import yt_dlp
    media_dir = Path(job) / 'source'
    media_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    def hook(d):
        if d.get('downloaded_bytes', 0) > max_bytes:
            raise ValueError('DOWNLOAD_SPACE_BUDGET_EXCEEDED')
        if d.get('status') == 'finished':
            paths.append(d['filename'])
    opts = {'outtmpl': str(media_dir / 'video.%(ext)s'), 'noplaylist': True,
            'format': VIDEO_FORMAT, 'merge_output_format': 'mp4',
            'quiet': True, 'no_warnings': True, 'logger': Logger(), 'progress_hooks': [hook],
            'retries': 0, 'fragment_retries': 0, 'extractor_retries': 0, 'socket_timeout': 30,
            'max_filesize': max_bytes, 'ffmpeg_location': tools()['ffmpeg'],
            'overwrites': False, 'continuedl': True}
    if cookie_file:
        opts['cookiefile'] = str(cookie_file)
    codec = ''
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(source['canonical_url'], download=True)
            if info.get('_type') in ('playlist', 'multi_video'):
                raise ValueError('UNEXPECTED_MULTIPART_DOWNLOAD')
            if info.get('cid') is not None and str(info['cid']) != str(source['cid']):
                raise ValueError('DOWNLOADED_CID_MISMATCH')
            candidate = info.get('filepath') or ydl.prepare_filename(info)
            codec = video_codec(info)
    except Exception as e:
        # yt-dlp's Bilibili extractor currently returns no formats under the site's
        # risk control; the public playurl API still serves DASH.
        try:
            return bili.download(source, job, cookie_file, max_bytes)
        except Exception as fallback:
            raise ValueError('MEDIA_DOWNLOAD_FAILED: ' + redact(e) +
                             ' | DASH_FALLBACK_FAILED: ' + redact(fallback))
    else:
        # A downloaded AV1/HEVC track is undecodable here, and letting the probe fail
        # through to NO_DECODEABLE_VIDEO would strand a mostly-wasted download; route
        # that case to the DASH path, which is pinned to H.264.
        if codec.startswith(('av01', 'hvc1', 'hev1')):
            try:
                return bili.download(source, job, cookie_file, max_bytes)
            except Exception as fallback:
                raise ValueError('UNDECODABLE_VIDEO_CODEC: ' + codec +
                                 ' | DASH_FALLBACK_FAILED: ' + redact(fallback))
    files = [Path(candidate), media_dir / 'video.mp4'] + [Path(x) for x in paths]
    for file in files:
        if not file.is_file():
            continue
        try:
            found = probe(file)
        except ValueError:
            # Damaged or undecodable leftovers must not mask a usable file.
            continue
        if any(x.get('codec_type') == 'video' for x in found['streams']):
            actual = found['duration_ms']
            if abs(actual - source['duration_ms']) > max(2000, source['duration_ms'] * .005):
                raise ValueError('MEDIA_TRUNCATED_OR_DURATION_MISMATCH')
            if sum(p.stat().st_size for p in media_dir.iterdir() if p.is_file()) > max_bytes:
                raise ValueError('DOWNLOAD_SPACE_BUDGET_EXCEEDED')
            return file
    raise ValueError('NO_DECODEABLE_VIDEO')
