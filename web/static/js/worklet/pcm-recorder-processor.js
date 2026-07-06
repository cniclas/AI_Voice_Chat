// Runs on the real-time audio thread — no DOM/network access here, so raw
// Float32 chunks are handed back to the main thread via postMessage.
class PCMRecorderProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel && channel.length) {
      this.port.postMessage(channel.slice());
    }
    return true;
  }
}

registerProcessor('pcm-recorder', PCMRecorderProcessor);
