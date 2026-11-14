# Artemis stack decisions

Goal for every choice: a blind developer completes the demo on Windows with a screen reader,
keyboard and voice, with no sighted help, in one weekend.

**Decided by the team:** Windows-first native desktop app on a headless core. Desktop is Tauri 2 +
React + TypeScript + Tailwind. Core is Python + FastAPI on a local API. No visual IDE.
Everything below works inside that frame. Items marked **Proposed** become decided once the owner
agrees on the board; record any change here.

## Summary

| # | Decision | Pick | Status | Owner |
|---|---|---|---|---|
| 1 | Platform | Tauri 2 desktop + Python FastAPI headless core, Windows only | Decided | all |
| 2 | Screen reader for testing | NVDA (free) on Windows 11; Narrator smoke test | Proposed | all |
| 3 | How Tauri runs the core | Separate process on `127.0.0.1` during the hackathon; Tauri sidecar bundling is a stretch | Proposed | Avery + Jun |
| 4 | Contracts | Pydantic models are the source; FastAPI's OpenAPI is to generate the TS types (not set up yet) | Proposed | Avery |
| 5 | Spoken output | Core `announce()`: speak through NVDA's controller API when NVDA runs, Windows SAPI otherwise; never both | Proposed (refines team's SAPI pick) | Yaovi |
| 6 | Audio capture + STT | Global push-to-talk in Tauri; the core records the mic and runs faster-whisper with a vocabulary prompt | Proposed | Yaovi |
| 7 | Agent loop | Anthropic Python SDK, our own tools, approval pause owned by the orchestrator | Proposed | David |
| 8 | Model | `claude-opus-5-5`, effort `medium` | Proposed | David |
| 9 | Patch format | Search/replace edits → core-built unified diff, hashed for approval | Proposed | David + Avery |
| 10 | Runner isolation | `run_program` with a fixed argv per file type, no shell; approval + scan first; git as the undo | Proposed | Avery |
| 11 | Echo Check | Playwright for Python (Chromium) + `aria_snapshot()` + axe-core; after the MVP, web projects only | Proposed | Avery + Jun |
| 12 | Event transport | `POST` commands + Server-Sent Events with sequence ids | Proposed | Avery |
| 13 | UI components | Semantic HTML first; React Aria for dialogs/listboxes; Tailwind for styling only | Proposed | Jun |
| 14 | Security scanning | Bandit (pure Python), run before every write and run approval. Semgrep: not implemented; revisit after the MVP | Proposed | David |
| 15 | Windows UI Automation | Not needed for the MVP (WebView2 already exposes UIA to NVDA); revisit for inspecting native apps | Deferred | Jun |

The confirmed MVP is "create a Python program that prints Hello World, run it, hear the result".
The architecture, risks and build order are in [`architecture.md`](architecture.md); the API is in
[`contracts.md`](contracts.md).

---

## 1. Platform (decided)

Tauri 2 on Windows renders in WebView2, which is Chromium. That's good news for accessibility:
NVDA's support for WebView2 content is close to Chrome's, so ARIA live regions, roles and focus
management behave the way the web team expects. On top of a browser tab, Tauri adds what a
voice-first tool needs: a global shortcut that works when the window isn't focused, a tray
presence, and a native window.

**Main risk: toolchain setup.** Tauri needs Rust plus the MSVC Build Tools; Windows 11 ships with
WebView2. Time-box at kickoff: Jun gets `npm create tauri-app` building on the demo laptop within an
hour. **Mitigation:** keep every Tauri-only call (global shortcut, window, sidecar) behind a small
`client/src/platform.ts` adapter. Then `client/` also runs as a plain Vite page in Chrome against
the same core, so teammates without Rust can still work on the UI, and the demo has a fallback if
the native build breaks.

## 2. Screen reader

Test with NVDA, the most-used screen reader among blind developers. It's free and installs in
minutes on every laptop. Do a quick pass with Narrator because it ships with Windows and judges may
try it. JAWS is out of scope.

## 3. How Tauri runs the Python core

**Tauri sidecar (a PyInstaller exe launched by Tauri).** For: one double-click to start. Against:
PyInstaller builds of faster-whisper and Playwright are large and fragile, and every Python change
means a rebuild.
**Separate process (pick for the weekend).** `infra/start.ps1` starts `uvicorn` on `127.0.0.1` with
a random token, then launches the app with the URL and token. Tauri only talks to the loopback
address, and every request carries the token, so other local processes or web pages can't drive
the agent. Sidecar bundling is a stretch for after the demo works.

## 4. Contracts

**Hand-written TS types and Pydantic models.** Against: they drift by Saturday night.
**Pydantic as the single source (pick).** Every message (transcript submission, tool request,
approval/rejection, task event) is a Pydantic model in `server/artemis/contracts/`. FastAPI
publishes OpenAPI, and `openapi-typescript` generates `client/src/generated/api.ts`. The same
models validate tool arguments in the policy gate. Generation is not set up yet: the client keeps
hand-written types in `client/src/contracts.ts` (there is no `client/src/generated/` so far). Next to
the models sit six sample payloads as JSON fixtures (`contracts/samples/`: typed request, write
approval, rejection, failed run, successful run, security block; contracts.md §4). There is no Echo
Check sample yet.
Everyone stubs against those samples first. David accepts contract changes until Avery is on site; then Avery owns them.

## 5. Spoken output: SAPI, live regions, or NVDA itself?

**Windows SAPI only (team's proposal).** For: always available, and we control it. Against: blind
developers run NVDA fast, with their own voice. A second voice talking over NVDA is the most common
complaint about "accessible" tools, and stopping one voice doesn't stop the other.
**ARIA live regions in the app.** For: NVDA speaks in the user's voice. Against: NVDA only reads
live regions from the *foreground* window. With a global push-to-talk key the user may be in
another app when the result arrives, and would hear nothing.
**Pick: one `announce(text, priority)` in the core, two backends.** If NVDA is running, speak
through NVDA's Controller Client (`nvdaControllerClient.dll`: `speakText`, `cancelSpeech`, and
`brailleMessage` for braille displays). That uses the user's voice and rate, and works whatever app
is focused. If NVDA isn't running, use Windows SAPI (`win32com` `SAPI.SpVoice`, interruptible).
Never both. The same text always goes to the app's `role="log"` region, so it can be reviewed and
replayed. `high` priority (approvals, failures) interrupts current speech, and failures always say
the exit code and the last line of the error output (the exception) verbatim before any
explanation. Earcons are Yaovi's and don't collide with speech; built so far: listening on and off.
The "still working" heartbeat is a low-priority spoken line from the core, not an earcon.

### 5a. ElevenLabs, Artemis's own voice (Yaovi; default when a key is set; issue #134)

**Why.** SAPI voices sound robotic, and Artemis is meant to sound like a voice you work with, not a
form read aloud. ElevenLabs streams a natural voice fast enough to start speaking in about a second
with its flash model. The voice, speed and engine are the user's choice.

**When it is used.** ElevenLabs is the first choice for Artemis's own lines, **even while NVDA
runs** (decided by Yaovi and David, #134). NVDA stays the screen reader: it keeps reading focus,
navigation and the user's own text, and Artemis's lines still go to the braille display through
NVDA's `brailleMessage`. NVDA is not asked to speak them, so no line is heard twice, and Artemis never
cancels NVDA's own speech. This revises "never both" in §5: one voice per line, but Artemis's voice
and NVDA's reading can overlap, and Artemis can't tell when NVDA is talking.
- No key, no voice id, or `ARTEMIS_VOICE_ENGINE=nvda` or `sapi`: today's order, NVDA when it runs,
  else SAPI, else silence. So a machine without a key behaves exactly as before, including the
  rehearsed demo.
- `screen_reader` in `voice.status` stays "nvda" only when Artemis speaks through NVDA, so "faster"
  reaches ElevenLabs instead of answering with the NVDA rate line.

**How it behaves.**
- Audio streams as raw PCM and starts playing on the first chunk. The download and the playback run
  on a worker thread, so the core's event loop never waits on the network or the audio device.
- A `high` line stops the current audio at once, and push-to-talk stops it before the listening
  earcon, as it does for SAPI.
- If a line can't start within `ARTEMIS_ELEVEN_TIMEOUT_S` (no key, a failed request, a slow first
  chunk), that same line is spoken by NVDA when it runs, else SAPI, and one warning is logged. After
  3 failures in a row, that fallback is used for the rest of the session. Each of the first 3
  failing lines can wait up to the timeout (1.5 s by default) before the fallback speaks it. The
  notice "My natural voice isn't reachable, so I'm using the Windows voice." is handed to the core
  through a hook; until the core wires it to the event bus, it is logged only.
- "Faster" and "slower" change the ElevenLabs speed by 0.1 within 0.7 to 1.2, for the session.
- A few short fixed lines (the ready lines, "Canceled.", the rate lines, "Still working.") are kept
  as audio in memory, per voice and speed, so they play at once. Transcripts, code, file contents and
  the agent's answers are never cached.

**Privacy.** With ElevenLabs on, the text Artemis speaks is sent to ElevenLabs to be turned into
audio. That includes file names, error lines, the agent's answers and the read-back of what you
said ("Heard: …"), so the text of your request leaves the computer even though the audio does not.
Speech recognition stays local: your recorded voice never leaves the computer, with or without
ElevenLabs. Every line is run through `redact()` just before it is sent, so secrets in answers,
transcripts and error lines are replaced with `[redacted]` first. The API key is read only from
the `ELEVENLABS_API_KEY` environment variable (Artemis does not read a `.env` file), sent only in
the request header, and never logged, spoken, put in an event or committed (`.env.example` holds
an empty placeholder).

**Settings.**

| Variable | Default | Meaning |
|---|---|---|
| `ARTEMIS_VOICE_ENGINE` | `elevenlabs` | Artemis's voice: `elevenlabs`, `nvda` or `sapi` (the last two keep NVDA first) |
| `ELEVENLABS_API_KEY` | unset | The API key. Without it, NVDA or SAPI is used |
| `ARTEMIS_ELEVEN_VOICE_ID` | `<PASTE_VOICE_ID>` | The voice. Without a real id, NVDA or SAPI is used |
| `ARTEMIS_ELEVEN_MODEL` | `eleven_flash_v2_5` | The model |
| `ARTEMIS_ELEVEN_SPEED` | `1.0` | Starting speed, clamped to 0.7..1.2 |
| `ARTEMIS_ELEVEN_STABILITY` | `0.5` | Voice stability, 0..1 |
| `ARTEMIS_ELEVEN_TIMEOUT_S` | `1.5` | Seconds to wait for the first audio before SAPI says the line |

**Follow-ups.** A spoken "change voice" command (new conversation logic, David's controller), and the
core wiring for the fallback notice (Avery).

## 6. Audio capture and speech-to-text

**Where to record.** Recording in the WebView means `getUserMedia` and WebView2's permission
prompt, and it only works while the window is focused. **Pick:** Tauri registers the global
push-to-talk shortcut and tells the core "start" and "stop". The core records with `sounddevice`
and transcribes locally with **faster-whisper** on CPU (`small.en` by default, about 1 to 2 s per
sentence; `ARTEMIS_WHISPER_MODEL=medium.en` is more accurate and slower; beam 1 unless
`ARTEMIS_WHISPER_BEAM` is set). Push-to-talk also keeps the mic from transcribing NVDA's own speech.

**Accuracy on code words.** Pass Whisper an `initial_prompt` that always starts with the wake word
and demo words ("Hey Artemis, Artemis, Python, main.py"), then the open project's file names and
symbols (`LoginForm`, `aria-label`, `index.html`). `ARTEMIS_WHISPER_PROMPT=natural` swaps the word
list for a spoken-style lead (opt-in). The transcript is read back ("Heard: …") and
stays in an editable field, so the user can correct it and resend. Writes and runs still need
approval, so a misheard request can't change anything on its own. A hosted STT can sit
behind the same `voice/` interface as a fallback.

**Shortcut choice.** Pick something that doesn't collide with NVDA (Insert or Caps Lock modifiers)
or Windows (Win+H is dictation). We started with Ctrl+Alt+Space, but another app already held it
on both Windows test laptops (RegisterHotKey error 1409), so the default is now Ctrl+Shift+Space (#54).

## 7. Agent loop

**Claude Agent SDK (Claude Code as a library).** Against: its built-in Bash/Edit tools act on the
filesystem directly. The handoff requires every action to pass our policy gate, with approval
bound to the exact patch.
**Our own tools on the Anthropic Python SDK (pick).** The tools are the ones in
[`contracts.md`](contracts.md) §3.2 (`create_project`, `open_project`, `list_files`, `read_file`,
`write_files`, `run_program`, `edit_file`, `inspect_page`, `scan_project`), with `strict` schemas. A
question to the user is the agent's final answer, not a tool. Every
call goes through the policy gate, which pauses the call until the user approves any write or run.
So "the agent can't write or run without approval" is a property of the design, not a prompt
instruction. Use the SDK's tool runner if its per-turn hooks fit the approval pause;
otherwise a manual loop where the orchestrator owns the pause across user turns.

## 8. Model

`claude-opus-5-5`, effort `medium` set explicitly (`ARTEMIS_MODEL`, `ARTEMIS_EFFORT`). Thinking is
always on for this model. The loop does not stream: progress is announced around each tool call. Handle `stop_reason: "refusal"` and enable the server-side
`fallbacks: "default"` option. If voice round-trip latency hurts the demo, measure lower effort
before switching models. The API key comes from the environment: `ANTHROPIC_API_KEY`, set in the
launching window or typed into the launcher's hidden prompt (optionally saved in Windows Credential
Manager). It never appears in prompts, logs or speech, and is never written to a file.

## 9. Patch format and approval

Whole-file rewrites can't be reviewed by ear. Model-written unified diffs are malformed often
enough to break a live demo. **Pick:** the agent sends `edit_file {path, edits: [{old, new}]}`. The
core checks that each `old` matches exactly once and builds the real unified diff. Approval binds to
a SHA-256 over the project slug and, per file, the path, the hash of the bytes on disk and the hash
of the new content (contracts.md §2), which gives "invalidate approval if the proposal or base files
change" for free. The spoken summary is written by the gateway from the files and line counts
("Change 1 file in signup-form: modify index.html, 1 line added, 1 removed."), not by the model.
The client shows the diff as one focusable block, read line by line with the screen reader; there
is no hunk-by-hunk navigation yet.

## 10. Runner isolation

Docker Desktop on Windows is a classic demo-day failure. **Pick:** `run_program` takes an entry
file, never a command string. The file type picks a fixed argv (`.py` → `[python, "-I", "-X", "utf8", entry, *args]`) run
with `shell=False`, the project folder as cwd, an explicit minimal environment (so a generated
program can't read `ANTHROPIC_API_KEY`), a timeout, an output cap, and a kill of the whole process
tree. Every run needs approval, and the code is scanned first (§14). Use a separate Python
interpreter, never the core's venv. Paths go through `Path.resolve()` and must stay under the workspace root,
which also catches symlinks and Windows junctions. The fixture is a git repo, so "recover an
unwanted edit" means restoring the touched files with git, recorded in the event log. Say plainly in
the pitch that this is allow-listing, not OS sandboxing.

## 11. Echo Check

Playwright for Python on Chromium, against the running fixture:
- `locator.aria_snapshot()` for roles and accessible names (the observed facts);
- axe-core injected into the page for rule violations such as a missing label;
- Tab through the form and read each focused element's role and name, for the keyboard claim.

The report says which checks ran, which couldn't, and what was observed, before and after the fix.

## 12. Event transport

WebSockets make reconnect, ordering and dedupe our job. **Pick:** `POST` for commands, and SSE
(`sse-starlette`) for events. The event `id` is the sequence number, the client resumes with
`Last-Event-ID` and drops anything it has already seen. That maps directly onto the handoff's
"event sequence" and "suppress duplicate operation ids".

## 13. UI components and Tailwind

Tailwind is fine for styling, but it isn't where accessibility comes from. Rules for `client/`:
semantic elements first (`button`, `form`, `label`, headings); React Aria Components for the hard
widgets (approval dialog, diff navigator, task history listbox); Tailwind's `sr-only` for text only
screen-reader users need. Never a `div` with `onClick`, and every state change the user needs is
announced. Focus moves deliberately: to the approval dialog when one opens, and back when it closes.

## 14. Security scanning

**Semgrep (team pick).** For: multi-language, well known, a strong "AI security" story. Against:
native Windows support is newer, and `--config p/python` downloads rules from the registry, which
fails on bad venue Wi-Fi. **Bandit.** For: pure Python, pip-installs anywhere, made exactly for the
Python MVP. Against: Python only. **Pick:** Bandit behind a `scan()` interface (built). Semgrep is
not implemented: the plan was to vendor its rules, and Bandit carries the MVP. Findings are
spoken as "1 high-severity issue, main.py line 4, subprocess call with shell=True", and a high
finding blocks the run until the user explicitly approves it.

## Security notes (David)

- Everything the model reads (files, page DOM, tool output, transcripts) goes into tool results
  as untrusted data. Operator instructions go only in the system prompt or mid-conversation
  `system` messages.
- Plant a prompt injection in the fixture (an HTML comment telling the agent to delete files). The
  demo shows it's harmless because no tool can do it. That's a stronger judge moment than a filter.
- The policy gate validates arguments again right before execution, not only when the request
  arrives.
- No network tool, no install tool. The model never sees `.env`. The core binds to loopback only
  and requires the per-launch token.
