/**
 * invite_link / remove_member — membership changes an agent may PROPOSE and
 * only the accountable human may approve (ADR-020 D1: anything that changes
 * who can see or join a surface goes through a card).
 *
 * Both execute with the approving owner's authority, so they must be held to
 * the gates the human routes hold that owner to:
 *  - invite links are for listed members (routes/podInvites.ts)
 *  - removal is for the pod creator, never of the creator
 *    (podController.removeMember)
 *  - humans only: an agent's posting rights live on its AgentInstallation,
 *    so pulling it from `members` would not actually remove it
 *  - DM pods are 1:1 (ADR-001 §3.10) and never change membership
 * and the gates are re-checked at execution, because membership can change
 * while the card waits.
 *
 * Siblings: approvalActionService.test.js (resolve/execute lifecycle),
 * approvalActionService.decider.test.js (who the owner is).
 */

const mockApprovalFindById = jest.fn();
const mockApprovalFindOneAndUpdate = jest.fn();
const mockApprovalCreate = jest.fn();
jest.mock('../../../models/ApprovalAction', () => ({
  findById: (...args) => mockApprovalFindById(...args),
  findOneAndUpdate: (...args) => mockApprovalFindOneAndUpdate(...args),
  create: (...args) => mockApprovalCreate(...args),
}));

let mockPodState = null;
const mockPodUpdateOne = jest.fn();
jest.mock('../../../models/Pod', () => ({
  findById: () => ({ select: () => ({ lean: async () => mockPodState }) }),
  updateOne: (...args) => mockPodUpdateOne(...args),
  create: jest.fn(),
}));

const mockInviteCreate = jest.fn();
jest.mock('../../../models/PodInvite', () => ({
  create: (...args) => mockInviteCreate(...args),
}));

let mockUsersByName = {};
jest.mock('../../../models/User', () => ({
  // resolveHumanDecider awaits findById directly; resolveApproval chains
  // .select().lean(). One object answers both.
  findById: (id) => ({
    _id: id,
    isBot: false,
    select: () => ({ lean: async () => ({ isBot: false }) }),
  }),
  findOne: ({ username }) => ({
    select: () => ({ lean: async () => mockUsersByName[username] || null }),
  }),
}));

jest.mock('../../../models/AgentRegistry', () => ({
  AgentInstallation: {
    findOne: jest.fn(async () => ({ installedBy: 'aaaaaaaaaaaaaaaaaaaaaaa1' })),
    findOneAndUpdate: jest.fn(),
  },
  AgentRegistry: { getByName: jest.fn(), create: jest.fn() },
}));

const mockPGRemoveMember = jest.fn();
jest.mock('../../../models/pg/Pod', () => ({
  removeMember: (...args) => mockPGRemoveMember(...args),
}));

const mockPostMembershipMessage = jest.fn();
jest.mock('../../../services/podMembershipMessageService', () => ({
  postMembershipMessage: (...args) => mockPostMembershipMessage(...args),
}));

const mockDeletePodWithContents = jest.fn();
jest.mock('../../../services/podDeletionService', () => ({
  deletePodWithContents: (...args) => mockDeletePodWithContents(...args),
}));

jest.mock('../../../services/agentMessageService', () => ({
  postMessage: jest.fn(async () => ({ success: true, message: { _id: 'msg-1' } })),
}));
jest.mock('../../../services/agentIdentityService', () => ({
  DM_POD_TYPES_GUARD: new Set(['agent-room', 'agent-dm']),
  getOrCreateAgentUser: jest.fn(),
  syncUserToPostgreSQL: jest.fn(),
}));
jest.mock('../../../models/Message', () => ({ updateOne: jest.fn().mockResolvedValue({}) }));
jest.mock('../../../config/socket', () => ({ getIO: jest.fn(() => null) }));
jest.mock('../../../services/attentionItemService', () => ({
  recordActionApproval: jest.fn().mockResolvedValue(undefined),
  resolve: jest.fn().mockResolvedValue(undefined),
}));

const AgentMessageService = require('../../../services/agentMessageService');
const { proposeAction, resolveApproval } = require('../../../services/approvalActionService');

const OWNER = 'aaaaaaaaaaaaaaaaaaaaaaa1'; // installer of the proposing agent
const CREATOR = 'aaaaaaaaaaaaaaaaaaaaaaa2';
const BOB = 'aaaaaaaaaaaaaaaaaaaaaaa3';
const BOT = 'bbbbbbbbbbbbbbbbbbbbbbb1';

const users = {
  owner: { _id: OWNER, username: 'owner', isBot: false },
  creator: { _id: CREATOR, username: 'creator', isBot: false },
  bob: { _id: BOB, username: 'bob', isBot: false },
  scout: { _id: BOT, username: 'scout', isBot: true },
};

const pod = (over = {}) => ({
  _id: 'pod-1', type: 'team', createdBy: OWNER, members: [OWNER, BOB, BOT], ...over,
});

const propose = (actionType, params = {}) => proposeAction({
  podId: 'pod-1',
  agentName: 'scout',
  instanceId: 'u1',
  actionType,
  params,
  summary: 'do the thing',
});

beforeEach(() => {
  jest.clearAllMocks();
  delete process.env.PG_HOST;
  mockUsersByName = users;
  mockPodState = pod();
  mockApprovalCreate.mockImplementation(async (doc) => ({
    _id: 'appr-1',
    expiresAt: new Date(Date.now() + 60 * 60 * 1000), // the model's default
    ...doc,
    save: jest.fn().mockResolvedValue(undefined),
  }));
});

describe('propose — refusals the agent can act on, before any card exists', () => {
  test('invite_link from a member owner mints a card', async () => {
    const res = await propose('invite_link');
    expect(res.ok).toBe(true);
    expect(mockApprovalCreate).toHaveBeenCalledWith(expect.objectContaining({
      actionType: 'invite_link', ownerUserId: OWNER,
    }));
  });

  test('invite_link is refused when the owner is not a listed member', async () => {
    mockPodState = pod({ members: [BOB, BOT] });
    const res = await propose('invite_link');
    expect(res.ok).toBe(false);
    expect(res.error).toMatch(/not a member/);
    expect(mockApprovalCreate).not.toHaveBeenCalled();
  });

  test('remove_member requires a username', async () => {
    const res = await propose('remove_member', {});
    expect(res.ok).toBe(false);
    expect(res.error).toMatch(/username is required/);
  });

  test('remove_member is refused when the owner did not create the pod', async () => {
    // The owner is the agent's installer; installing an agent into someone
    // else's pod must not borrow the creator's right to remove people.
    mockPodState = pod({ createdBy: CREATOR, members: [CREATOR, OWNER, BOB, BOT] });
    const res = await propose('remove_member', { username: 'bob' });
    expect(res.ok).toBe(false);
    expect(res.error).toMatch(/only the pod creator/);
    expect(mockApprovalCreate).not.toHaveBeenCalled();
  });

  test.each([
    ['an agent', 'scout', /only human members/],
    ['the creator', 'owner', /creator cannot be removed/],
    ['a non-member', 'creator', /not a member/],
    ['an unknown username', 'nobody', /not a member/],
  ])('remove_member refuses %s', async (_label, username, error) => {
    const res = await propose('remove_member', { username });
    expect(res.ok).toBe(false);
    expect(res.error).toMatch(error);
    expect(mockApprovalCreate).not.toHaveBeenCalled();
  });

  test('delete_pod from the creator owner mints a card', async () => {
    const res = await propose('delete_pod');
    expect(res.ok).toBe(true);
    expect(mockApprovalCreate).toHaveBeenCalledWith(expect.objectContaining({
      actionType: 'delete_pod', ownerUserId: OWNER,
    }));
  });

  test('delete_pod is refused when the owner did not create the pod', async () => {
    // Same gate as remove_member: installing Scout into someone else's pod
    // must not borrow the creator's right to delete it.
    mockPodState = pod({ createdBy: CREATOR, members: [CREATOR, OWNER, BOB, BOT] });
    const res = await propose('delete_pod');
    expect(res.ok).toBe(false);
    expect(res.error).toMatch(/only the pod creator/);
    expect(mockApprovalCreate).not.toHaveBeenCalled();
  });

  test.each(['agent-room', 'agent-dm'])('both actions are refused in a %s pod', async (type) => {
    mockPodState = pod({ type });
    expect((await propose('invite_link')).error).toMatch(/1:1/);
    expect((await propose('remove_member', { username: 'bob' })).error).toMatch(/1:1/);
    expect(mockApprovalCreate).not.toHaveBeenCalled();
  });
});

describe('approved execution — the owner\'s authority, re-checked', () => {
  const approve = async (actionType, params) => {
    const row = {
      _id: 'appr-1',
      podId: 'pod-1',
      messageId: 'mongo-msg-1',
      ownerUserId: OWNER,
      agentName: 'scout',
      instanceId: 'u1',
      actionType,
      params,
      summary: 'do the thing',
      status: 'flagged',
      expiresAt: new Date(Date.now() + 60 * 60 * 1000),
      save: jest.fn().mockResolvedValue(undefined),
    };
    mockApprovalFindById.mockResolvedValue(row);
    const resolved = { ...row, status: 'resolved', decision: 'approved' };
    mockApprovalFindOneAndUpdate.mockResolvedValue(resolved);
    const res = await resolveApproval({ approvalId: 'appr-1', callerUserId: OWNER, decision: 'approved' });
    return { res, resolved };
  };

  test('invite_link mints an invite owned by the OWNER and returns its in-app path', async () => {
    mockInviteCreate.mockImplementation(async (doc) => doc);
    const { res, resolved } = await approve('invite_link', {});

    expect(res.status).toBe(200);
    const [doc] = mockInviteCreate.mock.calls[0];
    // D2: the user's authority owns the result — never the bot.
    expect(doc).toEqual(expect.objectContaining({ podId: 'pod-1', createdBy: OWNER }));
    // Same token shape POST /api/pods/:podId/invites issues.
    expect(doc.token).toMatch(/^[0-9a-f]{32}$/);
    expect(resolved.executionResult).toEqual({ invitePath: `/v2/invite/${doc.token}` });
    expect(resolved.executionError).toBeUndefined();
  });

  test('remove_member pulls the member from Mongo and the PG mirror', async () => {
    process.env.PG_HOST = 'pg';
    const { res, resolved } = await approve('remove_member', { username: 'bob' });

    expect(res.status).toBe(200);
    expect(mockPodUpdateOne).toHaveBeenCalledWith(
      { _id: 'pod-1' },
      expect.objectContaining({ $pull: { members: BOB } }),
    );
    expect(mockPGRemoveMember).toHaveBeenCalledWith('pod-1', BOB);
    expect(resolved.executionResult).toEqual({ removedUsername: 'bob' });
    // The removal lands in the message sequence, authored by the approving owner.
    expect(mockPostMembershipMessage).toHaveBeenCalledWith(expect.objectContaining({
      podId: 'pod-1', actorUserId: OWNER, event: 'removed', username: 'bob',
    }));
  });

  test('a member who left while the card waited fails honestly, removing nothing', async () => {
    mockPodState = pod({ members: [OWNER, BOT] });
    const { res, resolved } = await approve('remove_member', { username: 'bob' });

    expect(res.status).toBe(200);
    expect(resolved.executionError).toMatch(/not a member/);
    expect(mockPodUpdateOne).not.toHaveBeenCalled();
    expect(mockPostMembershipMessage).not.toHaveBeenCalled();
  });

  test('delete_pod deletes the pod with its contents', async () => {
    const { res, resolved } = await approve('delete_pod', {});

    expect(res.status).toBe(200);
    expect(mockDeletePodWithContents).toHaveBeenCalledWith('pod-1');
    expect(resolved.executionResult).toEqual({ deletedPodId: 'pod-1' });
    expect(resolved.executionError).toBeUndefined();
  });

  test('delete_pod re-checks the creator at execution, deleting nothing', async () => {
    mockPodState = pod({ createdBy: CREATOR });
    const { resolved } = await approve('delete_pod', {});

    expect(resolved.executionError).toMatch(/only the pod creator/);
    expect(mockDeletePodWithContents).not.toHaveBeenCalled();
  });

  test('an owner who left while an invite card waited cannot mint the link', async () => {
    mockPodState = pod({ members: [BOB, BOT] });
    const { resolved } = await approve('invite_link', {});

    expect(resolved.executionError).toMatch(/not a member/);
    expect(mockInviteCreate).not.toHaveBeenCalled();
  });
});

describe('threat-lab auto-approve — no card, executed in-line', () => {
  beforeEach(() => {
    process.env.COMMONLY_AUTO_APPROVE_MS = '2000';
    // proposeAction hands the created row straight to resolveApproval.
    mockApprovalCreate.mockImplementation(async (doc) => {
      const row = {
        _id: 'appr-1', expiresAt: new Date(Date.now() + 60 * 60 * 1000), status: 'flagged', ...doc,
        save: jest.fn().mockResolvedValue(undefined),
      };
      mockApprovalFindById.mockResolvedValue(row);
      mockApprovalFindOneAndUpdate.mockResolvedValue({ ...row, status: 'resolved', decision: 'approved' });
      return row;
    });
    mockInviteCreate.mockImplementation(async (doc) => doc);
  });
  afterEach(() => { delete process.env.COMMONLY_AUTO_APPROVE_MS; });

  test('the row is minted and executed, the result goes back to the agent, and no card is posted', async () => {
    const res = await propose('invite_link');

    expect(mockApprovalCreate).toHaveBeenCalledTimes(1);
    expect(AgentMessageService.postMessage).not.toHaveBeenCalled();
    expect(res).toEqual(expect.objectContaining({ ok: true, approvalId: 'appr-1', autoApproved: true }));
    expect(res.executionResult.invitePath).toMatch(/^\/v2\/invite\/[0-9a-f]{32}$/);
  });

  test('an execution failure comes back to the agent as an error, still without a card', async () => {
    mockPodUpdateOne.mockRejectedValue(new Error('write failed'));
    const res = await propose('remove_member', { username: 'bob' });

    expect(AgentMessageService.postMessage).not.toHaveBeenCalled();
    expect(res).toEqual(expect.objectContaining({ ok: true, autoApproved: true, executionError: 'write failed' }));
    mockPodUpdateOne.mockReset();
  });
});
