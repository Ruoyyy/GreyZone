// One-shot seed for the self-contained local stack (docker-compose.stack.yml).
//
// On an EMPTY database it creates exactly:
//   - admin (role admin) and ruoyu, through the real signup path — so each gets
//     what any new user gets: a My Workspace, the starter tasks, their own Scout;
//   - the pod "Pod", created by admin, with admin's Scout and the Claude Code
//     seat "kai" installed by admin, and ruoyu joined through an admin invite;
//   - a runtime token for kai and a device token for ruoyu (the user MCP),
//     written only into their own secrets volumes.
// On any database that already has a human user it does nothing: re-running
// `docker compose up` must never duplicate the seed.
//
// Product code is called as the product calls it (controllers with the acting
// user, the same token issuers the UI uses), so the seeded state is the state a
// person clicking through the app would have produced.

/* eslint-disable @typescript-eslint/no-require-imports, global-require, no-console */
import crypto from 'crypto';
import fs from 'fs';

const mongoose = require('mongoose');
const User = require('../models/User');
const Pod = require('../models/Pod');
const PodInvite = require('../models/PodInvite').default;
const { AgentInstallation, AgentRegistry } = require('../models/AgentRegistry');
const AgentIdentityService = require('../services/agentIdentityService');
const { issueRuntimeTokenForAgent } = require('../routes/registry/tokens');
const { hashDeviceCredential } = require('../services/deviceAuthorizationService');
const authController = require('../controllers/authController');
const { pool } = require('../config/db-pg');
const podController = require('../controllers/podController');

const POD_NAME = 'Pod';
const KAI_TOKEN_FILE = '/run/kai/agent_token';
const MCP_TOKEN_FILE = '/run/mcp/user_token';

const accounts = {
  admin: {
    username: 'admin',
    email: process.env.SEED_ADMIN_EMAIL || 'admin@commonly.local',
    password: process.env.SEED_ADMIN_PASSWORD || 'admin',
  },
  ruoyu: {
    username: 'ruoyu',
    email: process.env.SEED_RUOYU_EMAIL || 'ruoyu@commonly.local',
    password: process.env.SEED_RUOYU_PASSWORD || 'ruoyu',
  },
};

type Handler = (req: unknown, res: unknown) => unknown;

// Run an Express handler in-process and return what it sent.
const call = (handler: Handler, req: Record<string, unknown>): Promise<{ status: number; body: any }> => (
  new Promise((resolve, reject) => {
    const res = {
      statusCode: 200,
      status(code: number) { this.statusCode = code; return this; },
      json(body: unknown) { resolve({ status: this.statusCode, body }); },
      send(body: unknown) { resolve({ status: this.statusCode, body }); },
      cookie() { return this; },
    };
    Promise.resolve(handler({ headers: {}, ...req }, res)).catch(reject);
  })
);

const asUser = (userId: string, extra: Record<string, unknown> = {}) => ({
  userId, user: { id: userId, _id: userId }, ...extra,
});

const writeSecret = (file: string, value: string) => {
  fs.writeFileSync(file, value, { mode: 0o444 });
};

// Signup answers before onboarding finishes: it queues the workspace setup
// (starter tasks, Scout, Scout's welcome message) without awaiting it. A
// one-shot process must wait for that tail, or it reads a half-made account and
// then disconnects the database underneath it. The welcome message is the last
// thing onboarding writes, so it is the signal.
const waitForOnboarding = async (userId: unknown, username: string) => {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    // eslint-disable-next-line no-await-in-loop
    const install = await AgentInstallation.findOne({ agentName: 'scout', installedBy: userId }).lean();
    if (install) {
      // eslint-disable-next-line no-await-in-loop
      const scout = await User.findOne({
        'botMetadata.agentName': 'scout', 'botMetadata.instanceId': install.instanceId,
      }).select('_id').lean();
      // eslint-disable-next-line no-await-in-loop
      const welcomed = scout && (await pool.query(
        'SELECT 1 FROM messages WHERE pod_id = $1 AND user_id = $2 LIMIT 1',
        [String(install.podId), String(scout._id)],
      )).rowCount > 0;
      if (welcomed) return;
    }
    // eslint-disable-next-line no-await-in-loop
    await new Promise((resolve) => { setTimeout(resolve, 500); });
  }
  throw new Error(`signup onboarding for ${username} did not finish within 60s (no Scout welcome)`);
};

const register = async ({ username, email, password }: typeof accounts.admin) => {
  const res = await call(authController.register, { body: { username, email, password } });
  if (res.status >= 300) throw new Error(`register ${username}: ${res.status} ${JSON.stringify(res.body)}`);
  const user = await User.findOne({ username });
  await waitForOnboarding(user._id, username);
  return user;
};

const main = async () => {
  await mongoose.connect(process.env.MONGO_URI);

  if (await User.countDocuments({ isBot: { $ne: true } }) > 0) {
    console.log('[seed] database already has users — nothing to do');
    return;
  }

  // 1. Accounts, through the real signup path.
  const admin = await register(accounts.admin);
  const ruoyu = await register(accounts.ruoyu);
  await User.updateOne({ _id: admin._id }, { $set: { role: 'admin' } });
  const ADMIN = String(admin._id);
  const RUOYU = String(ruoyu._id);
  console.log('[seed] users: admin (admin), ruoyu');

  // 2. The pod, created by admin.
  const created = await call(podController.createPod, asUser(ADMIN, { body: { name: POD_NAME, type: 'team' } }));
  if (created.status >= 300) throw new Error(`createPod: ${created.status} ${JSON.stringify(created.body)}`);
  const podId = String(created.body._id);
  console.log(`[seed] pod "${POD_NAME}" ${podId}`);

  const join = async (agentUser: { _id: unknown }) => {
    await Pod.updateOne({ _id: podId, members: { $ne: agentUser._id } }, { $push: { members: agentUser._id } });
  };

  // 3. admin's own Scout (made at admin's signup), installed into the pod by admin.
  //    Wake-on-message is per installation and starts off, as for any added agent.
  const scoutSrc = await AgentInstallation.findOne({ agentName: 'scout', installedBy: admin._id }).lean();
  if (!scoutSrc) throw new Error('admin has no Scout — signup did not provision one');
  const scoutConfig = { ...(scoutSrc.config || {}) };
  delete scoutConfig.wakeOnMessage;
  await AgentInstallation.create({
    agentName: 'scout',
    instanceId: scoutSrc.instanceId,
    podId,
    version: scoutSrc.version,
    displayName: scoutSrc.displayName,
    scopes: scoutSrc.scopes,
    config: scoutConfig,
    status: 'active',
    installedBy: admin._id,
  });
  await join(await AgentIdentityService.getOrCreateAgentUser('scout', {
    instanceId: scoutSrc.instanceId, displayName: scoutSrc.displayName,
  }));

  // 4. The Claude Code seat "kai": registry entry published by admin, installed
  //    by admin, and its runtime token handed to the Kai container only.
  const manifest = {
    name: 'kai',
    version: '1.0.0',
    description: 'A containerised Claude Code agent.',
    capabilities: [],
  };
  await AgentRegistry.create({
    agentName: 'kai',
    displayName: 'kai',
    description: manifest.description,
    manifest,
    latestVersion: manifest.version,
    versions: [{ version: manifest.version, manifest, publishedAt: new Date() }],
    registry: 'commonly-community',
    publisher: { userId: admin._id, name: 'admin' },
  });
  const kaiInstall = await AgentInstallation.create({
    agentName: 'kai',
    instanceId: 'default',
    podId,
    version: '1.0.0',
    displayName: 'Kai',
    scopes: ['context:read', 'messages:write', 'memory:read', 'memory:write'],
    config: { runtime: { runtimeType: 'claude-code', host: 'byo' } },
    status: 'active',
    installedBy: admin._id,
  });
  const kaiUser = await AgentIdentityService.getOrCreateAgentUser('kai', { instanceId: 'default', displayName: 'Kai' });
  await join(kaiUser);
  const issued = await issueRuntimeTokenForAgent(kaiUser, 'Kai (local stack)', kaiInstall, { ownerUserId: admin._id });
  writeSecret(KAI_TOKEN_FILE, issued.token);
  console.log('[seed] agents in pod: Scout (admin), Kai (admin)');

  // 5. ruoyu joins through a single-use invite admin issued (routes/podInvites.ts).
  const invite = await PodInvite.create({
    token: crypto.randomBytes(16).toString('hex'), podId, createdBy: admin._id, maxUses: 1,
  });
  await Pod.updateOne({ _id: podId }, { $addToSet: { members: ruoyu._id }, $set: { updatedAt: new Date() } });
  invite.useCount += 1;
  invite.lastUsedAt = new Date();
  await invite.save();
  console.log('[seed] ruoyu joined via invite');

  // 6. ruoyu's device token for the user MCP — revocable from ruoyu's devices.
  const userToken = `cm_${crypto.randomBytes(32).toString('hex')}`;
  await User.updateOne({ _id: RUOYU }, {
    $push: { deviceTokens: { tokenHash: hashDeviceCredential(userToken), label: 'user-mcp', createdAt: new Date() } },
  });
  writeSecret(MCP_TOKEN_FILE, userToken);
  console.log('[seed] done');
};

main()
  .then(async () => { await mongoose.disconnect(); process.exit(0); })
  .catch(async (err) => {
    console.error('[seed] FAILED:', err);
    await mongoose.disconnect().catch(() => undefined);
    process.exit(1);
  });

export {};
