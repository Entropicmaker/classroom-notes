import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

// The installed Harness is the authority for Cordis and credential-store versions.
let dshRequire;
export async function loadDsh(name) {
  if (!dshRequire) {
    const root = execFileSync('npm', ['root', '-g'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
    dshRequire = createRequire(path.join(root, '@deepseek-ai/dsh/package.json'));
  }
  return import(pathToFileURL(dshRequire.resolve(name)).href);
}
export async function credentials() {
  const { Context } = await loadDsh('@deepseek-ai/cordis');
  const { default: LocalCredentials } = await loadDsh('@deepseek-ai/dsh-credentials-local');
  const ctx = new Context();
  await ctx.plugin(LocalCredentials, { watch: false });
  return { store: ctx.credentials, close: () => ctx.fiber.dispose() };
}
