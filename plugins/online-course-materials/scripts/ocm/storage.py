"""Course / lesson layout with stable locations across sessions and dates."""
from datetime import datetime
from pathlib import Path
import re
import time
from .common import atomic, key, lock, read_json, safe_path

CONFIG = Path(__file__).resolve().parents[2] / 'references/storage.json'


def settings():
    return read_json(CONFIG)


def component(value):
    # Course names are explicit grouping decisions, never silently rewritten.
    if not value or value in ('.', '..') or value.startswith('.') or len(value) > 100 or any(c in value for c in '/\\\x00\n\r'):
        raise ValueError('INVALID_COURSE_FOLDER')
    return value


def title_component(value):
    value = re.sub(r'[/:\\\x00-\x1f<>|?*]', ' ', str(value))
    return re.sub(r'\s+', ' ', value).strip(' .')[:55] or '未命名课程'


def allocate(root, source, source_key, course, series, study_date, new_version=False):
    series = component(series or course)
    metadata = safe_path(root, '.dsh-data/online-course-materials/locations')
    metadata.mkdir(parents=True, exist_ok=True)
    entry = metadata / (key({'source': source_key, 'course': course, 'series': series}) + '.json')
    with lock(metadata):
        if not new_version and entry.is_file():
            value = read_json(entry)
            job = safe_path(root, value['path'])
            if not job.is_dir():
                raise ValueError('LESSON_DIRECTORY_MISSING: 已登记课次被移动或删除，保留记录，需明确另建版本。')
            return job, value['id'], series
        # Old workspaces remain usable; this does not move or reinterpret old jobs.
        legacy = safe_path(root, '.dsh-data/online-course-materials/jobs/' + source_key[:24])
        if not new_version and (legacy / 'state.json').is_file():
            old = read_json(legacy / 'state.json')
            if old['course']['name'] == course and series == course:
                atomic(entry, {'path': str(legacy.relative_to(root)), 'id': old['id']})
                return legacy, old['id'], series
        course_folder = safe_path(root, series)
        course_folder.mkdir(exist_ok=True)
        job_id = source_key[:24] + ('-' + str(time.time_ns()) if new_version else '')
        label = f"{study_date} 第{source['page_number']:02d}节 {title_component(source.get('part_title') or source.get('title'))} [{source_key[:8]}]"
        if new_version:
            label += ' 重整-' + job_id.split('-')[-1]
        job = safe_path(course_folder, label)
        # A foreign folder must not be adopted merely because its name matches.
        if job.exists():
            raise ValueError('LESSON_FOLDER_CONFLICT: 不覆盖已有课次目录。')
        job.mkdir()
        if not new_version:
            atomic(entry, {'path': str(job.relative_to(root)), 'id': job_id})
        return job, job_id, series
