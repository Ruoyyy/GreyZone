/* eslint-disable global-require, @typescript-eslint/no-require-imports */
// A membership change (someone joined, someone was removed) as a 'system'
// message in the pod's ordinary message sequence. It is authored by whoever
// made the change — the joiner, or the person who removed the member — so it
// is deleted by the same rules as any other message: its author or the pod
// creator (DELETE /api/messages/:id), or a delete_messages card.
//
// Not delivered to agents: a join is not something to answer, and a
// wake-on-message seat would otherwise spend a turn on every one.
// Best-effort: the membership change has already committed, so a failure
// here is logged and never surfaced to the caller.

export interface MembershipEvent {
  podId: string;
  actorUserId: string;
  event: 'joined' | 'removed';
  username: string;
  actorUsername?: string;
}

export const postMembershipMessage = async (input: MembershipEvent): Promise<void> => {
  const {
    podId, actorUserId, event, username, actorUsername,
  } = input;
  // Shell renders `payload`; `content` is the plain-text fallback for
  // surfaces that do not know the kind (v1, digests, agent context reads).
  const payload = {
    kind: 'membership', event, username, ...(actorUsername ? { actorUsername } : {}),
  };
  const content = event === 'joined'
    ? `${username} joined the pod`
    : `${username} was removed from the pod by ${actorUsername || 'the pod creator'}`;

  try {
    let message: Record<string, unknown> | null = null;
    if (process.env.PG_HOST) {
      try {
        const PGMessage = require('../models/pg/Message');
        const created = await PGMessage.create(podId, actorUserId, content, 'system', null, payload);
        message = (await PGMessage.findById(created.id)) || created;
      } catch (pgErr) {
        console.warn('[membership-message] PG write failed, using Mongo:', (pgErr as Error).message);
      }
    }
    if (!message) {
      const MongoMessage = require('../models/Message');
      const created = await MongoMessage.create({
        podId, userId: actorUserId, content, messageType: 'system', payload,
      });
      message = { _id: String(created._id), createdAt: created.createdAt };
    }

    const io = require('../config/socket').getIO();
    if (io) {
      const id = String(message._id || message.id);
      io.to(`pod_${podId}`).emit('newMessage', {
        _id: id,
        id,
        pod_id: podId,
        podId,
        content,
        messageType: 'system',
        userId: message.userId || actorUserId,
        username: message.username,
        createdAt: message.createdAt,
        payload,
      });
    }
  } catch (err) {
    console.warn('[membership-message] failed:', (err as Error).message);
  }
};
