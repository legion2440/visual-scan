export async function activateCameraAfterPlayback({
  getActiveStream,
  getCurrentIntakeRevision,
  intakeRevision,
  onReady,
  stopCamera,
  stream,
  video,
}) {
  await video.play();
  if (
    intakeRevision !== getCurrentIntakeRevision()
    || getActiveStream() !== stream
    || video.srcObject !== stream
  ) {
    if (getActiveStream() === stream) {
      stopCamera();
    } else {
      stream.getTracks().forEach((track) => track.stop());
    }
    return false;
  }
  onReady();
  return true;
}
