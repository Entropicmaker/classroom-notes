// Uses the existing offline AudioService without opening its cloud credential store.
import path from 'node:path';
import os from 'node:os';
import { pathToFileURL } from 'node:url';

try {
  const args = JSON.parse(process.argv[2] ?? '{"action":"doctor"}');
  if (!['doctor', 'prepare', 'start', 'poll', 'status', 'read'].includes(args.action))
    throw new Error('UNSUPPORTED_LOCAL_AUDIO_ACTION');
  if (args.engine && args.engine !== 'local') throw new Error('LOCAL_ONLY');
  const core = path.join(os.homedir(), '.dsh/vendor/dsh-classroom-audio/lib/core.js');
  const { AudioService } = await import(pathToFileURL(core).href);
  const c = new AbortController();
  process.once('SIGTERM', () => c.abort());
  process.once('SIGINT', () => c.abort());
  const service = new AudioService({ root: process.cwd(), signal: c.signal });
  console.log(JSON.stringify(await service.call({ ...args, engine: 'local' })));
} catch (e) {
  console.error(e.message);
  process.exitCode = 1;
}
