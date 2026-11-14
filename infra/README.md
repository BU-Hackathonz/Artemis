# infra

`start.ps1` starts Artemis on Windows: the core first, then the desktop app (docs/contracts.md §6).

```powershell
# one-time: install the core's dependencies (the voice group downloads faster-whisper)
uv sync --project server --group voice --group dev
cd server; uv run --group voice python -m artemis.voice.setup; cd ..   # NVDA DLL + speech model

powershell -ExecutionPolicy Bypass -File infra\start.ps1                 # core + npm run tauri dev
powershell -ExecutionPolicy Bypass -File infra\start.ps1 -Agent claude   # real agent; needs ANTHROPIC_API_KEY
powershell -ExecutionPolicy Bypass -File infra\start.ps1 -AppPath C:\path\artemis-client.exe
powershell -ExecutionPolicy Bypass -File infra\start.ps1 -CoreOnly       # just the core
```

| Parameter | Default | Meaning |
|---|---|---|
| `-Dev` | (no effect) | Not read by the script. Without `-AppPath`, the app runs with `npm run tauri dev` in `client/` |
| `-AppPath <exe>` | | Run a built app instead |
| `-CoreOnly` | | Start only the core and print its address |
| `-Agent stub\|claude` | `stub` | Sets `ARTEMIS_AGENT`; `claude` also syncs the `agent`, `security` and `echo` groups |
| `-Voice whisper\|stub` | `whisper` | Sets `ARTEMIS_VOICE`; `whisper` syncs the `voice` group |
| `-Conversation` | off | Sets `ARTEMIS_CONVERSATION=on`: built-in spoken phrases ("what are you doing", "keep it brief") and one waiting follow-up request (contracts.md §5.4). Rehearse with it before using it in the demo |
| `-HealthTimeoutS` | `120` | How long to wait for the core's `/health` |

## Speech voice and rate (Windows voice only)

These apply when Artemis speaks through Windows SAPI. Set them in the PowerShell window you launch
from, before `start.ps1`; the core inherits them.

- `ARTEMIS_SAPI_VOICE`: part of an installed voice's name, such as `Zira`. An unknown name keeps
  the Windows default voice.
- `ARTEMIS_SAPI_RATE`: a whole number from `-3` to `6`; other numbers are clamped to that range.
  Unset or invalid keeps your own Windows speech rate.
- Both are ignored while NVDA is speaking. NVDA's own voice and rate are never changed.
- With conversation mode (`-Conversation`), saying "faster" or "slower" steps the Windows voice
  rate within `-3` to `6`.

```powershell
$env:ARTEMIS_SAPI_VOICE = "Zira"
$env:ARTEMIS_SAPI_RATE = "2"
powershell -ExecutionPolicy Bypass -File infra\start.ps1
```

What it does:
1. Makes a 32-byte random launch token (base64url) and picks a free port on `127.0.0.1`.
2. Starts `uv run uvicorn artemis.core.main:app --host 127.0.0.1 --port <port>` with
   `ARTEMIS_TOKEN` in the core's environment. Core output goes to `%LOCALAPPDATA%\Artemis\logs\core.log`.
3. Waits until `GET /health` answers with the token (no `Origin` header, which §6 allows).
4. Starts the app with `ARTEMIS_CORE_URL` and `ARTEMIS_TOKEN` in the app's environment.
5. When the app exits, or on Ctrl+C, kills the core's whole process tree.

Security notes:
- The token is passed only through child environments, never on a command line, in a URL, in a
  log or on screen. Windows lets any process read other processes' command lines.
- The core listens on `127.0.0.1` only. It requires the token on every route and rejects any
  `Origin` other than the Tauri app's, so a web page open in a browser can't drive it.
- Malware running as the same Windows user is out of scope for the weekend.

If it fails, the script prints the reason and the last lines of `core.log`.

## setup-demo.ps1

Prepares the demo projects before a demo or rehearsal ([`docs/demo.md`](../docs/demo.md)):

```powershell
powershell -ExecutionPolicy Bypass -File infra\setup-demo.ps1          # asks before deleting or resetting
powershell -ExecutionPolicy Bypass -File infra\setup-demo.ps1 -Force   # resets without asking
```

- Copies `fixture/injected-readme` to `calc`, `fixture/risky-run` to `old-app`, and
  `fixture/echo-page/before.html` to `signup-page\index.html` (never `after.html`), in
  `ARTEMIS_PROJECTS_DIR` or `%USERPROFILE%\ArtemisProjects`.
- Asks (y/N) before deleting an earlier `hello-world`, so the Hello World demo starts clean.
- Idempotent: projects that already match the fixture are left alone; one that differs (say,
  `signup-page` after the agent fixed it) is reset only after a y. Other projects are listed, never
  touched. It deletes nothing outside the projects folder and removes links without following them.
- Restart `start.ps1` afterwards, so the session starts fresh.

## Double-click launcher (demo/dev, not an installer)

`Artemis.cmd` (double-click it, or make a desktop shortcut to it) runs `launch.ps1`, which wraps
`start.ps1`:

1. Finds `artemis-client.exe` in `%LOCALAPPDATA%\Artemis\app\`, then in
   `client\src-tauri\target\release\`. If it is missing it says where to get it: GitHub Actions,
   "Windows exe (manual)", download the `artemis-client-windows` artifact.
2. Asks for your Anthropic API key with a hidden prompt. Press Enter to use the scripted stub
   agent instead. If `ANTHROPIC_API_KEY` is already in the environment (set in that window, or
   saved in your Windows settings), it uses that.
3. Runs `start.ps1 -Agent <stub|claude> -AppPath <exe>`. Closing the Artemis window stops the core.

The key lives only in the launcher's process environment: never in a file, on a command line, in a
log or in the exe. It is removed from the environment when the launcher exits, and any in-memory
copies end with the process.

Options: `-Agent stub|claude`, `-Voice whisper|stub`, `-AppPath <exe>`, `-NoConversation`,
`-ForgetKey`, and `-NoPause` (for scripts: never wait for a key press). The launcher turns
conversation mode on by default (built-in spoken phrases and one waiting follow-up, contracts.md
§5.4); `start.ps1` itself still defaults to off.

### The window never closes unread

Whenever the launcher stops for any reason other than you closing Artemis, the console window
stays open with the reason as its last lines and waits for a key press, so a screen reader can
read it. `-NoPause` turns the waiting off for scripts.

| `Artemis.cmd` exit code | Meaning |
|---|---|
| `0` | The app was closed normally (or `-ForgetKey` finished) |
| `1` | Could not start, or the core stopped while the app was running. The window says which |
| other | PowerShell's or `start.ps1`'s own code, passed through unchanged |

### If the launcher will not start

"Windows PowerShell could not run the launcher" means `launch.ps1` itself never ran. The lines
above that message are PowerShell's own explanation. The usual causes:

- `powershell.exe` is not on `PATH`, or your organization blocks it.
- A policy (AppLocker, or an antivirus) blocks scripts from this folder. Move the repository to
  a folder you are allowed to run scripts from, or ask whoever manages the laptop.
- `infra\launch.ps1` is missing or damaged. Restore it with `git checkout -- infra`.

"The launcher hit an unexpected error" means `launch.ps1` started and then failed in a way it
has no specific message for; the line above it is the error. `Artemis.cmd -Agent stub` avoids
the key prompt and Credential Manager, which is a quick way to tell whether those are involved.

This is **not** one-click packaging: it still needs this repository, `uv` and the synced
dependencies above, because the Python core is started from the repository (decisions.md #3).
It still shows a console window.

### Saving the key in Windows Credential Manager (optional)

After you type the key, the launcher asks whether to save it (and the workspace ID) in Windows
Credential Manager. Say yes and later launches skip the prompt. It is stored as a generic
credential for your Windows user only, protected by DPAPI. The launcher writes no file and
prints nothing; Windows keeps it encrypted in your profile. See or delete it in Credential
Manager under "Artemis/AnthropicApiKey", or run `infra\Artemis.cmd -ForgetKey`. Anyone signed in as you on this laptop could read it, so don't save
it on a shared account. `-NoPause` never prompts or saves; it only reads what is already saved.

## Ubuntu

On Ubuntu the core runs from the same repository with `infra/start.sh`. The GNOME Shell extension is
the client (there is no Tauri app for the Linux demo), so the script starts the core only and writes
the launch info the extension reads.

```bash
bash infra/setup-ubuntu.sh                          # once: apt, uv, dependencies, Whisper model
bash infra/setup-ubuntu.sh --extension-dir PATH     # also copy and enable the GNOME extension in PATH
infra/start.sh                                      # each launch: stub agent, Whisper voice
infra/start.sh --agent claude                       # real agent; asks for ANTHROPIC_API_KEY (hidden) if unset
infra/start.sh --voice stub --conversation          # no microphone, built-in phrases on
```

`setup-ubuntu.sh` runs these steps, and says each one before it does it:

1. **apt**: installs `libportaudio2` (sounddevice needs it for the microphone) and `curl` if missing.
   apt-get installs system packages, so this needs `sudo`, which asks for your password.
   `--no-apt` skips it.
2. **uv**: if it is missing, asks (y/N) before running uv's official installer. The script downloads the
   installer first and runs that file; nothing is piped to a shell. `--yes` answers yes.
3. **Dependencies**: `uv sync` for `server/` with the security, voice, agent and echo groups.
4. **Whisper model**: downloads the model the core uses (`ARTEMIS_WHISPER_MODEL`, default `small.en`) once,
   so the first push-to-talk does not stall. The download is large. `--no-model` skips it.
5. **GNOME extension**: copies the folder given by `--extension-dir` into
   `~/.local/share/gnome-shell/extensions/<uuid>/` (a copy, not a link; the uuid comes from its
   `metadata.json`) and enables it with `gnome-extensions enable` if that command is there. Without
   `--extension-dir` this step is skipped. `client/prototypes/water-desktop` is not on `main` yet.
   On Wayland, log out and back in afterwards so GNOME Shell loads the extension.

The script never asks for, reads, prints or stores an API key.

`start.sh` does what `start.ps1` does:

- Makes a fresh 32-byte base64url launch token and picks a free port on `127.0.0.1`.
- Starts `uv run --project server --group security [--group voice] [--group agent --group echo] uvicorn artemis.core.main:app --host 127.0.0.1 --port <port>`.
  The token, `ARTEMIS_AGENT`, `ARTEMIS_VOICE`, `ARTEMIS_CONVERSATION` and, if you typed one, the Anthropic
  key and workspace ID go into the core's environment only, never on a command line.
- Writes `$XDG_RUNTIME_DIR/artemis/launch.json` (or `/tmp/artemis-$UID/launch.json` without
  `XDG_RUNTIME_DIR`) with `url`, `token` and `pid`. The folder is 0700 and the file 0600, both created
  before anything is written. The file holds no API key, and it is deleted when `start.sh` exits.
- Sends the core's output to `${XDG_STATE_HOME:-~/.local/state}/artemis/core.log`.
- Waits for `GET /health` with the token, then prints `Core is ready.`. The token is sent to curl on
  stdin, not in its arguments.
- Stops the core's whole process group on Ctrl+C or when the script is terminated.

Options: `--agent stub|claude`, `--voice whisper|stub`, `--conversation`, `--voice-engine elevenlabs|nvda|sapi`
(sets `ARTEMIS_VOICE_ENGINE`), `--core-only` (accepted for symmetry), `--health-timeout N`.
Run `infra/start.sh --help` for the list.

Tests: `cd server && uv run pytest artemis/test_infra_sh.py` (Linux and bash only). They use a fake `uv`,
so they need no network, no apt and no sudo.
