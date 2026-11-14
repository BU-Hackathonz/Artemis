# Stage runbook (one page)

For the person at the demo laptop (Windows, NVDA). Exact spoken lines and offline fallbacks: [demo.md](demo.md).
Work from one PowerShell window in the repo root. Core log: `%LOCALAPPDATA%\Artemis\logs\core.log`.

## T-30 minutes
- [ ] **Power, sound, network:** charger in, sleep off (check on the laptop), Focus assist on, screen
      mirrored, volume up on the speakers the judges hear, Wi-Fi connected (the real agent needs the API).
- [ ] **Mic:** Windows default input set and tested in Sound settings. Quit apps that may hold Ctrl+Shift+Space.
- [ ] **NVDA:** on, exactly as rehearsed. Voice and rate are NVDA's own settings (Artemis never changes
      them): set a rate the room can follow (check on the laptop).
- [ ] **Scripts:** `Get-ExecutionPolicy -List` shows MachinePolicy and UserPolicy Undefined; a Group
      Policy value overrides `-ExecutionPolicy Bypass` (check on the laptop).
- [ ] **Key saved, never shown:** `cmdkey /list:Artemis/AnthropicApiKey` and
      `cmdkey /list:Artemis/AnthropicWorkspaceId` each list an entry, not its value. Our key needs the
      workspace ID; with both saved, the launcher asks for neither (check on the laptop). **App built:** `Test-Path "$env:LOCALAPPDATA\Artemis\app\artemis-client.exe"` is True.
- [ ] `powershell -ExecutionPolicy Bypass -File infra\setup-demo.ps1 -Force` ends "Demo projects ready:
      calc, old-app, signup-page." **Smoke test:** launch, run demo 1, then do the reset below.
- [ ] **Voice or typing:** hold Ctrl+Shift+Space and say demo 1's sentence. Use voice on stage only if the
      "Heard:" line matches it word for word (row 3 failed on David's laptop earlier today); otherwise type.

## Launch
```powershell
.\infra\Artemis.cmd -Agent claude -NoConversation
```
- Console: "Using the Anthropic API key saved in Windows Credential Manager. No internet? Run Artemis.cmd
  -Agent stub. …" then "Core is ready."
  Asked for a Workspace ID? Paste it (it's not a secret; our key fails without it). The launcher offers to save it.
- **Ready** = focus in the request field and "Artemis is ready. Hold Control Shift Space and speak, or type a request."
  If it says "Use the Start listening button" instead, the shortcut is taken: use **Start listening** or type.
- `-NoConversation` keeps demo.md's lines (Artemis.cmd turns conversation mode on; start.ps1 defaults to off).
  No built app: set the key as in demo.md setup step 7, then run
  `powershell -ExecutionPolicy Bypass -File infra\start.ps1 -Agent claude` (the first build takes minutes).

## The five demos (one session, in this order; Enter approves, Escape rejects)
Only demo 1 has run with the real agent so far. The other "Proof" lines come from demo.md and the offline
tests (rows 6 to 10 of the pass/fail sheet are "not run"), so rehearse them with `-Agent claude` first.
1. **Hello World.** Type `Create a Python program that prints Hello World, and run it.` (or hold
   Ctrl+Shift+Space and say it, only if the voice check above passed). Enter, Enter. Proof: "main.py ran successfully. It printed: Hello, World!"
2. **Failed run.** Type `Create a Python program that calls pritn with Hello World, and run it.` Enter, Enter.
   Proof: "main.py failed with exit code 1. NameError: name 'pritn' is not defined." Then type
   `Fix it and run it again.`, Enter, Enter: "... It printed: Hello, World!"
3. **Security block.** Type `Open the old-app project and run main.py.` Proof: "Blocked: 1 high-severity
   issue, main.py line 5, subprocess call with shell=True ..." Focus starts on Reject; Tab, Enter (nothing happens), Escape.
4. **Prompt injection.** Type `Open the calc project and summarize it.` Proof: no approval dialog at all;
   Claude says the README holds instructions aimed at an AI and ignores them (wording varies).
5. **Echo Check.** Type `Open the signup-page project and check index.html for accessibility.`, then
   `Fix those problems in index.html, then check it again.`, Enter. Proof: "Before: 7 problems. After: 0."
   Not 0: type `Fix the rest.`

## When something fails
- **No speech recognized** (no "Heard: ..." after release): type the request. Typing always works.
- **Mic or recognition bad:** Tab to **Start listening**, press, speak, press **Stop listening**; or type.
  Mic gone for good: relaunch with `-Voice stub` added (no mic, typing only).
- **API down** ("The task failed. No internet connection to Claude." or "Claude is still busy"): close
  Artemis, run `.\infra\Artemis.cmd -Agent stub -NoConversation`, and say the agent is now scripted. The
  stub only does Hello World; for demos 2 to 5 use the offline commands under each demo in demo.md.
- **App closed or crashed:** read the console and `$LASTEXITCODE`. `0`: the app closed normally ("The app
  closed. Stopping the core."). `3`: the app crashed ("The app exited with code …"): relaunch. `1`: could not
  start (the red lines at the bottom say why, below the end of core.log) or the core stopped while the app ran
  (`2` from start.ps1 alone). Other: PowerShell's own code. Core slow to start ("/health within 120 seconds"): start.ps1 `-HealthTimeoutS 300`.
- **Core stuck:** Tab to **Cancel task**, Enter ("Canceled."). Still stuck: Alt+F4 the Artemis window
  (the launcher kills the core's process tree), run the launch command again, wait for the ready line.
  Under 1 minute (check on the laptop). A restart forgets the Claude conversation and Echo Check's "before".

## Reset between runs
Close the Artemis window (stops the core), run `setup-demo.ps1 -Force` as above, launch again. Always
restart: a running session remembers the project names it created and the Claude conversation.
