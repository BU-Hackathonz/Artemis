# Artemis contracts (proposal for kickoff)

Status: **proposed**. Avery owns the contract; David accepts changes until Avery is on site. Once agreed, these become Pydantic models in
`server/artemis/contracts/`, the TS types are generated from FastAPI's OpenAPI, and the samples
below become JSON fixtures that every stub is tested against. Change the contract first, then the
code.

Conventions: JSON, `snake_case`, ids are strings (`ses_…`, `tsk_…`, `apr_…`, `op_…`), times are
ISO 8601 UTC, and workspace paths are always **relative** to the project root.

## 1. Client → core (HTTP, `127.0.0.1` only)

Every request sends `Authorization: Bearer <launch token>`. `infra/start.ps1` generates the token
and passes it to both processes.

| Method | Path | Body → Response | Notes |
|---|---|---|---|
| GET | `/health` | → `{ok, version, voice_ready, screen_reader}` | `screen_reader`: `"nvda"` or `null` |
| POST | `/sessions` | `{}` → `{session_id}` | One session per app launch |
| GET | `/sessions/{sid}/events` | SSE stream of `Event` (§2) | Resumes from `Last-Event-ID` |
| POST | `/sessions/{sid}/tasks` | `{client_request_id, source: "voice"\|"keyboard", text}` → `{task_id}`, or `409 {error: "task_active", task_id}` | Idempotent per `client_request_id`; one active task per session (§5.2) |
| POST | `/tasks/{tid}/reply` | `{text, source}` → `204` | **Reserved, unused in the MVP.** The agent asks a question by ending its turn; the user's next request is the answer |
| POST | `/approvals/{aid}` | `{decision: "approve"\|"reject", approval_hash}` → `204`, `409` if stale | The hash must match the one that was offered |
| POST | `/tasks/{tid}/cancel` | `{}` → `204` | Stops active work; the event reports what was already applied |
| POST | `/voice/listen` | `{action: "start"\|"stop"}` → `204` | Push-to-talk down/up; `start` stops current speech first (§5.1); the transcript arrives as events |
| POST | `/speech` | `{action: "stop"\|"repeat"}` → `204` | Stop speaking / repeat the last announcement |

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
  "announce": { "text": "Waiting for your approval to create main.py.", "priority": "high" },  // or null
  "data": { }                      // type-specific, below
}
```

`priority`: `low` (progress, may be dropped if speech is busy), `normal` (queued), `high`
(interrupts: approvals, failures, completion).

| `type` | `data` |
|---|---|
| `transcript.partial` / `transcript.final` | `{text, client_request_id}` |
| `task.state` | `{state, reason?}`, where `state` ∈ `queued, working, awaiting_reply, awaiting_approval, running, succeeded, failed, canceled`. `awaiting_reply` is reserved and unused in the MVP |
| `agent.message` | `{text, kind: "answer"\|"question"\|"progress"}` |
| `approval.requested` | `Approval` (below) |
| `approval.resolved` | `{approval_id, decision, by: "user"\|"invalidated"}` |
| `file.changed` | `{op_id, path, action: "created"\|"modified"\|"deleted", lines_added, lines_removed}` |
| `run.result` | `{op_id, preset, exit_code, timed_out, duration_ms, stdout_tail, stderr_tail, truncated}` |
| `security.findings` | `{op_id, tool: "bandit"\|"semgrep", findings: [{rule, severity, path, line, message}]}` |
| `error` | `{code, message, recovery}`, where `recovery` is one sentence the user can act on |
| `voice.status` | `{voice_ready, screen_reader}`, emitted at session start and whenever either value changes (§5.3) |

```jsonc
// Approval
{
  "approval_id": "apr_3",
  "kind": "write_files",          // or "run_program"
  "approval_hash": "sha256:…",    // over diff + base file hashes + workspace id (or preset + args)
  "summary": "Create project hello-world with one file, main.py, 1 line.",
  "files": [{ "path": "main.py", "action": "created", "lines_added": 1, "lines_removed": 0 }],
  "diff": "--- /dev/null\n+++ main.py\n@@ -0,0 +1 @@\n+print(\"Hello, World!\")\n",
  "run": null,                    // for run_program: {"preset": "python", "entry": "main.py", "timeout_s": 10}
  "findings": [],                 // security findings that apply to this approval
  "blocking": false               // true when a high-severity finding applies (default false)
}
```

When `blocking` is true the summary starts with "Blocked: …", the client's default action is
Reject, and approving needs a distinct key (not plain Enter), so a risky write or run is never
approved by reflex. The user can still override: Artemis doesn't overrule the developer.

Rules: the core never sends two events with the same `seq`; the client drops any `seq` it has
already seen. If the files under an approval change, the core emits `approval.resolved` with
`by: "invalidated"` and a fresh `approval.requested`.

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
`ToolResult(ok=False, error="rejected_by_user")`, which the agent explains instead of retrying.
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
| `edit_file` | `{path, edits: [{old, new}]}` | yes | after MVP |
| `inspect_page` | `{url}` (fixture only) | no | Echo Check |

There is no shell, delete, network or install tool, and the agent can't apply anything without
approval. `ask_user` and `scan` are not model tools: a question is the agent's final answer, and
the scan runs inside the gateway.
Paths are rejected unless `Path(root / p).resolve()` stays under `root`.

### 3.3 Voice (Yaovi ↔ Avery)

```python
class Voice(Protocol):
    async def start_listening(self) -> None: ...
    async def stop_listening(self, client_request_id: str) -> str: ...   # final transcript; partials go via emit
    def set_vocabulary(self, words: list[str]) -> None: ...               # file names and symbols for the prompt
    async def announce(self, text: str, priority: Priority) -> None: ...  # NVDA if running, else SAPI
    async def stop_speech(self) -> None: ...
    async def repeat_last(self) -> None: ...
```

The event bus calls `announce()` for every event that has an `announce` field, so no other
component speaks directly.

### 3.4 Runner (Avery, infrastructure)

```python
class Runner(Protocol):
    def create_project(self, name: str) -> ProjectRef: ...          # %USERPROFILE%\ArtemisProjects\<slug>, git init
    def write_file(self, project: ProjectRef, path: str, content: str) -> FileChange: ...
    async def run(self, project: ProjectRef, entry: str, args: list[str], timeout_s: int = 10) -> RunResult: ...
```

`run()`:
- finds the interpreter once at startup (`uv python find`, then `py -3`, else fail with a spoken
  message); never the core's own Python (`sys.executable`);
- uses a fixed argv `[python_exe, "-I", "-X", "utf8", entry, *args]`, `shell=False`,
  `cwd=project.root`, `stdin=DEVNULL` (an `input()` call gets end-of-file instead of hanging);
- passes an explicit env: only `SYSTEMROOT`, `TEMP`, `TMP`, `PATH` (interpreter dir + System32) and
  `PYTHONIOENCODING=utf-8`; no API keys;
- has a 10 s timeout, kills the whole process tree, and caps captured output at 64 KB;
- runs `subprocess.run` inside `asyncio.to_thread`, not `asyncio.create_subprocess_exec` (on
  Windows, uvicorn's `--reload` event loop can't start subprocesses).

## 4. Sample payloads (the first fixtures)

1. **Typed request:** `POST /sessions/ses_1/tasks`
   `{"client_request_id":"cr_1","source":"keyboard","text":"create a Python program that prints Hello World"}`
2. **Write approval:** the `Approval` example above, then `POST /approvals/apr_3 {"decision":"approve","approval_hash":"sha256:…"}`
3. **Rejection:** the same with `"reject"`. Expect `approval.resolved`, then `agent.message` "Okay, I didn't create anything.", then `task.state` `succeeded`, and no `file.changed` event.
4. **Failed run:**
   ```json
   {"type":"run.result","announce":{"text":"main.py failed with exit code 1. NameError: name 'pritn' is not defined, line 1.","priority":"high"},
    "data":{"op_id":"op_9","preset":"python","exit_code":1,"timed_out":false,"duration_ms":81,
            "stdout_tail":"","stderr_tail":"Traceback (most recent call last):\n  File \"main.py\", line 1 ...\nNameError: name 'pritn' is not defined","truncated":false}}
   ```
5. **Successful run:** `exit_code` 0, `stdout_tail` `"Hello, World!\n"`, announce "main.py ran successfully. It printed: Hello, World!"
6. **Security block (Phase 4):** `security.findings` with `{"rule":"B602","severity":"high","path":"main.py","line":4,"message":"subprocess call with shell=True"}`, then `approval.requested` for `run_program` listing that finding.

## 5. Turn-taking rules (voice and tasks)

### 5.1 Push-to-talk cuts off speech
On `POST /voice/listen {action: "start"}`, the core only calls `voice.start_listening()`. That
call does the whole sequence: stop current speech (SAPI purge, or NVDA's `cancelSpeech`), play the
"listening" earcon to the end, then open the mic. `stop_listening()` plays the "stopped" earcon.
The core doesn't call `stop_speech()` itself here. Artemis's own voice (SAPI through the speakers)
must never be recorded and transcribed as a request. The client's own "Stop speech" control is
unchanged.

### 5.2 One active task per session
A task is active from `queued` until `succeeded`, `failed` or `canceled`.
- **Typed request while a task is active:** `POST /sessions/{sid}/tasks` returns
  `409 {error: "task_active", task_id}`. No second task starts.
- **Spoken request while a task is active:** the core still emits `transcript.final` (so the text
  stays in the editable field) but doesn't create a task. It emits `error`
  `{code: "task_active", message, recovery}` with `announce` at `high`:
  "Still working on the last request. Say cancel to stop it, or wait."
- **Spoken control words.** The core checks the final transcript after stripping the wake word,
  lowercasing and removing punctuation. Only a whole-utterance match counts, and no model is
  involved:

  | Utterance | While a task is active | No active task |
  |---|---|---|
  | `cancel`, `stop`, `cancel that`, `never mind` | same as `POST /tasks/{id}/cancel` | "Nothing to cancel." |
  | `repeat`, `say that again` | same as `POST /speech {action: "repeat"}` | same |
  | `approve`, `yes`, `reject`, `no` while `awaiting_approval` | not applied; announce "Use the keyboard to decide: Enter on the dialog to approve, Escape to reject." | treated as a normal request |

  Approvals stay keyboard-only in the MVP: a misheard "yes" must never write or run anything.

### 5.3 Speech availability can change at runtime
`voice_ready` and `screen_reader` can change while the app runs: NVDA starts or stops, or NVDA is
running but its controller DLL is missing, which makes the core silent. The core emits
`voice.status` at session start and on every change. The client turns its log's live region on
(`aria-live="polite"`) whenever `voice_ready` is false and off when it's true, instead of deciding
once from `/health` at connect.
