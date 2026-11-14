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
| `-Dev` | on | Run the app with `npm run tauri dev` in `client/` |
| `-AppPath <exe>` | | Run a built app instead |
| `-CoreOnly` | | Start only the core and print its address |
| `-Agent stub\|claude` | `stub` | Sets `ARTEMIS_AGENT`; `claude` also syncs the `agent` and `security` groups |
| `-Voice whisper\|stub` | `whisper` | Sets `ARTEMIS_VOICE`; `whisper` syncs the `voice` group |
| `-HealthTimeoutS` | `120` | How long to wait for the core's `/health` |

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
