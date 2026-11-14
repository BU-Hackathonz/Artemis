# Artemis contracts

Status: **agreed and implemented** as Pydantic models in `server/artemis/contracts/`, with the
samples below as JSON fixtures that every stub is tested against. Avery owns the contract.
Change the contract first, then the code.

Conventions: JSON, `snake_case`, ids are strings (`ses_…`, `tsk_…`, `apr_…`, `op_…`), times are
ISO 8601 UTC, and workspace paths are always **relative** to the project root.

## 1. Client → core (HTTP, `127.0.0.1` only)

Every request sends `Authorization: Bearer <launch token>`. `infra/start.ps1` generates the token
and passes it to both processes (§6).

Errors outside the table: a missing or wrong token is `401 {error: "unauthorized"}` and an unlisted
`Origin` is `403 {error: "origin_not_allowed"}` (§6). An unknown id is `404` in FastAPI's
`HTTPException` shape, `{detail: {error: "session_not_found"}}` (or `"task_not_found"`,
`"approval_not_found"`), and a body that fails its model is `422` with FastAPI's
`{detail: [...]}`. The `409` and `501` bodies in the table are flat, as written.

| Method | Path | Body → Response | Notes |
|---|---|---|---|
| GET | `/health` | → `{ok, version, voice_ready, screen_reader}` | `screen_reader`: `"nvda"` or `null` |
| POST | `/sessions` | `{}` → `{session_id}` | One session per app launch |
| GET | `/sessions/{sid}/events` | SSE stream of `Event` (§2) | Replays every kept event after `Last-Event-ID` (the last 5000; none or not a number replays from the start), then live |
| POST | `/sessions/{sid}/tasks` | `{client_request_id, source: "voice"\|"keyboard", text}` → `{task_id}`, or `409 {error: "task_active", task_id}` | Idempotent per `client_request_id` (a repeat gets the same `task_id`, even while that task is active); one active task per session (§5.2) |
| POST | `/tasks/{tid}/reply` | `{text, source}` → `501 {error: "not_supported"}` for a known task (the body is not read) | **Reserved, unused in the MVP.** The agent asks a question by ending its turn; the user's next request is the answer |
| POST | `/approvals/{aid}` | `{decision: "approve"\|"reject", approval_hash}` → `204`, or `409 {error: "stale"}` | The hash must match the one that was offered. Stale: a different hash (the approval stays open), or one already decided, canceled or invalidated |
| POST | `/tasks/{tid}/cancel` | `{}` → `204`, also for a task that already ended | Stops active work, including a running program (its whole process tree is killed). An `agent.message` at high says what was already applied ("Canceled. Already written: main.py."), then `task.state` `canceled` |
| POST | `/voice/listen` | `{action: "start"\|"stop"}` → `204` | Push-to-talk down/up; `start` stops current speech first (§5.1); the transcript arrives as events. Always the newest session (one per app launch): `409 {detail: {error: "no_session"}}` before there is one, `422 {detail: {error: "bad_action"}}` for any other action. A microphone failure is an `error` event with code `mic_error` |
| POST | `/speech` | `{action: "stop"\|"repeat"}` → `204` | Stop speaking / repeat the last announcement (the last one at `normal` or `high`). Any other action: `422 {detail: {error: "bad_action"}}` |
| POST | `/sessions/{sid}/diagnostics` | `{code: "shortcut_registration_failed", shortcut?: "Control+Alt+Space"}` → `204` | A problem only the client can see. The core speaks a **fixed** sentence per code (`error` event, priority high), once per code and shortcut per session (the client announces an exact repeat itself); `shortcut` allows letters, digits and `+` only, 1 to 40 characters, and other fields are rejected (`422`). No free text is ever spoken. While `voice_ready` is true the client doesn't also announce it (§5.3) |
| POST | `/sessions/{sid}/ready` | `{shortcut?: "Control+Shift+Space"}` → `204` | The native app finished setting up for this session (after its push-to-talk shortcut registered, or failed); the browser build does not call it. The core speaks one **fixed** line, once per session, as `agent.message` (`kind: "answer"`) with no `task_id` at priority normal: "Artemis is ready. Hold Control Shift Space and speak, or type a request." Without `shortcut` it names the Start listening button instead: "Artemis is ready. Use the Start listening button and speak, or type a request." `shortcut` allows letters, digits and `+` only, 1 to 40 characters, and other fields are rejected (`422`); no free text is ever spoken |

## 2. Core → client: the event stream

One envelope for everything. The client renders every event's `announce.text` in the log
region. **Speech happens in the core** (`announce()` → NVDA or SAPI), so the client's live region
stays silent while the core can speak, and turns on whenever it can't (§5.3).

```jsonc
{
  "seq": 42,                       // SSE id; strictly increasing per session
  "session_id": "ses_1",
  "task_id": "tsk_7",              // null for session-level events
  "type": "task.state",            // see the table below
  "at": "2026-11-14T15:02:11Z",
  "announce": { "text": "Working on: create a Python program that prints Hello World", "priority": "normal" },  // or null
  "data": { }                      // type-specific, below
}
```

`priority`: `low` (progress, may be dropped if speech is busy), `normal` (queued), `high`
(interrupts: approval requests, run results, errors, cancel). The agent's final answer is `normal`.
`repeat` says the last `normal` or `high` line again.

| `type` | `data` |
|---|---|
| `transcript.partial` / `transcript.final` | `{text, client_request_id, intent?}`. `text` is the transcript exactly as the voice returned it (the voice strips a leading "Artemis" or "Hey Artemis"; the core never rewrites it). `transcript.final` is announced "Heard: …" at normal (an empty one is silent). `intent` is set when the conversation controller is on and the text isn't empty (§5.4); otherwise null. `transcript.partial` is reserved: the core never emits it (Whisper transcribes once, on release) |
| `task.state` | `{state, reason?}`, where `state` ∈ `queued, working, awaiting_reply, awaiting_approval, running, succeeded, failed, canceled`. `awaiting_reply` is reserved and unused in the MVP. Spoken: `queued` ("Working on: …", normal), `running` ("Running main.py.", low) and `canceled` ("Canceled.", high); the others are silent. `failed` follows an `error` event that speaks the reason ("The task failed. … You can try again.") and carries the failure code as `reason` |
| `agent.message` | `{text, kind: "answer"\|"question"\|"progress"}`. The agent's answers are `normal` and its progress `low`; the core's "Still working." heartbeat (after about 3 s of silence while `working` or `running`) is `progress` at `low` |
| `approval.requested` | `Approval` (below), announced with its `summary` at high, right after `task.state` `awaiting_approval` |
| `approval.resolved` | `{approval_id, decision: "approve"\|"reject", by: "user"\|"invalidated"}`, not announced. An invalidated one (files changed, or the task was canceled) carries `"reject"` |
| `file.changed` | `{op_id, path, action: "created"\|"modified"\|"deleted", lines_added, lines_removed}`, announced "Created main.py, 1 line." at normal. No tool deletes, so `deleted` is never sent today |
| `run.result` | `{op_id, preset, exit_code, timed_out, duration_ms, stdout_tail, stderr_tail, truncated}`, announced at high by the gateway (§4 samples 4 and 5). `exit_code` is null when the run timed out |
| `security.findings` | `{op_id, tool: "bandit"\|"semgrep", findings: [{rule, severity, path, line, message}]}`, not announced: the findings are spoken in the approval summary that follows, or in `scan_project`'s own line. Sent only when there are findings. `tool` is always `"bandit"` today (Semgrep is not implemented) |
| `error` | `{code, message, recovery}`, where `recovery` is one sentence the user can act on. Announced at high as "message recovery", except a failed task's, which says "The task failed. <reason> You can try again." |
| `voice.status` | `{voice_ready, screen_reader}`, emitted at session start and whenever either value changes (§5.3) |

```jsonc
// Approval
{
  "approval_id": "apr_3",
  "kind": "write_files",          // or "run_program"
  "approval_hash": "sha256:…",    // see "What the hash binds" below
  "summary": "Create project hello-world with 1 file: main.py, 1 line. No security findings. Enter to approve, Escape to reject.",
  "files": [{ "path": "main.py", "action": "created", "lines_added": 1, "lines_removed": 0 }],
  "diff": "--- /dev/null\n+++ main.py\n@@ -0,0 +1 @@\n+print(\"Hello, World!\")\n",
  "run": null,                    // for run_program: {"preset": "python", "entry": "main.py", "timeout_s": 10}
  "findings": [],                 // security findings that apply to this approval
  "blocking": false               // true when a high-severity finding applies (default false)
}
```

The summary is written by the gateway, not the model. Without a scanner installed it says "Not
scanned: the security scanner isn't installed." instead of "No security findings."
When `blocking` is true the summary starts with "Blocked: …" and ends "Escape to reject, or use
the override key to approve anyway.", the client's default action is Reject, and approving needs a
distinct key (not plain Enter), so a risky write or run is never approved by reflex. The user can
still override: Artemis doesn't overrule the developer.

What the hash binds (`policy/hashing.py`): `"sha256:"` + SHA-256 of canonical JSON (sorted keys,
no spaces) of `{kind, workspace_id, payload}`, where `workspace_id` is the project slug. For
`write_files` and `edit_file` the payload is, per file and sorted, `[path, sha256(bytes on disk, or
empty for a new file), sha256(new content)]`; for `run_program` it is `[entry, args,
sha256(entry file bytes)]`. The diff and the run preset are not hashed: the diff follows from the
two content hashes, and the preset from the entry.

Rules: the core never sends two events with the same `seq`; the client drops any `seq` it has
already seen. When the user approves, the gateway recomputes the hash from the disk. If the files
under the approval changed meanwhile, it emits `approval.resolved` with `decision: "reject"`,
`by: "invalidated"` and a fresh `approval.requested`; a second change fails the tool call with
`invalidated` and nothing is applied. A reject is not rechecked. Canceling the task while an
approval is open also resolves it with `by: "invalidated"`.

## 3. Inside the core (Python interfaces, not HTTP)

These are the seams between owners. Each one gets a stub on day one.

### 3.1 Orchestrator ↔ agent (Avery ↔ David)

```python
class Agent(Protocol):
    async def run(self, task: TaskContext, tools: ToolGateway, emit: Emit) -> AgentOutcome: ...

@dataclass
class TaskContext:
    task_id: str
    request: str                 # the final transcript or typed text
    project: ProjectRef | None   # root path + slug; None until one is created or selected
    history: list[TurnSummary]   # earlier tasks in this session, short
    style: Style                 # {verbosity: brief|normal|detailed, focus: none|visual|code}; defaults unless §5.4 is on

AgentOutcome = Answered(text) | Failed(code, message) | Canceled()
```

The agent never touches files or processes directly; it only calls `tools`. Canceling a task
cancels the coroutine (`asyncio.CancelledError`). `emit(agent.message …)` reports progress.

### 3.2 Agent → policy gate → tools (David ↔ Avery)

```python
class ToolGateway(Protocol):
    async def call(self, task_id: str, name: str, args: dict) -> ToolResult: ...
```

`call()` validates `args` against the tool's Pydantic model, checks policy, and if the tool needs
approval it emits `approval.requested` and **awaits** the user's decision (an `asyncio.Future`
resolved by `POST /approvals/{aid}`). Then it validates again and executes. A rejection returns
`ToolResult(ok=False, error="rejected_by_user")` (`"scan_blocked"` when the rejected approval was
blocking), which the agent explains instead of retrying.
A security scan runs inside the gateway before each write approval (on the proposed files, in a
temp folder outside the workspace) and again before each run approval; its findings go into the
`Approval`.

| Tool | Args | Approval | MVP |
|---|---|---|---|
| `create_project` | `{name}` | no: it only reserves the name; the folder is created by the first approved `write_files` | yes |
| `open_project` | `{name}` | no | yes |
| `list_files` | `{dir?}` | no | yes |
| `read_file` | `{path, start_line?, end_line?}` | no | yes |
| `write_files` | `{files: [{path, content}]}`: one approval for the whole batch. Overwrites only in a project created this session | yes, after a scan | yes |
| `run_program` | `{entry, args?: [str]}`; the preset comes from the file type | yes, after a scan | yes |
| `edit_file` | `{path, edits: [{old, new}]}`: each `old` must match exactly once; works in any project, not only one created this session. Approved as a `write_files` approval | yes, after a scan | after MVP (built) |
| `inspect_page` | `{path}`: an `.html` or `.htm` file in the project | no: read-only | Echo Check |
| `scan_project` | `{path?}`: a file or folder in the project; omitted scans all of it | no: read-only | after MVP (built) |

There is no shell, delete, network or install tool, and the agent can't apply anything without
approval. `ask_user` is not a model tool: a question is the agent's final answer. The scan
before a write or a run is not a model tool either; it always runs inside the gateway.
`scan_project` is the same Bandit scan when the developer asks for one ("check my code for
security problems"). It changes nothing, emits `security.findings`, and the gateway speaks the
counts and the worst finding. It fails with `scan_unavailable` when the scanner is missing or
crashes, and says so when there are no Python files, so it never reports unscanned code as clean.
Paths are rejected unless `Path(root / p).resolve()` stays under `root`.

### 3.3 Voice (Yaovi ↔ Avery)

```python
class Voice(Protocol):
    async def start_listening(self) -> None: ...
    async def stop_listening(self, client_request_id: str) -> str: ...   # final transcript, wake word stripped; no partials
    def set_vocabulary(self, words: list[str]) -> None: ...               # file names and symbols for the prompt
    async def announce(self, text: str, priority: Priority) -> None: ...  # NVDA if running, else SAPI
    async def stop_speech(self) -> None: ...
    async def repeat_last(self) -> None: ...
```

The event bus calls `announce()` for every event that has an `announce` field, so no other
component speaks directly. The core also uses these when a voice has them (none are required):
`warm()` (load the speech model at startup), `announcer` (`status()` for `voice_ready` and
`screen_reader`, `watch()` for `voice.status` changes, `current().busy()`), `set_rate(delta)`
(§5.3) and `working_cue()` (replaces the "Still working." line; no voice has one yet).

### 3.4 Runner (Avery, infrastructure)

```python
class Runner(Protocol):
    def create_project(self, name: str) -> ProjectRef: ...          # %USERPROFILE%\ArtemisProjects\<slug>, git init
    def write_file(self, project: ProjectRef, path: str, content: str) -> FileChange: ...
    async def run(self, project: ProjectRef, entry: str, args: list[str], timeout_s: int = 10) -> RunResult: ...
```

`run()`:
- finds the interpreter once per runner, on first use (the core probes it in the background when
  the first session starts and speaks `error` `no_interpreter` if there is none): `ARTEMIS_RUN_PYTHON`
  if set, else `uv python find --system` or `--no-project`, else `py -3` on Windows, else `python3`
  or `python` on PATH; never the core's own Python (`sys.executable`) or anything in its venv; if
  none is found, fails with a spoken message;
- uses a fixed argv `[python_exe, "-I", "-X", "utf8", entry, *args]`, `shell=False`,
  `cwd=project.root`, `stdin=DEVNULL` (an `input()` call gets end-of-file instead of hanging);
- passes an explicit env, never `os.environ`: `PYTHONIOENCODING=utf-8` and `PATH` (interpreter dir,
  then System32 on Windows or `/usr/bin` and `/bin` elsewhere); on Windows also `SYSTEMROOT`, and
  `USERPROFILE`, `LOCALAPPDATA`, `APPDATA`, `TEMP`, `TMP` when set; elsewhere `HOME` and `TMPDIR`
  when set; no API keys;
- has a 10 s timeout, kills the whole process tree (`taskkill /T /F` on Windows, the process group
  elsewhere), and keeps the last 64 KB of each of stdout and stderr;
- kills the whole process tree too when the task is canceled during the run, before the cancel
  is reported;
- runs `subprocess.Popen` on a worker thread (`asyncio.to_thread`), not
  `asyncio.create_subprocess_exec` (on Windows, uvicorn's `--reload` event loop can't start
  subprocesses).

Child processes the core starts for itself (`git init`, the interpreter probe, Playwright's
browser for Echo Check) get `os.environ` without any secret-named variable: a name starting
`ANTHROPIC_` or ending `_TOKEN`, `_KEY` or `_SECRET` (`artemis/childenv.py`). Bandit gets only
`SYSTEMROOT`, `TEMP`, `TMP`, `USERPROFILE`, `LOCALAPPDATA` and `APPDATA`. Playwright's Node driver
has no `env` option, so those variables are taken out of `os.environ` only while it starts, then
put back.

## 4. Sample payloads (the first fixtures)

1. **Typed request:** `POST /sessions/ses_1/tasks`
   `{"client_request_id":"cr_1","source":"keyboard","text":"create a Python program that prints Hello World"}`
2. **Write approval:** the `Approval` example above, then `POST /approvals/apr_3 {"decision":"approve","approval_hash":"sha256:…"}`
3. **Rejection:** the same with `"reject"`. Expect `approval.resolved` (`by: "user"`), `task.state` `working`, then `agent.message` "Okay, I didn't create anything.", then `task.state` `succeeded`, and no `file.changed` event. (The fixture lists the three events the client acts on, without `working`.)
4. **Failed run:**
   ```json
   {"type":"run.result","announce":{"text":"main.py failed with exit code 1. NameError: name 'pritn' is not defined.","priority":"high"},
    "data":{"op_id":"op_9","preset":"python","exit_code":1,"timed_out":false,"duration_ms":81,
            "stdout_tail":"","stderr_tail":"Traceback (most recent call last):\n  File \"main.py\", line 1 ...\nNameError: name 'pritn' is not defined","truncated":false}}
   ```
   The announce is the exit code and the last line of `stderr_tail`, verbatim (on Python 3.12 that
   line also ends "Did you mean: 'print'?").
5. **Successful run:** `exit_code` 0, `stdout_tail` `"Hello, World!\n"`, announce "main.py ran successfully. It printed: Hello, World!"
6. **Security block (Phase 4):** `security.findings` (not announced) with `{"rule":"B602","severity":"high","path":"main.py","line":4,"message":"subprocess call with shell=True"}`, then `task.state` `awaiting_approval`, then `approval.requested` for `run_program` listing that finding, `blocking: true`, announced "Blocked: 1 high-severity issue, main.py line 4, subprocess call with shell=True. Run main.py with Python. Escape to reject, or use the override key to approve anyway."

`contracts/test_samples.py` checks that the spoken text in samples 2, 4, 5 and 6 is what the
gateway writes for that data.

## 5. Turn-taking rules (voice and tasks)

### 5.1 Push-to-talk cuts off speech
On `POST /voice/listen {action: "start"}`, the core only calls `voice.start_listening()`. That
call does the whole sequence: stop current speech (SAPI purge, or NVDA's `cancelSpeech`), play the
"listening" earcon to the end, then open the mic. `stop_listening()` plays the "stopped" earcon and
closes the mic. Only when the opt-in post-roll is enabled (`ARTEMIS_PTT_POST_ROLL`, seconds, e.g. 0.35;
default 0, off) does it keep the mic open that long (the end of the last word), close it, then play the earcon.
The core doesn't call `stop_speech()` itself here. Artemis's own voice (SAPI through the speakers)
must never be recorded and transcribed as a request. The client's own "Stop speech" control is
unchanged. Cutting off speech never hides an open approval: see §5.4.

### 5.2 One active task per session
A task is active from `queued` until `succeeded`, `failed` or `canceled`. The spoken rules below
are the default; `ARTEMIS_CONVERSATION=on` replaces them with §5.4 (bare "stop" then stops speech,
not the task, and a spoken request during a task waits its turn).
- **Typed request while a task is active:** `POST /sessions/{sid}/tasks` returns
  `409 {error: "task_active", task_id}`. No second task starts.
- **Spoken request while a task is active:** the core still emits `transcript.final` (so the text
  stays in the editable field) but doesn't create a task. It emits `error`
  `{code: "task_active", message, recovery}` with `announce` at `high`:
  "Still working on the last request. Say cancel to stop it, or wait."
- **Spoken control words.** The core checks the final transcript (the voice has already stripped a
  leading wake word) after lowercasing and replacing punctuation with spaces. Only a whole-utterance match counts, and no model is
  involved:

  | Utterance | While a task is active | No active task |
  |---|---|---|
  | `cancel`, `stop`, `cancel that`, `never mind`, `nevermind` | same as `POST /tasks/{id}/cancel` | "Nothing to cancel." |
  | `repeat`, `say that again` | same as `POST /speech {action: "repeat"}` | same |
  | `approve`, `yes`, `reject`, `no` while `awaiting_approval` | not applied; announce "Use the keyboard to decide: Enter on the dialog to approve, Escape to reject." | treated as a normal request |

  Approvals stay keyboard-only in the MVP: a misheard "yes" must never write or run anything.

### 5.3 Speech availability can change at runtime
`voice_ready` and `screen_reader` can change while the app runs: NVDA starts or stops, or NVDA is
running but its controller DLL is missing, which makes the core silent. The core emits
`voice.status` at session start and on every change. The client turns its log's live region on
(`aria-live="polite"`) whenever `voice_ready` is false and off when it's true, instead of deciding
once from `/health` at connect.

What the developer can change by voice depends on who is speaking. With SAPI, "stop talking" stops
only Artemis. With NVDA, Artemis speaks through NVDA's controller, so stopping also cuts whatever
NVDA itself was saying, and Artemis can't change NVDA's speaking rate: "slower" is answered with a
pointer to NVDA's own rate setting. The controller knows which applies from `voice.status`.
On SAPI the rate is the user's Windows setting unless `ARTEMIS_SAPI_RATE` replaces it, clamped to
-3..6. Conversation's "faster" and "slower" change only the SAPI rate, one step at a time, and say
so when a limit is reached.

### 5.4 Conversation (behind `ARTEMIS_CONVERSATION=on`, default off)
With the flag off, §5.1 and §5.2 apply exactly as written above. With it on, every final
transcript goes to `artemis.conversation.Controller.decide()`, which is deterministic (no model,
no audio) and returns what the core should do. `transcript.final` always carries the transcript
as the voice returned it, plus `intent`: `"request"` or the built-in phrase's name (`cancel`,
`stop_speaking`, `repeat`, `status`, `changed_files`, `brief`, `detailed`, `normal`, `visual`,
`code`, `slower`, `faster`, `approval_word`, or `wake_word`); `intent` is null for an empty
transcript.

- **Built-in phrases.** Matched only when the phrase is the whole utterance, after the wake word,
  fillers and "please" are stripped from the edges: "stop the server from crashing" goes to the
  agent. Everything that isn't one of these goes to the agent in the developer's exact words.

  | Phrase (examples) | What happens |
  |---|---|
  | `stop`, `stop talking`, `be quiet` | Stops speech only; the task keeps going. With a task active and no approval open, the core adds "Say cancel to stop the task." unless speech was cut off |
  | `cancel`, `cancel the task`, `never mind` | Drops the waiting follow-up if there is one ("Dropped: …"); otherwise cancels the active task; otherwise "Nothing to cancel." |
  | `repeat`, `say that again` | With an approval open, its summary again at `high`; otherwise the last announcement |
  | `what are you doing`, `status`, `are you done` | A fixed sentence from task state. No task is created. The "Still working." heartbeat is never reported as the last step |
  | `which file did you change`, `what changed` | The files changed by the current or last task |
  | `keep it brief`, `deep dive`, `normal mode`, `visual mode`, `code mode` | Sets `TaskContext.style` for the following tasks and confirms it |
  | `slower`, `faster` | See §5.3 |
  | `approve`, `yes`, `reject`, `no` while `awaiting_approval` | Never applied: "Use the keyboard to decide…" as in §5.2 |
  | `approve`, `yes`, `reject`, `no` with no approval open | If no task is active and the agent's last answer was a question: the answer to it, sent as the next request (design A4; anything it leads to is still approved on the keyboard). Otherwise "There's nothing to approve right now.", never a task or a follow-up |
  | Nothing (an empty push-to-talk tap) or only the wake word (`Hey Artemis`) | Never a task. Silent, unless an approval is open: then its summary again, once, at `high`. The voice already strips "Artemis" and "Hey/OK Artemis", so those arrive empty (`intent` null); `wake_word` is the intent for a form only the controller strips, such as "Hi Artemis" |

  No phrase can approve a write or a run, hide a warning, or run code.
- **A spoken request while a task is active** is kept as the one waiting follow-up ("I'll do that
  next.") instead of the `task_active` error. A second one doesn't replace it: "I already have one
  waiting: …. Say cancel to drop it." When the active task succeeds, the follow-up is started as
  a normal task, announced with "Starting the follow-up." and then read back by the usual
  "Working on: …". When it fails or is canceled, the follow-up is
  dropped and the core says so. A typed request still gets `409 task_active`.
- **An interruption never hides an open approval** (adds to §5.1). While an approval is open,
  `stop`, the changed-files and answer-style phrases, `slower`/`faster` and `cancel` of a waiting
  follow-up add "A decision is still waiting. Say repeat to hear it again."; `repeat`, `status`,
  an empty tap and the wake word alone say the approval summary itself; approval words get
  "Use the keyboard to decide…"; a queued request adds "A decision is still waiting".
- **Errors.** If handling an utterance fails (for example the voice can't change its rate), the
  core speaks an `error` with code `conversation_failed` and the session keeps working.
- **Agent prompt.** With the flag on, the agent's system prompt also gets a "How to read a spoken
  request" paragraph (`artemis.agent.prompts.system_prompt(True)`). With it off, the system prompt
  is unchanged.
- **A clarifying question ends the task** (design A4 stands): the developer's next utterance is a
  new task in the same agent conversation. `/tasks/{tid}/reply` and `awaiting_reply` stay reserved.

## 6. Launcher and launch token (decided 2026-11-14)

**`infra/start.ps1` (Avery)**
1. Generates a 32-byte random token (base64url) and picks a free port on `127.0.0.1`.
2. Starts the core with `ARTEMIS_TOKEN` in its environment
   (`uv run uvicorn ... --host 127.0.0.1 --port <port>`).
3. Waits until `GET /health` with the token answers.
4. Launches the Tauri app with `ARTEMIS_CORE_URL` and `ARTEMIS_TOKEN` in the app's environment.
5. Stops the core when the app exits.

Environment variables, not command-line arguments: on Windows any process can list other
processes' command lines. Malware running as the same user is out of scope for the weekend.

**Client (Jun)**
- Reads `ARTEMIS_CORE_URL` and `ARTEMIS_TOKEN` once into native state, and exposes one application
  command, `get_core_config`, to the main window. For push-to-talk, the local main window may also
  use only the global-shortcut plugin's register, unregister and is-registered permissions; no shell,
  filesystem, generic environment or other plugin permissions are granted (approved by Avery as acting lead).
- Checks that the address is loopback, then connects automatically: no address or token form.
- If either value is missing or partial, it shows an actionable launch error. It never falls back
  to a remote or unauthenticated connection.
- Browser dev mode (`npm run dev`) keeps the manual address and token form.

**Core (Avery)**
- Requires `Authorization: Bearer <token>` on every route, the SSE stream included.
- CORS allows only `http://tauri.localhost` and `http://127.0.0.1:1420`, methods `GET` and `POST`,
  headers `Authorization`, `Content-Type` and `Last-Event-ID`.
- Origin check: a request **with no `Origin`** (the launcher's readiness probe, curl) is allowed
  when the token is valid; a **listed** Origin is allowed; **any other** Origin gets `403`,
  including `Origin: null` (sandboxed frames, `file:` pages), which counts as unlisted. That way
  a website open in the user's browser can't drive the local core.
- The token is never logged, spoken, put in a URL, or included in an event.
- Redaction (`policy/redact.py`) replaces secrets with `[redacted]` in every event the tool gateway
  emits (announce text and data), every tool result sent to the model, and every log record (its
  message, arguments, traceback and stack) through a filter on each handler that exists when the
  core starts. Secrets are: the value (6 or more characters) of any environment variable whose
  name starts `ANTHROPIC_` or ends `_TOKEN`, `_KEY` or `_SECRET`; `sk-ant-…` keys; the value after
  `Bearer` or `Authorization: bearer`; PEM private-key blocks; and the value after `api_key`
  (`apikey`, `api-key`), `token`, `password`, `passwd` or `secret` followed by `:` or `=` when it is
  quoted or looks like a literal (code such as `token = get_token()` is left alone). Events the core
  emits itself (`transcript.final`, `task.state`, its fixed `error` lines) and the agent's own
  `agent.message` text are not passed through it.
- Settings come from `ARTEMIS_*` environment variables (and `ANTHROPIC_API_KEY`,
  `ANTHROPIC_WORKSPACE_ID` for the real agent). The full list, with defaults, is the docstring of
  `server/artemis/core/main.py`.
