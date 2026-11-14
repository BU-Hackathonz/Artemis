# Artemis

A voice-first development environment for blind developers. Open a project, direct and review a
change, run it, and understand what the app does, all by voice, keyboard and screen reader, without
sighted help. Built at HackUMass 2026.

- Rules for every coding agent, the board, and who owns which folder: [`AGENTS.md`](AGENTS.md)
- Stack decisions and the debates behind them: [`docs/decisions.md`](docs/decisions.md)
- Agent board: issue #1, via `tools/board` (needs `bash` + `gh`)

## Join the board (2 minutes)

```sh
gh auth login
mkdir -p .board
echo 'AGENT_TAG="<YourName>'"'"'s <Agent>"' > .board/local.env   # e.g. David's Claude
tools/board whoami
tools/board unread
```
