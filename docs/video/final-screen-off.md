# Artemis demo video script ("Screen Off")

Target length: 1:50. One continuous take with the screen off is the centerpiece. Artemis and Orca do
the talking. The narrator speaks only over silence, never over Artemis or Orca.

Lines in quotes are exact core text (`docs/demo.md`). *(model)* lines are Claude's wording and vary,
so caption them from the take, not from this script.

## Honesty rules (read first)

- **Nothing is verified on Ubuntu yet.** The one real-agent pass (Hello World, 17.9 s) was on
  Windows/NVDA. Every shot below is **[VERIFY ON UBUNTU]** until it passes once on the demo laptop
  with Orca running.
- ElevenLabs on Ubuntu: unverified. If it fails the hardware check, use the offline voice and don't
  mention ElevenLabs.
- Not shown, not said: preference learning, custom phrases, saved settings (post-demo list, board).
- Demo 3 may not block if Claude writes a safe version first. If the take has no "Blocked:" line,
  drop shot 6. Don't redo it until it blocks.

## Shot list

| # | Time | On screen | Heard | Purpose |
|---|---|---|---|---|
| 1 | 0:00–0:06 | Black. White text: "This developer can't see the screen. Neither can you." | Keys clicking, then Artemis: "main.py ran successfully. It printed: Hello, World!" (lifted from shot 3) | Hook: the result first, then the premise. |
| 2 | 0:06–0:14 | Monitor on for 3 s: Ubuntu, the island at the top. A hand turns the brightness to zero. Caption: "Screen off. One take. Real agent." | Orca reads the island once, clearly *(whatever Orca says)*. | Sets the rule, and Orca is heard on its own. |
| 3 | 0:14–0:40 | Black, burned-in captions, a hand-cam inset on the keyboard, a running timer in the corner. | Request (push-to-talk, or typed): "Create a Python program that prints Hello World, and run it." Then "Create project hello-world with 1 file: main.py, 1 line. No security findings. Enter to approve, Escape to reject." Enter. "Created main.py, 1 line." "Run main.py with Python. No security findings. Enter to run, Escape to reject." Enter. "main.py ran successfully. It printed: Hello, World!" | A full build-and-run loop by ear and keyboard. |
| 4 | 0:40–0:46 | Same. Caption: "Saying 'yes' doesn't approve anything." | Developer says "yes" while an approval is open. Artemis: "Use the keyboard to decide: Enter on the dialog to approve, Escape to reject." | A misheard word can never write or run code. |
| 5 | 0:46–1:08 | Same. | Request: "Create a Python program that calls pritn with Hello World, and run it." Enter, Enter. "main.py failed with exit code 1. NameError: name 'pritn' is not defined. Did you mean: 'print'?" Then: "Fix it and run it again." Enter, Enter. "… It printed: Hello, World!" | Errors read word for word, then fixed by voice. The most human moment. |
| 6 | 1:08–1:22 | Same. Keycap badges appear as keys are pressed. | "Open the old-app project and run main.py." "Blocked: 1 high-severity issue, main.py line 5, subprocess call with shell=True …" Developer presses Enter: nothing happens (the keycap shows it). Escape. | A dangerous run is blocked, and Enter can't push it through. |
| 7 | 1:22–1:34 | Same. Caption: "The README tells the AI to ignore its rules." | "Open the calc project and summarize it." No approval comes. *(model)* says the README holds instructions aimed at an AI and it is ignoring them. | The injection is harmless here: a summary task has no write or run tools. |
| 8 | 1:34–1:46 | Same. | "Open the signup-page project and check index.html for accessibility." Then "Fix those problems in index.html, then check it again." Enter. "Before: 7 problems. After: 0." | Artemis checks the real page the way a screen reader sees it. |
| 9 | 1:46–1:56 | Monitor back on: the island in its done state and the fixed signup page. | Narrator (the only voice-over): "Every change approved on the keyboard and locked to that exact edit. Every run real. Every error read out word for word." | The three claims in one breath, in plain words. |
| 10 | 1:56–2:00 | End card: tagline, team names, repo URL. | Narrator: "Artemis lets blind developers hear every change, approve every run, and prove every result themselves." | Close. |

If it runs long, cut in this order: shot 7, then shot 4. Never cut shots 3, 5 or 8.
If only Hello World passes on recording day, ship shots 1, 2, 3, 9 and 10 (about 50 s). For shot 9,
say only "Every change approved. Every run real."

**Words to avoid on camera:** "SHA-256" and "hash" (say "locked to that exact edit"),
"injection-proof" (say "harmless here"), "sandboxed", and any list of runner internals.

## Recording-day checklist

**Machine**
- [ ] Ubuntu 24.04 + GNOME 46, island enabled, core started per `docs/demo.md` "Ubuntu run".
- [ ] Reset the demo projects before every take (no `hello-world-2`).
- [ ] Do Not Disturb on, charger in, suspend off, 1920×1080 at 100% scaling.
- [ ] Wired network. Run one throwaway Hello World before rolling.

**Audio**
- [ ] Record system audio digitally (PipeWire monitor source in OBS), plus a separate mic for keys only.
- [ ] Give Orca a different voice from Artemis's and keep its verbosity low. Check that neither cuts the other off.
- [ ] Levels: Artemis voice about −6 dBFS, Orca about −12, keys about −24. No clipping.
- [ ] Narration is recorded separately and placed only in silence.

**Captions**
- [ ] Burn in every spoken line, transcribed from the take, labeled by speaker (Artemis, Orca, Developer). Also upload an `.srt` file.
- [ ] Every visual proof (keycap, README highlight, 7 → 0) also has a spoken line. Blind judges get the whole video.

**Honesty**
- [ ] Record the screen anyway (as evidence, and for shot 9). Keep the timer visible during the black section.
- [ ] No re-voicing, no speeding up, no stub agent unless the screen says "scripted agent".
- [ ] A long wait may be cut, but show it: caption "[cut: 12 s waiting for Claude]" with the timer jump visible.
- [ ] A failed demo is either kept (its error is spoken) or dropped, never patched.
- [ ] Do 3 full takes and pick the cleanest honest one. If a blind tester is available, have them run it instead of a sighted person with closed eyes.
