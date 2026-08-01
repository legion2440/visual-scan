import assert from 'node:assert/strict';
import test from 'node:test';

import { activateCameraAfterPlayback } from '../frontend/utils/camera.js';

test('failed newer intake restores the old image after stale video.play completion', async () => {
  let resolvePlayback;
  let currentIntakeRevision = 1;
  let stoppedTracks = 0;
  let previewHidden = false;
  const stream = {
    getTracks: () => [{ stop: () => { stoppedTracks += 1; } }],
  };
  let activeStream = stream;
  const video = {
    hidden: false,
    srcObject: stream,
    play: () => new Promise((resolve) => { resolvePlayback = resolve; }),
  };

  const cameraCompletion = activateCameraAfterPlayback({
    getActiveStream: () => activeStream,
    getCurrentIntakeRevision: () => currentIntakeRevision,
    intakeRevision: 1,
    onReady: () => { previewHidden = true; },
    stopCamera: () => {
      activeStream.getTracks().forEach((track) => track.stop());
      activeStream = null;
      video.srcObject = null;
      video.hidden = true;
      previewHidden = false;
    },
    stream,
    video,
  });

  // A newer local/sample intake reserves the revision but fails before commit.
  currentIntakeRevision = 2;
  resolvePlayback();

  assert.equal(await cameraCompletion, false);
  assert.equal(activeStream, null);
  assert.equal(video.srcObject, null);
  assert.equal(video.hidden, true);
  assert.equal(previewHidden, false);
  assert.equal(stoppedTracks, 1);
});

test('stale camera completion does not clear a replacement camera stream', async () => {
  let resolvePlayback;
  let stoppedOldTracks = 0;
  let stopCameraCalls = 0;
  const oldStream = {
    getTracks: () => [{ stop: () => { stoppedOldTracks += 1; } }],
  };
  const replacementStream = { getTracks: () => [] };
  let activeStream = oldStream;
  const video = {
    srcObject: oldStream,
    play: () => new Promise((resolve) => { resolvePlayback = resolve; }),
  };

  const cameraCompletion = activateCameraAfterPlayback({
    getActiveStream: () => activeStream,
    getCurrentIntakeRevision: () => 2,
    intakeRevision: 1,
    onReady: () => assert.fail('The stale camera must not become ready.'),
    stopCamera: () => { stopCameraCalls += 1; },
    stream: oldStream,
    video,
  });

  activeStream = replacementStream;
  video.srcObject = replacementStream;
  resolvePlayback();

  assert.equal(await cameraCompletion, false);
  assert.equal(activeStream, replacementStream);
  assert.equal(video.srcObject, replacementStream);
  assert.equal(stopCameraCalls, 0);
  assert.equal(stoppedOldTracks, 1);
});
