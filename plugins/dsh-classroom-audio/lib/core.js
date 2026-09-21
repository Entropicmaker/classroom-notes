import { createReadStream, openAsBlob } from 'node:fs';
import * as fs from 'node:fs/promises';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import ffmpegInstaller from '@ffmpeg-installer/ffmpeg';
import ffprobe from '@ffprobe-installer/ffprobe';
import { LOCAL_MODEL, localDoctor, launchLocal, localState, processAlive } from './local.js';

const run = promisify(execFile);
const ffmpeg = ffmpegInstaller.path;
export const MODEL = 'qwen-audio-3.0-asr-flash-filetrans';
const ENDPOINTS = { beijing: 'https://dashscope.aliyuncs.com/api/v1', singapore: 'https://dashscope-intl.aliyuncs.com/api/v1' };
const EXT = new Set(['.mp3', '.m4a', '.wav', '.flac', '.aac', '.ogg', '.opus', '.wma', '.amr', '.mp4', '.webm', '.caf', '.aiff']);
function fail(message) { throw new Error(message); }
export function within(root, target) {
  const rel = path.relative(root, target);
  return rel === '' || (!rel.startsWith('..' + path.sep) && rel !== '..' && !path.isAbsolute(rel));
}
async function sourcePath(root, name) {
  const p = await fs.realpath(path.resolve(root, name));
  if (!within(root, p)) fail('SOURCE_OUTSIDE_WORKSPACE: 请先用 DSH 已授权的文件工具把附件复制到当前会话工作区。');
  const st = await fs.stat(p);
  if (!st.isFile() || st.size > 2 * 1024 ** 3 || !EXT.has(path.extname(p).toLowerCase())) fail('INVALID_AUDIO_FILE');
  return p;
}
// Reject cache symlinks, including components that do not exist yet.
export async function safeDir(root, relative) {
  let dir = root;
  for (const part of relative.split('/')) {
    if (!part || part === '.' || part === '..') fail('INVALID_CACHE_PATH');
    dir = path.join(dir, part);
    try { await fs.mkdir(dir, { mode: 0o700 }); } catch (e) { if (e.code !== 'EEXIST') throw e; }
    const st = await fs.lstat(dir);
    if (!st.isDirectory() || st.isSymbolicLink()) fail('UNSAFE_CACHE_DIRECTORY');
  }
  return dir;
}
async function regular(file) {
  const st = await fs.lstat(file);
  if (!st.isFile() || st.isSymbolicLink()) fail('UNSAFE_CACHE_FILE');
  return st;
}
async function json(file) { await regular(file); return JSON.parse(await fs.readFile(file, 'utf8')); }
async function atomic(file, value) {
  const tmp = file + '.' + randomUUID() + '.tmp';
  await fs.writeFile(tmp, typeof value === 'string' ? value : JSON.stringify(value, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
  await fs.rename(tmp, file);
}
async function retainRaw(file, value) {
  const text = JSON.stringify(value, null, 2) + '\n', tmp = file + '.' + randomUUID() + '.tmp';
  await fs.writeFile(tmp, text, { flag: 'wx', mode: 0o400 });
  try {
    try { await fs.link(tmp, file); } catch (e) {
      if (e.code !== 'EEXIST') throw e;
      await regular(file);
      if (await fs.readFile(file, 'utf8') !== text) fail('RAW_RESULT_CHANGED: 已有原始转写保留不覆盖，请人工检查服务结果变化。');
    }
  } finally { await fs.unlink(tmp); }
}
async function sha(file, signal) {
  const h = createHash('sha256');
  for await (const b of createReadStream(file, { signal })) h.update(b);
  return h.digest('hex');
}
function stableHash(x) { return createHash('sha256').update(JSON.stringify(x)).digest('hex').slice(0, 24); }
export function stamp(ms) {
  const t = Math.max(0, Math.floor(ms / 1000));
  return `${String(Math.floor(t / 3600)).padStart(2, '0')}:${String(Math.floor(t / 60) % 60).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`;
}
async function probe(file, signal) {
  const { stdout } = await run(ffprobe.path, ['-v', 'error', '-show_format', '-show_streams', '-of', 'json', file], { signal, timeout: 60000, maxBuffer: 2 * 1024 ** 2 });
  const p = JSON.parse(stdout), a = p.streams?.find(s => s.codec_type === 'audio');
  const duration = Number(p.format?.duration ?? a?.duration);
  if (!a || !Number.isFinite(duration) || duration <= 0 || duration > 7200) fail('INVALID_DURATION: 单份须为可解码的 0–120 分钟音频。');
  return { duration_ms: Math.round(duration * 1000), channels: a.channels, sample_rate: Number(a.sample_rate), codec: a.codec_name };
}
export function normalize(raw, audio, model = MODEL) {
  const segments = [];
  for (const t of raw.transcripts ?? []) for (const s of t.sentences ?? []) {
    if (!Number.isFinite(s.begin_time) || !Number.isFinite(s.end_time) || s.begin_time < 0 || s.end_time < s.begin_time || typeof s.text !== 'string') fail('INVALID_TRANSCRIPT_TIMESTAMPS');
    if (s.end_time > audio.duration_ms + 2000) fail('TRANSCRIPT_EXCEEDS_AUDIO');
    segments.push({ source_id: audio.id, begin_ms: s.begin_time, end_ms: s.end_time, speaker_id: s.speaker_id ?? null, channel: t.channel_id ?? 0, text: s.text, confidence: null, review_status: 'automatic_only' });
  }
  segments.sort((a, b) => a.begin_ms - b.begin_ms || a.end_ms - b.end_ms);
  if (!segments.length) fail('EMPTY_TRANSCRIPT: 不把空转写当成成功，需检查录音或服务结果。');
  const gaps = []; let end = 0;
  for (const s of segments) { if (s.begin_ms > end + 1000) gaps.push({ begin_ms: end, end_ms: s.begin_ms, status: 'no_asr_text_unverified' }); end = Math.max(end, s.end_ms); }
  if (end < audio.duration_ms - 1000) gaps.push({ begin_ms: end, end_ms: audio.duration_ms, status: 'no_asr_text_unverified' });
  return { source_id: audio.id, source_name: audio.name, duration_ms: audio.duration_ms, model, segments, gaps, warning: 'speaker ID 仅在本文件内有效，不代表老师身份；本地未启用声纹分离时身份为未知。无文字区间不能自动当成静音。' };
}
export function providerURL(value) {
  const u = new URL(value);
  if (u.protocol !== 'https:' || u.username || u.password || u.port || !u.hostname.endsWith('.aliyuncs.com')) fail('UNTRUSTED_PROVIDER_URL');
  return u.href;
}
function publicJob(job, dir) {
  return { job_id: job.id, directory: dir, engine: job.engine ?? 'cloud', region: job.region, model: job.model,
    audios: job.audios.map(a => ({ id: a.id, name: a.name, duration_ms: a.duration_ms, source_channels: a.channels, selected_channel: a.channel, status: a.status, task_id: a.task_id, progress: a.progress, error: a.error })),
    complete: job.audios.every(a => a.status === 'DONE'),
    next: job.audios.every(a => a.status === 'DONE') ? 'read: 逐页读取两个音频的全部 segments，再按课堂技能融合；完成不等于听核。' : job.engine === 'local' ? (job.audios.some(a => a.status === 'FAILED') ? '先检查 error。INVALID_LOCAL_ALIGNMENT / LOCAL_ALIGNMENT_MISSING / LOCAL_TRANSCRIPT_TRUNCATED 不属于进程中断，不要盲目 start。旧词表任务改为不传 vocabulary 的新任务一次，保留旧证据；仍失败则报告具体片段。' : 'start 一次启动后台任务。处理课件时每 30–60 秒 poll；任一文件 DONE 即可 read。空闲时用技能 wait_audio.py 有界等待，随后 poll 发布转写；不要阻塞十分钟。仅 INTERRUPTED 可直接 start 续跑，不自动转云端。') : 'start 每次提交一份；poll 每次查询一次。两次 poll 建议间隔 15–30 秒。SUBMIT_UNKNOWN 禁止自动重发。' };
}

export class AudioService {
  constructor({ root, getKey = async () => undefined, signal, fetcher = fetch, resolveInput }) { this.rootInput = root; this.getKey = getKey; this.signal = signal; this.fetcher = fetcher; this.resolveInput = resolveInput; }
  async root() {
    if (!this.rootInput || !path.isAbsolute(this.rootInput)) fail('SESSION_WORKSPACE_REQUIRED');
    const root = await fs.realpath(this.rootInput);
    if (root === '/' || root === process.env.HOME) fail('请在独立的课程工作目录运行，不要把主目录或磁盘根目录设为工作区。');
    return root;
  }
  async request(url, options = {}, ms = 45000) {
    this.signal?.throwIfAborted();
    let res;
    try {
      res = await this.fetcher(url, { ...options, redirect: 'error', signal: AbortSignal.any([AbortSignal.timeout(ms), ...(this.signal ? [this.signal] : [])]) });
    } catch { fail('NETWORK_OR_CANCELLED: 网络失败、超时或已取消；已提交任务不要重复创建。'); }
    if (!res.ok) { const e = new Error(`HTTP_${res.status}: 请检查地域、模型权限、余额或服务限流。`); e.httpStatus = res.status; throw e; }
    return res;
  }
  async key() { const k = await this.getKey(); if (!k) fail('MISSING_DASHSCOPE_API_KEY: 请先运行本机密钥设置入口，不要在聊天中粘贴密钥。'); return k; }
  async doctor() {
    const executable = async p => { try { await fs.access(p, 1); return true; } catch { return false; } };
    return { plugin: 'dsh-classroom-audio', version: '1.1.1', default_engine: 'local', local: await localDoctor(), cloud_model: MODEL,
      key_configured: Boolean(await this.getKey()), ffmpeg: await executable(ffmpeg), ffprobe: await executable(ffprobe.path),
      default_region: 'beijing', workflow: '本地后台顺序处理两份约 45 分钟录音，按自然低能量处分段并保存进度，保留独立时间轴，融合为一篇课堂笔记。',
      uploads: '默认本地 start/poll 不上传、不计 API 费用。仅显式选择 engine=cloud 且授权后 start 才上传。无自动云端回退。' };
  }
  async prepare(args) {
    const root = await this.root(), region = args.region ?? 'beijing', engine = args.engine ?? 'local';
    if (!['local', 'cloud'].includes(engine)) fail('INVALID_ENGINE');
    const model = engine === 'local' ? LOCAL_MODEL : MODEL;
    if (!ENDPOINTS[region]) fail('INVALID_REGION');
    if (!Array.isArray(args.files) || args.files.length < 1 || args.files.length > 4) fail('FILES_REQUIRED: 一次 1–4 份，按课堂顺序。');
    const vocabulary = [...new Set(args.vocabulary ?? [])];
    if (engine === 'local' && vocabulary.length) fail('LOCAL_CONTEXT_DISABLED: 本地课堂转写不传 vocabulary；词表仅用于转写后校订。请省略该参数重新 prepare，不会上传。');
    if (vocabulary.length > 100 || vocabulary.some(x => typeof x !== 'string' || !x.trim() || x.length > 60)) fail('INVALID_VOCABULARY: 最多 100 个课件术语，每词不超过 60 字。');
    const audios = [];
    for (const [i, f] of args.files.entries()) {
      const staged = this.resolveInput ? await this.resolveInput(root, f) : null;
      const source = await sourcePath(root, staged?.path ?? f), p = await probe(source, this.signal);
      let channel = args.channel ?? 'auto';
      if (channel === 'auto' && p.channels > 1 && engine === 'local') channel = 'mix';
      if (channel === 'auto' && p.channels > 1) return { needs_channel_choice: true, file: path.basename(source), ...p, next: '多声道未被自动混合。可用 channel="0"/"1"选择经确认的教师麦克风，或明确同意 channel="mix"混合。先保留原件并确认声道；若未知可请用户选择。' };
      if (channel === 'auto') channel = '0';
      if (channel !== 'mix' && (!/^\d+$/.test(channel) || Number(channel) >= p.channels)) fail('INVALID_CHANNEL');
      audios.push({ id: `audio${i + 1}`, name: staged?.name ?? path.basename(source), source, ...p, channel, sha256: await sha(source, this.signal), status: 'PREPARED' });
    }
    if (new Set(audios.map(a => a.sha256)).size !== audios.length) fail('DUPLICATE_AUDIO: 同一录音被重复提供。');
    if (audios.reduce((n, a) => n + a.duration_ms, 0) > 180 * 60000) fail('CLASS_TOO_LONG: 单次课堂最多 180 分钟，请按课堂分批。');
    const id = stableHash([model, region, audios.map(a => [a.sha256, a.channel]), vocabulary]);
    const dir = await safeDir(root, `.dsh-work/classroom-audio/${id}`);
    return this.lock(dir, async () => {
      try { return publicJob(await json(path.join(dir, 'job.json')), dir); } catch (e) { if (e.code !== 'ENOENT') throw e; }
      for (const a of audios) {
        const ext = engine === 'local' ? 'wav' : 'flac';
        const out = path.join(dir, `${a.id}.${ext}`), tmp = path.join(dir, `${a.id}.${randomUUID()}.${ext}`);
        const filters = a.channel === 'mix' ? ['-ac', '1'] : ['-af', `pan=mono|c0=c${a.channel}`];
        await run(ffmpeg, ['-nostdin', '-v', 'error', '-n', '-i', a.source, '-map', '0:a:0', ...filters, '-ar', '16000', '-sample_fmt', 's16', '-c:a', engine === 'local' ? 'pcm_s16le' : 'flac', tmp], { signal: this.signal, timeout: 10 * 60000, maxBuffer: 1024 ** 2 }).catch(() => fail('AUDIO_CONVERSION_FAILED: 原件保留；检查格式、空间或取消状态。'));
        const converted = await probe(tmp, this.signal);
        if (Math.abs(converted.duration_ms - a.duration_ms) > 1000) fail('DURATION_CHANGED_DURING_CONVERSION');
        await fs.chmod(tmp, 0o600); await fs.rename(tmp, out);
        a.prepared_sha256 = await sha(out, this.signal);
        a.preparation = `${ext}, 16 kHz, 16 bit, mono; no denoise, silence removal, or timeline trim; channel=${a.channel}`;
      }
      const job = { version: 2, id, engine, model, region, vocabulary, created_at: new Date().toISOString(), audios };
      await atomic(path.join(dir, 'job.json'), job);
      return publicJob(job, dir);
    });
  }
  async lock(dir, fn) {
    const p = path.join(dir, '.lock');
    let handle;
    try { handle = await fs.open(p, 'wx', 0o600); } catch (e) { if (e.code === 'EEXIST') fail('JOB_LOCKED: 另一个调用正在处理；如进程曾崩溃，先确认无人处理再手动移走该任务 .lock。'); throw e; }
    try { await handle.writeFile(String(process.pid)); return await fn(); } finally { await handle.close(); await fs.unlink(p); }
  }
  async job(args, fn) {
    if (!/^[a-f0-9]{24}$/.test(args.job_id ?? '')) fail('INVALID_JOB_ID');
    const root = await this.root(), dir = await safeDir(root, `.dsh-work/classroom-audio/${args.job_id}`);
    return this.lock(dir, async () => {
      const job = await json(path.join(dir, 'job.json'));
      if (job.id !== args.job_id || !ENDPOINTS[job.region] || job.model !== (job.engine === 'local' ? LOCAL_MODEL : MODEL)) fail('INVALID_JOB');
      return fn(job, dir, root);
    });
  }
  async start(args) {
    return this.job(args, async (job, dir) => {
      if (job.engine === 'local') {
        if (job.vocabulary?.length) fail('LOCAL_CONTEXT_DISABLED: 旧词表任务不再启动；保留证据，省略 vocabulary 重新 prepare。');
        await this.refreshLocal(job, dir);
        if (job.audios.every(a => a.status === 'DONE')) return publicJob(job, dir);
        if (processAlive(job.local_pid) && job.audios.some(a => a.status === 'LOCAL_RUNNING')) return publicJob(job, dir);
        this.signal?.throwIfAborted();
        const started = await launchLocal(dir);
        job.local_pid = started.pid;
        for (const a of job.audios) if (a.status !== 'DONE') { a.status = 'LOCAL_RUNNING'; delete a.error; }
        await atomic(path.join(dir, 'job.json'), job);
        return publicJob(job, dir);
      }
      if (args.cloud_authorized !== true) fail('CLOUD_AUTHORIZATION_REQUIRED: 只有用户已授权云端处理课堂录音时才可设置为 true。');
      const a = args.audio_id ? job.audios.find(a => a.id === args.audio_id) : job.audios.find(a => a.status === 'PREPARED');
      if (!a) return publicJob(job, dir);
      if (a.status !== 'PREPARED') return publicJob(job, dir);
      const key = await this.key(), base = ENDPOINTS[job.region], file = path.join(dir, `${a.id}.flac`);
      const st = await regular(file);
      if (await sha(file, this.signal) !== a.prepared_sha256) fail('PREPARED_AUDIO_CHANGED');
      const headers = { Authorization: `Bearer ${key}` };
      const policy = (await (await this.request(`${base}/uploads?action=getPolicy&model=${MODEL}`, { headers })).json()).data;
      if (!policy || !policy.upload_dir || !Number.isFinite(policy.max_file_size_mb)) fail('INVALID_UPLOAD_POLICY');
      if (st.size > policy.max_file_size_mb * 1024 ** 2) fail('UPLOAD_TOO_LARGE: 转换后的文件仍超服务上限，尚未提交计费任务。');
      const ossKey = `${policy.upload_dir}/${randomUUID()}.flac`, form = new FormData();
      for (const [k, v] of Object.entries({ OSSAccessKeyId: policy.oss_access_key_id, policy: policy.policy, Signature: policy.signature, key: ossKey, 'x-oss-object-acl': 'private', 'x-oss-forbid-overwrite': 'true', success_action_status: '200' })) {
        if (typeof v !== 'string') fail('INVALID_UPLOAD_POLICY'); form.append(k, v);
      }
      form.append('file', await openAsBlob(file, { type: 'audio/flac' }), path.basename(ossKey));
      await this.request(providerURL(policy.upload_host), { method: 'POST', body: form }, 10 * 60000);
      // Persist BEFORE POST. Lost acknowledgements must never auto-repeat a billable submission.
      a.status = 'SUBMIT_UNKNOWN'; a.submitted_at = new Date().toISOString();
      await atomic(path.join(dir, 'job.json'), job);
      const parameters = { channel_id: [0], diarization_enabled: true, language_hints: ['zh', 'en'] };
      if (job.vocabulary.length) parameters.vocabulary = Object.fromEntries(job.vocabulary.map(w => [w, 3]));
      let result;
      try {
        result = await (await this.request(`${base}/services/audio/asr/transcription`, {
          method: 'POST', headers: { ...headers, 'Content-Type': 'application/json', 'X-DashScope-Async': 'enable', 'X-DashScope-OssResourceResolve': 'enable' },
          body: JSON.stringify({ model: MODEL, input: { file_urls: [`oss://${ossKey}`] }, parameters })
        })).json();
      } catch (e) {
        if (e.httpStatus && e.httpStatus >= 400 && e.httpStatus < 500 && e.httpStatus !== 408) a.status = 'PREPARED';
        a.error = e.message; await atomic(path.join(dir, 'job.json'), job); throw e;
      }
      const task = result.output?.task_id;
      if (typeof task !== 'string' || !/^[\w-]+$/.test(task)) fail('SUBMIT_UNKNOWN: 服务未返回有效任务编号；禁止自动重发。');
      a.task_id = task; a.status = 'PENDING'; delete a.error;
      await atomic(path.join(dir, 'job.json'), job);
      return publicJob(job, dir);
    });
  }
  async poll(args) {
    return this.job(args, async (job, dir) => {
      if (job.engine === 'local') { await this.refreshLocal(job, dir); return publicJob(job, dir); }
      const a = args.audio_id ? job.audios.find(a => a.id === args.audio_id) : job.audios.find(a => ['PENDING', 'RUNNING'].includes(a.status));
      if (!a || !['PENDING', 'RUNNING'].includes(a.status)) return publicJob(job, dir);
      const key = await this.key();
      const result = await (await this.request(`${ENDPOINTS[job.region]}/tasks/${encodeURIComponent(a.task_id)}`, { headers: { Authorization: `Bearer ${key}` } })).json();
      const out = result.output;
      if (!out || !['PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELED', 'UNKNOWN'].includes(out.task_status)) fail('INVALID_TASK_RESPONSE');
      if (out.task_status === 'SUCCEEDED') {
        if (!Array.isArray(out.results) || out.results.length !== 1 || out.results[0].subtask_status !== 'SUCCEEDED') {
          a.status = 'FAILED'; a.error = 'SUBTASK_FAILED: 总任务成功但文件子任务不成功，未当成完整转写。';
        } else {
          const raw = await (await this.request(providerURL(out.results[0].transcription_url))).json();
          // ASR content only; task/file signed URLs and credentials are never persisted.
          const retained = { properties: raw.properties, transcripts: raw.transcripts };
          await retainRaw(path.join(dir, `${a.id}.raw.json`), retained);
          const normalized = normalize(retained, a);
          await atomic(path.join(dir, `${a.id}.transcript.json`), normalized);
          await atomic(path.join(dir, `${a.id}.transcript.md`), `# ${a.id}: ${a.name}\n\n自动转写，未逐句听核。说话人标签不跨文件共享。\n\n` + normalized.segments.map(s => `[${a.id} ${stamp(s.begin_ms)}–${stamp(s.end_ms)}] [speaker:${s.speaker_id ?? '?'}] ${s.text}`).join('\n\n') + '\n');
          a.status = 'DONE'; delete a.error;
        }
      } else if (['FAILED', 'CANCELED', 'UNKNOWN'].includes(out.task_status)) {
        a.status = out.task_status; a.error = '服务任务未成功；不得自动重新发起付费任务。';
      } else a.status = out.task_status;
      await atomic(path.join(dir, 'job.json'), job);
      return publicJob(job, dir);
    });
  }
  async refreshLocal(job, dir) {
    const state = await localState(dir);
    for (const a of job.audios) {
      if (a.status === 'DONE') continue;
      if (state?.audios?.[a.id]) a.progress = state.audios[a.id];
      try {
        const raw = await json(path.join(dir, `${a.id}.local.raw.json`));
        const data = normalize(raw, a, LOCAL_MODEL);
        await retainRaw(path.join(dir, `${a.id}.raw.json`), raw);
        await atomic(path.join(dir, `${a.id}.transcript.json`), data);
        await atomic(path.join(dir, `${a.id}.transcript.md`), `# ${a.id}: ${a.name}\n\n本地自动转写，未逐句听核；发言者身份未知。\n\n` + data.segments.map(s => `[${a.id} ${stamp(s.begin_ms)}–${stamp(s.end_ms)}] ${s.text}`).join('\n\n') + '\n');
        a.status = 'DONE'; delete a.error;
      } catch (e) {
        if (e.code !== 'ENOENT') { a.status = 'FAILED'; a.error = e.message; continue; }
        if (state?.status === 'FAILED') { a.status = 'FAILED'; a.error = state.error ?? 'LOCAL_WORKER_FAILED'; }
        else if (a.status === 'LOCAL_RUNNING' && !processAlive(job.local_pid)) { a.status = 'INTERRUPTED'; a.error = '本地进程已结束；再次 start 从已完成的分段继续。'; }
      }
    }
    await atomic(path.join(dir, 'job.json'), job);
  }
  async read(args) {
    return this.job(args, async (job, dir) => {
      const a = job.audios.find(a => a.id === args.audio_id);
      if (!a || a.status !== 'DONE') fail('TRANSCRIPT_NOT_READY');
      const data = await json(path.join(dir, `${a.id}.transcript.json`));
      const offset = args.offset ?? 0, limit = args.limit ?? 60;
      if (!Number.isInteger(offset) || offset < 0 || offset > data.segments.length || !Number.isInteger(limit) || limit < 1 || limit > 100) fail('INVALID_PAGINATION');
      const segments = []; let bytes = 0;
      for (const s of data.segments.slice(offset, offset + limit)) { const n = Buffer.byteLength(JSON.stringify(s)); if (bytes + n > 24000 && segments.length) break; segments.push(s); bytes += n; }
      const end = offset + segments.length;
      return { source_id: a.id, name: a.name, duration_ms: a.duration_ms, total: data.segments.length, offset, next_offset: end < data.segments.length ? end : null, segments,
        gaps_file: path.join(dir, `${a.id}.transcript.json`), gaps_count: data.gaps.length, warning: data.warning };
    });
  }
  async clip(args) {
    return this.job(args, async (job, dir, root) => {
      const a = job.audios.find(a => a.id === args.audio_id), start = args.start_seconds, end = args.end_seconds;
      if (!a || !Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start || end * 1000 > a.duration_ms || end - start > 120) fail('INVALID_CLIP_RANGE: 片段最长 120 秒。');
      const source = await sourcePath(root, a.source);
      if (await sha(source, this.signal) !== a.sha256) fail('ORIGINAL_AUDIO_CHANGED');
      const file = path.join(dir, `${a.id}.review-${start}-${end}.wav`);
      try { await regular(file); } catch (e) {
        if (e.code !== 'ENOENT') throw e;
        await run(ffmpeg, ['-nostdin', '-v', 'error', '-n', '-ss', String(start), '-i', source, '-t', String(end - start), '-map', '0:a:0', '-c:a', 'pcm_s16le', file], { signal: this.signal, timeout: 60000, maxBuffer: 1024 ** 2 }).catch(() => fail('CLIP_FAILED'));
        await fs.chmod(file, 0o600);
      }
      return { file, source_id: a.id, offset_ms: start * 1000, duration_ms: (end - start) * 1000, note: '保留原声道，不降噪；可供用户回听。DSH 文字模型不得声称听过。二次转写的时间戳必须加 offset_ms。' };
    });
  }
  async call(args) {
    switch (args.action) {
      case 'doctor': return this.doctor();
      case 'prepare': return this.prepare(args);
      case 'start': return this.start(args);
      case 'poll': return this.poll(args);
      case 'status': return this.job(args, async (job, dir) => { if (job.engine === 'local') await this.refreshLocal(job, dir); return publicJob(job, dir); });
      case 'read': return this.read(args);
      case 'clip': return this.clip(args);
      default: fail('UNKNOWN_ACTION');
    }
  }
}
