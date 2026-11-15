# Live run checklist: Artemis by voice on the Ubuntu laptop

Use this tonight. Quotes are exact strings from the code or the demo runbook; anything marked
"brief" is the phrase we plan to say, not a string the code matches.

## Setup (once per laptop, or after a pull)

1. Update the repo: `cd <repo> && git pull --ff-only origin main`.
2. Install and enable the island: `bash infra/setup-ubuntu.sh --extension-dir client/prototypes/water-desktop/artemis-water@bu-hackathonz.github.io`.
3. On Wayland (the script says so when `XDG_SESSION_TYPE=wayland`): log out and back in, then run the
   `gnome-extensions enable ...` line the script prints.
4. Check the island loaded and is current (the installed copy is not a link, so a pull alone changes nothing):
   - `gnome-extensions info artemis-water@bu-hackathonz.github.io` must say `State: ACTIVE`.
     `ERROR` with a `GLib.FileError` in `journalctl --user -b -o cat /usr/bin/gnome-shell` means the schema was
     not compiled: rerun step 2 (setup compiles it and stops if it cannot).
   - `cat ~/.local/share/gnome-shell/extensions/artemis-water@bu-hackathonz.github.io/installed-from` must match
     `git rev-parse --short HEAD`. If not, rerun step 2 and log out and back in.
   - The island shows at the top edge after login.

## Start

5. Start the core with the real agent: `infra/start.sh --agent claude`.
6. If `ANTHROPIC_API_KEY` is not set, type the key at the hidden prompt. Do not paste it into chat or a file.
7. Wait for the health check. If it fails, the script prints the last 20 lines of `core.log`.
8. Confirm `core.log` shows `Wake word on: say "Hey Artemis" and the request`. Otherwise the mic is off
   (the core falls back to push-to-talk and logs `Wake word could not start the microphone`).

## Say these, in order

9. **Hello World, created and run.** Say: "Hey Artemis, Create a Python program that prints Hello World, and run it."
   (demo runbook string). Expect a plan, then a create approval: "Enter to approve, Escape to reject."
   Press Enter for the write, then Enter for the run. The stub agent's scripted reply is
   "I created hello-world with main.py and ran it. It printed Hello, World!" The real agent's words vary.
10. **Open in VS Code.** Say (brief): "Hey Artemis, open it in VS Code." The code maps "vs code" and "vscode"
   to Visual Studio Code (`server/artemis/policy/desktop.py`, `_ALIASES`). The full phrase is not in the code,
   so if the agent does not pick `open_app`, say "open VS Code" and note it. Approve the dialog if one appears.
   Depends on: nothing new; the desktop tools are on main.
11. **Type into the text editor.** Say (brief): "Hey Artemis, type hello into my editor." The test uses this
   wording (`server/artemis/core/test_desktop_e2e.py`), and `editor` maps to "text editor". Enter approves typing
   into that window; Escape rejects. Depends on: island typing support. The extension on main has no typing
   handler yet (`island-typing` work). Until it lands, expect item 13's message.
12. **Blocked run.** The code has no fixed phrase for this. A blocked run shows "Blocked: ..." followed by
   "Escape to reject, or use the override key to approve anyway." Press Escape. Approve only with Shift+Enter;
   Enter alone never approves a blocking approval (`approvalKeys.js`). To get a failed run instead, say
   (demo runbook): "Hey Artemis, Create a Python program that calls pritn with Hello World, and run it."
   Depends on: the ring cues, which are part of the island work.
13. Ring: each step should show a cue on the island. Depends on: the island work (ring cues reach it only
   when the extension is enabled and connected). If no cue shows, check item 4 before the core.

## Where to look

14. Live core log: `tail -f ~/.local/state/artemis/core.log | grep -E "Wake word|desktop op"`.
   `Wake word on` means listening; `Wake word: ignored speech` means it heard you but no wake word.
   `desktop op <id>: <kind>, <n> chars` means a desktop action reached the island (the text itself is never logged).
15. Session event log: `ls -t ~/.artemis/logs/*.jsonl | head -1`, one JSON line per event. Typed text is not
   written there, only its length.

## What each failure means

16. `Typing needs the Artemis island, and it isn't connected.` (error `desktop_unavailable`): the extension is
   not enabled, or the island did not send `desktop: true` on ready. Fix items 2-4, then restart the core.
17. `The Artemis island didn't answer, so nothing was typed.` The island is connected but did not reply in time.
   Check the GNOME Shell session, then retry.
18. `The island couldn't tell which window has the focus.` Click the target window and say the request again.
19. `Enter to approve, Escape to reject.` Plain approval. `Blocked: ...` needs Shift+Enter, or Escape to reject.
20. `Call plan first: say the goal and the steps, then act.` (error `plan_required`): the agent acted before its
   plan. This is a model-side error, not yours. Repeat the request once; if it recurs, note the session JSONL.
21. Nothing heard: check the wake word line in item 8 and the mic, then say "Hey Artemis" before the request.
