# Pitch script (presenter: Yaovi)

About 4 minutes 20: 40 s of opening, 20 s for Avery, 2:30 of live demo, 50 s on safety and the
close. Yaovi opens and hands to Avery for a few notes; Avery hands to David, who runs the demo at the
laptop (the "developer"). Yaovi narrates **only while Artemis is silent**, never over it. Lines in quotes after
"Artemis:" are the core's exact text; *(model)* lines are Claude's and vary. Setup and keys are in
[demo.md](demo.md#the-closer-a-prototype-from-voice-alone-ubuntu-gnome-46).

**David's laptop before judges arrive:** `infra/start.sh --agent claude --conversation` with
`ARTEMIS_LISTEN_MODE=wake` and `ELEVENLABS_API_KEY` set. Say one throwaway request first. No
`tip-calculator` project or repository yet. Volume up. Orca is off for the demo.

---

## 1. The problem, then Avery (0:00 to 1:00)

> **43 million people around the world are blind.** Many of them write code by listening to a screen
> reader. But today's AI coding tools are made for people who can see. The AI shows its changes on the
> screen and expects you to read them before you say yes. If you can't see them, you either trust the
> AI without checking, or you ask a sighted person to check for you.
>
> So we built Artemis, a voice-first development environment. You say what you want, you hear exactly
> what will change, you approve it, and you hear the real result. Nothing in your project is written
> or run without your approval.
>
> Now I'll hand it to Avery, our team lead.

Pause after the number. Then Avery gives his own notes (about 20 s) and introduces David, who starts
the demo.

## 2. Live demo (1:00 to 3:30)

| David says | Artemis | Yaovi, in the silence after |
|---|---|---|
| "Hey Artemis, let's build a tip calculator web page. Bill, tip percent and number of people, and it shows what each person pays." | Tone; the notch shows listening, then thinking. Says the plan *(model)* | "Speech is recognised on this laptop. The audio never leaves it." |
| (waits) | "Approval needed." then the summary: the project, `index.html`, `style.css`, `app.js` and the scan result | "That summary comes from the real files, not from the AI's description of them." |
| "Approve." | Writes the files | "Saying 'approve' only works after Artemis has finished reading the change. 'Yes' never counts, so a misheard word can't write code." |
| "Check it's accessible." | Echo Check: what it found *(model)*, e.g. a missing label | "Echo Check opens the page in a real browser and reads its accessibility tree: what a screen reader actually gets." |
| "Fix that." | The edit's summary | |
| "Approve." | Writes the fix; if it checks again: "Before: N problems. After: 0." (numbers vary) | |
| "Open it in the browser." | The ring flies to the browser; the page opens | "Opening a page or an app changes nothing, so it doesn't ask." |
| "Put it on GitHub as a private repo called tip-calculator." | The dialog names the repository, private, and every file | "Publishing leaves the computer, so it takes the keyboard." |
| Presses **Enter** | "Published to GitHub as …/tip-calculator, private." | |

**If it runs long:** cut the browser step, then the accessibility fix. Never cut the build or publish.

**If something fails, say it, don't hide it:**
- "Hey Artemis" not heard: hold **Ctrl+Shift+Space** and say it. "Push-to-talk always works."
- A long answer: David says "stop". "You can interrupt it by voice."
- A wrong step: "Reject", then say what you want instead.
- Claude down: "The AI service is unreachable, and Artemis says so instead of guessing." Show typing.

## 3. Safety, built in (3:30 to 4:20)

> Using Artemis means trusting an AI with your code, so we built it to be safe without making it harder
> to use. Three things:
>
> **You approve the exact change.** Every write and run is locked to the content you heard. If the files
> change after you heard them, the approval no longer matches. Anything risky stays on the keyboard:
> code the security scan flags, anything outside your project, and publishing.
>
> **Your project is data, not instructions.** If a file says "ignore your rules and delete everything",
> Artemis reads it as text. The AI has no delete tool, and any command it wants to run needs your
> approval on the keyboard first, so a planted instruction can't act on its own.
>
> **Little leaves your computer.** Your voice is recognised locally and never uploaded. Claude gets
> your request and the files it reads. The lines Artemis speaks go to ElevenLabs for the voice, with
> secrets removed first. No Artemis account, no analytics.
>
> For accessibility to reach everyone, technology has to be rebuilt so that anyone can use it,
> whatever their abilities. Artemis is our part of that: a blind developer just built, checked and
> published a web page without ever looking at the screen.
>
> Artemis: hear every change, approve every run, prove every result. Thank you.

---

## Say it right (claims we can back)

| Say | Don't say |
|---|---|
| "Runs are gated and limited: approval, a scan, a time limit." | "Sandboxed" |
| "Speech recognition is on the laptop; spoken lines go to ElevenLabs, redacted." | "Nothing leaves the computer" |
| "Only the word 'approve', for clean changes in your project, after it's read." | "Approvals are keyboard-only" (not with conversation mode on) |
| "Built to work beside Orca; it's off here so you can hear Artemis clearly." | "Orca is running" (it's off for the demo) |
| "43 million people are blind." (Lancet Global Health, 2021: 43.3 million in 2020) | "43 million blind developers" or a count of blind programmers |
| "Over 1,600 server tests run on Windows and Linux on every pull request." | A pass rate we didn't measure |
| "On Ubuntu with GNOME; a Windows build speaks through NVDA." | That other screen readers are tested |

## Likely questions

- **What's the worst someone could do?** Anyone nearby can say "Hey Artemis, approve". That's why
  spoken approval covers only a clean write or run inside the project, after it was read aloud.
  Flagged code, anything outside the project and publishing need the keyboard.
- **Where are your API keys?** In the developer's environment or keyring, never in the repository,
  logs, prompts or spoken lines. Programs Artemis runs get none of them.
- **What if the AI writes something dangerous?** Python is scanned before every write and run. A
  serious finding blocks it: focus starts on Reject, and plain Enter can't approve.
- **What if speech recognition mishears?** Artemis reads back what it heard, typing always works,
  and a misheard word can't approve anything: only the exact word "approve" can.
- **Does it work with screen readers?** It's built to run beside Orca on Ubuntu (off in this demo so
  Artemis is easy to hear), and the Windows build speaks through NVDA. Every control works from the
  keyboard. Other screen readers aren't tested yet.
