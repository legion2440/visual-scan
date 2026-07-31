/** Cross-tab authentication-change hints. No credentials or CSRF values cross this channel. */

export const AUTH_SYNC_CHANNEL = 'visual-scan-auth-v1';

function fallbackSourceId() {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function validUserId(value) {
  return value === null || (typeof value === 'string' && value.length > 0);
}

export function createAuthSync(
  onIdentityChange,
  {
    BroadcastChannelImpl = globalThis.BroadcastChannel,
    sourceId = globalThis.crypto?.randomUUID?.() || fallbackSourceId(),
  } = {},
) {
  if (typeof onIdentityChange !== 'function') {
    throw new TypeError('Authentication sync requires a change handler.');
  }

  let channel = null;
  if (typeof BroadcastChannelImpl === 'function') {
    try {
      channel = new BroadcastChannelImpl(AUTH_SYNC_CHANNEL);
    } catch {
      channel = null;
    }
  }

  if (channel) {
    channel.onmessage = (event) => {
      const message = event?.data;
      if (
        !message
        || message.type !== 'identity-change'
        || message.version !== 1
        || typeof message.sourceId !== 'string'
        || !message.sourceId
        || message.sourceId === sourceId
        || !validUserId(message.userId)
      ) return;
      onIdentityChange(Object.freeze({ userId: message.userId }));
    };
  }

  return Object.freeze({
    supported: Boolean(channel),
    publish(userId) {
      if (!channel || !validUserId(userId)) return;
      channel.postMessage({
        type: 'identity-change',
        version: 1,
        sourceId,
        userId,
      });
    },
    close() {
      if (!channel) return;
      channel.onmessage = null;
      channel.close();
      channel = null;
    },
  });
}
