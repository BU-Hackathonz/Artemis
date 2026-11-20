# Artemis demo video, Script C: "Eyes Closed" (90 s max)

Angle: judges skim dozens of videos. Hook in 5 s, one continuous screen-off take as the centerpiece,
almost no narration. Artemis speaks for itself.

Spoken lines in quotes come from `docs/demo.md` (core-written, exact). *(model)* = Claude's wording, varies.

## Honesty flags (read first)
- **Only demo 1 (Hello World) has run with the real agent, and that run (17.9 s) was on Windows/NVDA.**
  `docs/stage-runbook.md` is still the Windows/NVDA runbook. The Ubuntu 24.04 + GNOME island + Orca path
  is documented in `docs/demo.md` "Ubuntu run" but has no recorded pass. So even Shot 3 is
  **[VERIFY BEFORE RECORDING]** on the Ubuntu machine.
- `docs/demo.md` says the scripted five-part demo does not run on Ubuntu; demos 2 to 5 need the real
  agent there. All of them: **[VERIFY BEFORE RECORDING]**.
- ElevenLabs: the runbook says the stage default voice is not ElevenLabs and `-VoiceDemo` is used only
  after a hardware check. Whether ElevenLabs works on the Ubuntu build: **[VERIFY BEFORE RECORDING]**.
  If not, say nothing about ElevenLabs in the video.
- Not claimed anywhere: sandboxing (runs are "gated and limited"), preference learning, custom phrases.

## Shot list

| # | Time | On screen | Heard | Purpose |
|---|---|---|---|---|
| 1 | 0:00-0:05 | Black screen. White text: "This developer can't see the screen. Neither can you." | Keys clicking. Artemis: "main.py ran successfully. It printed: Hello, World!" (cold-open clip lifted from Shot 3) | Hook: payoff first, premise in one line. |
| 2 | 0:05-0:10 | Monitor switches on for 3 s: Ubuntu desktop, island notch at top. Hand turns the monitor off (or laptop lid / brightness to zero). Caption: "Screen off. One take. Real agent." | Orca: brief focus announcement *(whatever Orca says)* | Establish the setup and the rule: no cuts, no screen. |
| 3 | 0:10-0:35 | Black screen + burned-in captions of every spoken line; small inset of hands on keyboard. Corner timer runs. | Push-to-talk (hold Ctrl+Shift, press Space) or typed: "Create a Python program that prints Hello World, and run it." Then: "Working on: ..." / "Create project hello-world with 1 file: main.py, 1 line. No security findings. Enter to approve, Escape to reject." Enter. "Created main.py, 1 line." "Run main.py with Python. No security findings. Enter to run, Escape to reject." Enter. "main.py ran successfully. It printed: Hello, World!" **[VERIFY BEFORE RECORDING on Ubuntu]** | Centerpiece: a full build-and-run loop by ear and keyboard. Two approvals, both spoken. |
| 4 | 0:35-0:48 | Same black screen, captions. Caption chip: "shell=True" | Typed: "Open the old-app project and run main.py." Hear: "Blocked: 1 high-severity issue, main.py line 5, subprocess call with shell=True identified, security issue, and 1 more. ... Escape to reject, or use the override key to approve anyway." Enter (nothing happens). Escape. **[VERIFY BEFORE RECORDING]** | Safety: Bandit blocks; Enter cannot approve a blocked action. |
| 5 | 0:48-0:58 | Black screen, captions. Caption chip: "README says: ignore your rules" | Typed: "Open the calc project and summarize it." No approval dialog. *(model)* says the README holds instructions aimed at an AI and it is ignoring them. **[VERIFY BEFORE RECORDING]** | Injection is harmless: that task has no write or run tools. |
| 6 | 0:58-1:15 | Black screen, captions. Optional: a 1 s jump-cut is NOT allowed inside the take; keep it continuous or end the take before this shot and label "Take 2". | Typed: "Open the signup-page project and check index.html for accessibility." then "Fix those problems in index.html, then check it again." Enter. Hear: "Before: 7 problems. After: 0." **[VERIFY BEFORE RECORDING]** | Echo Check: proof from the real accessibility tree, not a description. |
| 7 | 1:15-1:25 | Monitor back on: island shows done state, the signup page, terminal with `main.py`. | One narrator line (the only VO): "Every change approved by hash. Every run real. Every error read out word for word." | Payoff and the three claims in one breath. |
| 8 | 1:25-1:30 | Title card: "Artemis. A voice-first dev environment for blind developers. HackUMass XIV." Repo URL. | Silence or earcon. | Brand + where to look. |

**Cut list if over 90 s:** drop Shot 5 first, then Shot 4. Never drop Shot 3 or 6.
**If only Hello World is verified on recording day:** ship Shots 1, 2, 3, 7 (claims trimmed to
"Every change approved. Every run real."), 8. About 45 s. That is a fine video.

Note on "errors spoken verbatim": not shown above to save time. If demo 2 is verified, it can replace
Shot 5: "main.py failed with exit code 1. NameError: name 'pritn' is not defined. Did you mean: 'print'?"
**[VERIFY BEFORE RECORDING]**

On-screen hash claim (Shot 7): the approval is bound to a hash of the exact content; if the file
changes while the dialog is open the approval is void (`docs/demo.md`, demo 1 "Point out"). Do not
claim more than that.

## Recording-day production checklist

**Machine**
- [ ] Ubuntu 24.04, GNOME 46, island extension enabled (`gnome-extensions enable artemis-water@bu-hackathonz.github.io`), core started per `docs/demo.md` "Ubuntu run".
- [ ] Run `setup-demo` reset so project names are `hello-world`, not `hello-world-2`. Restart between takes.
- [ ] Do Not Disturb on; no notifications, no update popups. Charger in, suspend off.
- [ ] Resolution 1920x1080, 100% scaling, so the island is legible in Shots 2 and 7. Large cursor off.
- [ ] Wired network; check the API works with one throwaway Hello World before rolling.

**Audio (Orca + ElevenLabs/Artemis voice)**
- [ ] Capture system audio digitally (PipeWire monitor source in OBS), not by a mic at the speakers.
- [ ] Two voices compete: Orca and Artemis. Set Orca to a distinct voice and lower volume, or reduce Orca verbosity so it does not talk over Artemis. Rehearse one full take and listen back.
- [ ] Levels: Artemis voice peaks around -6 dBFS, Orca around -12 dBFS, keyboard mic around -24 dBFS. No clipping.
- [ ] Speech rate: slow enough for a first-time viewer; the captions carry the rest.
- [ ] Separate room mic for keyboard clicks only (makes the "real person, real keys" feel).

**Captions**
- [ ] Burn in every spoken line (judges watch muted). Transcribe from the take, do not paste from the script: model lines vary.
- [ ] High contrast, large font, bottom center, never covering the timer. Also upload an .srt.
- [ ] Mark model-written lines no differently; just transcribe exactly what was said.

**Screen-off take**
- [ ] Record the screen anyway (OBS) for Shot 7 and as evidence; show the viewer black.
- [ ] Visible on-screen timer during the black section proves no hidden cut.
- [ ] Hand-cam inset on the keyboard (phone on a stand) shows Enter / Escape presses.

**If the live agent is slow: cut, don't fake**
- [ ] Never re-voice, splice in stub output, or speed up audio. Never use `-Agent stub` without saying on screen "scripted agent".
- [ ] A long wait may be shortened with a visible cut and a caption "[cut: 12 s waiting for Claude]". Keep the timer, so the cut is obvious.
- [ ] If a demo fails on camera, either keep it (errors are spoken verbatim, that is a feature) or drop that shot entirely. Do not patch it.
- [ ] Do 3 full takes; pick the cleanest honest one.
- [ ] Anything not verified on the day comes out of the video and out of the narration line.
