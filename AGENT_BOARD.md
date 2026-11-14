# Agent Board

Board issue: #1   (pinned: "Agent Board: message channel")
https://github.com/BU-Hackathonz/Artemis/issues/1

## Ownership (agents: edit ONLY your own stream's folders)
| Stream   | Folders                                          | Owner (GitHub)              | Agent tag(s)                   | Scope |
|----------|--------------------------------------------------|-----------------------------|--------------------------------|-------|
| frontend | /web                                             | @DxubleBTW (Avery)          | Avery's \<Agent\>              | UI, pages, components, client state, calls to /api |
| backend  | /api                                             | @DxubleBTW (Avery)          | Avery's \<Agent\>              | server, routes, DB/schema, auth, API contract |
| ai       | /ai                                              | @dribeiro0 (David)          | David's \<Agent\>              | models, prompts, training/eval, inference service the API calls |
| flex     | /infra, /scripts, /tests, /.github, /pitch       | @sysoutprntlne (Jun), @YAOEI_LOGIN (Yaoei) | Jun's Claude, Jun's Codex, Yaoei's \<Agent\> | everything else: CI/deploy, integration + e2e tests, glue scripts, demo, pitch deck, cross-stream bug hunting |
| shared   | /docs, README.md, AGENT_BOARD.md                 | anyone                      | any                            | docs only, no code |

**API contract:** `/docs/api.md` is the single source of truth between frontend, backend and ai.
Anyone may *propose* a change there, but only the backend owner merges contract changes.

**Flex stream:** Jun and Yaoei pick up whatever is unowned or blocking. Before you start
something, claim it on the board (`claim: <task> | folders: <paths>`) so nobody duplicates it.
Flex agents may *not* edit /web, /api or /ai directly. They open a request instead (rule 2),
or, if the owner says "go ahead" on the board, they do it in a PR that the owner reviews.

## Protocol (every agent follows this)
1. Start of every task: run `scripts/board.sh new` and read what changed.
2. Only edit files inside your stream's folders (plus `shared` docs). If you need a
   change elsewhere, don't make it. Post a request that tags that stream's owner.
3. Work on a branch named `<stream>/<short-task>`. Open a PR, never push straight to main.
4. When you finish anything meaningful, post to the board:
   `scripts/board.sh post "<what changed> | files: <paths> | PR #<n> | needs: <@login or none>"`
5. When you're tagged, do the request, then reply and tag them back with the
   commit/PR and "re-review please".
6. Reviews: when you're asked to review a PR, check it out, review it, and post
   "approved" or "changes requested: <specific, file:line>" on the board.
7. When you're idle or waiting on someone, run `scripts/board.sh wait 540` in a loop
   until a mention arrives, then act on it.
8. Keep posts short and factual. One post per finished unit of work. Never paste secrets.

## Same-owner agents (Jun runs Claude + Codex)
- Each agent has its own tag: `Jun's Claude`, `Jun's Codex`. Set it in `.board/local.env`
  or per call: `AGENT_TAG="Jun's Codex" scripts/board.sh post "..."`.
  Read-state is stored per tag, so both agents see every message.
- Use separate git worktrees (`git worktree add ../artemis-codex`) so the two agents never
  share a working tree or branch.
- Always `claim:` a task before starting it. If the other agent already claimed it, pick another.
- To hand work to the other agent, tag `@sysoutprntlne` and start the post with
  `to: Jun's Codex` (or `to: Jun's Claude`). Both agents are woken by the mention;
  only the addressed one acts.
- Either agent can review the other's PR, but a human (Jun) merges.
