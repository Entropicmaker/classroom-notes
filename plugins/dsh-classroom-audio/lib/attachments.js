import path from 'node:path';
import * as fs from 'node:fs/promises';
import { pipeline } from 'node:stream/promises';
import { randomUUID } from 'node:crypto';
import { safeDir, within } from './core.js';

const AUDIO = /\.(m4a|mp3|wav|flac|aac|ogg|opus|wma|amr|mp4|webm|caf|aiff)$/i;
export function sessionFiles(session, store) {
  const files = new Map();
  for (const message of session?.deriveMessages?.() ?? []) {
    if (message.role !== 'user' || !Array.isArray(message.content)) continue;
    for (const block of message.content) {
      if (block.type !== 'file' || !AUDIO.test(block.attachment?.name ?? '')) continue;
      const ref = block.attachment, host = store?.fileHostPath(ref);
      if (host) files.set(host, { path: host, name: ref.name, ref });
    }
  }
  return [...files.values()];
}
export async function resolveUploadedInput(root, input, session, store, signal) {
  const requested = path.resolve(root, input);
  if (within(root, requested)) return null;
  // Never authorize the entire ~/.dsh tree: match a real file reference in this session.
  const entry = sessionFiles(session, store).find(x => x.path === requested);
  if (!entry) throw new Error('NOT_A_SESSION_ATTACHMENT: 非工作区文件也不是当前会话已上传附件。请上传该录音，或由 DSH 文件工具在限定授权后复制。');
  const maxBytes = 2 * 1024 ** 3;
  if (!Number.isSafeInteger(entry.ref.bytes) || entry.ref.bytes <= 0 || entry.ref.bytes > maxBytes) throw new Error('INVALID_AUDIO_FILE: 单份录音最多 2 GiB。');
  const inbox = await safeDir(root, '.dsh-work/classroom-audio/inbox');
  const safeName = path.basename(entry.name).replace(/[^\p{L}\p{N}._-]/gu, '_');
  const temp = path.join(inbox, `${randomUUID()}-${safeName}`);
  async function* bounded() {
    let size = 0;
    for await (const chunk of store.readFileStream(entry.ref, signal)) {
      size += chunk.length;
      if (size > maxBytes || size > entry.ref.bytes) throw new Error('ATTACHMENT_SIZE_MISMATCH');
      yield chunk;
    }
    if (size !== entry.ref.bytes) throw new Error('ATTACHMENT_SIZE_MISMATCH');
  }
  // Open before consuming input so a rejected stream cannot create its file after cleanup.
  const handle = await fs.open(temp, 'wx', 0o600);
  try {
    await pipeline(bounded(), handle.createWriteStream(), { signal });
  } catch (e) {
    await handle.close();
    await fs.unlink(temp).catch(error => { if (error.code !== 'ENOENT') throw error; });
    throw e;
  }
  return { path: temp, name: entry.name };
}
