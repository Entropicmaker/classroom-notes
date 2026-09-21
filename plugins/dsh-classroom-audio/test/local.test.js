import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { AudioService } from '../lib/core.js';
import { LOCAL_MODEL } from '../lib/local.js';
import { sessionFiles, resolveUploadedInput } from '../lib/attachments.js';
import { loadDsh } from '../bin/runtime.js';
import { apply } from '../lib/index.js';

function wav() {
  const b = Buffer.alloc(44 + 32000);
  b.write('RIFF'); b.writeUInt32LE(b.length - 8, 4); b.write('WAVEfmt ', 8); b.writeUInt32LE(16, 16);
  b.writeUInt16LE(1, 20); b.writeUInt16LE(1, 22); b.writeUInt32LE(16000, 24); b.writeUInt32LE(32000, 28);
  b.writeUInt16LE(2, 32); b.writeUInt16LE(16, 34); b.write('data', 36); b.writeUInt32LE(32000, 40);
  return b;
}
test('local vocabulary fails before staging; cloud still accepts vocabulary', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'dsh-local-test-'));
  let touched = false;
  const guarded = new AudioService({ root, resolveInput: () => { touched = true; throw Error('unexpected staging'); } });
  await assert.rejects(guarded.prepare({ files: ['lecture.wav'], vocabulary: ['岩盘'] }), /LOCAL_CONTEXT_DISABLED/);
  assert.equal(touched, false);
  await fs.writeFile(path.join(root, 'lecture.wav'), wav());
  const service = new AudioService({ root });
  const prepared = await service.prepare({ files: ['lecture.wav'], engine: 'cloud', vocabulary: ['岩盘'] });
  assert.equal(prepared.engine, 'cloud');
});
test('old vocabulary job cannot relaunch GPU worker', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'dsh-local-test-'));
  await fs.writeFile(path.join(root, 'lecture.wav'), wav());
  const service = new AudioService({ root }), prepared = await service.prepare({ files: ['lecture.wav'] });
  const file = path.join(prepared.directory, 'job.json');
  const job = JSON.parse(await fs.readFile(file)); job.vocabulary = ['岩盘'];
  await fs.writeFile(file, JSON.stringify(job));
  await assert.rejects(service.start({ job_id: prepared.job_id }), /LOCAL_CONTEXT_DISABLED/);
  await assert.rejects(fs.stat(path.join(prepared.directory, 'local-state.json')), { code: 'ENOENT' });
});
test('default engine is local, no key or network, separate from cloud job identity', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'dsh-local-test-'));
  await fs.writeFile(path.join(root, 'lecture.wav'), wav());
  const service = new AudioService({ root, fetcher: () => { throw Error('unexpected network'); } });
  const p = await service.prepare({ files: ['lecture.wav'] });
  assert.equal(p.engine, 'local'); assert.equal(p.model, LOCAL_MODEL);
  assert.ok((await fs.stat(path.join(p.directory, 'audio1.wav'))).size > 32000);
  const c = await service.prepare({ files: ['lecture.wav'], engine: 'cloud' });
  assert.notEqual(p.job_id, c.job_id);
});
test('local result normalization keeps punctuation, numbers, null speakers, and is resumable', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'dsh-local-test-'));
  await fs.writeFile(path.join(root, 'lecture.wav'), wav());
  const service = new AudioService({ root }), p = await service.prepare({ files: ['lecture.wav'] });
  const raw = { properties: {}, transcripts: [{ channel_id: 0, sentences: [{ begin_time: 0, end_time: 950, text: '0.5克，不是5克。', speaker_id: null }] }] };
  await fs.writeFile(path.join(p.directory, 'audio1.local.raw.json'), JSON.stringify(raw));
  assert.equal((await service.poll({ job_id: p.job_id })).complete, true);
  const r = await service.read({ job_id: p.job_id, audio_id: 'audio1' });
  assert.equal(r.segments[0].text, '0.5克，不是5克。'); assert.equal(r.segments[0].speaker_id, null);
  // Already complete: start does not launch a GPU process or require cloud authorization.
  assert.equal((await service.start({ job_id: p.job_id })).complete, true);
});
test('attachment intake authorizes only current-session user file references', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'dsh-local-test-'));
  const file = { name: '老师录音.wav', bytes: wav().length, attachmentId: 'mock-reference' };
  const store = { fileHostPath: () => '/somewhere/uploaded.wav', async *readFileStream() { yield wav(); } };
  const session = { deriveMessages: () => [{ role: 'user', content: [{ type: 'file', attachment: file }] }] };
  assert.equal(sessionFiles(session, store).length, 1);
  const staged = await resolveUploadedInput(root, '/somewhere/uploaded.wav', session, store);
  assert.equal(staged.name, file.name); assert.equal((await fs.readFile(staged.path)).length, wav().length);
  await assert.rejects(resolveUploadedInput(root, '/etc/passwd', session, store), /NOT_A_SESSION_ATTACHMENT/);
  assert.equal(sessionFiles({ deriveMessages: () => [{ role: 'assistant', content: [{ type: 'file', attachment: file }] }] }, store).length, 0);
});
test('real DSH attachment store -> native tool -> automatic local preparation', async () => {
  const home = await fs.mkdtemp(path.join(os.tmpdir(), 'dsh-attachment-store-test-'));
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'dsh-attachment-work-test-'));
  const { Context } = await loadDsh('@deepseek-ai/cordis');
  const { default: LocalAttachments } = await loadDsh('@deepseek-ai/dsh-attachment-local');
  const ctx = new Context();
  await ctx.plugin(LocalAttachments, { dshHome: home });
  try {
    const ref = await ctx.attachments.saveFile({ name: '课堂上半节.wav', data: wav() });
    const session = { header: { cwd: root }, deriveMessages: () => [{ role: 'user', content: [{ type: 'file', attachment: ref }] }] };
    let tool;
    await apply({ tools: { register: t => { tool = t; } }, attachments: ctx.attachments, credentials: { resolve: async () => undefined } });
    const exec = { agent: { session }, signal: new AbortController().signal };
    const listed = JSON.parse(await tool.execute({ action: 'attachments' }, exec));
    assert.equal(listed.files[0].name, ref.name);
    const prepared = JSON.parse(await tool.execute({ action: 'prepare', files: [listed.files[0].path] }, exec));
    assert.equal(prepared.engine, 'local');
    assert.equal(prepared.audios[0].name, ref.name);
    const job = JSON.parse(await fs.readFile(path.join(prepared.directory, 'job.json')));
    assert.deepEqual(await fs.readFile(job.audios[0].source), wav());
    assert.ok(job.audios[0].source.startsWith(await fs.realpath(root)));
  } finally { await ctx.fiber.dispose(); }
});
test('oversized or truncated attachment is refused and partial copy is removed', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'dsh-local-test-'));
  const ref = { name: '课堂.wav', bytes: 2 * 1024 ** 3 + 1, attachmentId: 'oversized' };
  const session = { deriveMessages: () => [{ role: 'user', content: [{ type: 'file', attachment: ref }] }] };
  const store = { fileHostPath: () => '/elsewhere/audio.wav', async *readFileStream() { yield Buffer.from('short'); } };
  await assert.rejects(resolveUploadedInput(root, '/elsewhere/audio.wav', session, store), /INVALID_AUDIO_FILE/);
  ref.bytes = 100;
  await assert.rejects(resolveUploadedInput(root, '/elsewhere/audio.wav', session, store), /ATTACHMENT_SIZE_MISMATCH/);
  assert.deepEqual(await fs.readdir(path.join(root, '.dsh-work/classroom-audio/inbox')), []);
});
