# Design debates: agent/ and policy/

Every implementation detail in David's area, argued both ways and settled, so the build has nothing
left to decide. The build steps are in [`agent-and-policy.md`](agent-and-policy.md); where the two
disagree, **this file wins**. Each verdict ends with what to build.

The test every decision has to pass: a blind developer on Windows with NVDA, on stage, on venue
Wi-Fi, must never be left in silence, never be surprised by a write or a run, and never be lied to
about a failure.

---

## A. Agent loop

### A1. Tool runner vs a hand-written loop
- **SDK tool runner** (`client.beta.messages.tool_runner` + `@beta_async_tool`). For: least code; the `claude-api` skill recommends it, and approval works by awaiting inside the tool function. Against: it's beta; the loop owns the message list, while we need one append-only conversation that lasts the whole session (A3); tests have to fake the runner's internals.
- **Manual loop.** For: we own `messages` exactly, which makes the session conversation, refusal and fallback handling, iteration caps and cancellation plain code; easy to test with a fake client; no beta dependency. Against: about 100 more lines.

**Verdict: manual loop.** The deciding reason is the session-long append-only conversation (A3).
Thinking blocks on Claude Opus 5.5 are only valid when sent back unchanged in the same
conversation, and a loop we own guarantees that. Build `ClaudeAgent.run()` as: call → append
`response.content` exactly as returned → run tool calls → append one user message with every
`tool_result` → repeat.

### A2. Streaming vs non-streaming
- **Streaming.** For: lower time to first token; the skill defaults to it for long outputs. Against: we never speak half-sentences, so the first token doesn't reach the user any sooner. It also needs `eager_input_streaming` handling and makes the test fakes harder.
- **Non-streaming `messages.create`.** For: simple; replies are short; `max_tokens=16000` stays well inside SDK timeouts. Against: a large file write arrives in one burst.

**Verdict: non-streaming.** Progress the user hears comes from our own deterministic tool-call
announcements (C3), not from model tokens. Revisit only if a request ever times out.

### A3. Conversation scope: per task or per session
- **Per task.** For: clean and small. Against: "now make it say goodbye" has no memory of the project the agent just made.
- **One conversation per session (append-only).** For: natural follow-ups; the prompt cache grows with it. Against: it grows; one bad turn stays in history.

**Verdict: per session.** `ClaudeAgent` keeps `self.messages` across tasks. Each task appends a user
message: the request plus a short task header (`<task id=… project=…>` and the current file tree,
A6). A spoken "start over" command (or a new session) resets it. No compaction this weekend. Never
edit or delete earlier messages: edits break preserved thinking.

### A4. `ask_user` tool vs ending the turn with a question
- **`ask_user` tool.** For: the task stays alive in `awaiting_reply`. Against: an extra contract path (`/tasks/{id}/reply`, a new state) for Jun and Avery to build.
- **End the turn with a question.** For: with a session conversation (A3), the user's next utterance *is* the answer; nothing new to build.

**Verdict: no `ask_user` tool.** A question is just the agent's final answer, and the client speaks it
like any other. The `/reply` endpoint and the `awaiting_reply` state stay in the contract unused
(additive, nothing breaks), so we can add them back if the demo needs it.

### A5. Model and effort
- **`claude-opus-5-5` at `medium` effort.** For: the strongest coding model we'd use, with fewer wrong tool calls on stage. Against: slower per turn.
- **`claude-sonnet-5-5`.** For: faster. Against: the team hasn't measured whether it's needed.

**Verdict: `claude-opus-5-5`, `effort: "medium"` set explicitly**, both read from env
(`ARTEMIS_MODEL`, `ARTEMIS_EFFORT`) so they're one-line switches. In M2, measure Hello World end to
end at `medium` and at `low`; if total voice-to-result is over about 15 s, try `low` first, then
discuss a model switch with David. Don't send `thinking` at all (it's always on for this model);
`max_tokens=16000`. Turn on the server-side refusal fallback the skill describes. Put top-level
`cache_control` on the request (the system prompt and tools are frozen).

### A6. How much project context to send
- **A full snapshot (tree + file contents) every task.** For: no read calls. Against: in a session conversation it re-sends the same files every turn.
- **Tree only; contents through `read_file`.** For: small; reads are visible ("Reading main.py") and wrapped as untrusted.

**Verdict: tree only** (≤ 200 entries; skip `.git`, `__pycache__`, `.venv`, `node_modules`), inside
the task header. Contents only through `read_file`.

### A7. Parallel tool calls
- **Run them concurrently.** Against: two approval dialogs at once is unusable with a screen reader.
- **Disable parallel tool use.** Against: slower when it reads several files.

**Verdict: allow parallel calls from the model, but execute them sequentially in the order given.**
All results go back in one user message, as the API expects. Writes are already batched (B1), so
there's at most one approval per kind per turn.

### A8. Loop limits and stop reasons
**Verdict:** at most 12 model calls per task. On `end_turn` → `Answered(text)`. On `refusal` →
`Failed("refused")` with a spoken "I can't help with that request." On `max_tokens` →
`Failed("too_long")` with "That got too long. Try a smaller request." Unknown tool → `tool_result`
with `is_error: true`. Typed SDK errors, most specific first: authentication → "The Claude API key
is missing or invalid" (never echo the key); rate limit or overloaded → speak "Claude is busy,
retrying", wait 2 s, retry once; connection error → "No internet connection to Claude" and
`Failed`. The SDK's own retries stay at the default 2.

### A9. Final answer: free text vs structured output
- **Structured output (`{spoken, detail}`).** For: guarantees a short spoken line. Against: mixing structured output with tool use adds schema work and failure modes.
- **Free text with style rules + a sanitizer.** For: simple. Against: models still slip in markdown.

**Verdict: free text.** The system prompt asks for at most 3 short sentences, leading with the
outcome. `agent/speech_text.py` strips markdown (backticks, asterisks, headings, bullets, links → link
text) before the text goes into `announce`. The unsanitized text goes in the event's `data` for the
log.

---

## B. Tools

### B1. One write per file vs batched writes
- **`write_file` per file.** Against: three files means three approvals.
- **`write_files {files: [{path, content}]}`.** For: one approval per change, "Create 3 files: main.py (12 lines), …", which matches how a person reviews.

**Verdict: `write_files`.** Overwriting an existing file is allowed only inside a project created in
this session (the M3 "fix" path), and the approval then says "modify" with a diff. `edit_file`
(search/replace) arrives in M3 for everything else.

### B2. `create_project`: its own approval, or folded into the first write
- **Separate approval.** Hello World would need three Enter presses (create, write, run).
- **Fold it in.** `create_project(name)` only reserves the slug and path (no disk change) and returns them. The folder is created by the first approved `write_files`, whose summary says "in the new project hello-world".

**Verdict: fold it in.** Name collisions get `-2`, `-3`. The root is `ARTEMIS_PROJECTS_DIR`, default
`%USERPROFILE%\ArtemisProjects`.

### B3. Final tool set
| Tool | Approval | Notes |
|---|---|---|
| `create_project {name}` | no (reserves only) | sets the session's current project |
| `open_project {name}` | no | picks an existing project folder under the root |
| `list_files {dir?}` | no | ≤ 200 entries |
| `read_file {path, start_line?, end_line?}` | no | ≤ 1 MB; returns numbered lines, wrapped as untrusted |
| `write_files {files}` | `write_files` | scanned *before* approval (D2) |
| `run_program {entry, args?}` | `run_program` | scanned again before approval |
| `edit_file {path, edits}` | `write_files` | M3 |

No shell, no delete, no network, no install, no `ask_user`. Every argument model is strict
(`extra="forbid"`), and the JSON Schema sent to the API is generated from the same Pydantic models
with `strict: true`.

### B4. What a tool result looks like to the model
**Verdict:** a JSON string, `{"ok": true, "data": …}` or `{"ok": false, "error": "<code>", "message": "…"}`.
Any text that came from files or programs sits inside `data` as
`<untrusted source="file:main.py">…</untrusted>`. Output is truncated to 8 KB, keeping the head and
tail with a `[… N bytes cut …]` marker, and redacted (D4). Error codes are fixed: `invalid_args`,
`path_rejected`, `rejected_by_user`, `invalidated`, `scan_blocked`, `timeout`, `not_found`.

---

## C. Approvals and announcements

### C1. What an approval binds to
**Verdict (unchanged from the docs):** `sha256` over canonical JSON. For writes: `workspace_id` plus a
list sorted by path of `(path, sha256(old or ""), sha256(new))`. For runs: `(entry, args,
sha256(entry file))`. The gateway re-hashes right before executing. A mismatch emits
`approval.resolved by=invalidated` and one fresh request; a second mismatch fails the call with
`invalidated`.

### C2. How a high-severity finding blocks
- **Hard block (can't run at all).** Against: the user is the developer; Artemis shouldn't overrule them.
- **Soft block with explicit override.** The approval carries `blocking: true`; the summary starts "Blocked: 1 high-severity issue …"; the client's default action is Reject, and approving needs a distinct key (Jun picks it, e.g. Shift+Enter).

**Verdict: soft block.** This is the only contract change: `blocking: bool = false` on `Approval`. It's
additive, so nothing Jun has built breaks.

### C3. Who writes the spoken text
- **The model narrates.** Against: it can soften a failure, and it varies between runs.
- **The gateway writes it deterministically, and the model explains after.**

**Verdict: deterministic.** Before each tool call, a `low` priority line such as "Reading main.py" or
"Running main.py". On `run.result`: `high` priority, "main.py ran successfully. It printed: …"
(the first 200 characters), or "main.py failed with exit code 1. <last non-empty stderr line>." The
model's explanation follows as a normal-priority `agent.message`. That satisfies "spoken responses
must not conceal important errors" by construction.

### C4. Approval summaries
**Verdict:** written by the gateway from the tool arguments, not by the model. For example: "Create
project hello-world with 1 file: main.py, 1 line. No security findings. Enter to approve, Escape to
reject." Files are listed by name and line count; content only through the diff navigator.

---

## D. Security

### D1. Path policy (Windows)
**Verdict:** `safe_resolve(root, rel)`:
- reject absolute paths, drive-relative paths (`C:foo`), UNC paths and `\\?\`;
- reject any component containing `:` (alternate data streams), names ending in `.` or a space, reserved device names (with or without an extension), `.git`, and `.env*`;
- resolve, then compare `os.path.normcase` strings with a trailing separator (Windows is case-insensitive, and a plain `startswith` would match `root2`);
- writes only to an extension allowlist: `.py .txt .md .json .toml .csv .html .css .js`. That rejects `.bat .cmd .ps1 .vbs .exe .dll .lnk .reg` and files with no extension;
- 256 KB per write, 1 MB per read, 20 files per batch.

### D2. When to scan
- **Before runs only.** Against: by then the user has already approved writing the dangerous code.
- **Before the write approval and again before the run.** For: the user hears the risk before anything touches disk, which is the Security Guardian story.

**Verdict: both.** Write the proposed files to a temp folder *outside* the workspace, scan it, include
the findings in the write approval, then delete the temp folder. Scan the real files again before
`run_program`, since they may have changed.

### D3. Which scanner
- **Bandit.** For: pure Python, pip-installs on Windows, deterministic. Against: Python only.
- **Semgrep.** For: many languages, well known. Against: its Windows support is newer, and registry rules need the network.
- **A Claude security review.** For: explains in plain language. Against: not deterministic, so it can't be the gate.

**Verdict: Bandit is the gate; Claude explains.** Run Bandit as a subprocess
(`python -m bandit -r <dir> -f json -q`, 20 s timeout, minimal env) and map severity × confidence to
`high` / `medium` / `low`. The agent gets the findings as a tool result and explains them in plain
words. Semgrep with rules stored in the repo is an M3 stretch, behind the same `scan()`.

### D4. Secrets
**Verdict:** `redact()` runs on every event text, every tool result going to the model, and every log
line. It replaces with `[redacted]`: the literal values of any `ANTHROPIC_*`, `*_TOKEN`, `*_KEY` or
`*_SECRET` env var (6+ characters); `sk-ant-[A-Za-z0-9_-]+`; `Bearer \S+`; PEM private-key blocks;
`(?i)(api[_-]?key|password|secret)\s*[:=]\s*\S+`. `.env*` files are unreadable (D1). Generated code
that hard-codes a password is caught by Bandit (B105/B106).

### D5. Prompt injection
- **A classifier** (a second model call or regex) that flags injected instructions. Against: extra latency and false positives, and it isn't the real defense.
- **Structural defense + honest narration.** Untrusted wrapping (B4); no dangerous tools exist; every write and run needs approval bound to the exact content; the system prompt tells the model to mention instructions it finds in data and not follow them.

**Verdict: structural, no classifier.** For the pitch, a demo file contains an instruction to delete
everything. The agent mentions it and ignores it, and the gateway shows it has no tool that could do
it anyway.

### D6. Running user programs (the contract for Avery's runner; the gateway sends this spec)
**Verdict:**
- the interpreter is found once at startup: `uv python find` → `py -3` → fail with a spoken message. Never the core's own Python (`sys.executable`);
- argv `[python, "-I", "-X", "utf8", entry, *args]`, `stdin=DEVNULL` (an `input()` call gets end-of-file instead of hanging);
- env limited to `SYSTEMROOT`, `TEMP`, `TMP`, `PATH` (interpreter dir + System32), and `PYTHONIOENCODING=utf-8`;
- 10 s timeout, kill the whole process tree, 64 KB output cap;
- run it with `subprocess.run` inside `asyncio.to_thread`, **not** `asyncio.create_subprocess_exec`. On Windows, uvicorn's `--reload` switches to an event loop that can't start subprocesses, and that would fail on stage;
- if stderr contains `EOFError`, the announcement adds "The program asked for input, which isn't supported yet."

---

## E. Engineering

### E1. Contracts ownership right now
**Verdict:** David writes `server/artemis/contracts/` v0 in M1 (after posting a claim on the board),
including C2's `blocking` field and the B3 tool models. Avery reviews on arrival. `docs/contracts.md`
is updated in the same PR, and a board post tells `@sysoutprntlne` about the one additive field.

### E2. Configuration
**Verdict:** a frozen dataclass reading env vars, with no new dependency: `ARTEMIS_AGENT=stub|claude`
(default `stub` until M2 is done), `ARTEMIS_MODEL`, `ARTEMIS_EFFORT`, `ARTEMIS_PROJECTS_DIR`,
`ARTEMIS_MAX_ITERATIONS=12`. `ANTHROPIC_API_KEY` is read only by the SDK and never stored on our objects.

### E3. Tests
**Verdict:** pytest, offline by default, under 3 s. The fake client returns real SDK `Message` objects
built with `Message.model_validate({...})`, scripted per test. Required cases:
- happy path event order;
- rejected write leaves disk untouched;
- path escapes (one test per D1 rule);
- invalidated approval;
- cancel during the approval wait writes nothing;
- `refusal`, `max_tokens`, unknown tool, malformed args;
- Bandit finding → `blocking` approval;
- `redact` on every pattern;
- markdown sanitizer;
- conversation is append-only across two tasks (assert earlier messages are byte-identical).

One `@pytest.mark.live` test (skipped without an API key) runs the real Hello World.

### E4. Async and cancellation
**Verdict:** everything in `agent/` and `policy/` is `async`. Cancel = the core cancels the task's
coroutine. The gateway catches `CancelledError` only to emit "Canceled. Nothing was written" (or
the list of files already written), then re-raises. The approval wait is the cancel point, so no write
is ever half-done.

### E5. Logging
**Verdict:** no logging framework. The core's event log (JSONL) is the record. `agent/` and `policy/`
emit events; debug text goes through `logging.getLogger("artemis.agent")` after `redact()`.

---

## Contract changes these verdicts make (all in M1's PR)

1. `Approval.blocking: bool = false` (additive).
2. §3.2 tool table → B3 (`write_files` replaces `write_file`; `open_project` added; `ask_user` and `scan` removed as model tools; the scan runs inside the gateway).
3. `/tasks/{id}/reply` and `awaiting_reply` are marked "reserved, unused in MVP".
4. §3.4 runner spec → D6.
