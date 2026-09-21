import { loadDsh } from '../bin/runtime.js';
import { AudioService } from './core.js';
import { sessionFiles, resolveUploadedInput } from './attachments.js';

export const name = 'dsh-classroom-audio';
export const inject = ['tools', 'credentials', 'attachments'];
export async function apply(ctx) {
  const { defineTool } = await loadDsh('@deepseek-ai/dsh-tools');
  const { credentialRef } = await loadDsh('@deepseek-ai/dsh-credentials');
  ctx.tools.register(defineTool({
    name: 'classroom_audio',
    description: '课堂长录音自动转写。先加载 classroom-notes 技能。默认本地 Qwen3-ASR，不用 Key、不上传。attachments 列出会话录音，prepare 可直接取上传附件，start 后台顺序处理两份45分钟录音，poll查进度，read分页读全文。仅显式 engine=cloud 且授权才用付费云端；本地不自动切云端。',
    parameters: {
      action: { type: 'string', required: true, enum: ['doctor', 'attachments', 'prepare', 'start', 'poll', 'status', 'read', 'clip'] },
      files: { type: 'array', items: { type: 'string' }, description: 'prepare: 按课堂顺序的上传音频路径或工作区路径（1–4 份）；attachments 可列出已上传文件' },
      engine: { type: 'string', enum: ['local', 'cloud'], description: 'prepare 默认 local，不需要 API Key。cloud 仅在用户明确选择时使用' },
      vocabulary: { type: 'array', items: { type: 'string' }, description: '仅 cloud prepare 可用，最多 100 个术语；local 必须省略，术语留作转写后校订，避免词表复述和对齐失败' },
      region: { type: 'string', enum: ['beijing', 'singapore'], description: 'prepare 默认 beijing，必须与 Key 地域一致' },
      channel: { type: 'string', description: 'prepare: auto 默认。本地自动混合普通手机双声道并保留原件；也可选 0/1 或 mix' },
      job_id: { type: 'string', description: 'prepare 返回的稳定任务标识，后续沿用' },
      audio_id: { type: 'string', description: 'audio1/audio2；read/clip 必填，start/poll 可省略自动选下一份' },
      cloud_authorized: { type: 'boolean', description: '仅 cloud start 要求 true。本地 start 不需此参数' },
      offset: { type: 'integer', description: 'read 起始句索引，默认 0' },
      limit: { type: 'integer', description: 'read 页大小，默认 60，最大 100，继续至 next_offset=null' },
      start_seconds: { type: 'number', description: 'clip 原录音起始秒数' },
      end_seconds: { type: 'number', description: 'clip 原录音结束秒数，片段最长 120 秒' }
    },
    timeoutMs: 20 * 60000,
    output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: value }] },
    async execute(args, exec) {
      const session = exec.agent?.session;
      if (args.action === 'attachments') return JSON.stringify({ files: sessionFiles(session, ctx.attachments).map(({ path, name }) => ({ path, name })), note: '只列本会话上传录音。按用户给出的顺序选择，不把历史其他课程的录音自动合并。' });
      const service = new AudioService({ root: exec.agent?.session.header.cwd, signal: exec.signal,
        resolveInput: (root, input) => resolveUploadedInput(root, input, session, ctx.attachments, exec.signal),
        getKey: async () => (await ctx.credentials.resolve(credentialRef('DASHSCOPE_API_KEY')))?.value });
      return JSON.stringify(await service.call(args));
    }
  }));
}
