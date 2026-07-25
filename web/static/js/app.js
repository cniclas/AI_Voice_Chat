(() => {
  const phaseBadge = document.getElementById('phase-badge');
  const avatar = document.getElementById('avatar');
  const statusText = document.getElementById('status-text');
  const landingPanel = document.getElementById('landing-panel');
  const chatPanel = document.getElementById('chat-panel');
  const chatLog = document.getElementById('chat-log');
  const controls = document.getElementById('controls');
  const inputSelect = document.getElementById('input-device');
  const outputSelect = document.getElementById('output-device');
  const btnTalk = document.getElementById('btn-talk');
  const btnChallenge = document.getElementById('btn-challenge');
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
  // The most recent assistant bubble's <audio> element — the single playback
  // source that a tts_audio cue auto-plays.
  let lastAssistantAudio = null;
  let sessionFinished = false;
  let reconnectDelay = 1000;
  let backendReady = false;
  // "translate" while the challenge waits for the spoken English translation
  // (Spanish is not an option for that turn), "converse" otherwise.
  let stage = 'converse';
  // What to put back on screen once a reply finishes playing. The server owns
  // it, so the prompt after the story is "translate it" and not the generic one.
  let idlePrompt = 'Your turn — press a language button and speak.';
  // Set from the server's ready message. In demo mode the language buttons
  // feed the next scripted manuscript line instead of recording the mic.
  let demoMode = false;

  function wsOpen() {
    return ws && ws.readyState === WebSocket.OPEN;
  }

  function updateLandingState() {
    const ready = backendReady && wsOpen();
    btnTalk.disabled = !ready;
    btnChallenge.disabled = !ready;
    loadingIndicator.hidden = ready;
  }

  // Re-arm the record buttons for the next turn. Spanish stays out of reach
  // during the translation stage: the challenge is to say the story's meaning
  // in English, and a Spanish answer would skip it.
  function enableRecordButtons() {
    if (recording) return;
    btnEn.disabled = false;
    btnEs.disabled = stage === 'translate';
    btnEs.title = stage === 'translate'
      ? 'Translate into English first — press 🎙 English.'
      : '';
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

    let audio = null;
    if (audioFilename && sessionName) {
      audio = document.createElement('audio');
      audio.controls = true;
      audio.preload = 'metadata';
      audio.className = 'msg-audio';
      audio.src = `/session/${sessionName}/${encodeURIComponent(audioFilename)}`;
      AudioPlayback.applyTo(audio);
      // Single-source rule: starting any bubble pauses every other one, so
      // two voices can never overlap and the visible slider always tracks
      // the sound actually playing.
      audio.addEventListener('play', () => {
        chatLog.querySelectorAll('audio').forEach((other) => {
          if (other !== audio) other.pause();
        });
        setAvatarState('speaking');
      });
      const backToIdle = () => {
        const anyPlaying = [...chatLog.querySelectorAll('audio')].some((a) => !a.paused);
        if (!anyPlaying) setAvatarState('idle');
      };
      audio.addEventListener('pause', backToIdle);
      audio.addEventListener('ended', backToIdle);
      body.appendChild(audio);
    }
    chatLog.appendChild(div);
    chatLog.scrollTop = chatLog.scrollHeight;
    return audio;
  }

  // A generated artifact the student can open mid-session — the lesson with
  // its literal translation, or the written-up review. The server only sends
  // these once they're safe to read (the lesson is the answer key, so it
  // arrives after the translation has been marked).
  function appendArtifact(filename, label) {
    if (!sessionName) return;
    const div = document.createElement('div');
    div.className = 'msg msg--artifact';
    const a = document.createElement('a');
    a.href = `/session/${sessionName}/${encodeURIComponent(filename)}`;
    a.target = '_blank';
    a.rel = 'noopener';
    a.textContent = label || filename;
    div.appendChild(a);
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

    ws.onmessage = (event) => {
      // Audio is no longer streamed over the WebSocket — bubbles fetch their
      // WAVs from the session route. Ignore any stray binary frame.
      if (typeof event.data !== 'string') return;
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
    chatPanel.hidden = true;
    controls.hidden = true;
    completePanel.hidden = true;
    chatLog.innerHTML = '';
    recording = false;
    lastAssistantAudio = null;
    stage = 'converse';
    idlePrompt = 'Your turn — press a language button and speak.';
    enableRecordButtons();
    btnStop.disabled = true;
    btnExit.disabled = false;
    updateLandingState();
  }

  // A tts_audio cue from the server: auto-play the just-appended assistant
  // bubble through its own <audio> element. Because playback runs in the
  // visible player itself, the slider is live from the first second and
  // there is never a second, hidden voice to collide with.
  async function playTranscriptAudio(turn) {
    const el = lastAssistantAudio;
    const finish = () => {
      if (turn === 'story') {
        if (wsOpen()) ws.send(JSON.stringify({ type: 'tts_playback_done' }));
      } else {
        statusText.textContent = demoMode
          ? 'Your turn — press a language button for the next scripted line.'
          : idlePrompt;
        enableRecordButtons();
      }
    };
    if (!el) {
      finish();
      return;
    }
    statusText.textContent = 'Playing back to you…';
    el.addEventListener('ended', finish, { once: true });
    try {
      el.currentTime = 0;
      await el.play();
    } catch (e) {
      // Autoplay refused or the file failed to load — leave it to the
      // user's play button, but don't wedge the session flow.
      el.removeEventListener('ended', finish);
      finish();
      statusText.textContent = 'Press play on the message to listen.';
    }
  }

  function enterConversation(mode) {
    landingPanel.hidden = true;
    chatPanel.hidden = false;
    controls.hidden = false;
    phaseBadge.textContent = mode === 'challenge' ? 'challenge' : 'talking';
  }

  function requestMode(mode) {
    if (!wsOpen() || !backendReady) return;
    if (sessionFinished) return;
    const message = { type: mode === 'challenge' ? 'start_challenge' : 'start_talk' };
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
      case 'stage':
        stage = msg.stage;
        if (msg.prompt) idlePrompt = msg.prompt;
        phaseBadge.textContent = stage === 'translate' ? 'translate' : 'talking';
        enableRecordButtons();
        break;
      case 'status':
        if (msg.state) setAvatarState(msg.state);
        if (msg.message) statusText.textContent = msg.message;
        // Back to idle means the turn is over — make the language buttons
        // pressable again (they're the simulate triggers in demo mode).
        if (msg.state === 'idle') enableRecordButtons();
        break;
      case 'tts_audio':
        playTranscriptAudio(msg.turn);
        break;
      case 'transcript': {
        const audioEl = appendMessage(msg.author, msg.language, msg.text, msg.audio_filename, msg.processing_ms);
        if (msg.author === 'assistant') lastAssistantAudio = audioEl;
        break;
      }
      case 'artifact':
        appendArtifact(msg.filename, msg.label);
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
    function addLink(href, text) {
      const wrap = document.createElement('div');
      const a = document.createElement('a');
      a.href = href;
      a.textContent = text;
      a.target = '_blank';
      wrap.appendChild(a);
      completeLinks.appendChild(wrap);
    }
    const artifacts = [
      [msg.transcript_filename, 'Open transcript'],
      [msg.lesson_filename, 'Open lesson (story + literal translation)'],
      [msg.review_filename, 'Open translation review'],
      [msg.homework_filename, 'Open homework'],
    ];
    artifacts.forEach(([filename, label]) => {
      if (msg.session_name && filename) {
        addLink(`/session/${msg.session_name}/${encodeURIComponent(filename)}`, label);
      }
    });
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
    statusText.textContent = stage === 'translate'
      ? 'Listening — say what the story means, sentence by sentence. Press Stop when done.'
      : `Listening (${language})… press Stop when you're done.`;
    try {
      await AudioCapture.start();
    } catch (e) {
      statusText.textContent = `Could not start recording: ${e.message}`;
      recording = false;
      enableRecordButtons();
      btnStop.disabled = true;
      setAvatarState('idle');
    }
  }

  async function stopRecording() {
    if (!recording) return;
    recording = false;
    enableRecordButtons();
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
  btnChallenge.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    requestMode('challenge');
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
