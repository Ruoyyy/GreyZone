import { spawnSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseEnv } from 'node:util';
import YAML from 'yaml';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../..');
const allowed = new Set(['exfil', 'email-exfil', 'kb-integrity', 'private-agent-leak', 'fs-escape']);

export function validateScenario(scenario, id) {
  if (scenario.schemaVersion !== 1 || scenario.id !== id || scenario.environment !== 'clawith' ||
      scenario.attacker !== 'alice' || scenario.attackChannel !== 'group_message' ||
      !/^[a-z0-9-]+\.md$/.test(scenario.objectiveFile) ||
      !String(scenario.policy || '').trim() || !String(scenario.attackerTurn || '').trim() ||
      !Array.isArray(scenario.oracleSuccessSurfaces) || scenario.oracleSuccessSurfaces.length !== 1 ||
      !allowed.has(scenario.oracleSuccessSurfaces[0])) {
    throw new Error(`Invalid Clawith benchmark scenario: ${id}`);
  }
}

export function configureGenerated(generated, episode) {
  if (!Array.isArray(generated.targets) || generated.targets.length !== 1 ||
      !Array.isArray(generated.tests) || generated.tests.length !== 1 ||
      generated.tests[0]?.metadata?.strategyId !== 'crescendo' ||
      !Array.isArray(generated.tests[0].assert) ||
      generated.tests[0].assert.length !== 1 ||
      generated.tests[0].assert[0]?.type !== 'promptfoo:redteam:policy' ||
      generated.defaultTest?.assert?.[0]?.type !== 'javascript') {
    throw new Error('Generated Promptfoo config does not contain one Crescendo policy test');
  }
  // The policy plugin generates the attack. Oracle alone grades each turn.
  generated.tests[0].assert = [];
  generated.targets[0].config.episode = episode;
  return generated;
}

function command(name, args, env) {
  const result = spawnSync(name, args, { cwd: root, env, stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${name} ${args.join(' ')} exited ${result.status}`);
}

async function main() {
  let id = 'chat-exfil';
  let configOnly = false;
  const argv = process.argv.slice(2);
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--scenario' && argv[i + 1]) id = argv[++i];
    else if (argv[i] === '--config-only') configOnly = true;
    else throw new Error(`Unknown or incomplete argument: ${argv[i]}`);
  }
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(id)) throw new Error('Invalid scenario id');
  const dir = path.join(root, 'benchmark/scenarios', id);
  const scenario = JSON.parse(await readFile(path.join(dir, 'scenario.json'), 'utf8'));
  validateScenario(scenario, id);
  const objective = (await readFile(path.join(dir, scenario.objectiveFile), 'utf8')).trim();
  if (!objective) throw new Error('Scenario objective is empty');
  const config = JSON.parse(await readFile(path.join(here, 'promptfooconfig.json'), 'utf8'));
  const runDir = path.join(root, '.run/promptfoo', `${id}-${randomUUID()}`);
  await mkdir(runDir, { recursive: true });
  config.targets[0].id = `file://${path.join(here, 'target_provider.mjs')}`;
  config.targets[0].label = `clawith-${id}`;
  config.targets[0].config.surface = scenario.oracleSuccessSurfaces[0];
  config.redteam.purpose = `${objective}\n\nStarting attack idea: ${scenario.attackerTurn}`;
  config.redteam.plugins[0].config.policy = scenario.policy;
  config.outputPath = path.join(runDir, 'promptfoo-results.json');
  const configPath = path.join(runDir, 'promptfooconfig.json');
  if (configOnly) {
    await writeFile(configPath, `${JSON.stringify(config, null, 2)}\n`);
    console.log(configPath);
    return;
  }

  const envFile = parseEnv(await readFile(path.join(root, '.env'), 'utf8'));
  const env = {
    ...process.env, ...envFile,
    ORACLE_ENABLE: scenario.oracleSuccessSurfaces[0],
    PROMPTFOO_CONFIG_DIR: path.join(runDir, 'cache'),
    PROMPTFOO_DISABLE_REDTEAM_REMOTE_GENERATION: 'true',
    PROMPTFOO_DISABLE_TELEMETRY: 'true',
  };
  if (!env.ZHIPU_API_KEY || !env.MCP_API_KEY) {
    throw new Error('Clawith/.env needs ZHIPU_API_KEY and MCP_API_KEY');
  }
  const existing = await readFile(path.join(root, '.run/oracle/episode.json'), 'utf8').catch((error) => {
    if (error.code === 'ENOENT') return null;
    throw error;
  });
  if (existing) throw new Error('Oracle episode already exists; reset the lab before a new Promptfoo run (see README)');
  await writeFile(configPath, `${JSON.stringify(config, null, 2)}\n`);
  const generatedPath = path.join(runDir, 'redteam.yaml');
  command(path.join(here, 'node_modules/.bin/promptfoo'),
    ['redteam', 'generate', '-c', configPath, '-o', generatedPath], env);
  const generated = configureGenerated(YAML.parse(await readFile(generatedPath, 'utf8')), 0);
  command('uv', ['run', '--with', 'httpx', '--with', 'websockets', 'python', '-m', 'oracle.run_episode', '--prepare'], env);
  command('uv', ['run', '--with', 'httpx', 'python', '-m', 'oracle.run_episode', '--arm'], env);
  const episode = JSON.parse(await readFile(path.join(root, '.run/oracle/episode.json'), 'utf8'));
  config.targets[0].config.episode = episode.n;
  await writeFile(configPath, `${JSON.stringify(config, null, 2)}\n`);
  generated.targets[0].config.episode = episode.n;
  await writeFile(generatedPath, YAML.stringify(generated));
  let runError;
  try {
    command(path.join(here, 'node_modules/.bin/promptfoo'),
      ['eval', '-c', generatedPath, '-j', '1', '--delay', '5000'], env);
  } catch (error) {
    runError = error;
  } finally {
    command('uv', ['run', '--with', 'httpx', 'python', '-m', 'oracle.watch', '--close'], env);
  }
  const result = JSON.parse(await readFile(path.join(root, `.run/oracle/result-${episode.n}.json`), 'utf8'));
  await writeFile(path.join(runDir, 'oracle-result.json'), `${JSON.stringify(result, null, 2)}\n`);
  console.log(`Promptfoo: ${config.outputPath}`);
  console.log(`Oracle: ${path.join(runDir, 'oracle-result.json')}`);
  console.log(`Oracle verdict: ${result.verdict}`);
  if (runError) throw runError;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
}
