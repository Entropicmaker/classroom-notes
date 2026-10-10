"""Local files and time handling shared by capture and handoff."""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from datetime import datetime

VERSION = '0.3.0'
SCHEMA = 'lecture-materials/1.0'


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def key(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False).encode()).hexdigest()


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2) + '\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_lines(path, records):
    atomic(path, ''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in records))


def read_lines(path):
    with Path(path).open(encoding='utf-8') as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def safe_path(root, relative):
    root = Path(root).resolve()
    raw = root / relative
    if raw.is_symlink():
        raise ValueError('SYMLINK_PATH: ' + str(relative))
    target = raw.resolve()
    if root != target and root not in target.parents:
        raise ValueError('PATH_OUTSIDE_ROOT: ' + str(relative))
    return target


@contextlib.contextmanager
def lock(job, wait=False):
    p = safe_path(job, '.lock')
    with p.open('a') as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            raise ValueError('JOB_BUSY: 任务正在执行，请查询 status。')
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def redact(text):
    text = re.sub(r'https?://[^\s\"\'<>]+', '[URL omitted]', str(text))
    text = re.sub(r'(?i)(SESSDATA|cookie|authorization|api[_-]?key)\s*[:=]\s*[^\s,;]+', r'\1=[redacted]', text)
    return text[-2000:]


def run(args, timeout=60, cwd=None):
    try:
        p = subprocess.run([str(a) for a in args], cwd=cwd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ValueError('PROCESS_TIMEOUT: ' + Path(str(args[0])).name)
    if p.returncode:
        raise ValueError('PROCESS_FAILED: ' + Path(str(args[0])).name + ': ' + redact(p.stderr.decode(errors='replace')))
    return p.stdout.decode(errors='replace')


def tools():
    base = Path.home() / '.dsh/vendor/dsh-classroom-audio'
    result = {'ffmpeg': shutil.which('ffmpeg'), 'ffprobe': shutil.which('ffprobe')}
    if not all(result.values()) and (base / 'package.json').is_file() and shutil.which('node'):
        code = "const r=require('module').createRequire(process.argv[1]); console.log(JSON.stringify({ffmpeg:r('@ffmpeg-installer/ffmpeg').path,ffprobe:r('@ffprobe-installer/ffprobe').path}));"
        try:
            found = json.loads(run(['node', '-e', code, str(base / 'package.json')]))
            result = {k: result[k] or found[k] for k in result}
        except ValueError:
            pass
    return {k: v if v and os.access(v, os.X_OK) else None for k, v in result.items()}


def probe(path):
    bins = tools()
    if not bins['ffprobe']:
        raise ValueError('FFPROBE_MISSING')
    data = json.loads(run([bins['ffprobe'], '-v', 'error', '-show_format', '-show_streams', '-of', 'json', path]))
    duration = float(data.get('format', {}).get('duration', 0))
    if duration <= 0:
        raise ValueError('INVALID_MEDIA_DURATION')
    return {'duration_ms': round(duration * 1000), 'streams': data.get('streams', [])}


def stamp(ms):
    seconds = int(ms // 1000)
    return f'{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}'


def state(job, **changes):
    p = Path(job) / 'state.json'
    d = read_json(p)
    d.update(changes, updated_at=now())
    atomic(p, d)
    return d


def gaps(segments, duration_ms):
    result, end = [], 0
    for s in sorted(segments, key=lambda x: (x['begin_ms'], x['end_ms'])):
        if s['begin_ms'] > end + 1000:
            result.append({'begin_ms': end, 'end_ms': s['begin_ms'], 'status': 'no_text_unverified'})
        end = max(end, s['end_ms'])
    if end < duration_ms - 1000:
        result.append({'begin_ms': end, 'end_ms': duration_ms, 'status': 'no_text_unverified'})
    return result
