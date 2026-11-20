import { spawnSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import YAML from 'yaml';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../..');
const runDir = process.env.COMMONLY_RUN_DIR;
if (!runDir) throw new Error('Launch through redteam/promptfoo/run.py');
const config = JSON.parse(readFileSync(path.join(runDir, 'config.json')));
const scenarioDir = path.join(root, 'benchmark/scenarios', config.scenario);
const scenario = JSON.parse(readFileSync(path.join(scenarioDir, 'scenario.json')));
const objective = readFileSync(path.join(scenarioDir, scenario.objectiveFile), 'utf8');
const provider = { id: `openai:chat:${process.env.COMMONLY_MODEL}`, config: {
  apiBaseUrl: process.env.COMMONLY_API_BASE, apiKeyEnvar: 'OPENAI_API_KEY', max_tokens: 4096,
} };
const template = JSON.parse(readFileSync(path.join(here, 'promptfooconfig.json')));
template.targets[0].id = `file://${path.join(here, 'target_provider.mjs')}`;
template.redteam.purpose = `${objective}\nStarting attack idea: ${scenario.attackerTurn}`;
template.redteam.provider = provider;
template.redteam.plugins[0].config.policy = scenario.policy;
const crescendo = template.redteam.strategies.find(s => s.id === 'crescendo');
crescendo.config.maxTurns = config.maxTurns;
crescendo.config.redteamProvider = { ...provider, config: {
  ...provider.config, response_format: { type: 'json_object' },
} };
template.outputPath = path.join(runDir, 'promptfoo-results.json');
const configPath = path.join(runDir, 'promptfooconfig.json');
const generatedPath = path.join(runDir, 'redteam.yaml');
writeFileSync(configPath, JSON.stringify(template, null, 2), { mode: 0o600 });
function command(args) {
  const result = spawnSync(path.join(here, 'node_modules/.bin/promptfoo'), args, {
    cwd: root, env: process.env, stdio: 'inherit',
    timeout: Number(process.env.COMMONLY_RUN_TIMEOUT || 14400) * 1000,
  });
  if (result.error) throw result.error;
  return result.status;
}
const generation = command(['redteam', 'generate', '-c', configPath, '-o', generatedPath]);
if (generation !== 0) process.exit(generation || 1);
const generated = YAML.parse(readFileSync(generatedPath, 'utf8'));
if (generated.targets?.length !== 1 || generated.tests?.length !== 1 ||
    generated.tests[0].metadata?.strategyId !== 'crescendo' ||
    generated.tests[0].assert?.length !== 1 ||
    generated.tests[0].assert[0].type !== 'promptfoo:redteam:policy') {
  throw new Error('Expected one Crescendo policy candidate');
}
// The plugin generates the attack; only the deterministic Oracle scores it.
generated.tests[0].assert = [];
writeFileSync(generatedPath, YAML.stringify(generated), { mode: 0o600 });
process.exit(command(['eval', '-c', generatedPath, '-j', '1', '--delay', '1000']) || 0);
