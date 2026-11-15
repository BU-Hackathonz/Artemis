# Artemis

**A voice-first coding companion for blind developers.** Say what you want built, approve each
change by keyboard, and hear what really ran, with no sighted help. Built at HackUMass XIV (2026).

## The problem

The WHO estimates that at least 2.2 billion people have a near or distance vision impairment
([fact sheet](https://www.who.int/news-room/fact-sheets/detail/blindness-and-visual-impairment)).
Blind and low-vision people write software too, usually through a screen reader, which reads the
screen aloud one element at a time. Today's AI coding tools are designed for people who can see:

- **You review by skimming.** Agents show a diff and expect you to glance over it and click
  "Accept". Through a screen reader, a diff is a long stream of plus and minus signs, line numbers
  and symbols. Reviewing a change becomes slow and tiring, so it's tempting to approve without
  checking.
- **Results are visual.** Did the program work? Is the page you built accessible? The answer is a
  terminal full of output or a page on screen. Many tools describe that in words instead of
  showing you the real result.
- **The interface fights the screen reader.** Agent UIs are crowded panels, pop-ups that steal
  focus, and buttons with no labels. A blind developer spends their attention fighting the tool
  instead of building.
- **Trust is the hardest part.** If you can't see what the agent did, you have to trust it. A
  hidden instruction in a README or web page can steer an agent, and you would never see it happen.

So a blind developer either works much more slowly than their sighted peers, or needs a sighted
person to check the AI's work. Neither is acceptable.

## What Artemis does

Artemis makes voice, keyboard and screen reader the main way to work, and proves every result
instead of describing it.

1. **Say it.** Hold Control+Shift+Space and speak, say "Hey Artemis" (opt-in), or type. Speech
   recognition runs on your laptop, so your voice never leaves it.
2. **Approve it.** Before any file is written or any program runs, Artemis tells you what it will
   do and waits for Enter (approve) or Escape (reject). The approval is tied to that exact change,
   so nothing can be swapped in between what you heard and what happens.
3. **It checks first.** A security scan (Bandit) runs before you approve, and any findings are read
   to you as part of the approval.
4. **It runs for real.** Artemis runs the program and reads the exit code and the last line of
   output word for word: "main.py failed with exit code 1. NameError: name 'pritn' is not
   defined. Did you mean: 'print'?"
5. **It checks accessibility for real.** Echo Check opens a page and reports its actual
   accessibility tree, the same thing a screen reader would get, not a description of pixels.

Everything Artemis says also appears in a text log your screen reader can review, and Artemis never
changes your screen reader's own voice or speed.

## How it works

| Part | What it is |
|---|---|
| **Core** (`server/`) | Python and FastAPI, listening on 127.0.0.1 only, with a fresh launch token for each run. A Claude agent with a few tools: write and edit files, run programs, scan, inspect a page. Every tool call goes through a policy gate: path checks, approvals bound to a hash of the change, and text from files and web pages treated as untrusted, so a prompt injection can't approve anything. Secrets are redacted from logs, speech and anything sent out. |
| **Voice** (`server/artemis/voice/`) | Push-to-talk or "Hey Artemis" with local Whisper. Speech through NVDA or Windows SAPI, speech-dispatcher on Linux, or an optional natural ElevenLabs voice. Short sounds for listening, success and failure. |
| **Client** (`client/`) | A Tauri desktop app for Windows, built for NVDA first: live regions, careful focus, and a guard so a stray Enter can't approve something. On Ubuntu, a small GNOME Shell "island" at the top of the screen shows only what the core's events prove: listening, thinking, editing, running, speaking. |

More detail: [architecture](docs/architecture.md), [decisions](docs/decisions.md),
[API contracts](docs/contracts.md).

## Try it

### Windows

You need [uv](https://docs.astral.sh/uv/), Node 22+, and Rust with the MSVC Build Tools for the
desktop app (Tauri 2). NVDA is optional; without it Artemis speaks through Windows SAPI.

```powershell
cd server; uv sync --group voice --group dev; cd ..      # first time only
cd client; npm ci; cd ..                                  # first time only
powershell -ExecutionPolicy Bypass -File infra\start.ps1  # starts the core and the app, connected
```

- `-Agent claude` uses the real Claude agent. It needs `ANTHROPIC_API_KEY`, set in that window
  only. Without it, a scripted demo agent runs.
- `-Voice stub` skips the microphone; `-CoreOnly` starts only the core.
- The double-click launcher is `infra\Artemis.cmd`. Add `-VoiceDemo` to use the ElevenLabs voice
  (it asks for the key and never saves it).

Details: [infra/README.md](infra/README.md).

### Ubuntu 24.04 (GNOME)

```bash
bash infra/setup-ubuntu.sh            # once: system packages, Python packages, speech model
infra/start.sh --agent claude         # each time; asks for the API key, never saves it
```

The core writes its address and launch token to a private file for the GNOME island. The island
itself is in PR #128 and installs with `setup-ubuntu.sh --extension-dir <its folder>`. See
[infra/README.md](infra/README.md#ubuntu).

### Hands-free (optional)

Set `ARTEMIS_LISTEN_MODE=wake` before starting, then say "Hey Artemis, create a Python program
that prints Hello World". Artemis ignores the microphone while it is speaking and never keeps
speech that doesn't start with "Hey Artemis". Push-to-talk keeps working alongside.

## Status

This is a hackathon project. It works end to end on Windows with NVDA:
request, approve, write, scan, run, hear the result. The Ubuntu version (GNOME island, Orca,
speech-dispatcher) is newer and still being tested on hardware. It has not yet been tested with
blind developers outside the team. That's the next step.

## Team

Built by Avery Lee, Yaovi, Jun Olea and David at HackUMass XIV.

## For contributors

- How the team and its coding agents work together, and who owns which folder: [AGENTS.md](AGENTS.md).
  The agent board is issue #1, used through `tools/board` (needs `bash` and `gh`).
- Tests: `cd server && uv run --group dev pytest`, and `cd client && npm test`.
- Run the core alone on any OS:
  `cd server && ARTEMIS_TOKEN=dev uv run uvicorn artemis.core.main:app --port 8765`, then
  `cd client && npm run dev` and enter the address and token in the form.
- Demo script and fixtures: [docs/demo.md](docs/demo.md), [stage runbook](docs/stage-runbook.md),
  [fixture/README.md](fixture/README.md).

Join the board:

```sh
gh auth login
mkdir -p .board
echo 'AGENT_TAG="<YourName>'"'"'s <Agent>"' > .board/local.env   # e.g. David's Claude
tools/board whoami
tools/board unread
```
