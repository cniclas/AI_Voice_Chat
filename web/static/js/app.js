(() => {
  const phaseBadge = document.getElementById('phase-badge');
  const avatar = document.getElementById('avatar');
  const statusText = document.getElementById('status-text');
  const landingPanel = document.getElementById('landing-panel');
  const userPicker = document.getElementById('user-picker');
  const sessionSetup = document.getElementById('session-setup');
  const chatPanel = document.getElementById('chat-panel');
  const chatLog = document.getElementById('chat-log');
  const controls = document.getElementById('controls');
  const inputSelect = document.getElementById('input-device');
  const outputSelect = document.getElementById('output-device');
  const btnTalk = document.getElementById('btn-talk');
  const btnChallenge = document.getElementById('btn-challenge');
  const btnChallengeReverse = document.getElementById('btn-challenge-reverse');
  const btnNative = document.getElementById('btn-native');
  const btnTarget = document.getElementById('btn-target');
  const challengeSub = document.getElementById('challenge-sub');
  const challengeReverseSub = document.getElementById('challenge-reverse-sub');
  const btnStop = document.getElementById('btn-stop');
  const btnExit = document.getElementById('btn-exit');
  const completePanel = document.getElementById('complete-panel');
  const completeLinks = document.getElementById('complete-links');
  const btnBack = document.getElementById('btn-back');
  const artifactViewer = document.getElementById('artifact-viewer');
  const artifactViewerTitle = document.getElementById('artifact-viewer-title');
  const artifactViewerContent = document.getElementById('artifact-viewer-content');
  const btnCloseViewer = document.getElementById('btn-close-viewer');
  const loadingIndicator = document.getElementById('loading-indicator');
  let sessionName = null;

  // Map backend avatar states to the CSS classes we actually have.
  const AVATAR_CLASS = {
    idle: 'idle', listening: 'listening', thinking: 'thinking',
    speaking: 'speaking', loading: 'thinking',
  };

  let ws = null;
  let selectedUserId = null;
  let recording = false;
  let currentLanguage = null;
  // The most recent assistant bubble's <audio> element — the single playback
  // source that a tts_audio cue auto-plays.
  let lastAssistantAudio = null;
  let sessionFinished = false;
  let reconnectDelay = 1000;
  let backendReady = false;
  // "translate" while the challenge waits for the spoken translation (only the
  // language it has to be in is pressable for that turn), "converse" otherwise.
  let stage = 'converse';
  // Which language the spoken translation has to be in — the student's own for
  // the reading challenge, the one they're learning for the reverse one. The
  // server sets it with the stage.
  let translateLang = null;
  // The student's language pair, sent by the server in `ready` — which two
  // languages the record buttons stand for depends on who is practicing.
  let nativeLang = { code: 'en', label: 'English' };
  let targetLang = { code: 'es', label: 'Español' };
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
    btnChallengeReverse.disabled = !ready;
    loadingIndicator.hidden = ready;
  }

  // Re-arm the record buttons for the next turn. During a translation only the
  // language being translated *into* is pressable — the server says which one,
  // since a reverse challenge asks for the target language and the reading
  // challenge for the student's own — and pressing the other would skip the
  // exercise.
  function enableRecordButtons() {
    // A translate stage that never named a language (an older server, a
    // dropped field) leaves both buttons live rather than none.
    const translating = stage === 'translate' && !!translateLang;
    if (recording) return;
    [[btnNative, nativeLang], [btnTarget, targetLang]].forEach(([btn, lang]) => {
      const wanted = !translating || lang.code === translateLang;
      btn.disabled = !wanted;
      btn.title = wanted
        ? ''
        : `Translate into ${labelFor(translateLang)} first — press 🎙 ${labelFor(translateLang)}.`;
    });
  }

  function labelFor(code) {
    return code === targetLang.code ? targetLang.label : nativeLang.label;
  }

  function escapeHtml(text) {
    return text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  function renderInlineMarkdown(text) {
    return text
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.+?)\*/g, '<em>$1</em>')
      .replace(/`([^`]+?)`/g, '<code>$1</code>')
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener">$1</a>');
  }

  function renderMarkdown(raw) {
    const lines = raw.split(/\r?\n/);
    let html = '';
    let inList = false;
    let inCode = false;

    for (const line of lines) {
      if (line.startsWith('```')) {
        if (inCode) {
          html += '</code></pre>';
          inCode = false;
        } else {
          html += '<pre><code>';
          inCode = true;
        }
        continue;
      }

      if (inCode) {
        html += escapeHtml(line) + '\n';
        continue;
      }

      if (/^#{1,6}\s+/.test(line)) {
        if (inList) { html += '</ul>'; inList = false; }
        const level = line.match(/^#+/)[0].length;
        const text = escapeHtml(line.slice(level + 1));
        html += `<h${level}>${renderInlineMarkdown(text)}</h${level}>`;
        continue;
      }

      if (/^[-*]\s+/.test(line)) {
        if (!inList) { html += '<ul>'; inList = true; }
        html += `<li>${renderInlineMarkdown(escapeHtml(line.replace(/^[-*]\s+/, '')))}</li>`;
        continue;
      }

      if (line.trim() === '') {
        if (inList) { html += '</ul>'; inList = false; }
        continue;
      }

      html += `<p>${renderInlineMarkdown(escapeHtml(line))}</p>`;
    }

    if (inList) html += '</ul>';
    return html;
  }

  function showMarkdownViewer(url, title) {
    if (!sessionName) return;
    artifactViewerTitle.textContent = title;
    artifactViewerContent.innerHTML = 'Loading…';
    artifactViewer.hidden = false;
    fetch(url)
      .then((res) => {
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        return res.text();
      })
      .then((text) => {
        artifactViewerContent.innerHTML = renderMarkdown(text);
      })
      .catch((err) => {
        artifactViewerContent.textContent = `Could not load file: ${err.message}`;
      });
  }

  function closeMarkdownViewer() {
    artifactViewer.hidden = true;
    artifactViewerContent.innerHTML = '';
  }

  // Put the pair on the buttons (and in the challenge blurb) once the server
  // has told us who is practicing.
  function applyLanguagePair(msg) {
    if (msg.native) nativeLang = msg.native;
    if (msg.target) targetLang = msg.target;
    btnNative.textContent = `🎙 ${nativeLang.label}`;
    btnTarget.textContent = `🎙 ${targetLang.label}`;
    if (challengeSub) {
      challengeSub.textContent =
        `Hear today's ${targetLang.label} story, translate it aloud into `
        + `${nativeLang.label}, get it marked — then keep talking`;
    }
    if (challengeReverseSub) {
      challengeReverseSub.textContent =
        `Hear the same story in ${nativeLang.label}, say it back in `
        + `${targetLang.label}, get it marked — then keep talking`;
    }
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
    const link = document.createElement('a');
    link.href = `/session/${sessionName}/${encodeURIComponent(filename)}`;
    link.target = '_blank';
    link.rel = 'noopener';
    link.textContent = label || filename;
    div.appendChild(link);

    if (filename.endsWith('.md')) {
      const actions = document.createElement('div');
      actions.className = 'artifact-actions';
      const viewButton = document.createElement('button');
      viewButton.type = 'button';
      viewButton.className = 'artifact-view-btn';
      viewButton.textContent = 'View in app';
      viewButton.addEventListener('click', () => {
        showMarkdownViewer(
          `/session/${sessionName}/${encodeURIComponent(filename)}`,
          label || filename,
        );
      });
      actions.appendChild(viewButton);
      div.appendChild(actions);
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
  btnCloseViewer.addEventListener('click', closeMarkdownViewer);

  function connect() {
    ws = new WebSocket(`ws://${location.host}/ws/session?user=${encodeURIComponent(selectedUserId)}`);
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
    artifactViewer.hidden = true;
    chatLog.innerHTML = '';
    recording = false;
    lastAssistantAudio = null;
    stage = 'converse';
    translateLang = null;
    idlePrompt = 'Your turn — press a language button and speak.';
    btnStop.disabled = true;
    btnExit.disabled = false;
    enableRecordButtons();

    if (selectedUserId) {
      userPicker.hidden = true;
      sessionSetup.hidden = false;
      statusText.textContent = 'Ready. Pick how you want to start.';
      phaseBadge.textContent = 'ready';
      loadingIndicator.hidden = !wsOpen();
    } else {
      userPicker.hidden = false;
      sessionSetup.hidden = true;
      statusText.textContent = 'Who is practicing today?';
      phaseBadge.textContent = 'select user';
      loadingIndicator.hidden = true;
    }

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

  // `direction` only means anything to a challenge: "into_native" is the
  // reading challenge, "into_target" the reverse one where the student produces
  // the language they're learning.
  function requestMode(mode, direction) {
    if (!wsOpen() || !backendReady) return;
    if (sessionFinished) return;
    const message = mode === 'challenge'
      ? { type: 'start_challenge', direction }
      : { type: 'start_talk' };
    ws.send(JSON.stringify(message));
  }

  function handleControlMessage(msg) {
    switch (msg.type) {
      case 'ready':
        backendReady = true;
        demoMode = !!msg.demo;
        applyLanguagePair(msg);
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
        translateLang = msg.language || null;
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
    function addLink(href, text, canView) {
      const wrap = document.createElement('div');
      const a = document.createElement('a');
      a.href = href;
      a.textContent = text;
      a.target = '_blank';
      a.rel = 'noopener';
      wrap.appendChild(a);
      if (canView) {
        const viewButton = document.createElement('button');
        viewButton.type = 'button';
        viewButton.className = 'artifact-view-btn';
        viewButton.textContent = 'View in app';
        viewButton.addEventListener('click', () => {
          showMarkdownViewer(href, text);
        });
        wrap.appendChild(viewButton);
      }
      completeLinks.appendChild(wrap);
    }
    const artifacts = [
      [msg.transcript_filename, 'Transcript', true],
      [msg.lesson_filename, 'Lesson (story + literal translation)', false],
      [msg.review_filename, 'Translation review', false],
      [msg.homework_filename, 'Homework', true],
    ];
    artifacts.forEach(([filename, label, canView]) => {
      if (msg.session_name && filename) {
        addLink(
          `/session/${msg.session_name}/${encodeURIComponent(filename)}`,
          label,
          canView,
        );
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
    // exchange (user line + AI answer) in the pressed language. The pressed
    // button's own disabled state doubles as the in-flight guard until the
    // reply has played — its own, because during a translation the other
    // button is disabled for the whole turn.
    const pressed = language === targetLang.code ? btnTarget : btnNative;
    if (!wsOpen() || pressed.disabled) return;
    btnNative.disabled = true;
    btnTarget.disabled = true;
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
    btnNative.disabled = true;
    btnTarget.disabled = true;
    btnStop.disabled = false;
    setAvatarState('listening');
    statusText.textContent = stage === 'translate'
      ? `Listening — your translation in ${labelFor(language)}, sentence by sentence. `
        + 'Press Stop when done.'
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

  document.querySelectorAll('.user-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      selectedUserId = btn.dataset.user;
      localStorage.setItem('lastUser', selectedUserId);
      userPicker.hidden = true;
      sessionSetup.hidden = false;
      connect();
    });
  });

  const lastUser = localStorage.getItem('lastUser');
  if (lastUser) {
    const lastBtn = document.querySelector(`.user-btn[data-user="${lastUser}"]`);
    if (lastBtn) lastBtn.classList.add('last-used');
  }

  btnTalk.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    requestMode('talk');
  });
  btnChallenge.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    requestMode('challenge', 'into_native');
  });
  btnChallengeReverse.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    requestMode('challenge', 'into_target');
  });
  btnNative.addEventListener('click', () => startRecording(nativeLang.code));
  btnTarget.addEventListener('click', () => startRecording(targetLang.code));
  btnStop.addEventListener('click', stopRecording);
  btnExit.addEventListener('click', () => {
    if (!wsOpen()) return;
    ws.send(JSON.stringify({ type: 'end_session' }));
    btnExit.disabled = true;
  });
  btnBack.addEventListener('click', () => {
    if (wsOpen()) {
      sessionFinished = true;
      backendReady = false;
      ws.close();
      ws = null;
    }
    resetToLanding();
    sessionFinished = false;
    if (selectedUserId) connect();
  });

  updateLandingState();
})();
