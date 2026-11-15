# Artemis

**A voice-first coding companion for blind developers.** Say what you want built, review and
approve project changes, and hear what really ran. Built at HackUMass XIV (2026).

**Ubuntu + GNOME is our primary development and demo platform, with Orca as our primary screen
reader.** We started on Windows and switched halfway through the hackathon because Ubuntu made
it easier to coordinate Artemis, desktop events, screen-reader interaction and spoken responses.
The Windows Tauri client and NVDA/SAPI support remain in the repository.

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

Artemis aims to make reviewing and controlling an AI coding agent more accessible, so developers
can follow its work through voice, keyboard and screen-reader feedback.

## What Artemis does

Artemis brings voice, keyboard and screen-reader interaction into a workflow with reviewable
actions and feedback from actual execution.

1. **Say it.** Hold Control+Shift+Space and speak, say "Hey Artemis" (opt-in), or type. Speech
   recognition runs on your laptop, so your voice never leaves it.
2. **Approve it.** Project writes and runs wait for approval bound to the proposed action. Use
   the focused dialog controls to approve or Escape to reject. In conversation mode, eligible
   project writes and runs also accept “approve” after the spoken summary and a short guard.
   Flagged actions, desktop actions requiring approval, and publishing stay on the keyboard.
3. **It checks first.** A security scan (Bandit) runs before you approve, and any findings are read
   to you as part of the approval.
4. **It runs for real.** Artemis runs the program and reads the exit code and the last line of
   output word for word: "main.py failed with exit code 1. NameError: name 'pritn' is not
   defined. Did you mean: 'print'?"
5. **Inspect accessibility.** Echo Check inspects local project pages with Playwright accessibility
   snapshots and axe-core, reporting structure, findings and checks needing human review. It is
   not a complete accessibility audit or a substitute for screen-reader testing.
6. **Use desktop tools and publish.** On Ubuntu, open apps, files and websites, or approve commands
   and typing through desktop tools. Ordinary allowed opens do not always need a dialog. Publish
   reviewed files to a new GitHub repository after keyboard approval of the destination,
   visibility and files. Publishing requires `git` and a signed-in GitHub CLI (`gh auth login`).

Everything Artemis says also appears in a text log your screen reader can review, and Artemis never
changes your screen reader's own voice or speed.

## How it works

| Part | What it is |
|---|---|
| **Core** (`server/`) | Python + FastAPI on loopback with a per-launch token. Sessions, approvals and an SSE event stream connect clients to a Claude agent using the Anthropic API. Every tool call passes a policy gate for validation, path checks, scans and action-specific approvals. File and webpage content is treated as untrusted; authorization is enforced separately. |
| **Voice** (`server/artemis/voice/`) | Local faster-whisper transcription, push-to-talk and opt-in wake word. Optional ElevenLabs speech with local speech-dispatcher fallback on Ubuntu; NVDA/SAPI support on Windows. Supported voice interruptions and short audio cues help users follow work. |
| **Ubuntu interface** (`client/prototypes/water-desktop/`) | GNOME Shell island, typed requests and approval dialogs. Orca is the primary screen-reader target. States reflect core events; the visual ring supplements spoken and screen-reader interaction. |
| **Windows client** (`client/`) | Retained Tauri 2 + React + TypeScript client with NVDA-oriented focus handling, live regions and guarded keyboard approvals. |
| **Tools** | Project editing, Bandit scanning, limited program runs, Echo Check, Linux desktop actions and reviewed GitHub publishing. |

Microphone audio is transcribed locally, but Artemis is not fully offline: Claude receives requests
and relevant project context. When ElevenLabs is enabled, spoken text is sent to it after secret
redaction. Approved GitHub publishing also sends files off the laptop. Redaction and scanning are
tested defenses, not guarantees that arbitrary content is safe.

`run_program` uses fixed argv presets without a shell, a separate interpreter, a scrubbed
environment, timeouts and output limits. This is not an OS sandbox. The separate Ubuntu
`run_command` tool can execute a shell command after keyboard approval.

More detail: [architecture](docs/architecture.md), [decisions](docs/decisions.md),
[API contracts](docs/contracts.md).

## Try it

### Ubuntu 24.04 + GNOME 46 (primary)

You need a GNOME desktop session, a microphone for voice input, and an Anthropic API key for the
real agent. Setup installs system/Python dependencies and downloads the local speech models.
Enable Orca for screen-reader interaction; Artemis's own speech does not replace it.

```bash
bash infra/setup-ubuntu.sh
client/prototypes/water-desktop/install.sh
# On Wayland, log out and back in after first installing the extension, then:
gnome-extensions enable artemis-water@bu-hackathonz.github.io
infra/start.sh --agent claude --conversation
```

The launcher asks for the API key if needed and does not save the prompted key. The island finds
the core through a private runtime file containing its address and launch token. The extension is
already in the repository; no separate PR checkout is needed.

- Without `--agent claude`, the launcher uses a scripted stub agent.
- `--voice stub` skips the microphone so you can type requests.
- `--conversation` enables built-in phrases, follow-ups and eligible voice approvals.
- Use Ctrl+Shift+Space for push-to-talk; open the island to type.
- Stop the core with Ctrl+C. Full setup and troubleshooting:
  [Ubuntu instructions](infra/README.md#ubuntu-run-the-core-and-the-gnome-island).

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

### Hands-free (optional)

On Ubuntu, start wake mode with conversation support:

```bash
ARTEMIS_LISTEN_MODE=wake infra/start.sh --agent claude --conversation
```

Say “Hey Artemis, create a Python program that prints Hello World.” Push-to-talk remains available.
With `ARTEMIS_BARGE_IN=on` (default), supported wake and stop phrases can interrupt Artemis's own
speech. Set it to `off` to drop microphone input while Artemis speaks. Real-room testing matters,
especially when screen-reader speech comes through speakers.

## Status

This is a hackathon project, with Ubuntu/Orca as the current priority. The connected GNOME island,
core workflow, eligible voice approvals, desktop tools and GitHub publishing are implemented.
Hardware validation and coordination between Orca and Artemis's speech remain ongoing; we do not
claim that voices can never overlap. Testing with blind developers outside the team is a next step.

CI runs server tests on Ubuntu and Windows, client unit and browser tests, the client build and
secret scanning. Platform test counts overlap, and optional/live integrations are not all covered
by every run. See [current CI results](https://github.com/BU-Hackathonz/Artemis/actions) and the
[workflow](.github/workflows/ci.yml).

Next: validate real coding workflows with blind developers, refine Orca interaction and speech
timing, improve voice settings, and ship an installer with clear setup requirements.

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
