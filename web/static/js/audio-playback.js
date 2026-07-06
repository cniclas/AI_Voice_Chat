// Playback happens entirely in the browser (not via server-side sd.play),
// on a device the user can choose independently from the input device —
// this is what lets a Bluetooth headset stay on the high-quality A2DP
// profile: as long as nothing ever opens a mic stream against it, the OS
// never renegotiates it down to HFP/HSP.
const AudioPlayback = (() => {
  const player = document.getElementById('player');

  function supportsSinkId() {
    return typeof player.setSinkId === 'function';
  }

  async function setOutputDevice(deviceId) {
    if (!deviceId) return;
    localStorage.setItem('outputDeviceId', deviceId);
    if (supportsSinkId()) {
      try {
        await player.setSinkId(deviceId);
      } catch (e) {
        console.warn('setSinkId failed:', e);
      }
    }
  }

  function playBlob(blob) {
    return new Promise((resolve) => {
      const url = URL.createObjectURL(blob);
      player.src = url;
      player.onended = () => {
        URL.revokeObjectURL(url);
        resolve();
      };
      player.play().catch((e) => {
        console.warn('playback failed:', e);
        URL.revokeObjectURL(url);
        resolve();
      });
    });
  }

  return { setOutputDevice, playBlob, supportsSinkId };
})();
