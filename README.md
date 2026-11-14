# Artemis

A voice-first development environment for blind developers. Open a project, direct and review a
change, run it, and understand what the app does, all by voice, keyboard and screen reader, without
sighted help. Built at HackUMass 2026.

- Rules for every coding agent, the board, and who owns which folder: [`AGENTS.md`](AGENTS.md)
- Stack (Tauri 2 desktop + Python FastAPI core) and the decisions behind it: [`docs/decisions.md`](docs/decisions.md)
- Architecture, risks and build order: [`docs/architecture.md`](docs/architecture.md); API contracts: [`docs/contracts.md`](docs/contracts.md)
- Agent board: issue #1, via `tools/board` (needs `bash` + `gh`)

## Join the board (2 minutes)

```sh
gh auth login
mkdir -p .board
echo 'AGENT_TAG="<YourName>'"'"'s <Agent>"' > .board/local.env   # e.g. David's Claude
tools/board whoami
tools/board unread
```

## Run Artemis (Windows)

Needs: `uv`, Node 20+, and for the desktop app Rust + MSVC Build Tools (Tauri 2). NVDA is optional; without it the core speaks through SAPI.

```powershell
cd server; uv sync --group voice --group dev; cd ..      # first time only
cd client; npm ci; cd ..                                  # first time only
powershell -ExecutionPolicy Bypass -File infra\start.ps1  # core + Tauri app, connected automatically
```

- `-CoreOnly` starts just the core and prints its address (never the token); `-Agent claude` uses the real agent (needs `ANTHROPIC_API_KEY`); `-Voice stub` skips the microphone. Details: [`infra/README.md`](infra/README.md).
- Core alone, any OS (bash): `cd server && ARTEMIS_TOKEN=dev uv run uvicorn artemis.core.main:app --port 8765`, then `cd client && npm run dev` and use the manual address + token form.
- Tests: `cd server; uv run --group dev pytest` and `cd client; npm test`.
- Demo fixtures (prompt-injection README, Security Guardian request): [`fixture/README.md`](fixture/README.md).
