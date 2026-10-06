/**
 * podMembershipMessageService — a membership change becomes an ordinary
 * 'system' message, authored by whoever made the change (so it is deletable
 * by the same rules as any message), broadcast live, never delivered to
 * agents, and never able to fail the membership change that already happened.
 */

const mockPgCreate = jest.fn();
const mockPgFindById = jest.fn();
jest.mock('../../../models/pg/Message', () => ({
  create: (...args) => mockPgCreate(...args),
  findById: (...args) => mockPgFindById(...args),
}));

const mockMongoCreate = jest.fn();
jest.mock('../../../models/Message', () => ({
  create: (...args) => mockMongoCreate(...args),
}));

const mockEmit = jest.fn();
const mockTo = jest.fn(() => ({ emit: mockEmit }));
jest.mock('../../../config/socket', () => ({ getIO: () => ({ to: mockTo }) }));

const mockEnqueueMentions = jest.fn();
jest.mock('../../../services/agentMentionService', () => ({
  enqueueMentions: (...args) => mockEnqueueMentions(...args),
}));

const { postMembershipMessage } = require('../../../services/podMembershipMessageService');

const REMOVED = {
  podId: 'pod-1', actorUserId: 'admin-id', event: 'removed', username: 'bob', actorUsername: 'admin',
};
const PAYLOAD = {
  kind: 'membership', event: 'removed', username: 'bob', actorUsername: 'admin',
};

beforeEach(() => {
  jest.clearAllMocks();
  process.env.PG_HOST = 'pg';
  mockPgCreate.mockResolvedValue({ id: 7 });
  mockPgFindById.mockResolvedValue({ id: '7', username: 'admin', createdAt: '2026-10-06T00:00:00Z' });
  mockMongoCreate.mockResolvedValue({ _id: 'mongo-1', createdAt: '2026-10-06T00:00:00Z' });
});

afterAll(() => { delete process.env.PG_HOST; });

test('writes a system message authored by the actor into the pod\'s message sequence', async () => {
  await postMembershipMessage(REMOVED);

  expect(mockPgCreate).toHaveBeenCalledWith(
    'pod-1', 'admin-id', 'bob was removed from the pod by admin', 'system', null, PAYLOAD,
  );
  expect(mockMongoCreate).not.toHaveBeenCalled();
});

test('a join is authored by the joiner', async () => {
  await postMembershipMessage({
    podId: 'pod-1', actorUserId: 'alice-id', event: 'joined', username: 'alice',
  });

  expect(mockPgCreate).toHaveBeenCalledWith(
    'pod-1', 'alice-id', 'alice joined the pod', 'system', null,
    { kind: 'membership', event: 'joined', username: 'alice' },
  );
});

test('broadcasts it live with its payload, and does not wake agents', async () => {
  await postMembershipMessage(REMOVED);

  expect(mockTo).toHaveBeenCalledWith('pod_pod-1');
  expect(mockEmit).toHaveBeenCalledWith('newMessage', expect.objectContaining({
    id: '7', podId: 'pod-1', messageType: 'system', payload: PAYLOAD,
  }));
  expect(mockEnqueueMentions).not.toHaveBeenCalled();
});

test('falls back to Mongo when the PG write fails', async () => {
  mockPgCreate.mockRejectedValue(new Error('fk violation'));
  await postMembershipMessage(REMOVED);

  expect(mockMongoCreate).toHaveBeenCalledWith({
    podId: 'pod-1', userId: 'admin-id', content: 'bob was removed from the pod by admin', messageType: 'system', payload: PAYLOAD,
  });
  expect(mockEmit).toHaveBeenCalledWith('newMessage', expect.objectContaining({ id: 'mongo-1' }));
});

test('never throws — the membership change has already committed', async () => {
  mockPgCreate.mockRejectedValue(new Error('pg down'));
  mockMongoCreate.mockRejectedValue(new Error('mongo down'));
  await expect(postMembershipMessage(REMOVED)).resolves.toBeUndefined();
});
