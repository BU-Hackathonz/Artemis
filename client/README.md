# Artemis client: keyboard skeleton

From `client/` with Node 22+:

```sh
npm ci
npm run dev
```

Open http://127.0.0.1:1420 and choose **Try in-memory demo**. After connecting, focus moves to the request field. Enter sends; Shift+Enter adds a newline. Type any request (the demo always proposes Hello World), approve the file proposal, separately approve the run, and read the simulated output. Escape rejects the current proposal. Blocked actions default to Reject and require Shift+Enter on the focused Approve button; Enter or a click alone cannot approve them. The demo never writes files, executes code, or records audio.

In browser mode on the Windows/NVDA laptop, choose **Try blocked-action demo**, submit any text and approve the normal file proposal. The next dialog has `blocking=true` and a clearly labeled synthetic high-severity finding (`DEMO_HIGH`); no scanner ran and no executable file exists. Focus defaults to Reject. Focus **Approve blocked action (Shift+Enter)**: Enter alone and clicking must do nothing; Shift+Enter accepts the simulated run. Escape must reject. After either outcome, focus returns to the request field. Repeat with NVDA on to verify that the shortcut reaches the dialog.

For the real core, enter its `http://127.0.0.1:<port>` address and per-launch token, then connect. Tokens are held in memory, never persisted or placed in a URL. This manual connection form is browser-only. Disconnect resets the page. The core must allow CORS for `http://127.0.0.1:1420` in browser dev, and the Tauri WebView origin on Windows (`http://tauri.localhost`), including Authorization, Content-Type, and Last-Event-ID headers. No unauthenticated or remote API fallback is used.

The fetch-based SSE connection sends Bearer auth and resumes with Last-Event-ID. Replayed sequence numbers are discarded; controls stop accepting actions while disconnected. Failed request submissions retain their request id for retry. Approvals send the exact supplied hash; a 409 stays visible and requires a fresh proposal. Speech belongs to the core; the log uses aria-live=off when voice_ready=true, polite otherwise, and updates immediately on voice.status events. Desktop push-to-talk uses Ctrl+Alt+Space by default. Hold the chord, wait for the core’s listening tone, speak, and release to stop. The shortcut works without window focus. If another app owns the chord, the error is announced and the focusable Start listening / Stop listening toggle remains available. Activate once to start and again to stop. Pressing and releasing the global chord also stops a session started with the button; this works with NVDA browse-mode clicks without needing focus mode. With core speech available, registration failures are reported through the bounded session diagnostics route and spoken once per shortcut per session by the core; an exact repeat is announced locally instead. Without core speech, or if reporting fails, the local alert remains. Global voice shortcut can be changed for this app launch. It requires Control or Alt plus another modifier, and known system shortcuts, single-modifier chords, duplicates and Control+Alt+letter combinations (AltGr/NVDA conflicts) are rejected. Typed requests always remain available.

Native launches follow docs/contracts.md §6: infra/start.ps1 passes ARTEMIS_CORE_URL and ARTEMIS_TOKEN in the app environment. Native state reads the two values once, checks the HTTP loopback root address and header-safe token, and exposes only get_core_config to the local main window. The client invokes it once per page load and connects automatically. Missing/partial/invalid native configuration gives an actionable launch error with no manual/demo fallback. The token never enters a URL, browser storage, a log, or an announcement. On key-up the client sends stop immediately even if the start response is pending; a final stop barrier settles before a rapid re-press can open another hold. Disconnect/unload also requests stop and native registration is cleaned up. If stopping cannot be confirmed, voice input is blocked and the UI instructs the user to close Artemis so the launcher stops its core. The launcher/core must be running for native testing; the browser retains the in-memory demos.

`src/contracts.ts` is a temporary adapter for docs/contracts.md v0, outside generated/. Replace it with types generated from core OpenAPI when available. `src/platform.ts` is the only platform boundary. The local main-window capability permits allow-get-core-config plus only the global-shortcut plugin’s register, unregister and is-registered commands, as approved in contracts §6. No generic environment, file, shell or process commands are enabled. The core records and transcribes; the client never captures microphone audio.

```sh
npm run build
npm test
npx playwright install chromium
npm run test:e2e
# Windows demo laptop with Rust, MSVC tools and WebView2:
npm run tauri dev
# Native config validation tests (Rust):
cargo test --manifest-path src-tauri/Cargo.toml
```

PTT unit tests cover repeat dedupe, early release, rapid re-press, disposal and failure cleanup. Native IPC is mocked in browser tests covering key state across blur, configurable chords, registration failure with a virtual-click toggle fallback and invalid-chord rejection, and unload cleanup. Actual OS-global shortcut delivery still needs Windows validation. Browser tests cover the two-approval workflow, Escape rejection, focus containment/restoration, automated accessibility checks, loopback-only configuration, manual reconnect followed by another drop, blocked-action shortcuts, stale hashes, task_active conflicts, and live speech availability. The HTTP streaming fixture is synthetic; real-core integration remains unverified. Native launcher tests use a mocked IPC bridge plus a synthetic streaming HTTP server; Rust tests validate config parsing and static recovery errors. Actual launcher → Windows IPC → real core, NVDA/Narrator, and speech require integration testing. Windows launcher auto-connect, normal write/run approvals and real Hello World execution were confirmed with SAPI (NVDA off) on main 199c64c. NVDA blocked-approval shortcuts and the new OS-global PTT keys still need hardware verification. The Tauri shell is unbundled (`bundle.active=false`) until launch orchestration is ready.
