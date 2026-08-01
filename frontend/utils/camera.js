function releaseCameraStream({ getActiveStream, stopCamera, stream }) {
  if (getActiveStream() === stream) {
    stopCamera();
  } else {
    stream.getTracks().forEach((track) => track.stop());
  }
}

export async function activateCameraAfterPlayback({
  getActiveStream,
  getCurrentIntakeRevision,
  intakeRevision,
  onReady,
  stopCamera,
  stream,
  video,
}) {
  try {
    await video.play();
  } catch (error) {
    releaseCameraStream({ getActiveStream, stopCamera, stream });
    throw error;
  }
  if (
    intakeRevision !== getCurrentIntakeRevision()
    || getActiveStream() !== stream
    || video.srcObject !== stream
  ) {
    releaseCameraStream({ getActiveStream, stopCamera, stream });
    return false;
  }
  onReady();
  return true;
}
