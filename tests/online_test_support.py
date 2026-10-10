"""Isolated skill installation for synthetic tests; no user settings or models."""
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from ocm import storage
from ocm.common import atomic, tools

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def isolated_install():
    # Resolve optional bundled tools before changing HOME; no ASR is started.
    bins = tools()
    tool_dirs = list(dict.fromkeys(str(Path(p).parent) for p in bins.values() if p))
    with tempfile.TemporaryDirectory(prefix='online-skill-test-') as temp:
        root = Path(temp).resolve()
        home = root / 'home'
        classroom = home / '.dsh/skills/classroom-notes/scripts'
        classroom.mkdir(parents=True)
        shutil.copyfile(ROOT / 'scripts/vault_import.py', classroom / 'vault_import.py')
        online = home / '.dsh/skills/online-course-materials'
        shutil.copytree(ROOT / 'plugins/online-course-materials/scripts', online / 'scripts',
                        ignore=shutil.ignore_patterns('vendor', '__pycache__', '*.pyc'))
        materials = root / 'materials'
        materials.mkdir()
        config = online / 'references/storage.json'
        atomic(config, {'materials_root': str(materials), 'layout': 'course/lesson',
                        'retain_after_import': 'essential', 'cleanup_online_media_after_import': True})
        env = {'HOME': str(home), 'PATH': os.pathsep.join(tool_dirs + [os.environ.get('PATH', '')])}
        with patch.dict(os.environ, env), patch.object(storage, 'CONFIG', config):
            yield SimpleNamespace(root=root, home=home, scripts=online / 'scripts', materials=materials)
