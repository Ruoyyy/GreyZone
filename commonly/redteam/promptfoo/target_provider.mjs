import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

export default class CommonlyTargetProvider {
  constructor(_options, dispatch = spawnSync) {
    this.sessionId = null;
    this.stopped = false;
    this.dispatch = dispatch;
  }
  id() { return 'commonly-alice-mcp'; }
  async callApi(prompt, context = {}) {
    const sessionId = String(context.vars?.sessionId || '');
    if (!sessionId || (this.sessionId && this.sessionId !== sessionId)) {
      return { error: 'Only one stateful candidate is allowed per Oracle episode' };
    }
    this.sessionId = sessionId;
    if (this.stopped) return { error: 'Candidate already stopped', sessionId };
    const result = this.dispatch(process.env.COMMONLY_PYTHON, ['-m', 'redteam.runtime'], {
      cwd: root, env: process.env, input: JSON.stringify({ message: String(prompt) }),
      encoding: 'utf8', timeout: 1_950_000,
    });
    if (result.status !== 0 || result.error) {
      this.stopped = true;
      return { error: 'Target turn failed; inspect host artifacts', sessionId };
    }
    let turn;
    try { turn = JSON.parse(result.stdout); }
    catch { this.stopped = true; return { error: 'Invalid turn response', sessionId }; }
    if (!['continue', 'oracle-success'].includes(turn.status)) {
      this.stopped = true;
      return { error: 'Target turn did not complete', sessionId };
    }
    const response = { output: turn.reply, sessionId, metadata: { oracleSuccess: turn.oracleSuccess } };
    if (turn.oracleSuccess || turn.conversationEnded) {
      this.stopped = true;
      response.conversationEnded = true;
      response.conversationEndReason = turn.stopReason || 'oracle-success';
    }
    return response;
  }
}
