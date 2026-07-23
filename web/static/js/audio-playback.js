// Playback happens entirely in the browser, on a device the user can choose
// independently from the input device — this is what lets a Bluetooth
// headset stay on the high-quality A2DP profile: as long as nothing ever
// opens a mic stream against it, the OS never renegotiates it down to
// HFP/HSP.
//
// Every sound in the app plays through a chat bubble's own <audio controls>
// element (a single always-controllable source per message); this module
// only remembers the chosen output device and routes those elements to it.
const AudioPlayback = (() => {
  // Never plays anything — exists purely to detect setSinkId support.
  const probe = document.getElementById('player');
  let outputDeviceId = null;

  function supportsSinkId() {
    return typeof probe.setSinkId === 'function';
  }

  async function applyTo(element) {
    if (!outputDeviceId || !supportsSinkId()) return;
    try {
      await element.setSinkId(outputDeviceId);
    } catch (e) {
      console.warn('setSinkId failed:', e);
    }
  }

  async function setOutputDevice(deviceId) {
    if (!deviceId) return;
    localStorage.setItem('outputDeviceId', deviceId);
    outputDeviceId = deviceId;
    // Retarget every bubble player already in the chat log.
    for (const el of document.querySelectorAll('#chat-log audio')) {
      await applyTo(el);
    }
  }

  return { setOutputDevice, supportsSinkId, applyTo };
})();
