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
  let sessionName = null;

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
  // Set from the server's ready message. In demo mode the language buttons
  // feed the next scripted manuscript line instead of recording the mic.
  let demoMode = false;

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

  function formatDuration(ms) {
    if (ms == null) return null;
    if (ms < 1000) return `${ms.toFixed(0)} ms`;
    return `${(ms / 1000).toFixed(1)} s`;
  }

  function appendMessage(author, language, text, audioFilename, processingMs = null) {
    const div = document.createElement('div');
    div.className = `msg msg--${author}`;
    const header = document.createElement('div');
    header.className = 'msg-header';
    const langTag = document.createElement('span');
    langTag.className = 'msg-lang';
    langTag.textContent = language;
    header.appendChild(langTag);

    if (processingMs != null) {
      const timer = document.createElement('span');
      timer.className = 'msg-timer';
      timer.textContent = formatDuration(processingMs);
      header.appendChild(timer);
    }

    div.appendChild(header);
    const body = document.createElement('div');
    body.className = 'msg-body';
    body.textContent = text;
    div.appendChild(body);

    if (audioFilename && sessionName) {
      const audio = document.createElement('audio');
      audio.controls = true;
      audio.preload = 'metadata';
      audio.className = 'msg-audio';
      audio.src = `/session/${sessionName}/${encodeURIComponent(audioFilename)}`;
      body.appendChild(audio);
    }
    chatLog.appendChild(div);
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  async function populateDevices() {
    // Demo mode never opens the mic, so don't ask for permission (device
    // labels may then be generic — fine for a design playground).
    if (!demoMode) {
      try {
        const temp = await navigator.mediaDevices.getUserMedia({ audio: true });
        temp.getTracks().forEach((t) => t.stop());
      } catch (e) {
        statusText.textContent = 'Microphone permission is required to record.';
      }
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
      statusText.textContent = 'Loading Whisper and Kokoro…';
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
      statusText.textContent = demoMode
        ? 'Your turn — press a language button for the next scripted line.'
        : 'Your turn — press a language button and speak.';
      btnEn.disabled = false;
      btnEs.disabled = false;
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
        demoMode = !!msg.demo;
        resetToLanding();
        phaseBadge.textContent = demoMode ? 'demo' : 'ready';
        setAvatarState('idle');
        statusText.textContent = demoMode
          ? 'Demo mode — the buttons play a scripted session, no mic needed.'
          : 'Ready. Pick how you want to start.';
        loadingIndicator.hidden = true;
        populateDevices();
        break;
      case 'mode':
        sessionName = msg.session_name || sessionName;
        enterConversation(msg.mode);
        break;
      case 'mode':
        enterConversation(msg.mode);
        break;
      case 'status':
        if (msg.state) setAvatarState(msg.state);
        if (msg.message) statusText.textContent = msg.message;
        // Back to idle means the turn is over — make the language buttons
        // pressable again (they're the simulate triggers in demo mode).
        if (msg.state === 'idle' && !recording) {
          btnEn.disabled = false;
          btnEs.disabled = false;
        }
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
        appendMessage(msg.author, msg.language, msg.text, msg.audio_filename, msg.processing_ms);
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

  async function playRemoteAudio(url) {
    try {
      setAvatarState('speaking');
      statusText.textContent = 'Loading replay audio…';
      const response = await fetch(url);
      if (!response.ok) throw new Error(`Failed to load ${response.status}`);
      const blob = await response.blob();
      await AudioPlayback.playBlob(blob);
      statusText.textContent = 'Replay finished.';
    } catch (error) {
      statusText.textContent = `Could not replay audio: ${error.message}`;
    } finally {
      setAvatarState('idle');
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
    function addLink(href, text) {
      const wrap = document.createElement('div');
      const a = document.createElement('a');
      a.href = href;
      a.textContent = text;
      a.target = '_blank';
      wrap.appendChild(a);
      completeLinks.appendChild(wrap);
    }
    if (msg.session_name && msg.transcript_filename) {
      addLink(`/session/${msg.session_name}/${encodeURIComponent(msg.transcript_filename)}`, 'Open transcript');
    }
    if (msg.session_name && msg.lesson_filename) {
      addLink(`/session/${msg.session_name}/${encodeURIComponent(msg.lesson_filename)}`, 'Open lesson');
    }
    if (msg.session_name && msg.homework_filename) {
      addLink(`/session/${msg.session_name}/${encodeURIComponent(msg.homework_filename)}`, 'Open homework');
    }
    if (!msg.transcript_filename) {
      const p = document.createElement('p');
      p.textContent = 'No conversation recorded.';
      completeLinks.appendChild(p);
    }
  }

  function sendSimulatedTurn(language) {
    // Demo mode: no recording — ask the server to play the next scripted
    // exchange (user line + AI answer) in the pressed language. The disabled
    // state doubles as the in-flight guard until the reply has played.
    if (!wsOpen() || btnEn.disabled) return;
    btnEn.disabled = true;
    btnEs.disabled = true;
    setAvatarState('thinking');
    ws.send(JSON.stringify({ type: 'simulate_turn', language }));
  }

  async function startRecording(language) {
    if (demoMode) {
      sendSimulatedTurn(language);
      return;
    }
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
