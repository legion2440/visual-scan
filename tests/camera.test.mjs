import assert from 'node:assert/strict';
import test from 'node:test';

import { activateCameraAfterPlayback } from '../frontend/utils/camera.js';

test('camera intake keeps a newer sample preview after stale video.play completion', async () => {
  let resolvePlayback;
  let currentIntakeRevision = 1;
  let stoppedTracks = 0;
  let uiState = 'camera-pending';
  const stream = {
    getTracks: () => [{ stop: () => { stoppedTracks += 1; } }],
  };
  let activeStream = stream;
  const video = {
    srcObject: stream,
    play: () => new Promise((resolve) => { resolvePlayback = resolve; }),
  };

  const cameraCompletion = activateCameraAfterPlayback({
    getActiveStream: () => activeStream,
    getCurrentIntakeRevision: () => currentIntakeRevision,
    intakeRevision: 1,
    onReady: () => { uiState = 'live-camera'; },
    stream,
    video,
  });

  currentIntakeRevision = 2;
  activeStream = null;
  video.srcObject = null;
  uiState = 'sample-b-preview';
  resolvePlayback();

  assert.equal(await cameraCompletion, false);
  assert.equal(uiState, 'sample-b-preview');
  assert.equal(stoppedTracks, 1);
});
