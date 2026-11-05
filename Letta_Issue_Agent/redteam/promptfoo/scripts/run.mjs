import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const promptfooDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
if (!process.env.OPENAI_API_KEY?.trim()) {
  try {
    process.loadEnvFile(path.resolve(promptfooDir, '../../.env'));
  } catch (error) {
    if (error?.code !== 'ENOENT') throw error;
  }
}
if (!process.env.OPENAI_API_KEY?.trim()) {
  throw new Error('OPENAI_API_KEY is required in the shell or Letta_Issue_Agent/.env');
}
if (!process.env.OPENAI_MODEL?.trim()) throw new Error('Set OPENAI_MODEL before attack generation');
const prepared = spawnSync(process.execPath,
  [path.join(promptfooDir, 'scripts/prepare_config.mjs'), ...process.argv.slice(2)],
  { cwd: promptfooDir, stdio: 'inherit' });
if (prepared.error) throw prepared.error;
if (prepared.status !== 0) process.exit(prepared.status ?? 1);

const result = spawnSync(path.join(promptfooDir, 'node_modules/.bin/promptfoo'),
  ['redteam', 'run', '-c', 'scenario-config.yaml', '-j', '1', '--delay', '5000'], {
    cwd: promptfooDir,
    stdio: 'inherit',
    env: {
      ...process.env,
      PROMPTFOO_CONFIG_DIR: path.join(promptfooDir, '.promptfoo'),
      PROMPTFOO_DISABLE_REDTEAM_REMOTE_GENERATION: 'true',
    },
  });
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
