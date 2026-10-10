"""Scan native decoded frames; keep events, before-change frames and anchors."""
from pathlib import Path
import queue
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageChops, ImageStat, ImageDraw
from .common import atomic, digest, probe, run, state, tools, write_lines


def select_candidates(candidates, max_images, duration_ms):
    """Keep temporal coverage under a local capture cap, never just the first N.

    Numerical salience is a scheduling hint, not semantic deduplication. Every
    detected event remains in events.jsonl, including those without a screenshot.
    """
    if max_images < 2:
        raise ValueError('VISUAL_BUDGET_EXCEEDED: 至少需要首尾两张；不能截断尾部。')
    ordered = sorted(candidates)
    if len(ordered) <= max_images:
        return ordered, []
    selected = {ordered[0], ordered[-1]}
    # Reserve one best event in each equal-duration window. This prevents busy
    # opening animations from consuming the entire video's capture allowance.
    n = max_images - 2
    bins = [[] for _ in range(n)]
    for t in ordered[1:-1]:
        if n:
            bins[min(n - 1, t * n // max(1, duration_ms))].append(t)
    rank = {'scene_cut': 5, 'settled_state': 4, 'before_cut': 3,
            'visual_change': 2, 'before_change': 1, 'periodic_anchor': 0}
    def priority(t):
        return rank.get(candidates[t], 0), -t
    for group in bins:
        if group:
            selected.add(max(group, key=priority))
    # Sparse windows leave capacity; fill from the highest-ranked other events.
    rest = sorted((t for t in ordered if t not in selected), key=priority, reverse=True)
    selected.update(rest[:max_images - len(selected)])
    # Coalesce the omissions into bounded review intervals, rather than requiring
    # thousands of nearly identical tiny JSON records for continuous motion.
    grouped = {}
    totals = {}
    for t in ordered:
        bucket = t // 120000
        totals[bucket] = totals.get(bucket, 0) + 1
        if t not in selected:
            grouped.setdefault(bucket, []).append(t)
    windows = [{'begin_ms': group[0], 'end_ms': group[-1],
                'detected_events': totals[bucket], 'not_materialized': len(group),
                'status': 'sampled_not_semantically_verified'}
               for bucket, group in sorted(grouped.items())]
    return sorted(selected), windows


def changed(a, b):
    d = ImageChops.difference(a, b)
    mean = max(ImageStat.Stat(d).mean)
    if mean > 9:
        return True
    hist = d.histogram()
    channels = len(d.getbands())
    fraction = sum(sum(hist[i * 256 + 18:(i + 1) * 256]) for i in range(channels)) / (a.width * a.height * channels)
    if fraction <= .006:
        return False
    tile = max(max(ImageStat.Stat(d.crop((x, y, x + 40, y + 45))).mean)
               for y in range(0, 180, 45) for x in range(0, 320, 40))
    return tile > 5


def screenshot(video, ms, dest):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_name('.' + dest.stem + '.png')
    # Default accurate seek before input; preserve full picture, only downscale oversized media.
    run([tools()['ffmpeg'], '-nostdin', '-v', 'error', '-y', '-ss', f'{max(ms, 0) / 1000:.3f}', '-i', video, '-frames:v', '1',
         '-vf', "scale='min(1920,iw)':-2", str(temp)], timeout=120)
    if not temp.is_file():
        raise ValueError('SCREENSHOT_NOT_PRODUCED')
    with Image.open(temp) as img:
        img.verify()
    temp.replace(dest)


def capture(video, job, duration_ms, max_images=2000, anchor_ms=30000, event_cooldown_ms=350):
    if event_cooldown_ms < 1 or anchor_ms < 1:
        raise ValueError('INVALID_CAPTURE_INTERVAL')
    bins = tools()
    if not bins['ffmpeg']:
        raise ValueError('FFMPEG_MISSING')
    cmd = [bins['ffmpeg'], '-nostdin', '-v', 'info', '-i', str(video), '-an',
           '-vf', 'scale=320:180,format=rgb24,showinfo', '-vsync', '0', '-f', 'rawvideo', 'pipe:1']
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    times, errors = queue.Queue(), []
    def logs():
        for line in iter(proc.stderr.readline, b''):
            s = line.decode(errors='replace')
            m = re.search(r'\bn:\s*\d+\s+pts:.*?pts_time:([\d.\-eE+]+)', s)
            if m:
                times.put(round(float(m.group(1)) * 1000))
            elif 'error' in s.lower():
                errors.append(s[-300:])
        times.put(None)
    thread = threading.Thread(target=logs, daemon=True)
    thread.start()
    candidates = {0: 'start'}
    previous = baseline = None
    previous_ms = last_event = last_anchor = count = 0
    motion_end = last_cut = -1000
    was_moving = False
    frame_size = 320 * 180 * 3
    try:
        while True:
            frame = bytearray()
            while len(frame) < frame_size:
                block = proc.stdout.read(frame_size - len(frame))
                if not block:
                    break
                frame.extend(block)
            if not frame:
                break
            if len(frame) != frame_size:
                raise ValueError('TRUNCATED_DECODED_FRAME')
            t = times.get(timeout=60)
            if t is None:
                raise ValueError('VIDEO_PTS_MISSING')
            current = Image.frombytes('RGB', (320, 180), bytes(frame))
            if baseline is None:
                baseline = current
            # Examine the whole picture. Never mask the teacher, borders or a
            # fixed projection ROI: those regions can hold an animation or label.
            motion = previous is not None and changed(previous, current)
            cut = previous is not None and max(ImageStat.Stat(ImageChops.difference(previous, current)).mean) > 28
            if cut and t - last_cut >= event_cooldown_ms:
                candidates[max(0, previous_ms)] = 'before_cut'
                candidates[max(0, t)] = 'scene_cut'
                last_cut = t
            if motion:
                motion_end, was_moving = t, True
            elif was_moving and t - motion_end >= 500:
                candidates[max(0, t)] = 'settled_state'
                was_moving = False
            if t - last_event >= event_cooldown_ms and changed(baseline, current):
                candidates.setdefault(max(0, previous_ms), 'before_change')
                candidates.setdefault(max(0, t), 'visual_change')
                baseline, last_event = current, t
            if t - last_anchor >= anchor_ms:
                candidates.setdefault(t, 'periodic_anchor')
                last_anchor = t
            previous, previous_ms = current, t
            count += 1
            if count % 1500 == 0:
                state(job, phase='visual_scan', scan_ms=t, decoded_frames=count)
        if proc.wait(timeout=30) != 0:
            raise ValueError('VIDEO_DECODE_FAILED')
    finally:
        proc.stdout.close()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        thread.join(timeout=2)
        proc.stderr.close()
    if errors or count == 0 or previous_ms < duration_ms - 2000:
        raise ValueError('VIDEO_SCAN_INCOMPLETE')
    candidates[max(0, previous_ms)] = 'end'
    bundle = Path(job) / 'bundle'
    selected, sampled_windows = select_candidates(candidates, max_images, duration_ms)
    selected_set = set(selected)
    write_lines(bundle / 'visuals/events.jsonl', [
        {'time_ms': t, 'reason': candidates[t], 'materialized': t in selected_set}
        for t in sorted(candidates)])
    images = bundle / 'visuals/images'
    images.mkdir(parents=True, exist_ok=True)
    def extract(item):
        i, (requested, reason) = item
        path = images / f'frame-{i:06d}-{requested:010d}.png'
        # Container tails can outlast the final decodable frame. Record both times.
        retreats = (0, 100, 250, 500, 1000, 2000) if requested >= duration_ms - 2000 else (0,)
        last = None
        for retreat in retreats:
            actual = max(0, requested - retreat)
            try:
                screenshot(video, actual, path)
                return requested, actual, reason, path
            except ValueError as e:
                last = e
        raise ValueError(f'SCREENSHOT_NOT_PRODUCED: {requested} ms: {last}')
    records, hashes = [], {}
    # Bounded CPU work; collect in time order for deterministic IDs and provenance.
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i, (requested, t, reason, path) in enumerate(pool.map(extract, enumerate((t, candidates[t]) for t in selected))):
            if i % 10 == 0:
                state(job, phase='screenshots', images_done=i, images_total=len(selected))
            h = digest(path)
            if h in hashes:
                records[hashes[h]]['occurrences_ms'].append(t)
                path.unlink()
                continue
            hashes[h] = len(records)
            records.append({'id': f'visual-{len(records):06d}', 'path': str(path.relative_to(bundle)),
                            'sha256': h, 'time_ms': t, 'requested_time_ms': requested,
                            'occurrences_ms': [t], 'reason': reason,
                            'scene': 'unreviewed', 'review_status': 'not_viewed',
                            'original_frame': True, 'downscale_max_width': 1920})
    state(job, images_done=len(selected), images_total=len(selected))
    write_lines(bundle / 'visuals/index.jsonl', records)
    atomic(bundle / 'visuals/scan.json', {'mode': 'native_frames_lowres', 'decoded_frames': count,
           'begin_ms': 0, 'last_decoded_ms': previous_ms, 'duration_ms': duration_ms,
           'analysis_size': [320, 180], 'analysis_channels': 'RGB', 'event_cooldown_ms': event_cooldown_ms, 'anchor_ms': anchor_ms,
           'max_images': max_images, 'detected_events': len(candidates), 'captured_events': len(selected),
           'review_workflow': 'temporal_previews_with_targeted_detail',
           'sampling_windows': sampled_windows,
           'warning': '全幅低清检测不证明微小/极短状态完整；超过本地截图额度按全片时间分配，未出图事件留在events.jsonl。必须检查sampling_windows，不把采样说成逐帧语义核对。'})
    contact_sheets(bundle, records)
    return records


def contact_sheets(bundle, records):
    dest = Path(bundle) / 'visuals/contact-sheets'
    dest.mkdir(parents=True, exist_ok=True)
    for batch in range(0, len(records), 24):
        group = records[batch:batch + 24]
        sheet = Image.new('RGB', (1280, 6 * 205), 'white')
        draw = ImageDraw.Draw(sheet)
        for i, r in enumerate(group):
            x, y = (i % 4) * 320, (i // 4) * 205
            with Image.open(Path(bundle) / r['path']) as img:
                img.thumbnail((316, 178))
                sheet.paste(img.convert('RGB'), (x, y))
            draw.text((x + 2, y + 180), f"{r['id']}  {r['time_ms'] / 1000:.2f}s", fill='black')
        sheet.save(dest / f'contact-{batch // 24:03d}.png')
