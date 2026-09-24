import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const promptfooDir = path.resolve(here, '..');
const args = process.argv.slice(2);
let scenarioId = 'runtime-credential-disclosure';
if (args.length) {
  if (args.length !== 2 || args[0] !== '--scenario' || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(args[1])) {
    throw new Error('Usage: prepare_config.mjs [--scenario NAME]');
  }
  scenarioId = args[1];
}
const scenarioDir = path.resolve(promptfooDir, '../../benchmark/scenarios', scenarioId);
const scenario = JSON.parse(await readFile(path.join(scenarioDir, 'scenario.json'), 'utf8'));
if (scenario.schemaVersion !== 1 || scenario.id !== scenarioId ||
    scenario.environment !== 'letta-issue' || scenario.objectiveFile !== 'objective.md' ||
    !Number.isInteger(scenario.maxTurns) || scenario.maxTurns < 2 || scenario.maxTurns > 20) {
  throw new Error('Invalid Letta benchmark scenario');
}
const objective = (await readFile(path.join(scenarioDir, scenario.objectiveFile), 'utf8')).trim();
if (!objective || !scenario.successCriteria?.trim() || !scenario.policy?.trim()) {
  throw new Error('Letta benchmark objective, success criteria, and policy are required');
}
const template = await readFile(path.join(promptfooDir, 'promptfooconfig.yaml'), 'utf8');
const objectiveMarker = '    __BENCHMARK_OBJECTIVE__';
const turnsMarker = 'maxTurns: 10 # __BENCHMARK_MAX_TURNS__';
const pluginMarker = '    - debug-access # __BENCHMARK_PLUGIN__';
const scenarioMarker = '__BENCHMARK_SCENARIO__';
if (template.split(objectiveMarker).length !== 2 || template.split(turnsMarker).length !== 2 ||
    template.split(pluginMarker).length !== 2 || template.split(scenarioMarker).length !== 2) {
  throw new Error('Promptfoo benchmark placeholders are missing or duplicated');
}
const purpose = `${objective}\n\nSuccess condition: ${scenario.successCriteria}`;
const plugin = scenarioId === 'runtime-credential-disclosure'
  ? '    - debug-access'
  : `    - id: policy\n      config:\n        policy: |\n${scenario.policy.split('\n').map((line) => `          ${line}`).join('\n')}`;
const config = template
  .replace(objectiveMarker, purpose.split('\n').map((line) => `    ${line}`).join('\n'))
  .replace(turnsMarker, `maxTurns: ${scenario.maxTurns}`)
  .replace(pluginMarker, plugin)
  .replace(scenarioMarker, scenarioId);
await writeFile(path.join(promptfooDir, 'scenario-config.yaml'), config, { mode: 0o600 });
console.log(`Prepared Promptfoo scenario: ${scenario.id}`);
