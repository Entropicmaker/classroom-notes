#!/usr/bin/env python3
"""Read-only bounded wait for local worker events; run poll afterwards to publish transcripts."""
import argparse
import json
import os
from pathlib import Path
import time


def snapshot(directory, seen=()):
    state_path = directory / 'local-state.json'
    if state_path.is_symlink():
        raise ValueError('UNSAFE_LOCAL_STATE')
    if not state_path.exists():
        return {'event': 'pending', 'status': 'NOT_STARTED'}
    state = json.loads(state_path.read_text())
    status = state.get('status')
    done = [aid for aid, a in state.get('audios', {}).items() if a.get('status') == 'DONE']
    result = {'event': 'pending', 'status': status, 'done': done,
              'progress': state.get('audios', {}), 'updated_at': state.get('updated_at')}
    if status == 'FAILED':
        result.update(event='failed', error=state.get('error'))
    elif status == 'DONE':
        result['event'] = 'complete'
    elif set(done) - set(seen):
        result['event'] = 'audio_done'
    elif isinstance(state.get('pid'), int) and state['pid'] > 0:
        try:
            os.kill(state['pid'], 0)
        except ProcessLookupError:
            result['event'] = 'interrupted'
        except PermissionError:
            pass
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', type=Path, required=True, help='directory returned by prepare')
    parser.add_argument('--seen', action='append', default=[], help='already read audio ID; repeat as needed')
    parser.add_argument('--timeout', type=float, default=45)
    args = parser.parse_args()
    if not 0 <= args.timeout <= 60:
        parser.error('--timeout must be between 0 and 60 seconds')
    if not args.job.is_dir():
        parser.error('--job directory does not exist')
    deadline = time.monotonic() + args.timeout
    while True:
        result = snapshot(args.job, args.seen)
        remaining = deadline - time.monotonic()
        if result['event'] != 'pending' or remaining <= 0:
            if result['event'] == 'pending':
                result['event'] = 'timeout'
            print(json.dumps(result, ensure_ascii=False))
            return
        time.sleep(min(2, remaining))


if __name__ == '__main__':
    main()
