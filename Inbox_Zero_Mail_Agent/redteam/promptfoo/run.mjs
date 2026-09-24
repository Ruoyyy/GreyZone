import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseEnv } from 'node:util';
import YAML from 'yaml';

const here = path.dirname(fileURLToPath(import.meta.url));
const environmentRoot = path.resolve(here, '../..');
const scenarioRoot = path.join(environmentRoot, 'benchmark', 'scenarios');
const appRoot = path.join(environmentRoot, 'apps', 'web');
const allowedMonitors = new Set(['kb', 'mail', 'phishing', 'pipeline']);

function parseArgs(args) {
  let scenarioId = 'knowledge-leak';
  let prepare = false;
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === '--scenario' && args[i + 1]) scenarioId = args[++i];
    else if (args[i] === '--prepare') prepare = true;
    else throw new Error(`Unknown or incomplete argument: ${args[i]}`);
  }
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(scenarioId)) {
    throw new Error('Scenario must be a name under benchmark/scenarios');
  }
  return { scenarioId, prepare };
}

function validateScenario(scenario, scenarioId) {
  if (scenario.schemaVersion !== 1 || scenario.id !== scenarioId || scenario.environment !== 'inbox-zero-mail') {
    throw new Error('Unsupported or mismatched benchmark scenario');
  }
  if (!/^[a-z0-9.-]+@[a-z0-9.-]+$/i.test(scenario.attackerEmail) ||
      !/^[a-z0-9.-]+@[a-z0-9.-]+$/i.test(scenario.managedEmail)) {
    throw new Error('Scenario email addresses are invalid');
  }
  if (!/^[a-z0-9-]+\.md$/.test(scenario.objectiveFile) ||
      !String(scenario.subject || '').trim() || !String(scenario.policy || '').trim()) {
    throw new Error('Scenario objectiveFile, subject, and policy are required');
  }
  if (!Number.isInteger(scenario.maxTurns) || scenario.maxTurns < 1 || scenario.maxTurns > 20 ||
      !Number.isInteger(scenario.replyTimeoutSeconds) || scenario.replyTimeoutSeconds < 1 || scenario.replyTimeoutSeconds > 300 ||
      !Number.isInteger(scenario.pollIntervalSeconds) || scenario.pollIntervalSeconds < 1 || scenario.pollIntervalSeconds > scenario.replyTimeoutSeconds) {
    throw new Error('Invalid scenario turn or time limits');
  }
  if (!Array.isArray(scenario.oracleMonitors) || !scenario.oracleMonitors.length ||
      !scenario.oracleMonitors.every((name) => allowedMonitors.has(name)) ||
      !Array.isArray(scenario.oracleSuccessSurfaces) || !scenario.oracleSuccessSurfaces.length ||
      !scenario.oracleSuccessSurfaces.every((name) => scenario.oracleMonitors.includes(name) && name !== 'pipeline')) {
    throw new Error('Invalid Oracle monitors or success surfaces');
  }
}

function buildConfig({ baseConfig, scenario, objective, runDir }) {
  const config = YAML.parse(baseConfig);
  const providerPath = path.join(here, 'mail_target_provider.mjs');
  if (!Array.isArray(config.targets) || config.targets.length !== 1 ||
      !config.targets[0]?.config?.mcp || !config.redteam ||
      !Array.isArray(config.redteam.plugins) || !Array.isArray(config.redteam.strategies)) {
    throw new Error('Promptfoo base config must define one MCP target, plugins, and strategies');
  }
  const policyPlugins = config.redteam.plugins.filter((plugin) => plugin.id === 'policy');
  if (policyPlugins.length !== 1 || !policyPlugins[0].config) {
    throw new Error('Promptfoo base config must define exactly one benchmark policy plugin');
  }
  const target = config.targets[0];
  target.id = `file://${providerPath}`;
  target.label = `inbox-zero-${scenario.id}`;
  Object.assign(target.config, {
    attackerEmail: scenario.attackerEmail,
    managedEmail: scenario.managedEmail,
    subject: scenario.subject,
    replyTimeoutSeconds: scenario.replyTimeoutSeconds,
    pollIntervalSeconds: scenario.pollIntervalSeconds,
    customerOutbox: path.join(appRoot, '.tmp', 'customer-mail'),
    transportUrl: process.env.TRANSPORT_URL || target.config.transportUrl,
  });
  Object.assign(target.config.mcp, {
    command: process.execPath,
    args: ['--import', 'tsx', path.join(appRoot, 'scripts', 'customer-mail-mcp.ts')],
    cwd: appRoot,
  });
  config.description = `${config.description} — ${scenario.id}`;
  config.outputPath = path.join(runDir, 'results.json');
  config.redteam.purpose = objective;
  policyPlugins[0].config.policy = scenario.policy;
  for (const strategy of config.redteam.strategies) {
    if (strategy.id === 'crescendo') strategy.config.maxTurns = scenario.maxTurns;
  }
  return config;
}

async function main() {
  const { scenarioId, prepare } = parseArgs(process.argv.slice(2));
  const scenarioDir = path.join(scenarioRoot, scenarioId);
  const scenarioBytes = await readFile(path.join(scenarioDir, 'scenario.json'));
  const scenario = JSON.parse(scenarioBytes.toString('utf8'));
  validateScenario(scenario, scenarioId);
  const objective = (await readFile(path.join(scenarioDir, scenario.objectiveFile), 'utf8')).trim();
  if (!objective) throw new Error('Benchmark objective is empty');
  const baseConfig = await readFile(path.join(here, 'promptfooconfig.yaml'), 'utf8');
  const contentHash = createHash('sha256').update(scenarioBytes).update(objective).update(baseConfig).digest('hex');
  const runId = `${new Date().toISOString().replace(/[:.]/g, '')}-${contentHash.slice(0, 8)}`;
  const runDir = path.join(environmentRoot, '.tmp', 'promptfoo', runId);
  await mkdir(runDir, { recursive: true });
  const config = buildConfig({ baseConfig, scenario, objective, runDir });
  const configPath = path.join(runDir, 'promptfooconfig.yaml');
  await writeFile(configPath, YAML.stringify(config), { mode: 0o600 });
  await writeFile(path.join(runDir, 'benchmark.json'), `${JSON.stringify({
    scenarioId,
    scenarioHash: createHash('sha256').update(scenarioBytes).update(objective).digest('hex'),
    promptfooConfigHash: createHash('sha256').update(baseConfig).digest('hex'),
    oracleMonitors: scenario.oracleMonitors,
    oracleSuccessSurfaces: scenario.oracleSuccessSurfaces,
    createdAt: new Date().toISOString(),
  }, null, 2)}\n`, { mode: 0o600 });
  console.log(`Prepared ${scenarioId}: ${runDir}`);
  if (prepare) return;
  try {
    process.loadEnvFile(path.join(here, '.env'));
  } catch (error) {
    if (error?.code !== 'ENOENT') throw error;
  }
  if (!process.env.ZAI_API_KEY) {
    try {
      const appEnv = parseEnv(await readFile(path.join(appRoot, '.env'), 'utf8'));
      if (appEnv.LLM_API_KEY) process.env.ZAI_API_KEY = appEnv.LLM_API_KEY;
    } catch (error) {
      if (error?.code !== 'ENOENT') throw error;
    }
  }
  const generators = [config.redteam.provider, ...config.redteam.strategies.map((strategy) => strategy.config?.redteamProvider)].filter(Boolean);
  for (const variable of new Set(generators.map((provider) => provider.config?.apiKeyEnvar).filter(Boolean))) {
    if (!process.env[variable]) throw new Error(`${variable} is required for local Promptfoo attack generation`);
  }
  const token = (await readFile(path.join(appRoot, '.tmp', 'customer-mail', '.transport-token'), 'utf8')).trim();
  if (!token) throw new Error('Customer mail transport token is empty');
  const promptfooBin = path.join(here, 'node_modules', '.bin', 'promptfoo');
  const env = {
    ...process.env,
    PATH: `${path.dirname(process.execPath)}${path.delimiter}${process.env.PATH || ''}`,
    TRANSPORT_TOKEN: token,
    PROMPTFOO_CONFIG_DIR: path.join(runDir, 'cache'),
    PROMPTFOO_DISABLE_REDTEAM_REMOTE_GENERATION: 'true',
    PROMPTFOO_DISABLE_TELEMETRY: 'true',
  };
  const lockRunner = path.join(environmentRoot, 'redteam', 'run_locked.py');
  const result = spawnSync('python3', [lockRunner, '--', promptfooBin, 'redteam', 'run',
    '-c', configPath, '-j', '1', '--delay', '5000'], {
    cwd: here,
    env,
    stdio: 'inherit',
  });
  if (result.error) throw result.error;
  process.exitCode = result.status ?? 1;
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});
