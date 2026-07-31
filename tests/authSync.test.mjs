import assert from 'node:assert/strict';
import test from 'node:test';

import {
  AUTH_SYNC_CHANNEL,
  createAuthSync,
} from '../frontend/utils/authSync.js';

class FakeBroadcastChannel {
  static instances = [];

  constructor(name) {
    this.name = name;
    this.messages = [];
    this.closed = false;
    this.onmessage = null;
    FakeBroadcastChannel.instances.push(this);
  }

  postMessage(message) {
    this.messages.push(message);
  }

  emit(message) {
    this.onmessage?.({ data: message });
  }

  close() {
    this.closed = true;
  }
}

test('auth sync publishes identity only and ignores its own message', () => {
  FakeBroadcastChannel.instances = [];
  const received = [];
  const sync = createAuthSync((message) => received.push(message), {
    BroadcastChannelImpl: FakeBroadcastChannel,
    sourceId: 'tab-a',
  });
  const channel = FakeBroadcastChannel.instances[0];

  sync.publish('user-a');

  assert.equal(sync.supported, true);
  assert.equal(channel.name, AUTH_SYNC_CHANNEL);
  assert.deepEqual(channel.messages, [{
    type: 'identity-change',
    version: 1,
    sourceId: 'tab-a',
    userId: 'user-a',
  }]);
  assert.equal(JSON.stringify(channel.messages).includes('csrf'), false);
  channel.emit(channel.messages[0]);
  assert.deepEqual(received, []);
});

test('auth sync accepts remote login/logout hints and rejects malformed data', () => {
  FakeBroadcastChannel.instances = [];
  const received = [];
  const sync = createAuthSync((message) => received.push(message), {
    BroadcastChannelImpl: FakeBroadcastChannel,
    sourceId: 'tab-a',
  });
  const channel = FakeBroadcastChannel.instances[0];

  for (const message of [
    null,
    { type: 'other', version: 1, sourceId: 'tab-b', userId: 'user-b' },
    { type: 'identity-change', version: 2, sourceId: 'tab-b', userId: 'user-b' },
    { type: 'identity-change', version: 1, sourceId: '', userId: 'user-b' },
    { type: 'identity-change', version: 1, sourceId: 'tab-b', userId: 42 },
  ]) channel.emit(message);

  channel.emit({
    type: 'identity-change', version: 1, sourceId: 'tab-b', userId: 'user-b',
  });
  channel.emit({
    type: 'identity-change', version: 1, sourceId: 'tab-b', userId: null,
  });

  assert.deepEqual(received, [{ userId: 'user-b' }, { userId: null }]);
  sync.close();
  assert.equal(channel.closed, true);
});

test('auth sync degrades to a no-op when BroadcastChannel is unavailable', () => {
  const sync = createAuthSync(() => {}, {
    BroadcastChannelImpl: null,
    sourceId: 'tab-a',
  });

  assert.equal(sync.supported, false);
  assert.doesNotThrow(() => sync.publish('user-a'));
  assert.doesNotThrow(() => sync.close());
});
