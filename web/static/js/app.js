(() => {
  const MODES = {
    story: { chat: true, label: 'Wikipedia story' },
    homework: { chat: true, label: 'Homework lesson' },
    flashcards: { chat: false, label: 'Flashcards' },
    fill_blanks: { chat: false, label: 'Fill in the blanks' },
  };

  const phaseBadge = document.getElementById('phase-badge');
  const avatar = document.getElementById('avatar');
  const statusText = document.getElementById('status-text');
  const storyPanel = document.getElementById('story-panel');
  const storyTitle = document.getElementById('story-title');
  const storyText = document.getElementById('story-text');
  const exercisePanel = document.getElementById('exercise-panel');
  const exerciseTitle = document.getElementById('exercise-title');
  const exerciseProgress = document.getElementById('exercise-progress');
  const cardMain = document.getElementById('card-main');
  const cardHint = document.getElementById('card-hint');
  const judgement = document.getElementById('judgement');
  const judgementText = document.getElementById('judgement-text');
  const btnNext = document.getElementById('btn-next');
  const chatPanel = document.getElementById('chat-panel');
  const chatLog = document.getElementById('chat-log');
  const controls = document.getElementById('controls');
  const inputSelect = document.getElementById('input-device');
  const outputSelect = document.getElementById('output-device');
  const btnEn = document.getElementById('btn-en');
  const btnEs = document.getElementById('btn-es');
  const btnStop = document.getElementById('btn-stop');
  const btnFinish = document.getElementById('btn-finish');
  const btnQuit = document.getElementById('btn-quit');
  const completePanel = document.getElementById('complete-panel');
  const completeLinks = document.getElementById('complete-links');
  const modeButtons = Array.from(document.querySelectorAll('.mode-btn'));

  let ws = null;
  let currentMode = null;
  let pendingMode = null;    // mode to start as soon as the current session closes
  let sessionActive = false;
  let sessionDone = false;
  let recording = false;
  let currentLanguage = null;
  let pendingBinaryTurn = null;

  function setAvatarState(state) {
    avatar.className = `avatar avatar--${state}`;
  }

  function isChatMode() {
    return currentMode !== null && MODES[currentMode].chat;
  }

  function setRecordEnabled(enabled) {
    btnEn.disabled = !enabled;
    btnEs.disabled = !enabled;
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

  inputSelect.addEventListener('change', () => {
    localStorage.setItem('inputDeviceId', inputSelect.value);
    AudioCapture.setInputDevice(inputSelect.value);
  });
  outputSelect.addEventListener('change', () => {
    localStorage.setItem('outputDeviceId', outputSelect.value);
    AudioPlayback.setOutputDevice(outputSelect.value);
  });

  function markActiveMode(mode) {
    modeButtons.forEach((b) => b.classList.toggle('active', b.dataset.mode === mode));
  }

  function resetSessionUI(mode) {
    chatLog.innerHTML = '';
    storyPanel.hidden = true;
    exercisePanel.hidden = true;
    completePanel.hidden = true;
    controls.hidden = true;
    judgement.hidden = true;
    btnNext.hidden = true;
    chatPanel.hidden = !MODES[mode].chat;
    // Exercise answers are always Spanish: hide the language choice.
    btnEn.hidden = !MODES[mode].chat;
    btnFinish.hidden = !MODES[mode].chat;
    btnEs.textContent = MODES[mode].chat ? '🎙 Español' : '🎙 Answer';
    exerciseTitle.textContent = MODES[mode].label;
    cardMain.textContent = '';
    cardHint.textContent = '';
    exerciseProgress.textContent = '';
    recording = false;
    btnStop.disabled = true;
    btnFinish.disabled = false;
    btnQuit.disabled = false;
    setRecordEnabled(true);
    sessionDone = false;
    pendingBinaryTurn = null;
  }

  function backToMenu(message) {
    currentMode = null;
    markActiveMode(null);
    controls.hidden = true;
    exercisePanel.hidden = true;
    phaseBadge.textContent = 'Pick a mode';
    statusText.textContent = message || 'Pick a mode above to start.';
    setAvatarState('idle');
  }

  function startMode(mode) {
    currentMode = mode;
    sessionActive = true;
    markActiveMode(mode);
    resetSessionUI(mode);
    phaseBadge.textContent = 'connecting';
    statusText.textContent = 'Starting…';
    setAvatarState('thinking');

    ws = new WebSocket(`ws://${location.host}/ws/session`);
    ws.binaryType = 'arraybuffer';

    ws.onopen = () => {
      ws.send(JSON.stringify({ type: 'start', mode }));
    };

    ws.onmessage = async (event) => {
      if (typeof event.data !== 'string') {
        await handleAudioFrame(event.data);
        return;
      }
      handleControlMessage(JSON.parse(event.data));
    };

    ws.onclose = () => {
      sessionActive = false;
      if (pendingMode) {
        const next = pendingMode;
        pendingMode = null;
        startMode(next);
        return;
      }
      if (!sessionDone) {
        backToMenu('Session ended — nothing was saved.');
      }
    };
  }

  async function handleAudioFrame(arrayBuffer) {
    const blob = new Blob([arrayBuffer], { type: 'audio/wav' });
    setAvatarState('speaking');
    statusText.textContent = 'Speaking…';
    await AudioPlayback.playBlob(blob);
    setAvatarState('idle');
    if (pendingBinaryTurn === 'story') {
      ws.send(JSON.stringify({ type: 'tts_playback_done' }));
    } else {
      statusText.textContent = 'Your turn — press Record.';
    }
    pendingBinaryTurn = null;
  }

  function handleControlMessage(msg) {
    switch (msg.type) {
      case 'mode_started':
        phaseBadge.textContent = MODES[msg.mode] ? MODES[msg.mode].label : msg.mode;
        break;
      case 'phase':
        phaseBadge.textContent = `${msg.phase} — ${msg.status}`;
        if (msg.phase === 'prepare' && msg.status === 'running') {
          statusText.textContent = 'Preparing your session…';
          setAvatarState('thinking');
        }
        if ((msg.phase === 'converse' || msg.phase === 'exercise') && msg.status === 'running') {
          controls.hidden = false;
          setAvatarState('idle');
          if (msg.phase === 'converse') {
            statusText.textContent = 'Your turn — press Record.';
          }
        }
        break;
      case 'story':
        if (msg.story) {
          storyPanel.hidden = false;
          storyTitle.textContent = msg.story_title;
          storyText.textContent = msg.story;
        }
        break;
      case 'lesson':
        storyPanel.hidden = false;
        storyTitle.textContent = msg.title;
        storyText.textContent = msg.body;
        break;
      case 'card':
        showCard(msg);
        break;
      case 'judgement':
        showJudgement(msg);
        break;
      case 'exercise_summary':
        statusText.textContent = `Done! Score: ${msg.correct}/${msg.total}.`;
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
        setRecordEnabled(true);
        break;
      case 'error':
        statusText.textContent = `Error: ${msg.message}`;
        setAvatarState('idle');
        setRecordEnabled(true);
        break;
      case 'aborted':
        // Server confirmed the discard; the socket closes right after and
        // ws.onclose decides whether to start the pending mode or show the menu.
        break;
      case 'done':
        finishSession(msg);
        break;
    }
  }

  function showCard(msg) {
    exercisePanel.hidden = false;
    exerciseProgress.textContent = `${msg.index + 1} / ${msg.total}`;
    if (msg.prompt !== undefined) {
      cardMain.textContent = msg.prompt;
      cardHint.textContent = 'Say it in Spanish';
    } else {
      cardMain.textContent = msg.sentence;
      cardHint.textContent = msg.hint || '';
    }
    judgement.hidden = true;
    btnNext.hidden = true;
    setRecordEnabled(true);
    setAvatarState('idle');
    statusText.textContent = 'Press Record and say your answer.';
  }

  function showJudgement(msg) {
    judgement.hidden = false;
    judgement.className = `judgement ${msg.correct ? 'judgement--correct' : 'judgement--incorrect'}`;
    const verdict = msg.correct ? '✓ Correct!' : '✗ Not quite.';
    judgementText.textContent =
      `${verdict} Expected: “${msg.expected}” — you said: “${msg.heard}”. ${msg.feedback || ''}`;
    setAvatarState('idle');
    if (msg.index + 1 < msg.total) {
      btnNext.hidden = false;
      statusText.textContent = 'Press Next card to continue.';
    } else {
      statusText.textContent = 'Wrapping up…';
    }
  }

  function finishSession(msg) {
    sessionDone = true;
    controls.hidden = true;
    btnNext.hidden = true;
    completePanel.hidden = false;
    setAvatarState('idle');
    statusText.textContent = '¡Hasta luego!';
    phaseBadge.textContent = 'done';
    markActiveMode(null);
    currentMode = null;

    completeLinks.innerHTML = '';
    const add = (text) => {
      const p = document.createElement('p');
      p.textContent = text;
      completeLinks.appendChild(p);
    };
    if (msg.transcript_path) add('Transcript saved to your session folder.');
    if (msg.homework_path) add('Homework saved to your session folder.');
    if (msg.results_path) add('Results saved to your session folder.');
    if (!msg.transcript_path && !msg.results_path) add('Nothing was saved.');
    add('Pick a mode above to start another session.');
  }

  async function startRecording(language) {
    if (recording || !sessionActive) return;
    recording = true;
    currentLanguage = language;
    setRecordEnabled(false);
    btnStop.disabled = false;
    setAvatarState('listening');
    statusText.textContent = `Recording (${language})… press Stop when done.`;
    try {
      await AudioCapture.start();
    } catch (e) {
      statusText.textContent = `Could not start recording: ${e.message}`;
      recording = false;
      setRecordEnabled(true);
      btnStop.disabled = true;
      setAvatarState('idle');
    }
  }

  async function stopRecording() {
    if (!recording) return;
    recording = false;
    btnStop.disabled = true;
    // In exercise modes, stay disabled until the judgement/next card arrives
    // so a stray recording can't desync the card protocol.
    setRecordEnabled(isChatMode());
    setAvatarState('thinking');
    statusText.textContent = 'Transcribing…';

    const wavBlob = AudioCapture.stop();
    const buffer = await wavBlob.arrayBuffer();
    ws.send(JSON.stringify({ type: 'user_audio', language: currentLanguage }));
    ws.send(buffer);
  }

  modeButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
      const mode = btn.dataset.mode;
      if (sessionActive) {
        if (mode === currentMode) return;
        // Abort the running session (discarding it) and switch directly.
        pendingMode = mode;
        markActiveMode(mode);
        statusText.textContent = 'Switching mode — discarding current session…';
        ws.send(JSON.stringify({ type: 'abort_session' }));
      } else {
        startMode(mode);
      }
    });
  });

  btnEn.addEventListener('click', () => startRecording('en'));
  btnEs.addEventListener('click', () => startRecording('es'));
  btnStop.addEventListener('click', stopRecording);
  btnNext.addEventListener('click', () => {
    btnNext.hidden = true;
    ws.send(JSON.stringify({ type: 'next_card' }));
  });
  btnFinish.addEventListener('click', () => {
    if (!sessionActive) return;
    btnFinish.disabled = true;
    statusText.textContent = 'Finishing session…';
    ws.send(JSON.stringify({ type: 'end_session' }));
  });
  btnQuit.addEventListener('click', () => {
    if (!sessionActive) return;
    pendingMode = null;
    btnQuit.disabled = true;
    statusText.textContent = 'Quitting — nothing will be saved…';
    ws.send(JSON.stringify({ type: 'abort_session' }));
  });

  populateDevices();
})();
