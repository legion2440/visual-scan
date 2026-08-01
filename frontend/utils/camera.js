export async function activateCameraAfterPlayback({
  getActiveStream,
  getCurrentIntakeRevision,
  intakeRevision,
  onReady,
  stream,
  video,
}) {
  await video.play();
  if (
    intakeRevision !== getCurrentIntakeRevision()
    || getActiveStream() !== stream
    || video.srcObject !== stream
  ) {
    stream.getTracks().forEach((track) => track.stop());
    return false;
  }
  onReady();
  return true;
}
