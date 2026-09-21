import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import ffmpegInstaller from '@ffmpeg-installer/ffmpeg';
import { AudioService, normalize, providerURL, within, stamp } from '../lib/core.js';

const run = promisify(execFile);
const ffmpeg = ffmpegInstaller.path;
async function fixture({ stereo = false, duration = 3 } = {}) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'dsh-classroom-test-'));
  for (let i = 1; i <= 2; i++) await run(ffmpeg, ['-nostdin', '-v', 'error', '-f', 'lavfi', '-i', `sine=frequency=${400 + i * 100}:sample_rate=16000`, '-t', String(duration), '-ac', stereo ? '2' : '1', path.join(root, `part${i}.wav`)]);
  return root;
}
const response = data => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } });
function remote({ lost = false, partial = false } = {}) {
  const calls = []; let count = 0;
  return { calls, fetcher: async (url, opts) => {
    calls.push({ url, opts });
    if (url.includes('getPolicy')) return response({ data: { upload_host: 'https://dashscope-test.oss-cn-beijing.aliyuncs.com', upload_dir: 'test', max_file_size_mb: 100, oss_access_key_id: 'test-access', policy: 'test-policy', signature: 'test-signature' } });
    if (url.includes('.oss-')) { assert.equal(opts.headers?.Authorization, undefined); assert.equal([...opts.body.keys()].at(-1), 'file'); return response({}); }
    if (url.includes('/transcription')) {
      if (lost) throw new Error('network contains secret that must not be logged');
      const body = JSON.parse(opts.body);
      assert.equal(body.parameters.diarization_enabled, true);
      assert.equal(opts.headers['X-DashScope-OssResourceResolve'], 'enable');
      return response({ output: { task_id: `task-${++count}` } });
    }
    if (url.includes('/tasks/')) return response({ output: { task_status: 'SUCCEEDED', results: [{ subtask_status: partial ? 'FAILED' : 'SUCCEEDED', transcription_url: 'https://results.aliyuncs.com/result?signature=private' }] } });
    assert.equal(opts.headers?.Authorization, undefined);
    return response({ properties: {}, transcripts: [{ channel_id: 0, sentences: [{ begin_time: 0, end_time: 1000, text: '这个不考。', speaker_id: 0 }, { begin_time: 1100, end_time: 1800, text: '例子说明条件。', speaker_id: 1 }] }] });
  } };
}
function service(root, mock = remote()) { return new AudioService({ root, getKey: async () => 'unit-test-key', fetcher: mock.fetcher }); }
const prep = s => s.call({ action: 'prepare', engine: 'cloud', files: ['part1.wav', 'part2.wav'], vocabulary: ['岩浆岩'] });

test('two independent recordings: prepare, submit, resume, partial reads, source timestamps, clip', async () => {
  const root = await fixture(), mock = remote(), s = service(root, mock), p = await prep(s);
  assert.equal(p.audios.length, 2); assert.equal(p.complete, false);
  const a = { job_id: p.job_id, cloud_authorized: true };
  await s.call({ ...a, action: 'start', audio_id: 'audio1' });
  await s.call({ ...a, action: 'start', audio_id: 'audio1' });
  await s.call({ ...a, action: 'start', audio_id: 'audio2' });
  assert.equal(mock.calls.filter(c => c.url.includes('/transcription')).length, 2);
  await s.call({ ...a, action: 'poll' });
  const done = await s.call({ ...a, action: 'poll' }); assert.equal(done.complete, true);
  const page = await s.call({ ...a, action: 'read', audio_id: 'audio2', limit: 1 });
  assert.equal(page.segments[0].begin_ms, 0); assert.equal(page.segments[0].source_id, 'audio2');
  assert.equal(page.segments[0].confidence, null); assert.equal(page.next_offset, 1);
  assert.match(page.segments[0].text, /不考/);
  const last = await s.call({ ...a, action: 'read', audio_id: 'audio2', offset: page.next_offset }); assert.equal(last.next_offset, null);
  assert.equal((await prep(service(root, mock))).complete, true);
  const clip = await s.call({ ...a, action: 'clip', audio_id: 'audio1', start_seconds: 1, end_seconds: 2 });
  assert.equal(clip.offset_ms, 1000); assert.ok((await fs.stat(clip.file)).size > 0);
  for (const file of (await fs.readdir(p.directory)).filter(x => x.endsWith('.json'))) {
    const content = await fs.readFile(path.join(p.directory, file), 'utf8'); assert.doesNotMatch(content, /unit-test-key|signature=private|test-signature/);
  }
});
test('45 minutes x 2 preserve full durations without memory-sized hashing or timeline merging', async () => {
  const root = await fixture({ duration: 2700 }), s = service(root), p = await prep(s);
  assert.equal(p.audios[0].duration_ms, 2700000); assert.equal(p.audios[1].duration_ms, 2700000);
  assert.ok((await fs.stat(path.join(p.directory, 'audio1.flac'))).size < 100 * 1024 ** 2);
});
test('ambiguous submission is durable and never auto retried', async () => {
  const root = await fixture(), mock = remote({ lost: true }), s = service(root, mock), p = await prep(s);
  const a = { job_id: p.job_id, audio_id: 'audio1', cloud_authorized: true, action: 'start' };
  await assert.rejects(s.call(a), /NETWORK_OR_CANCELLED/);
  const status = await s.call(a); assert.equal(status.audios[0].status, 'SUBMIT_UNKNOWN');
  assert.equal(mock.calls.filter(c => c.url.includes('/transcription')).length, 1);
  assert.doesNotMatch(JSON.stringify(status), /secret/);
});
test('success envelope does not conceal subtask failure', async () => {
  const root = await fixture(), s = service(root, remote({ partial: true })), p = await prep(s);
  await s.call({ action: 'start', job_id: p.job_id, audio_id: 'audio1', cloud_authorized: true });
  const r = await s.call({ action: 'poll', job_id: p.job_id }); assert.equal(r.audios[0].status, 'FAILED'); assert.equal(r.complete, false);
});
test('stereo requires explicit choice and retains originals', async () => {
  const root = await fixture({ stereo: true }), s = service(root);
  assert.equal((await prep(s)).needs_channel_choice, true);
  const p = await s.call({ action: 'prepare', files: ['part1.wav'], channel: '0' });
  assert.equal(p.audios[0].source_channels, 2); assert.equal(p.audios[0].selected_channel, '0');
});
test('missing credentials and cloud authorization prevent network', async () => {
  const root = await fixture(), mock = remote(), s = new AudioService({ root, getKey: async () => undefined, fetcher: mock.fetcher }), p = await prep(s);
  await assert.rejects(s.call({ action: 'start', job_id: p.job_id }), /CLOUD_AUTHORIZATION/);
  await assert.rejects(s.call({ action: 'start', job_id: p.job_id, cloud_authorized: true }), /MISSING_DASHSCOPE/);
  assert.equal(mock.calls.length, 0); assert.equal((await s.doctor()).key_configured, false);
});
test('scope and symlink guards', async () => {
  assert.equal(within('/a/b', '/a/bad/file'), false); assert.equal(within('/a/b', '/a/b/file'), true);
  const root = await fixture(), outside = await fixture(), s = service(root);
  await assert.rejects(s.call({ action: 'prepare', files: [path.join(outside, 'part1.wav')] }), /OUTSIDE_WORKSPACE/);
  await fs.symlink(path.join(outside, 'part1.wav'), path.join(root, 'linked.wav'));
  await assert.rejects(s.call({ action: 'prepare', files: ['linked.wav'] }), /OUTSIDE_WORKSPACE/);
  await fs.symlink(outside, path.join(root, '.dsh-work'));
  await assert.rejects(prep(s), /UNSAFE_CACHE/);
  await assert.rejects(s.call({ action: 'status', job_id: '../../escape' }), /INVALID_JOB_ID/);
});
test('duplicate sources and modified prepared files rejected before upload', async () => {
  const root = await fixture(), mock = remote(), s = service(root, mock);
  await assert.rejects(s.call({ action: 'prepare', files: ['part1.wav', 'part1.wav'] }), /DUPLICATE_AUDIO/);
  const p = await prep(s); await fs.appendFile(path.join(p.directory, 'audio1.flac'), 'altered');
  await assert.rejects(s.call({ action: 'start', job_id: p.job_id, cloud_authorized: true }), /PREPARED_AUDIO_CHANGED/); assert.equal(mock.calls.length, 0);
});
test('normalization gaps remain unverified, no invented confidence, invalid metadata fails', () => {
  const a = { id: 'audio2', name: 'second.m4a', duration_ms: 2700000 };
  const n = normalize({ transcripts: [{ sentences: [{ begin_time: 2000, end_time: 3000, text: '课堂' }] }] }, a);
  assert.equal(n.gaps.length, 2); assert.equal(n.segments[0].speaker_id, null); assert.equal(n.segments[0].confidence, null);
  assert.throws(() => normalize({ transcripts: [] }, a), /EMPTY_TRANSCRIPT/);
  assert.throws(() => normalize({ transcripts: [{ sentences: [{ begin_time: -1, end_time: 1, text: 'x' }] }] }, a), /TIMESTAMPS/);
  assert.equal(stamp(2700000), '00:45:00');
});
test('only HTTPS provider hosts accepted; redirects are disabled by request', () => {
  assert.throws(() => providerURL('http://results.aliyuncs.com/a'), /UNTRUSTED/);
  assert.throws(() => providerURL('https://aliyuncs.com.attacker.test/a'), /UNTRUSTED/);
  assert.throws(() => providerURL('https://x:y@results.aliyuncs.com/a'), /UNTRUSTED/);
  assert.match(providerURL('https://results.aliyuncs.com/a'), /^https:/);
});
