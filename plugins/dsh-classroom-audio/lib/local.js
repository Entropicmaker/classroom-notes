import * as fs from 'node:fs/promises';
import { constants } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

export const LOCAL_MODEL = 'Qwen3-ASR-1.7B-MLX';
export const INSTALL = path.join(os.homedir(), '.dsh/vendor/dsh-classroom-audio');
export const PYTHON = path.join(INSTALL, '.venv/bin/python');
export const MODELS = path.join(os.homedir(), '.dsh/models');
export const WORKER = fileURLToPath(new URL('../bin/local-worker.py', import.meta.url));
const exists = async p => { try { await fs.access(p); return true; } catch { return false; } };
export async function localDoctor() {
  const model = path.join(MODELS, 'Qwen3-ASR-1.7B'), aligner = path.join(MODELS, 'Qwen3-ForcedAligner-0.6B');
  const ready = async dir => {
    if (!await exists(path.join(dir, 'config.json'))) return false;
    const files = await fs.readdir(dir).catch(() => []);
    let weights = files.filter(f => f.endsWith('.safetensors'));
    if (files.includes('model.safetensors.index.json')) {
      const index = JSON.parse(await fs.readFile(path.join(dir, 'model.safetensors.index.json'), 'utf8'));
      weights = [...new Set(Object.values(index.weight_map ?? {}))];
    }
    if (!weights.length) return false;
    return (await Promise.all(weights.map(async f => {
      if (path.basename(f) !== f) return false;
      try { return (await fs.stat(path.join(dir, f))).size > 1024 * 1024; } catch { return false; }
    }))).every(Boolean);
  };
  const runtime = await exists(PYTHON), weights = await ready(model), timestamps = await ready(aligner);
  return { ready: runtime && weights && timestamps, runtime, weights, timestamps, model: LOCAL_MODEL, api_key_required: false, uploads_audio: false, speaker_diarization: false };
}
export function processAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try { process.kill(pid, 0); return true; } catch (e) { return e.code === 'EPERM'; }
}
export async function localState(dir) {
  try {
    const file = path.join(dir, 'local-state.json'), st = await fs.lstat(file);
    if (!st.isFile() || st.isSymbolicLink()) throw new Error('UNSAFE_LOCAL_STATE');
    return JSON.parse(await fs.readFile(file, 'utf8'));
  } catch (e) { if (e.code === 'ENOENT') return null; throw e; }
}
export async function launchLocal(dir) {
  if (!(await localDoctor()).ready) throw new Error('LOCAL_MODEL_NOT_READY: 本地运行环境或模型缺失；不会自动改用云端。');
  const previous = await localState(dir);
  if (previous && processAlive(previous.pid) && !['DONE', 'FAILED'].includes(previous.status)) return previous;
  // No listener socket or server to manage. The job owns a short-lived offline worker.
  const output = await fs.open(path.join(dir, 'local-worker.log'), constants.O_WRONLY | constants.O_CREAT | constants.O_APPEND | constants.O_NOFOLLOW, 0o600);
  try {
    const child = spawn(PYTHON, [WORKER, '--job', dir, '--models', MODELS], {
      detached: true, stdio: ['ignore', output.fd, output.fd],
      env: { PATH: process.env.PATH, HOME: os.homedir(), LANG: 'zh_CN.UTF-8',
        HF_HUB_OFFLINE: '1', HF_HUB_DISABLE_TELEMETRY: '1', DO_NOT_TRACK: '1', TOKENIZERS_PARALLELISM: 'false', PYTHONUNBUFFERED: '1' }
    });
    await new Promise((resolve, reject) => { child.once('spawn', resolve); child.once('error', reject); });
    child.unref();
    return { pid: child.pid, status: 'STARTING' };
  } finally { await output.close(); }
}
