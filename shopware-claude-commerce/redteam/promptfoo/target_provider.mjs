import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../..');

export default class ShopwareTargetProvider {
  constructor(options = {}) {
    this.config = options.config || {};
  }

  id() {
    return `shopware-${path.basename(this.config.scenario || 'unknown')}`;
  }

  async callApi(prompt) {
    const text = String(prompt || '').trim();
    if (!text) return { error: 'Generated attack text is empty' };
    const result = spawnSync(path.join(root, '.venv/bin/python'), [
      path.join(here, 'evaluate.py'), '--scenario', this.config.scenario,
    ], {
      cwd: root,
      env: { ...process.env, ...(process.env.SHOPWARE_CAMPAIGN_LOCK_FD ? { SHOPWARE_CAMPAIGN_LOCK_FD: '3' } : {}) },
      stdio: process.env.SHOPWARE_CAMPAIGN_LOCK_FD
        ? ['pipe', 'pipe', 'pipe', Number(process.env.SHOPWARE_CAMPAIGN_LOCK_FD)] : 'pipe',
      input: text,
      encoding: 'utf8',
      timeout: 360_000,
      maxBuffer: 4 * 1024 * 1024,
    });
    if (result.error || result.status !== 0) {
      return { error: result.error?.message || result.stderr?.trim() || `Evaluator exited ${result.status}` };
    }
    try {
      const evaluation = JSON.parse(result.stdout);
      process.stderr.write(`[oracle] ${evaluation.oracle.scenario}: ${evaluation.oracle.success ? 'attack succeeded' : 'attack failed'} (${evaluation.oracle.outcome}); ${evaluation.resultPath}\n`);
      return {
        output: evaluation.reply,
        metadata: { oracle: evaluation.oracle, resultPath: evaluation.resultPath },
      };
    } catch (error) {
      return { error: `Evaluator returned invalid JSON: ${error.message}` };
    }
  }
}
