# Mobile-enabling AI_Voice_Chat — Stage 1 plan + commercial road ahead

> Status: **strategy document, not yet implemented.** Written to capture the findings of a
> full mobile-readiness audit of the browser UI, and to record the decisions taken so the
> reasoning survives. Line references are against the repo as of this document's commit.

## Context

The app today is a **single-user, Windows-only, localhost desktop tool**. It works because every
piece assumes one machine: Ollama on `localhost:11434`, Whisper `large-v3` loaded once into the
server process, Kokoro pipelines built at import, and all state in `<repo>/recordings/`. The browser
UI already exists (`web/`), which is a big head start — but it has **never run against a phone**, and
in its current form it does not work on one at all.

The goal now is narrow: **use this from my own phone, over the internet.** The wider purpose is to
learn what a commercial hosted version would demand, so §6 sketches that road without committing to it.

Decisions taken:
- **Scope:** Stage 1 only — get it working on my phone. No accounts, no billing, no multi-tenancy.
- **Inference:** *Hybrid* — LLM moves to a managed API; Kokoro TTS and Whisper stay self-hosted on the
  existing RTX 3060. (§6 shows why this is also the right commercial call: managed TTS would cost
  ~$0.60–1.70 per session, self-hosted Kokoro costs ~$0.)
- **Delivery:** Responsive web + PWA. One codebase, no app store.

---

## The four hard blockers

These are not polish items. Today, on an iPhone or Android phone, the app is **non-functional** —
and three of the four fail *silently*, which is why they haven't surfaced.

| # | Blocker | Why it fails | Symptom on a phone |
|---|---|---|---|
| **B1** | Not a secure context | `CLAUDE.md:16` binds `127.0.0.1`. A phone reaches the PC over LAN HTTP, where `navigator.mediaDevices` is `undefined` | "Microphone permission is required." Nothing works. |
| **B2** | `ws://` hardcoded — `web/static/js/app.js:156` | Mixed-content block the instant the page is HTTPS (which B1 forces) | Socket never opens |
| **B3** | `AudioContext` built *after* an `await` — `web/static/js/audio-capture.js:27-28` | The click's user activation is consumed by `getUserMedia`; iOS creates the context `suspended`. There is **no `resume()` anywhere in the codebase** | Recording appears to work; every turn returns "No speech detected" |
| **B4** | TTS autoplay — `web/static/js/app.js:213-242` | `el.play()` is driven by a `tts_audio` WebSocket message, i.e. no gesture. iOS unlock is **per-element**, and every reply creates a fresh `<audio>` (`app.js:87-91`) | Tutor never speaks. Worse: in story mode the catch immediately sends `tts_playback_done` (`app.js:217`), so the server advances **past the story before a word is heard** |

---

## Stage 1a — make it reachable and make audio work

### A. Transport and access

1. **Cloudflare Tunnel** from the Windows box. Free, gives a real HTTPS hostname, no port forwarding,
   no router changes, works over cellular. This alone resolves B1 (secure context restored →
   `getUserMedia`, `AudioWorklet`, `enumerateDevices` all come back).
2. **Cloudflare Access** in front of the origin — email OTP or Google login. Zero auth code to write,
   and it closes a real hole: `GET /session/{session_name}/{filename}` (`web/app_factory.py:99-106`)
   blocks path traversal but has **no authorization at all**, and `session_name` is a guessable
   timestamp (`session_core.py:95-98`). Today anyone reaching the origin can enumerate and download
   every recording, transcript and homework file.
3. Fix the WS scheme — `app.js:156`: `location.protocol === 'https:' ? 'wss:' : 'ws:'`.
4. Bind `0.0.0.0` in the run command; update `web/server.py`'s docstring and `CLAUDE.md`.

### B. Audio path (the part that actually decides whether this works)

5. **Fix B3** in `web/static/js/audio-capture.js:18-37`: construct the `AudioContext` *synchronously*
   at the top of `start()`, before the `getUserMedia` await, then `await audioContext.resume()`.
   Add a `webkitAudioContext` alias and a feature check before touching `audioContext.audioWorklet`
   (currently a bare `TypeError` surfaces as `Could not start recording: undefined is not an object`).
6. **Fix B4** with a *single moved player element*. Keep the existing "one always-controllable source
   in the bubble" design, but stop creating a new element per reply:
   - Reuse the `<audio id="player">` that already exists at `index.html:73` (today it is only a
     `setSinkId` feature probe — `audio-playback.js:11`).
   - Unlock it once inside the first mode-button tap (`app.js:398-407`): play a silent data-URI WAV,
     then pause.
   - On each new assistant bubble, `appendChild` that same element into the bubble. Moving a node
     does not reset its unlock state, so `play()` succeeds. Tapping an older bubble moves the player
     back into it and plays — that's a user gesture, so it needs no unlock either.
   - This also deletes the mutual-pause bookkeeping at `app.js:96-101`: with one element there is
     only ever one voice.
7. **Downsample to 16 kHz client-side** before WAV encoding (`audio-capture.js:59-91`), via
   `OfflineAudioContext`. The server converts to 16 kHz anyway (`session_core.py:78-82`), so this is
   free quality *and* a **3× smaller upload**: a 15-second turn drops from ~1.4 MB to ~480 KB. The
   server's `np.interp` decimation has no anti-alias filter, so doing it properly in the browser also
   improves transcription. Add a max-duration guard (~120 s) — `chunks` currently grows unbounded
   (~375 `postMessage`/sec, ~40 MB of live copies for a 60 s clip; a real tab-crash risk on an older iPhone).
8. `deviceId: { exact: … }` (`audio-capture.js:24`) → `ideal`, or retry without the constraint on
   `OverconstrainedError`. iOS rotates salted device IDs, so a stale `localStorage` value
   (`audio-capture.js:11`) hard-fails capture with no fallback.
9. Move `populateDevices()` off the `ready` message (`app.js:270`) to the first mode-button tap —
   it currently prompts for the mic on page load, before the user has expressed any intent.
10. Hide the Speaker picker entirely when `setSinkId` is absent. It is **unsupported on both iOS Safari
    and Android Chrome**, and the explanation is a `title` tooltip (`app.js:148`) that touch users
    cannot see. Also drop the dead `.replay-btn` CSS (`app.css:128-139`).
11. **Screen Wake Lock** (`navigator.wakeLock.request('screen')`) while a session is live. Without it
    the screen sleeps, Safari suspends the tab, the WebSocket dies, and the session resets.
12. Add a WS keepalive ping (~20 s) and an `onerror` handler. Carrier NATs drop idle sockets in
    30–60 s, and there is a long idle window while Whisper + LLM + Kokoro run (`session.py:203-240`).

### C. Responsive CSS — `web/static/css/app.css`

The stylesheet has a correct viewport tag (`index.html:5`) and **zero `@media` queries**. It is
desktop-fixed and merely happens to be narrow.

13. `.device-pickers` (`:151-168`) — add `flex-wrap: wrap`, change `min-width: 180px` to
    `min(180px, 100%)`. Two 180 px selects + 1.5rem gap = 384 px minimum vs ~327 px of content width
    on a 375 px iPhone: **guaranteed horizontal page scroll today.**
14. Raise select `font-size` to ≥16 px (`:155` is `0.85rem`). Below 16 px iOS auto-zooms on focus and
    never zooms back out.
15. `#chat-log` (`:93-99`) — replace `max-height: 360px` with `flex: 1; min-height: 0` inside a
    `100dvh` body, so the log fills the actual screen.
16. Add `.msg-audio { width: 100%; display: block }` — the class is assigned at `app.js:90` and
    **never styled**; a native player's ~300 px intrinsic width overflows its `max-width: 80%` bubble.
17. `button { min-height: 44px }` — current padding gives ~37 px (`:199-207`), under the iOS target,
    and `#btn-stop` is one you must hit reliably mid-sentence.
18. `viewport-fit=cover` on the meta tag + `env(safe-area-inset-bottom)` padding on `#controls`, made
    sticky at the bottom, so the record bar clears the iPhone home indicator.
19. One `@media (max-width: 480px)` block: shrink the 96 px avatar (`:65-69`), tighten `main` padding.

---

## Stage 1b — session survival (do this second; it's the expensive one)

Reconnection exists (`app.js:175-189`) but is **destructive**: the server builds a fresh
`SessionOrchestrator` per socket (`app_factory.py:114`), so `ready` triggers `resetToLanding()` which
does `chatLog.innerHTML = ''` (`app.js:199`). Lock your phone for 30 seconds and you come back to a
wiped conversation, while the server orphans the old session mid-flight — its
`finally: await self._analyze_and_persist()` (`session.py:88-89`) fires against a dead socket.

Wake Lock + keepalive (items 11–12) mitigate this. Real resumption fixes it:

20. Server-side session registry in `web/app_factory.py`: `dict[token, SessionOrchestrator]` with a TTL
    (~10 min). Issue the token with the existing `mode` message.
21. `web/session.py` — split `run()` so disconnect *detaches* the socket instead of running the
    analyze/persist phase; only TTL expiry or an explicit `end_session` finalizes. Guard `_send`
    (`:62-63`) against a detached socket.
22. Client — store the token in `sessionStorage`, send `{type: 'resume', token}` on reconnect, and
    make `resetToLanding()` conditional on it being a genuinely new session.

Also worth fixing while in here: `session.py:194` does an unbounded `receive_bytes()` after a
`user_audio` header. If the socket dies between the JSON header (`app.js:394`) and the binary frame
(`app.js:395`) — very plausible on cellular — the orchestrator blocks forever.

---

## Stage 1c — hybrid inference

23. **Add a config layer.** There is currently **zero** env-var handling in application code — every
    host, model and path is a module-level literal. Create `config.py` reading `.env` via
    `python-dotenv` (already in `uv.lock` transitively): `LLM_PROVIDER`, `LLM_MODEL`, API key,
    `OLLAMA_URL`, `WHISPER_MODEL`, `RECORDINGS_ROOT`, `WIKI_USER_AGENT`.
24. **Move the LLM to a managed API.** `curriculum.chat_completion` (`curriculum.py:39-56`) is the
    *single* LLM entry point for the whole app — story selection, story generation, every conversation
    turn, weakness analysis, homework. Swap its body for a provider dispatch and keep Ollama as a
    fallback. The one real adapter to write is `chat_completion_json` (`:59-68`): it relies on Ollama's
    `format` schema parameter, which managed providers express as JSON mode or tool use instead. The
    retry-once-on-bad-JSON wrapper stays as-is.
    - Why this and not the other two: `llama3.1:8b` + `whisper large-v3` together are a tight fit on a
      12 GB 3060, and the 8B model is the weakest link in tutoring quality. Moving it frees VRAM,
      cuts latency, and improves the product.
25. **Keep Kokoro local, but make it headless-safe.** `kokoro/tts.py:7` imports `sounddevice`
    unconditionally and `:47-57` calls `sd.query_devices()` **at import time** — both only matter to
    the CLI `play=True` path (`:127`). Make them lazy so the server never needs PortAudio.
    Also build the two `KPipeline`s (`:41-44`) lazily instead of at import.
26. **Serve TTS as Opus or MP3, not 24 kHz WAV.** Kokoro writes ~48 KB/s (`tts.py:23`), so a narrated
    Wikipedia story is a **2–4 MB uncompressed GET** (`session.py:161-169`). Compression is roughly a
    **10× downlink saving** — the single biggest mobile-data win available.
27. Scope the `Cache-Control: no-store` middleware (`app_factory.py:83-91`) to `/static` and HTML only.
    It currently applies to session WAVs (re-downloaded on every replay tap) *and* to the AudioWorklet
    module, which is therefore **re-fetched over the network before every single recording**.
28. Drop `pyaudio` from the server dependency path — the web path never imports it. Replace the personal
    email hardcoded in the Wikipedia `User-Agent` (`curriculum.py:20`) with a config value.

**Whisper stays local** on the 3060 for Stage 1. Note the ceiling for later: one process-global model
behind a global `asyncio.Lock` (`app_factory.py:66`, `session.py:203`) serializes transcription across
all connections. Irrelevant for one user; a hard wall for anything commercial.

---

## Stage 1d — PWA shell

29. `web/static/manifest.webmanifest` (`display: standalone`, portrait, theme colour matching the dark
    `:root` palette at `app.css:1-12`) + apple-touch-icon, linked from `index.html`.
30. A minimal service worker caching only the static shell. **Do not** cache `/session/*` or the
    WebSocket path.

---

## Verification

Every one of B1–B4 fails silently in a desktop browser, so **desktop testing proves nothing here.**
Test on a real phone.

1. `uv run python -m uvicorn web.server:app --host 0.0.0.0 --port 8000`, `cloudflared tunnel --url http://localhost:8000`.
2. Open the HTTPS URL on an **iPhone (Safari)** and an **Android (Chrome)**. Confirm the Access login gate.
3. Landing screen: no horizontal scroll at 375 px and at 320 px; no zoom-on-focus when tapping a select.
4. Tap "Push-to-talk", grant the mic. Speak 5 s, tap Stop.
   - **The B3 check:** the transcript bubble must contain real text. "No speech detected" means the
     `AudioContext` is still suspended.
   - Confirm the uploaded WAV in `recordings/<session>/` has a **16000 Hz** header (item 7).
5. **The B4 check:** the tutor's reply must play *by itself*, with no tap. Confirm the audio control
   is inside the bubble and full-width.
6. Story mode: confirm the story plays fully and that `tts_playback_done` is sent on real `ended`, not
   from the autoplay-rejected catch path.
7. **Backgrounding:** lock the phone mid-session for 60 s, unlock. With Stage 1a only, expect the reset —
   confirm Wake Lock keeps the screen alive during an active session. After Stage 1b, the chat log must
   survive and the session must continue.
8. Switch to cellular; confirm a 15 s turn uploads in reasonable time and the story downloads compressed.
9. Add to Home Screen; confirm it launches standalone.
10. `recordings/<session>/` contains the usual `transcript.md`, `analysis.json`, `homework.md` — and
    `student_profile.json` is updated with the same shape as before (`curriculum.py:442-449`).

There are **no tests in the repo today**. Worth adding alongside this work: `load_audio_16k`
round-trip, the `chat_completion` provider dispatch, and a WebSocket protocol test using FastAPI's
`TestClient`.

---

## §6 — What commercial hosting would additionally require

Not Stage 1 work. This is the map, so the choices above don't paint us into a corner.

### The five architectural facts that block multi-tenancy

| Today | Commercial requirement |
|---|---|
| One global `recordings/student_profile.json` (`curriculum.py:25`), non-atomic `write_text` (`:469-471`), no locking — concurrent sessions race and lose data | Per-user row in a real DB (Postgres). The JSON shape maps cleanly to a `jsonb` column. |
| All state on local disk under `<repo>/recordings/` (`session_core.py:17`) | Object storage (S3/R2) with signed URLs |
| Session ID = server wall-clock timestamp (`session_core.py:95-98`) | Opaque UUIDs, scoped to a user |
| No auth anywhere; `/session/{name}/{file}` serves any session to anyone | Real accounts + per-object authorization |
| One Whisper model behind one global lock (`session.py:203`) | Stateless workers, or managed STT |

Also: Windows-only by policy (`CLAUDE.md:36`), `.venv/Lib/site-packages` path hacks assume the Windows
venv layout (`app_factory.py:21`), and there is **no Dockerfile and no CI that builds or tests anything**
— the two workflows in `.github/workflows/` are Claude bots. Containerizing on Linux is a prerequisite.

### Unit economics — why hybrid is also the commercial answer

Per ~15-minute session (~6 min of student speech, ~15 turns, ~5,700 characters of synthesized speech):

| Component | Managed API | Self-hosted |
|---|---|---|
| STT | ~$0.02 (Deepgram $0.004/min, ElevenLabs Scribe $0.004/min) | ~$0 on the 3060 |
| LLM | ~$0.07 | ~$0 (but weakest quality, and VRAM-contended) |
| **TTS** | **~$0.60–1.70** (ElevenLabs $0.10–0.30 per 1k chars) | **~$0** — Kokoro is 82M params, 300 MB, CPU-capable |
| **Total** | **~$0.70–1.80** | ~$0 marginal + GPU rent |

TTS is the whole story. A €10/month subscription dies instantly on managed TTS at daily use; with
Kokoro self-hosted it is comfortable. **Keeping Kokoro is the decision that makes the business model
work**, which is exactly the hybrid split chosen for Stage 1. Serverless GPU (scale-to-zero) beats a
dedicated GPU until roughly 25–50% utilization, so the migration path is: PC → serverless GPU for
Whisper+Kokoro → dedicated GPU only once genuinely busy.

Figures are July 2026 list prices, gathered while writing this document; re-check before relying on them.

### Legal and licensing

- **Voice recordings of a learner are personal data under GDPR** (and arguably biometric-adjacent).
  A commercial version needs explicit consent, a retention/deletion policy, an export path, and a DPA
  with every processor. The app currently keeps every WAV forever.
- **Wikipedia content is CC BY-SA.** The generated stories are derivative works — attribution is required.
  `curriculum.py:19-22` also ships a personal email in the API `User-Agent`.
- Kokoro is Apache-2.0 and Whisper MIT — both fine commercially. Llama 3.1's 700M-MAU clause is
  irrelevant at this scale, and moot once the LLM moves to a managed API.

### Product notes

The five-phase arc (story → conversation → analysis → homework → persistent profile) is genuinely
differentiated — most competitors stop at free conversation. The daily Wikipedia hook gives a real
reason to return. The existing demo mode (`web/demo.py`, `web/demo_manuscript.json`) already runs the
full UI with **no models loaded** — that is a ready-made zero-cost marketing demo and free-trial
surface. Worth noting the app is hardcoded to `en`/`es` in four places (`session_core.py:53-56`,
`kokoro/tts.py:28-31`, `index.html:60-61`); more language pairs is the obvious growth axis and would
want a language registry rather than more literals.

### Suggested sequence after Stage 1

Containerize on Linux → Postgres for the profile → real accounts → object storage → Stripe →
serverless GPU for Whisper/Kokoro. Nothing in Stage 1 conflicts with any of it; the config layer
(item 23) and the provider dispatch (item 24) are the two seams that make all of it cheaper later.
