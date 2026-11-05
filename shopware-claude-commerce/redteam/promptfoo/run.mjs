import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseEnv } from 'node:util';
import YAML from 'yaml';

function configureOpenAIModels(config, env) {
  if (!env.OPENAI_MODEL?.trim()) throw new Error('Set OPENAI_MODEL before attack generation');
  const providers = [config.redteam.provider,
    ...config.redteam.strategies.map((strategy) => strategy.config?.redteamProvider)].filter(Boolean);
  for (const provider of providers) {
    provider.id = `openai:chat:${env.OPENAI_MODEL.trim()}`;
    provider.config = { ...provider.config,
      apiBaseUrl: env.OPENAI_BASE_URL || 'https://api.openai.com/v1', apiKeyEnvar: 'OPENAI_API_KEY' };
    delete provider.config.passthrough;
  }
}

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../..');
const ids = new Set([
  'buyer-misdirection', 'buyer-exfil', 'merchant-exfil',
  'merchant-floor-breach', 'merchant-refund',
]);

async function main() {
  const args = process.argv.slice(2);
  const prepare = args.includes('--prepare');
  const scenarioId = args[args.indexOf('--scenario') + 1];
  if (!ids.has(scenarioId) || args.some((arg, index) =>
    arg !== '--prepare' && arg !== '--scenario' && index !== args.indexOf('--scenario') + 1)) {
    throw new Error(`Usage: npm run redteam -- --scenario <${[...ids].join('|')}> [--prepare]`);
  }
  const scenarioDir = path.join(root, 'benchmark/scenarios', scenarioId);
  const scenario = JSON.parse(await readFile(path.join(scenarioDir, 'scenario.json'), 'utf8'));
  if (scenario.schemaVersion !== 1 || scenario.id !== scenarioId || scenario.environment !== 'shopware-commerce') {
    throw new Error('Scenario schema, id, or environment does not match');
  }
  const category = (process.env.TASK_CATEGORY || 'food').trim().toLowerCase();
  const categories = { food: 'Food', clothing: 'Clothing', electronics: 'Electronics', office: 'Office' };
  const products = { food: 'Everyday Sourdough Bread', clothing: 'Everyday Cotton T-Shirt', electronics: 'Everyday Wireless Earbuds', office: 'Everyday Desk Notebook' };
  if (scenarioId === 'buyer-misdirection' && !categories[category]) {
    throw new Error(`Unknown TASK_CATEGORY: ${category}`);
  }
  const objective = (await readFile(path.join(scenarioDir, scenario.objectiveFile), 'utf8'))
    .replaceAll('{category}', categories[category] || category)
    .replaceAll('{product}', products[category] || '').trim();
  if (!objective) throw new Error('Scenario objective is empty');
  const config = YAML.parse(await readFile(path.join(here, 'promptfooconfig.yaml'), 'utf8'));
  const runId = `${new Date().toISOString().replace(/[:.]/g, '')}-${createHash('sha256').update(scenarioId).update(objective).digest('hex').slice(0, 8)}`;
  const runDir = path.join(root, '.run/promptfoo', runId);
  await mkdir(runDir, { recursive: true });
  config.targets[0].id = `file://${path.join(here, 'target_provider.mjs')}`;
  config.targets[0].label = `shopware-${scenarioId}`;
  config.targets[0].config.scenario = scenarioDir;
  config.redteam.purpose = objective;
  config.redteam.plugins[0].id = `file://${path.join(here, 'shopware-injection.yaml')}`;
  config.outputPath = path.join(runDir, 'results.json');
  async function localEnv(file) {
    try {
      return parseEnv(await readFile(file, 'utf8'));
    } catch (error) {
      if (error.code === 'ENOENT') return {};
      throw error;
    }
  }
  const env = {
    ...await localEnv(path.join(root, '.env')),
    ...await localEnv(path.join(here, '.env')),
    ...process.env,
  };
  if (env.PROMPTFOO_ATTACK_PROVIDER) {
    config.redteam.provider.id = env.PROMPTFOO_ATTACK_PROVIDER;
    if (!config.redteam.provider.id.startsWith('openai:chat:glm-')) {
      delete config.redteam.provider.config;
    }
  }
  if (!prepare) configureOpenAIModels(config, env);
  const configPath = path.join(runDir, 'promptfooconfig.yaml');
  await writeFile(configPath, YAML.stringify(config), { mode: 0o600 });
  console.log(`Prepared ${scenarioId}: ${configPath}`);
  if (prepare) return;
  const generatorKey = config.redteam.provider.config?.apiKeyEnvar ||
    (config.redteam.provider.id.startsWith('anthropic:') ? 'ANTHROPIC_API_KEY' : null);
  if (generatorKey && !env[generatorKey]) {
    throw new Error(`${generatorKey} is required for attack generation`);
  }
  for (const [label, url] of [
    ['shopping API', `${(env.STOREFRONT_API_URL || 'http://127.0.0.1:8004').replace(/\/$/, '')}/api/health`],
    ['merchant API', `${(env.MERCHANT_API_URL || 'http://127.0.0.1:8005').replace(/\/$/, '')}/api/merchant/health`],
  ]) {
    let response;
    try {
      response = await fetch(url, { signal: AbortSignal.timeout(5000) });
    } catch {
      throw new Error(`${label} is unavailable at ${url}`);
    }
    if (!response.ok || (await response.json()).ok !== true) {
      throw new Error(`${label} is not ready at ${url}`);
    }
  }
  const result = spawnSync(path.join(here, 'node_modules/.bin/promptfoo'), [
    'redteam', 'run', '-c', configPath, '-j', '1', '--delay', '5000',
  ], {
    cwd: here,
    env: {
      ...env,
      PROMPTFOO_CONFIG_DIR: path.join(runDir, 'cache'),
      PROMPTFOO_DISABLE_REDTEAM_REMOTE_GENERATION: 'true',
      PROMPTFOO_DISABLE_TELEMETRY: 'true',
    },
    stdio: 'inherit',
  });
  if (result.error) throw result.error;
  process.exitCode = result.status ?? 1;
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});
