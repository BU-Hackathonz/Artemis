# Artemis architecture, risks and build order

Most of this plan is built and merged on `main` (status: [`demo.md`](demo.md)). This doc still
separates what the team has **confirmed**, what is **proposed**, and what was **not started** at
kickoff. API shapes are in
[`contracts.md`](contracts.md); the reasoning behind each technology pick is in
[`decisions.md`](decisions.md).

## Status

| Item | Status |
|---|---|
| Windows-only native desktop app + headless core + fully nonvisual interaction | **Confirmed** |
| Live demo on Ubuntu 24.04 + GNOME 46 with Jun's GNOME Shell island; Windows build later ([decisions §16](decisions.md#16-ubuntu-demo-decided-by-david-2026-11-14)) | **Confirmed** (David, 2026-11-14) |
| Six workstreams and owners (Avery also owns Infrastructure; David approves merges until Avery arrives) | **Confirmed** |
| First MVP: "create a Python program that prints Hello World", run it, hear the result | **Confirmed** |
| Tauri 2 + React + TS + Tailwind; Python + FastAPI core | Proposed by team (treated as the plan) |
| Whisper STT, SAPI TTS, Semgrep, UI Automation, axe-core + Playwright | Proposed by team |
| NVDA speech via its controller API, push-to-talk, Bandit fallback, SSE, approval hashing | Proposed here |
| Security Guardian (Bandit gate, `scan_project`), editing existing code (`edit_file`) | Merged (#11, #12, #35, #37) |
| Debugging | Not started, after MVP |
| Echo Check | Merged (#25, #26), web projects only |

## Feasibility

**The MVP is feasible in the HackUMass window if two things happen in the first hours:**
Avery has time for the runner as well as the core (it is on the MVP's critical path, step 7), and a
keyboard-only walking skeleton with a stub agent works end to end by about hour 8. Everything after
that is swapping real parts in behind interfaces that already work.

What makes it tractable: the MVP touches one new file in one new folder and runs one program. No
editor, no diff of existing code, no web page. Most of the risk is in Windows setup and audio, not in
the AI.

## Runtime shape

```
Tauri app (client/)  ── global shortcut (push-to-talk), window, NVDA-friendly UI
   │  HTTP POST commands + SSE event stream, 127.0.0.1 only, per-launch token
   ▼
FastAPI core (server/artemis/)                     one Python process
   core/        sessions, task state machine, approvals, event bus, HTTP + SSE  (Avery)
   voice/       mic capture, faster-whisper, announce() → NVDA / SAPI            (Yaovi)
   agent/       Claude tool loop, project context, explanations                  (David)
   policy/      tool gate, path checks, approval requirements, scanners          (David)
   runner/      workspace files, restricted program runs                         (Avery)
   echo/        Echo Check (post-MVP)                                            (Avery + Jun)
   │
   ▼
Workspace: %USERPROFILE%\ArtemisProjects\<project>\   (git repo per project)
Runs: separate Python interpreter, scrubbed env, timeout, killed with its process tree
```

Inside the core the parts talk through plain Python interfaces, not HTTP (see `contracts.md` §3).
One process means no service discovery, no extra ports, and one log. "Independent of the desktop
interface" holds because nothing in `server/` imports or knows about Tauri; the same API could serve
a CLI or a VS Code extension later.

## How one MVP request flows

1. User holds **Ctrl+Shift+Space** (Tauri global shortcut, works from any app) and says
   "Artemis, create a Python program that prints Hello World". The client calls `POST /voice/listen`
   with `start` on key down and `stop` on key up; the core records and transcribes. The leading "Artemis" is stripped. No wake-word
   engine is needed.
2. The core announces "Heard: create a Python program that prints Hello World" and starts a task.
3. The agent (Claude) plans the work and calls `create_project` and `write_files`. Writes need
   approval, so the policy gate pauses the task and emits `approval.requested`: "Create project
   hello-world with 1 file: main.py, 1 line. No security findings. Enter to approve, Escape to
   reject."
4. The user presses Enter. The runner writes the file and the task resumes.
5. The agent calls `run_program`. The policy gate scans the file (Bandit), then asks for
   approval again: "Run main.py with Python. No security findings. Enter to run, Escape to reject."
6. The runner executes it with a 10 s timeout and a scrubbed environment. The `run.result` event
   carries the exit code and the output.
7. The agent explains the result. `announce()` speaks through NVDA if it's running (SAPI if not),
   and the same text appears in the app's log, so it can be replayed or read in braille.

Every pause, failure and completion is spoken. Silence longer than about 3 seconds while the task
is working or running gets a quiet spoken "Still working." (low priority, so it never talks over
anything; there is no heartbeat earcon), so the user never wonders whether Artemis heard them.

## Integration risks and missing components

| # | Risk or gap | Impact | Fix |
|---|---|---|---|
| 1 | **Avery owns both orchestration and infrastructure**, and isn't on site yet; the runner is MVP step 7 | MVP blocked | Keep `runner/` small (§ contracts 3.4) and stub it first; anyone may build the stub, with a handoff on the board |
| 2 | **Spoken output when Artemis isn't focused.** Live regions and a webview's speech only reach NVDA from the foreground window | User misses results | `announce()` in the core speaks through NVDA's Controller Client (`nvdaController_speakText`), falling back to SAPI. Works from any app |
| 3 | **Two voices at once** (SAPI over NVDA) | Unusable for real NVDA users | Use one output at a time: NVDA if running, else SAPI. The client's live region stays quiet |
| 4 | **Generated programs inherit the core's environment**, including `ANTHROPIC_API_KEY` | Secret leak through generated code | Runner starts children with an explicit minimal env, never `os.environ` |
| 5 | **"Restricted environment" on Windows without Docker** | Overclaiming in the pitch | Approval before every run + static scan + separate interpreter + timeout + process-tree kill (`taskkill /T /F`; no Job Object). Call it "gated and limited", not "sandboxed" |
| 6 | **Workspace management is undefined** (where projects go, how a spoken name becomes a folder) | Agent writes in random places | `%USERPROFILE%\ArtemisProjects\<slug>`; `create_project` slugs the name and `git init`s it; all paths resolve under it |
| 7 | **Which Python runs user code** | Generated code imports core dependencies | Runner uses a dedicated interpreter (`ARTEMIS_RUN_PYTHON` if set, else `uv python find`, else `py -3`, else `python3`), with `-I` isolated mode, never the core's venv |
| 8 | **Approval by voice** can misfire on a misheard "yes" | Unwanted writes or runs | MVP approval is keyboard: Enter or Escape on the focused dialog. Voice "approve" is post-MVP and needs a read-back |
| 9 | **Latency with no feedback** (STT, then model, then run) | User thinks it froze | Announce state changes; a spoken "Still working." heartbeat; announce agent progress per tool call (the agent loop doesn't stream) |
| 10 | **Errors softened by summaries** | Violates "don't conceal errors" | Failure events are high priority, interrupt speech, and always speak the exit code and the last line of the error output (the exception) verbatim before any explanation |
| 11 | **Core not running or crashed** | App is silent | The client announces "Artemis core is not reachable" and retries; `infra/start.ps1` starts both |
| 12 | **Tauri toolchain** (Rust, MSVC Build Tools) | Hours lost | Time-box 1 h at kickoff on the demo laptop; the client also runs in Chrome against the core |
| 13 | **Semgrep on Windows** (native support is newer; registry rules need network) | Scan step fails on stage | Time-box install; vendor the rules locally; Bandit (pure Python) is the fallback for the Python MVP |
| 14 | **Mic device, permissions and noise** | Bad transcripts on stage | The mic is Windows' default input device (no picker in the app yet); test in a noisy room; transcript read back before the task starts; typed input always available |
| 15 | **Global shortcut collisions** with NVDA (Insert or Caps Lock modifiers) and Windows (Win+H) | Shortcut swallowed | Ctrl+Shift+Space by default (Ctrl+Alt+Space was taken by another app on both test laptops), changeable in the app; Start listening button as fallback |

Not needed for the MVP (deliberately cut): Windows UI Automation for inspecting other apps, Docker,
a database (state is in memory plus a JSONL event log), WebSockets, wake-word detection,
multi-project management, Echo Check.

## Build order

**Phase 0: kickoff (first 2 to 3 hours), everyone**
- David accepts the contracts (Avery reviews on arrival). Agree on [`contracts.md`](contracts.md) and the sample payloads.
- Jun: Tauri builds and runs on the demo laptop; NVDA installed.
- Yaovi: faster-whisper model downloaded; mic records on the demo laptop; the NVDA controller DLL speaks a test string.
- David: API key works; one tool call round-trips; Bandit and Semgrep install check.
- Avery: `uv` project in `server/`, FastAPI health endpoint, SSE endpoint emitting a test event.

**Phase 1: walking skeleton, keyboard only, stubs (target about hour 8).** This is the most
important milestone. Type the request → `POST /sessions/{sid}/tasks` → **stub agent** that
always proposes `hello-world/main.py` → approval dialog → write → stub run → `run.result` → spoken and in the log.
This proves the client, core, approvals, events and speech before any AI or audio is live.
**End Phase 1 with an eyes-closed rehearsal** (monitor off, NVDA on) of this stub flow. It catches
focus, speech-overlap, cancel and approval problems while there is still time to fix them.

**Phase 2: swap in the real parts (about hours 8 to 18), in parallel, each behind its interface**
- David: real agent loop + policy gate + scan before run.
- Yaovi: push-to-talk → transcript → task; NVDA/SAPI `announce()`.
- Avery (infrastructure): real runner (env scrub, timeout, process-tree kill), `create_project`.
- Jun: approval dialog, log navigation, "repeat last" and "stop speech" keys, settings.
- Avery: cancel, error events, reconnect and replay with `Last-Event-ID`.

**Phase 3: MVP rehearsal (about hours 18 to 22).** Run the 10-step demo with the real agent and
voice, eyes closed (monitor off), twice. Fix every missed announcement, focus trap and silent
failure before adding anything. This is the second rehearsal; the first ends Phase 1.

**Phase 4: differentiators, in this order, each demoable alone**
1. **Failed run explained**: the agent introduces a bug, the run fails, Artemis speaks the error and fixes it. Debugging is cheap once runs work.
2. **Security Guardian**: generated code with a planted vulnerability (e.g. `subprocess` with `shell=True`, or a hard-coded secret); the scan finding is spoken and blocks the run until the user decides. Also the planted prompt injection in a repository file, shown to be harmless. (Best AI Security Hack.)
3. **Edit existing code + spoken diff review** (search/replace edits, hunk-by-hunk navigation).
4. **Echo Check**, kept small and only once the path above is reliable: one fixture web page, `aria_snapshot()` roles and accessible names plus axe-core, run before and after the fix, findings spoken through the same `announce()`. (Best Accessibility Hack.)

**Phase 5: final rehearsal and pitch.** Freeze features about 4 hours before judging. In the
pitch, say clearly which parts are the real integrated run and which are simulated client demos.
The line against general voice assistants: Artemis proves its results. Every change is approved
and bound to the exact edit, every run is real, and Echo Check reports the real accessibility tree
instead of describing the screen.

## Keep it modular without overengineering

- One Python process with internal interfaces; no microservices, no message broker.
- One HTTP API for every UI. Tauri-only calls go behind `client/src/platform.ts`.
- Pydantic contracts are the only shared types. The plan is to generate the TS types from them;
  until then the client keeps hand-written types in `client/src/contracts.ts`.
- Every subsystem gets a stub first (`agent/stub.py`, `voice/stub.py`, and `FakeRunner` in
  `policy/testing.py`, selected with `ARTEMIS_RUNNER=fake`), so each owner can work alone and the
  skeleton never breaks.
- No plugin system, no config framework, no DB until a feature actually needs one.

## Ubuntu runtime shape (demo target since 2026-11-14)

The core doesn't change. The UI around it does. Reasons: [`decisions.md`](decisions.md) §16.

```
GNOME Shell extension (Jun, client/prototypes/water-desktop)
   top-edge notch: listening / working / result states; only Settings expands
   typed-request panel (fallback); approval modal dialog with the Enter guard
   │  HTTP POST commands + SSE event stream, 127.0.0.1 only, per-launch token
   │  address + token read from a 0600 file under $XDG_RUNTIME_DIR
   ▼
FastAPI core (server/artemis/)       same process and routes as on Windows
   voice/   wake word, mic capture, faster-whisper, Linux speech (ElevenLabs, offline fallback)  (Yaovi)
   agent/, policy/, conversation/  same code, platform-aware where it matters            (David)
   runner/  same argv presets; Linux interpreter lookup (#139)                              (Avery)
   │
   ▼
Workspace: ~/ArtemisProjects/<project>/   (may be a symlink; links inside a project aren't followed)
Screen reader: Orca. The core speaks for itself, so Orca and Artemis must not talk over each other
```

`infra/start.sh` (Avery) generates the token, starts the core, writes the `0600` runtime file
and enables the extension. The API key is typed at a hidden prompt and never saved. The island's
states come only from real events: `voice.activity` (listening, transcribing, speaking) and
`task.state`, `file.changed` and `run.result` for the rest.

Ubuntu-specific risks:

| # | Risk | Fix |
|---|---|---|
| U1 | ElevenLabs is a cloud voice: venue Wi-Fi can drop, and spoken text leaves the laptop | An offline fallback voice (Yaovi) when ElevenLabs is unreachable; spoken text is redacted before it is sent (#129) |
| U2 | Orca and Artemis speaking at once | One voice at a time, as on Windows (§ risk 3); Avery's voice lane owns detection |
| U3 | Wake word hears Artemis's own speech | The mic is muted while Artemis speaks (Avery, voice lane) |
| U4 | Shell extension crashes take the UI down | The typed-request panel is the fallback; the core keeps running |
| U5 | No Ubuntu hardware for anyone but David | David runs the hardware QA and integration on his laptop |
