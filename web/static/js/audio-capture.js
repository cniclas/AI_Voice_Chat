// Microphone capture via AudioWorklet: raw PCM chunks are concatenated and
// encoded into a standard WAV container client-side, so the server can reuse
// its existing WAV decoding path (session_core.load_audio_16k) unchanged —
// no WebM/Opus decoding or ffmpeg dependency needed.
const AudioCapture = (() => {
  let audioContext = null;
  let workletNode = null;
  let sourceNode = null;
  let mediaStream = null;
  let chunks = [];
  let inputDeviceId = localStorage.getItem('inputDeviceId') || null;

  function setInputDevice(deviceId) {
    inputDeviceId = deviceId || null;
    if (deviceId) localStorage.setItem('inputDeviceId', deviceId);
  }

  async function start() {
    chunks = [];
    const constraints = {
      audio: {
        channelCount: 1,
        echoCancellation: true,
        deviceId: inputDeviceId ? { exact: inputDeviceId } : undefined,
      },
    };
    mediaStream = await navigator.mediaDevices.getUserMedia(constraints);
    audioContext = new AudioContext();
    await audioContext.audioWorklet.addModule('/static/js/worklet/pcm-recorder-processor.js');

    sourceNode = audioContext.createMediaStreamSource(mediaStream);
    workletNode = new AudioWorkletNode(audioContext, 'pcm-recorder');
    workletNode.port.onmessage = (event) => chunks.push(event.data);

    // Deliberately not connected to destination — no mic monitoring/loopback.
    sourceNode.connect(workletNode);
  }

  function stop() {
    const sampleRate = audioContext ? audioContext.sampleRate : 48000;

    sourceNode?.disconnect();
    workletNode?.disconnect();
    mediaStream?.getTracks().forEach((track) => track.stop());
    audioContext?.close();

    const totalLength = chunks.reduce((sum, c) => sum + c.length, 0);
    const merged = new Float32Array(totalLength);
    let offset = 0;
    for (const chunk of chunks) {
      merged.set(chunk, offset);
      offset += chunk.length;
    }
    chunks = [];

    return encodeWav(merged, sampleRate);
  }

  function encodeWav(float32Data, sampleRate) {
    const bytesPerSample = 2;
    const dataSize = float32Data.length * bytesPerSample;
    const buffer = new ArrayBuffer(44 + dataSize);
    const view = new DataView(buffer);

    const writeString = (offset, str) => {
      for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
    };

    writeString(0, 'RIFF');
    view.setUint32(4, 36 + dataSize, true);
    writeString(8, 'WAVE');
    writeString(12, 'fmt ');
    view.setUint32(16, 16, true);            // PCM chunk size
    view.setUint16(20, 1, true);             // audio format = PCM
    view.setUint16(22, 1, true);             // channels = mono
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * bytesPerSample, true); // byte rate
    view.setUint16(32, bytesPerSample, true);              // block align
    view.setUint16(34, 16, true);            // bits per sample
    writeString(36, 'data');
    view.setUint32(40, dataSize, true);

    let offset = 44;
    for (let i = 0; i < float32Data.length; i++) {
      const s = Math.max(-1, Math.min(1, float32Data[i]));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
      offset += bytesPerSample;
    }

    return new Blob([buffer], { type: 'audio/wav' });
  }

  return { start, stop, setInputDevice };
})();
