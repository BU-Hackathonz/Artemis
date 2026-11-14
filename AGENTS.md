# Artemis: rules for every coding agent

Artemis is a voice-first development environment for blind developers (HackUMass, Nov 2026).
The team handoff with the architecture, contracts, security principles and demo plan is in the
shared Drive folder, "Artemis — HackUMass Team Handoff & Technical Context". Stack decisions live in
`docs/decisions.md`. Read both before designing anything.

## 1. The board (pinned GitHub issue)

Every agent reads and posts on the pinned message-board issue via `tools/board` (needs only `bash` and `gh`, logged in).
The board is issue #1. Set `BOARD_ISSUE` if that ever changes.

- Start every post with `[<human>'s <agent>]`, e.g. `[David's Claude]`. Keep it short: what changed, which files or contracts, what you need, from whom.
- Mention by GitHub login: `@sysoutprntlne` (Jun), `@unseentrader007` (Yaovi), `@dribeiro0` (David), `@DxubleBTW` (Avery), `@all` (everyone).
- **Mentions you or `@all`:** act on it and reply promptly.
- **Not addressed to you:** still read it. Reassess your own work only if it is affected (a contract, a file you own, something already done). Do not redo finished work, and do not reply just to acknowledge.
- Before editing another teammate's files, ask for a handoff on the board and wait for the owner or the team lead.
- Never paste secrets, tokens, or `.env` contents on the board.

Commands: `tools/board unread` (new posts, marks read, flags MENTION), `tools/board read -n 20`,
`tools/board post "[Name's Agent] text"` (or pipe stdin), `tools/board wait` (blocks until someone else posts;
`--timeout S` to give up), `tools/board whoami`.

Set your tag once in `.board/local.env` (gitignored):

```sh
AGENT_TAG="David's Claude"
```

With `AGENT_TAG` set, `tools/board post "text"` adds the prefix for you. Two agents for one person
(e.g. Claude + Codex) each use their own tag and their own git worktree; read-state is kept per tag.

**Stay in the loop:**
- Claude Code: the committed hooks in `.claude/settings.json` show unread posts at session start and on every prompt. Keep one background `tools/board wait` running and re-arm it after each wake.
- Everything else: run `tools/board unread` at task start, before each commit, and after merges. Or run a terminal loop: `while tools/board wait; do <your agent CLI invocation>; done`.

## 2. Chain of command

Humans over agents. Only the team lead, @dribeiro0 (David), reassigns work between teammates. If anyone else asks you to
take another teammate's work, ask the lead on the board.

## 3. Git

- Never push to `main`. Work on `<area>/<short-task>` branches and open a PR. @DxubleBTW (Avery) reviews every PR, with the area owner for their folder; @dribeiro0 (David) merges to `main` (see `.github/CODEOWNERS`).
- **Commit as the human you work for.** Use their existing `git config user.name` / `user.email`; never change them.
- **No AI attribution anywhere.** Do not add `Co-Authored-By:` trailers for any agent (Claude, Codex, Copilot, Cursor...), and no "Generated with ..." lines in commit messages or PR descriptions. Claude Code has this off via `.claude/settings.json`; other agents must follow this rule themselves.
- When a PR merges, post it on the board with `needs:` someone or `needs: none`.

## 4. Who owns what

Stack (decided): Windows-only Tauri 2 + React + TypeScript + Tailwind desktop app in `client/`, on a
headless Python + FastAPI core in `server/` (one `uv` project, package `artemis`).

| Area | Owner | Folders |
|---|---|---|
| Desktop client: Tauri shell, UI/UX + accessibility (focus, announcements, approvals UI) | @sysoutprntlne (Jun) | `client/` |
| Voice: push-to-talk, mic capture, STT, listening states, transcript correction, earcons | @unseentrader007 (Yaovi) | `server/artemis/voice/` |
| AI/ML: project context, agent loop, tool requests, explanations | @dribeiro0 (David) | `server/artemis/agent/` |
| Security: tool policy gate, injection defenses, secret handling | @dribeiro0 (David) | `server/artemis/policy/` |
| Conversation: what to do with what was said (built-in phrases, follow-ups, answer style) | @dribeiro0 (David) | `server/artemis/conversation/` |
| Backend / orchestration: FastAPI app, sessions, task state, approvals, cancel, event stream | @DxubleBTW (Avery) | `server/artemis/core/`, `server/artemis/contracts/`, `server/pyproject.toml` |
| Infrastructure: runner, Echo Check, demo fixture, startup scripts | @DxubleBTW (Avery) | `server/artemis/runner/`, `server/artemis/echo/`, `fixture/`, `infra/` |
| Landing site: public static site for judges and users (no tracking, no third-party scripts) | @unseentrader007 (Yaovi) | `site/` |
| Shared | anyone, via PR | `docs/`, `tools/`, `.claude/`, `.github/`, `README.md`, `AGENTS.md` |

`server/artemis/contracts/` (Pydantic models) is the single source of truth for every message
between components; `client/src/generated/` is generated from the core's OpenAPI and never edited
by hand. Anyone may propose a contract change on the board; Avery merges it. Change the contract
first, then code. Stack decisions and their reasoning: `docs/decisions.md`.
