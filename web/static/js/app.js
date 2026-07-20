(() => {
  const phaseBadge = document.getElementById('phase-badge');
  const avatar = document.getElementById('avatar');
  const statusText = document.getElementById('status-text');
  const landingPanel = document.getElementById('landing-panel');
  const storyPanel = document.getElementById('story-panel');
  const storyTitle = document.getElementById('story-title');
  const storyText = document.getElementById('story-text');
  const chatPanel = document.getElementById('chat-panel');
  const chatLog = document.getElementById('chat-log');
  const controls = document.getElementById('controls');
  const inputSelect = document.getElementById('input-device');
  const outputSelect = document.getElementById('output-device');
  const btnTalk = document.getElementById('btn-talk');
  const btnStory = document.getElementById('btn-story');
  const btnEn = document.getElementById('btn-en');
  const btnEs = document.getElementById('btn-es');
  const btnStop = document.getElementById('btn-stop');
  const btnExit = document.getElementById('btn-exit');
  const completePanel = document.getElementById('complete-panel');
  const completeLinks = document.getElementById('complete-links');
  const loadingIndicator = document.getElementById('loading-indicator');

  // Map backend avatar states to the CSS classes we actually have.
  const AVATAR_CLASS = {
    idle: 'idle', listening: 'listening', thinking: 'thinking',
    speaking: 'speaking', loading: 'thinking',
  };

  let ws = null;
  let recording = false;
  let currentLanguage = null;
  let pendingBinaryTurn = null;
  let sessionFinished = false;
  let reconnectDelay = 1000;
  let backendReady = false;

  function wsOpen() {
    return ws && ws.readyState === WebSocket.OPEN;
  }

  function updateLandingState() {
    const ready = backendReady && wsOpen();
    btnTalk.disabled = !ready;
    btnStory.disabled = !ready;
    loadingIndicator.hidden = ready;
  }

  function setAvatarState(state) {
    avatar.className = `avatar avatar--${AVATAR_CLASS[state] || 'idle'}`;
  }

  function appendMessage(author, language, text) {
    const div = document.createElement('div');
    div.className = `msg msg--${author}`;
    const langTag = document.createElement('span');
    langTag.className = 'msg-lang';
    langTag.textContent = language;
    div.appendChild(langTag);
    div.appendChild(document.createTextNode(text));
    chatLog.appendChild(div);
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  async function populateDevices() {
    try {
      const temp = await navigator.mediaDevices.getUserMedia({ audio: true });
      temp.getTracks().forEach((t) => t.stop());
    } catch (e) {
      statusText.textContent = 'Microphone permission is required to record.';
    }

    const devices = await navigator.mediaDevices.enumerateDevices();
    const inputs = devices.filter((d) => d.kind === 'audioinput');
    const outputs = devices.filter((d) => d.kind === 'audiooutput');

    inputSelect.innerHTML = inputs
      .map((d) => `<option value="${d.deviceId}">${d.label || 'Microphone'}</option>`)
      .join('');
    outputSelect.innerHTML = outputs
      .map((d) => `<option value="${d.deviceId}">${d.label || 'Speaker'}</option>`)
      .join('');

    const savedInput = localStorage.getItem('inputDeviceId');
    const savedOutput = localStorage.getItem('outputDeviceId');
    if (savedInput && inputs.some((d) => d.deviceId === savedInput)) inputSelect.value = savedInput;
    if (savedOutput && outputs.some((d) => d.deviceId === savedOutput)) outputSelect.value = savedOutput;

    AudioCapture.setInputDevice(inputSelect.value);
    await AudioPlayback.setOutputDevice(outputSelect.value);

    if (!AudioPlayback.supportsSinkId()) {
      outputSelect.disabled = true;
      outputSelect.title = 'This browser does not support choosing an output device — try Chrome or Edge.';
    }
  }

  inputSelect.addEventListener('change', () => AudioCapture.setInputDevice(inputSelect.value));
  outputSelect.addEventListener('change', () => AudioPlayback.setOutputDevice(outputSelect.value));

  function connect() {
    ws = new WebSocket(`ws://${location.host}/ws/session`);
    ws.binaryType = 'arraybuffer';

    ws.onopen = () => {
      reconnectDelay = 1000;
      backendReady = false;
      phaseBadge.textContent = 'starting up';
      statusText.textContent = 'Loading Whisper and Piper…';
      setAvatarState('loading');
      updateLandingState();
    };

    ws.onmessage = async (event) => {
      if (typeof event.data !== 'string') {
        await handleAudioFrame(event.data);
        return;
      }
      handleControlMessage(JSON.parse(event.data));
    };

    ws.onclose = () => {
      // After a completed session the server closes the socket on purpose —
      // leave the completion screen alone. Any other close (server down,
      // uvicorn --reload restart, network hiccup) gets automatic retries,
      // so a page opened while the backend is still loading Whisper simply
      // connects once it's up instead of dead-ending at "Disconnected".
      if (sessionFinished) return;
      backendReady = false;
      phaseBadge.textContent = 'reconnecting';
      setAvatarState('loading');
      statusText.textContent = 'Loading Whisper model…';
      updateLandingState();
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, 10000);
    };
  }

  function resetToLanding() {
    // A reconnect gets a brand-new server-side session; drop any stale
    // mid-session UI from the previous connection.
    landingPanel.hidden = false;
    storyPanel.hidden = true;
    chatPanel.hidden = true;
    controls.hidden = true;
    completePanel.hidden = true;
    chatLog.innerHTML = '';
    recording = false;
    pendingBinaryTurn = null;
    btnEn.disabled = false;
    btnEs.disabled = false;
    btnStop.disabled = true;
    btnExit.disabled = false;
    updateLandingState();
  }

  async function handleAudioFrame(arrayBuffer) {
    const blob = new Blob([arrayBuffer], { type: 'audio/wav' });
    setAvatarState('speaking');
    statusText.textContent = 'Playing back to you…';
    await AudioPlayback.playBlob(blob);
    setAvatarState('idle');
    if (pendingBinaryTurn === 'story') {
      if (wsOpen()) ws.send(JSON.stringify({ type: 'tts_playback_done' }));
    } else {
      statusText.textContent = 'Your turn — press a language button and speak.';
    }
    pendingBinaryTurn = null;
  }

  function enterConversation(mode) {
    landingPanel.hidden = true;
    chatPanel.hidden = false;
    controls.hidden = false;
    phaseBadge.textContent = mode === 'story' ? 'story' : 'talking';
  }

  function requestMode(mode) {
    if (!wsOpen() || !backendReady) return;
    if (sessionFinished) return;
    const message = { type: mode === 'story' ? 'start_story' : 'start_talk' };
    ws.send(JSON.stringify(message));
  }

  function handleControlMessage(msg) {
    switch (msg.type) {
      case 'ready':
        backendReady = true;
        resetToLanding();
        phaseBadge.textContent = 'ready';
        setAvatarState('idle');
        statusText.textContent = 'Ready. Pick how you want to start.';
        loadingIndicator.hidden = true;
        populateDevices();
        break;
      case 'mode':
        enterConversation(msg.mode);
        break;
      case 'status':
        if (msg.state) setAvatarState(msg.state);
        if (msg.message) statusText.textContent = msg.message;
        break;
      case 'story':
        if (msg.story) {
          storyPanel.hidden = false;
          storyTitle.textContent = msg.story_title;
          storyText.textContent = msg.story;
        }
        break;
      case 'tts_audio':
        pendingBinaryTurn = msg.turn;
        break;
      case 'transcript':
        appendMessage(msg.author, msg.language, msg.text);
        break;
      case 'no_speech':
        statusText.textContent = 'No speech detected, try again.';
        setAvatarState('idle');
        break;
      case 'error':
        statusText.textContent = `Error: ${msg.message}`;
        setAvatarState('idle');
        break;
      case 'done':
        finishSession(msg);
        break;
    }
  }

  function finishSession(msg) {
    sessionFinished = true;
    landingPanel.hidden = true;
    controls.hidden = true;
    completePanel.hidden = false;
    setAvatarState('idle');
    statusText.textContent = '¡Hasta luego!';
    phaseBadge.textContent = 'done';

    completeLinks.innerHTML = '';
    if (msg.transcript_path) {
      const p = document.createElement('p');
      p.textContent = 'Transcript saved to your session folder.';
      completeLinks.appendChild(p);
    }
    if (msg.homework_path) {
      const p = document.createElement('p');
      p.textContent = 'Homework saved to your session folder.';
      completeLinks.appendChild(p);
    }
    if (!msg.transcript_path) {
      const p = document.createElement('p');
      p.textContent = 'No conversation recorded.';
      completeLinks.appendChild(p);
    }
  }

  async function startRecording(language) {
    if (recording) return;
    recording = true;
    currentLanguage = language;
    btnEn.disabled = true;
    btnEs.disabled = true;
    btnStop.disabled = false;
    setAvatarState('listening');
    statusText.textContent = `Listening (${language})… press Stop when you're done.`;
    try {
      await AudioCapture.start();
    } catch (e) {
      statusText.textContent = `Could not start recording: ${e.message}`;
      recording = false;
      btnEn.disabled = false;
      btnEs.disabled = false;
      btnStop.disabled = true;
      setAvatarState('idle');
    }
  }

  async function stopRecording() {
    if (!recording) return;
    recording = false;
    btnEn.disabled = false;
    btnEs.disabled = false;
    btnStop.disabled = true;
    setAvatarState('thinking');
    statusText.textContent = 'Transcribing…';

    const wavBlob = AudioCapture.stop();
    const buffer = await wavBlob.arrayBuffer();
    if (!wsOpen()) return;
    ws.send(JSON.stringify({ type: 'user_audio', language: currentLanguage }));
    ws.send(buffer);
  }

  btnTalk.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    requestMode('talk');
  });
  btnStory.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    requestMode('story');
  });
  btnEn.addEventListener('click', () => startRecording('en'));
  btnEs.addEventListener('click', () => startRecording('es'));
  btnStop.addEventListener('click', stopRecording);
  btnExit.addEventListener('click', () => {
    if (!wsOpen()) return;
    ws.send(JSON.stringify({ type: 'end_session' }));
    btnExit.disabled = true;
  });

  updateLandingState();
  connect();
})();
