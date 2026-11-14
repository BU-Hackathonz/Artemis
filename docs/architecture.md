# Artemis architecture, risks and build order

Nothing is implemented yet. This doc separates what the team has **confirmed**, what is
**proposed**, and what is **not started** (everything, as of kickoff). API shapes are in
[`contracts.md`](contracts.md); the reasoning behind each technology pick is in
[`decisions.md`](decisions.md).

## Status

| Item | Status |
|---|---|
| Windows-only native desktop app + headless core + fully nonvisual interaction | **Confirmed** |
| Six workstreams and owners (Avery also owns Infrastructure; David approves merges until Avery arrives) | **Confirmed** |
| First MVP: "create a Python program that prints Hello World", run it, hear the result | **Confirmed** |
| Tauri 2 + React + TS + Tailwind; Python + FastAPI core | Proposed by team (treated as the plan) |
| Whisper STT, SAPI TTS, Semgrep, UI Automation, axe-core + Playwright | Proposed by team |
| NVDA speech via its controller API, push-to-talk, Bandit fallback, SSE, approval hashing | Proposed here |
| Security Guardian, debugging, editing existing code | Not started, after MVP |
| Echo Check | Not started, after MVP (web projects only) |

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

1. User holds **Ctrl+Alt+Space** (Tauri global shortcut, works from any app) and says
   "Artemis, create a Python program that prints Hello World". The client calls `voice/listen/start`
   and `stop`; the core records and transcribes. The leading "Artemis" is stripped. No wake-word
   engine is needed.
2. The core announces "Heard: create a Python program that prints Hello World" and starts a task.
3. The agent (Claude) plans the work and calls `create_project` and `write_file`. Writes need
   approval, so the policy gate pauses the task and emits `approval.requested`: "Create project
   hello-world with one file, main.py, 1 line: print Hello World. Press Enter to approve, Escape
   to reject."
4. The user presses Enter. The runner writes the file and the task resumes.
5. The agent calls `run_program`. The policy gate scans the file (Bandit or Semgrep), then asks for
   approval again: "Run main.py with Python. No security findings. Enter to run."
6. The runner executes it with a 10 s timeout and a scrubbed environment. The `run.result` event
   carries the exit code and the output.
7. The agent explains the result. `announce()` speaks through NVDA if it's running (SAPI if not),
   and the same text appears in the app's log, so it can be replayed or read in braille.

Every pause, failure and completion is spoken. Silence longer than about 3 seconds gets a
"still working" earcon, so the user never wonders whether Artemis heard them.

## Integration risks and missing components

| # | Risk or gap | Impact | Fix |
|---|---|---|---|
| 1 | **Avery owns both orchestration and infrastructure**, and isn't on site yet; the runner is MVP step 7 | MVP blocked | Keep `runner/` small (§ contracts 3.4) and stub it first; anyone may build the stub, with a handoff on the board |
| 2 | **Spoken output when Artemis isn't focused.** Live regions and a webview's speech only reach NVDA from the foreground window | User misses results | `announce()` in the core speaks through NVDA's Controller Client (`nvdaController_speakText`), falling back to SAPI. Works from any app |
| 3 | **Two voices at once** (SAPI over NVDA) | Unusable for real NVDA users | Use one output at a time: NVDA if running, else SAPI. The client's live region stays quiet |
| 4 | **Generated programs inherit the core's environment**, including `ANTHROPIC_API_KEY` | Secret leak through generated code | Runner starts children with an explicit minimal env, never `os.environ` |
| 5 | **"Restricted environment" on Windows without Docker** | Overclaiming in the pitch | Approval before every run + static scan + separate interpreter + timeout + Job Object kill. Call it "gated and limited", not "sandboxed" |
| 6 | **Workspace management is undefined** (where projects go, how a spoken name becomes a folder) | Agent writes in random places | `%USERPROFILE%\ArtemisProjects\<slug>`; `create_project` slugs the name and `git init`s it; all paths resolve under it |
| 7 | **Which Python runs user code** | Generated code imports core dependencies | Runner uses a dedicated interpreter (`uv python find` result or the system `py`), with `-I` isolated mode, never the core's venv |
| 8 | **Approval by voice** can misfire on a misheard "yes" | Unwanted writes or runs | MVP approval is keyboard: Enter or Escape on the focused dialog. Voice "approve" is post-MVP and needs a read-back |
| 9 | **Latency with no feedback** (STT, then model, then run) | User thinks it froze | Announce state changes; earcon heartbeat; stream agent progress |
| 10 | **Errors softened by summaries** | Violates "don't conceal errors" | Failure events are high priority, interrupt speech, and always speak the exit code and the first error line verbatim before any explanation |
| 11 | **Core not running or crashed** | App is silent | The client announces "Artemis core is not reachable" and retries; `infra/start.ps1` starts both |
| 12 | **Tauri toolchain** (Rust, MSVC Build Tools) | Hours lost | Time-box 1 h at kickoff on the demo laptop; the client also runs in Chrome against the core |
| 13 | **Semgrep on Windows** (native support is newer; registry rules need network) | Scan step fails on stage | Time-box install; vendor the rules locally; Bandit (pure Python) is the fallback for the Python MVP |
| 14 | **Mic device, permissions and noise** | Bad transcripts on stage | Device picker in settings; test in a noisy room; transcript read back before the task starts; typed input always available |
| 15 | **Global shortcut collisions** with NVDA (Insert or Caps Lock modifiers) and Windows (Win+H) | Shortcut swallowed | Ctrl+Alt+Space, configurable |

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
important milestone. Type the request → `POST /tasks` → **stub agent** that always proposes
`hello-world/main.py` → approval dialog → write → stub run → `run.result` → spoken and in the log.
This proves the client, core, approvals, events and speech before any AI or audio is live.

**Phase 2: swap in the real parts (about hours 8 to 18), in parallel, each behind its interface**
- David: real agent loop + policy gate + scan before run.
- Yaovi: push-to-talk → transcript → task; NVDA/SAPI `announce()`.
- Avery (infrastructure): real runner (env scrub, timeout, process-tree kill), `create_project`.
- Jun: approval dialog, log navigation, "repeat last" and "stop speech" keys, settings.
- Avery: cancel, error events, reconnect and replay with `Last-Event-ID`.

**Phase 3: MVP rehearsal (about hours 18 to 22).** Run the 10-step demo with eyes closed (monitor
off), twice. Fix every missed announcement, focus trap and silent failure before adding anything.

**Phase 4: differentiators, in this order, each demoable alone**
1. **Failed run explained**: the agent introduces a bug, the run fails, Artemis speaks the error and fixes it. Debugging is cheap once runs work.
2. **Security Guardian**: generated code with a planted vulnerability (e.g. `subprocess` with `shell=True`, or a hard-coded secret); the scan finding is spoken and blocks the run until the user decides. Also the planted prompt injection in a repository file, shown to be harmless. (Best AI Security Hack.)
3. **Edit existing code + spoken diff review** (search/replace edits, hunk-by-hunk navigation).
4. **Echo Check** on a small generated web page: Playwright + `aria_snapshot()` + axe-core, spoken findings. (Best Accessibility Hack.)

**Phase 5: final rehearsal and pitch.** Freeze features about 4 hours before judging.

## Keep it modular without overengineering

- One Python process with internal interfaces; no microservices, no message broker.
- One HTTP API for every UI. Tauri-only calls go behind `client/src/platform.ts`.
- Pydantic contracts are the only shared types; TS types are generated from them.
- Every subsystem gets a stub first (`agent/stub.py`, `runner/stub.py`, `voice/stub.py`), so each owner can work alone and the skeleton never breaks.
- No plugin system, no config framework, no DB until a feature actually needs one.
