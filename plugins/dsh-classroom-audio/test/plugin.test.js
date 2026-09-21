import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import path from 'node:path';
import os from 'node:os';
import { promisify } from 'node:util';
import { apply } from '../lib/index.js';
const run = promisify(execFile);

test('register with installed DSH defineTool and run actual tool implementation', async () => {
  let tool;
  await apply({ tools: { register: t => { tool = t; } }, credentials: { resolve: async () => undefined } });
  assert.equal(tool.name, 'classroom_audio');
  const result = JSON.parse(await tool.execute({ action: 'doctor' }, { signal: new AbortController().signal }));
  assert.equal(result.key_configured, false); assert.equal(result.ffmpeg, true); assert.equal(result.ffprobe, true);
  await assert.rejects(tool.execute({ action: 'prepare', files: ['secret.wav'] }, { signal: new AbortController().signal }), /SESSION_WORKSPACE_REQUIRED/);
});
test('native credential helper saves through stdin without echo, isolated from user store', async () => {
  const home = await mkdtemp(path.join(os.tmpdir(), 'dsh-classroom-credentials-test-'));
  const env = { ...process.env, DSH_HOME: home }; delete env.DASHSCOPE_API_KEY;
  const setter = path.resolve('bin/set-key.mjs');
  const code = `import {spawn} from 'node:child_process'; const p=spawn(process.execPath,[${JSON.stringify(setter)}],{stdio:['pipe','pipe','pipe']});p.stdin.end('fake-unit-test-no-billing');p.stdout.pipe(process.stdout);p.stderr.pipe(process.stderr);p.on('close',c=>process.exit(c));`;
  const saved = await run(process.execPath, ['--input-type=module', '-e', code], { env });
  assert.doesNotMatch(saved.stdout + saved.stderr, /fake-unit-test-no-billing/);
  assert.match(saved.stdout, /已写入/);
  const checked = await run(process.execPath, [path.resolve('bin/audio.mjs'), '{"action":"doctor"}'], { env });
  assert.equal(JSON.parse(checked.stdout).key_configured, true);
});
