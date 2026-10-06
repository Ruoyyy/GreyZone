/**
 * delete_messages — an agent may PROPOSE deleting specific messages; it runs
 * with the approving owner's authority, the same shape as remove_member and
 * delete_pod (approvalActionService.membership.test.js). The owner — the
 * agent's installer, else the pod creator — must have sent every listed
 * message, or be a global admin:
 *  - an owner who did not send a listed message → refused
 *  - a global-admin owner may delete anyone's messages
 *  - every message must belong to THIS pod (no reaching across pods by id)
 *  - the card carries server-read excerpts, not the agent's params
 *  - all of it re-checked at execution, because messages and roles can change
 *    while the card waits
 */

const mockApprovalFindById = jest.fn();
const mockApprovalFindOneAndUpdate = jest.fn();
const mockApprovalCreate = jest.fn();
jest.mock('../../../models/ApprovalAction', () => ({
  findById: (...args) => mockApprovalFindById(...args),
  findOneAndUpdate: (...args) => mockApprovalFindOneAndUpdate(...args),
  create: (...args) => mockApprovalCreate(...args),
}));

jest.mock('../../../models/Pod', () => ({
  findById: () => ({ select: () => ({ lean: async () => ({ _id: 'pod-1', type: 'team', createdBy: 'admin-id' }) }) }),
}));
jest.mock('../../../models/PodInvite', () => ({ create: jest.fn() }));

const users = {
  'admin-id': { role: 'admin' },
  'bob-id': { role: 'user' },
  'alice-id': { role: 'user' },
};
jest.mock('../../../models/User', () => ({
  // resolveHumanDecider awaits findById directly; the owner check chains
  // .select().lean(). One object answers both.
  findById: (id) => ({
    _id: id,
    isBot: false,
    select: () => ({ lean: async () => users[id] || null }),
  }),
}));

// The owner is whoever installed the proposing agent here.
let mockInstalledBy = 'admin-id';
jest.mock('../../../models/AgentRegistry', () => ({
  AgentInstallation: { findOne: jest.fn(async () => ({ installedBy: mockInstalledBy })) },
  AgentRegistry: {},
}));

let mockPgMessages = {};
const mockPgDelete = jest.fn();
jest.mock('../../../models/pg/Message', () => ({
  findById: async (id) => mockPgMessages[id] || null,
  delete: (...args) => mockPgDelete(...args),
  updatePayload: jest.fn(),
}));

jest.mock('../../../services/agentMessageService', () => ({
  postMessage: jest.fn(async () => ({ success: true, message: { _id: '99' } })),
}));
jest.mock('../../../services/agentIdentityService', () => ({ DM_POD_TYPES_GUARD: new Set() }));
jest.mock('../../../models/Message', () => ({ updateOne: jest.fn() }));
jest.mock('../../../config/socket', () => ({ getIO: jest.fn(() => null) }));
jest.mock('../../../services/attentionItemService', () => ({
  recordActionApproval: jest.fn().mockResolvedValue(undefined),
  resolve: jest.fn().mockResolvedValue(undefined),
}));

const { proposeAction, resolveApproval } = require('../../../services/approvalActionService');

const msg = (userId, content, podId = 'pod-1') => ({
  pod_id: podId, user_id: userId, username: userId.replace('-id', ''), content,
});

const propose = (messageIds, extraParams = {}) => proposeAction({
  podId: 'pod-1',
  agentName: 'scout',
  instanceId: 'u1',
  actionType: 'delete_messages',
  params: { messageIds, ...extraParams },
  summary: 'delete them',
});

beforeEach(() => {
  jest.clearAllMocks();
  process.env.PG_HOST = 'pg';
  mockInstalledBy = 'admin-id';
  mockPgMessages = {
    41: msg('bob-id', 'buy cheap watches'),
    42: msg('bob-id', 'buy them now'),
    43: msg('alice-id', 'hello all'),
    50: msg('bob-id', 'in another pod', 'pod-2'),
  };
  mockApprovalCreate.mockImplementation(async (doc) => ({
    _id: 'appr-1',
    expiresAt: new Date(Date.now() + 60 * 60 * 1000),
    ...doc,
    save: jest.fn().mockResolvedValue(undefined),
  }));
});

afterAll(() => { delete process.env.PG_HOST; });

describe('propose — the owner\'s authority', () => {
  test('an admin owner may delete anyone\'s messages; the card carries server-read excerpts', async () => {
    const res = await propose(['41', '43'], { messages: [{ id: '41', excerpt: 'forged' }] });
    expect(res.ok).toBe(true);
    const [doc] = mockApprovalCreate.mock.calls[0];
    expect(doc).toEqual(expect.objectContaining({ actionType: 'delete_messages', ownerUserId: 'admin-id' }));
    // The agent's own `messages` field is replaced by what the store says.
    expect(doc.params).toEqual({
      messageIds: ['41', '43'],
      messages: [
        { id: '41', author: 'bob', excerpt: 'buy cheap watches' },
        { id: '43', author: 'alice', excerpt: 'hello all' },
      ],
    });
  });

  test('a non-admin owner may delete messages they sent', async () => {
    mockInstalledBy = 'bob-id';
    const res = await propose(['41', '42']);
    expect(res.ok).toBe(true);
    expect(mockApprovalCreate.mock.calls[0][0].ownerUserId).toBe('bob-id');
  });

  test('a non-admin owner may not delete someone else\'s message', async () => {
    mockInstalledBy = 'bob-id';
    const res = await propose(['41', '43']);
    expect(res.ok).toBe(false);
    expect(res.error).toMatch(/only its sender or an admin/);
    expect(mockApprovalCreate).not.toHaveBeenCalled();
  });

  test.each([
    ['a message in another pod', ['50']],
    ['an unknown id', ['777']],
  ])('refuses %s, even for an admin owner', async (_label, ids) => {
    const res = await propose(ids);
    expect(res.ok).toBe(false);
    expect(res.error).toMatch(/not in this pod/);
  });

  test.each([
    ['an empty list', []],
    ['more than 20 ids', Array.from({ length: 21 }, (_, i) => String(i))],
  ])('refuses %s', async (_label, ids) => {
    const res = await propose(ids);
    expect(res.ok).toBe(false);
    expect(res.error).toMatch(/messageIds/);
  });
});

describe('approved execution — re-checked, then deleted', () => {
  const approve = async (ownerUserId, messageIds) => {
    const row = {
      _id: 'appr-1',
      podId: 'pod-1',
      messageId: '99',
      ownerUserId,
      agentName: 'scout',
      instanceId: 'u1',
      actionType: 'delete_messages',
      params: { messageIds },
      summary: 'delete them',
      status: 'flagged',
      expiresAt: new Date(Date.now() + 60 * 60 * 1000),
      save: jest.fn().mockResolvedValue(undefined),
    };
    mockApprovalFindById.mockResolvedValue(row);
    const resolved = { ...row, status: 'resolved', decision: 'approved', save: jest.fn() };
    mockApprovalFindOneAndUpdate.mockResolvedValue(resolved);
    const res = await resolveApproval({ approvalId: 'appr-1', callerUserId: ownerUserId, decision: 'approved' });
    return { res, resolved };
  };

  test('deletes exactly the listed messages', async () => {
    const { res, resolved } = await approve('admin-id', ['41', '43']);
    expect(res.status).toBe(200);
    expect(mockPgDelete.mock.calls.map(([id]) => id)).toEqual(['41', '43']);
    expect(resolved.executionResult).toEqual({ deletedMessageIds: ['41', '43'] });
  });

  test('a message already gone while the card waited fails honestly, deleting nothing', async () => {
    delete mockPgMessages[42];
    const { resolved } = await approve('bob-id', ['41', '42']);
    expect(resolved.executionError).toMatch(/not in this pod/);
    expect(mockPgDelete).not.toHaveBeenCalled();
  });

  test('an owner whose admin role was revoked while the card waited can no longer delete others\' messages', async () => {
    users['admin-id'].role = 'user';
    try {
      const { resolved } = await approve('admin-id', ['43']);
      expect(resolved.executionError).toMatch(/only its sender or an admin/);
      expect(mockPgDelete).not.toHaveBeenCalled();
    } finally {
      users['admin-id'].role = 'admin';
    }
  });
});
