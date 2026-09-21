#!/usr/bin/env node
import { AudioService } from '../lib/core.js';
import { credentials } from './runtime.js';

let runtime;
try {
  const args = JSON.parse(process.argv[2] ?? '{"action":"doctor"}');
  runtime = await credentials();
  const controller = new AbortController();
  process.once('SIGINT', () => controller.abort());
  process.once('SIGTERM', () => controller.abort());
  const service = new AudioService({ root: process.cwd(), signal: controller.signal,
    getKey: async () => (await runtime.store.resolve('DASHSCOPE_API_KEY'))?.value });
  console.log(JSON.stringify(await service.call(args), null, 2));
} catch (e) {
  console.error(e.message); process.exitCode = 1;
} finally { await runtime?.close(); }
