# In-app key and status screen: design proposal

Status: **proposal for security and contract review**, 2026-11-14, David's Opus 2. Nothing here is
built. David chose this as a next build item; it must fit the notch design (setup lives in the full
window, never in the notch). Owners as in `AGENTS.md`: the contract and core are Avery's, the client
is Jun's, security review is David's with Avery.

## What exists today (verified in the repo)

- The core picks its agent at start from `ARTEMIS_AGENT`; `agent/config.py` says the API key is
  deliberately not stored there: only the Anthropic SDK reads `ANTHROPIC_API_KEY` from the environment.
  `ANTHROPIC_WORKSPACE_ID` is read the same way and sent as a header.
- `infra/launch.ps1` asks for the key with a hidden prompt and can save it in Windows Credential
  Manager (`infra/credstore.ps1`, opt-in). The app itself has no key handling and never receives it.
- #58 and #86 (merged) keep `ANTHROPIC_*` out of the app and out of child processes; #94 (open when
  this was written) clears the launch token from the app's environment after it is read.
- Avery's key is not scoped to a workspace, so a request needs `anthropic-workspace-id`; without it
  the API answers 400, with a wrong id 404, with a bad or expired key 401.

## The gap

A user who double-clicks the app cannot give it a key without a terminal or the launcher. A key
problem is also reported by one spoken sentence ("The Claude API key is missing or invalid.") that
does not say whether the key, the workspace id, or the network is at fault.

## Proposal

1. **Core, new endpoints (contract change first, Avery).**
   - `GET /config/status` returns `{agent: "stub"|"claude", key_present: bool, workspace_present: bool}`.
     Never a value.
   - `POST /config/credentials` with `{api_key, workspace_id?}`, launch token and Origin check as for
     every route, `204` on success. The body is never logged: it must be excluded from access logging
     and covered by `RedactFilter`. The key lives in process memory only (a secret-string type whose
     repr is hidden) and is passed to the Anthropic client constructor, **not** written to
     `os.environ`, so child processes cannot inherit it.
   - The core checks the key itself with one `GET /v1/models` call (key and optional
     `anthropic-workspace-id` header) and answers with a fixed result code, so the secret never goes
     back to the browser: `ok`, `invalid_key` (401), `needs_workspace` (400), `workspace_not_found` (404),
     `unreachable`. Each maps to one fixed spoken sentence naming the real cause.
   - The agent is created from the key when it arrives. Until then the agent is the stub and the core
     says once, in a fixed sentence, that Artemis needs a key and where to enter it.
2. **Client (Jun), in the full window only.** A Setup section with a labelled password field (the
   characters are not read aloud) and a workspace-id field, a Save button, and a status region that
   announces each result code. Never in the notch. The key goes to a **Tauri command**, not a `fetch`
   from page script: the command writes it to Windows Credential Manager (DPAPI, this user only) and
   POSTs it to the core over loopback with the launch token. The page clears the field the moment
   the command is invoked and never stores it (no state, no localStorage, no logs).
3. **Next launch.** The same Tauri command layer reads the saved credential at startup and posts it to
   the core, so no launcher prompt is needed. The launcher's own saved entry uses the same Credential
   Manager target names (`Artemis/AnthropicApiKey`, `Artemis/AnthropicWorkspaceId`), so both paths agree.
   A "Forget key" control deletes both.

**Decided by David (2026-11-14): the webview password field for now.**

**Noted upgrade path: an OS credential dialog opened from Rust (CredUI).** It keeps the key out of
the webview entirely, and NVDA reads the system dialog, but it is more native code (unsafe Windows
API calls) to write and test. Move to it if any of these happens: the webview field is judged too
exposed in a security review; the page ever loads anything other than our own local files; a
screen-reader problem with the password field cannot be fixed in the page; or after the demo when
there is time to test native code. The core endpoint and the Credential Manager storage are
identical in both designs, so the upgrade changes only how the key is typed, not the core or the
saved entry.

Considered and not chosen: a hosted proxy holding the key: needs auth, spend limits and a new
attack surface; rejected earlier.

## Security requirements (David's rules, restated as tests)

- The key and workspace id never appear in: the repo, a PR, `core.log`, a session log, an event, an
  error message, the activity log, a screenshot, an artifact, or the UI bundle. Tests: post a
  recognizable fake key and assert it is absent from core.log, every event on the bus and the session
  JSONL, and from `os.environ` and child-process environments.
- The endpoint rejects without the launch token or with a foreign Origin, and a malformed body gets a
  fixed error that does not echo the body.
- Credential Manager entries are per Windows user; the README must say a saved key can be read by
  anything running as that user.
- The existing secret scan (gitleaks) stays green; add the new endpoint's fake-key test values to its
  narrow allowlist only if they trip it.

## Accessibility requirements

Every control labelled; the Setup section reachable by Tab from the full window and announced by
NVDA; each result announced once through the same live region and the core's speech, in the fixed
sentences; focus never lost when the section updates; keyboard-only completion; no secret echoed to
speech or the log; a clear way back to the request field.

## Steps, owners, effort

| Step | Owner | Effort |
|---|---|---|
| Contract note for `/config/*` and the result codes | Avery | S |
| Core endpoints, redaction, tests with a fake key | Avery (security review: David) | M |
| Tauri commands (Credential Manager read/write/delete, POST to core) | Jun, with Avery's review of capabilities | M |
| Setup section in the full window | Jun | M |
| Windows and NVDA hardware pass | Opus 2 (UIA) plus a person with NVDA | S |
| Docs and stage runbook step | Opus 2 | S |

Order: contract, core, then client; the launcher path keeps working throughout, so nothing blocks
the demo. After the demo, not before the keyed rehearsal passes.

## Decisions

- David: **webview password field, decided.** OS credential dialog is the noted upgrade (above).

## Still open

- Avery: the endpoint shape and result codes.
- Jun: whether Setup sits inside the notch's "Open controls" window or its own section of the full window.
