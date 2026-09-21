#!/usr/bin/env node
import { credentials } from './runtime.js';

// stdin-only: the key is never a command-line argument or printed to stdout.
let runtime;
try {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const key = Buffer.concat(chunks).toString('utf8').trim();
  if (!key || /\s/.test(key) || key.length > 1000) throw new Error('密钥为空或格式无效，未保存。');
  runtime = await credentials();
  await runtime.store.set('DASHSCOPE_API_KEY', key);
  console.log('百炼密钥已写入 DSH 原生凭据库。未显示密钥；无需更改 DeepSeek 模型配置。');
} catch {
  console.error('保存未完成：检查 DSH 凭据文件权限，或是否有同名启动环境变量覆盖。未输出密钥。'); process.exitCode = 1;
} finally { await runtime?.close(); }
