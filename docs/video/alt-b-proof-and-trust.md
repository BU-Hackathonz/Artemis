# Artemis demo video, script B: "It proves its results" (Proof and Trust)

Target 2:25. Also fits the AI Security track. Ubuntu 24.04 + GNOME 46, island notch at the top, Orca on, ElevenLabs as Artemis's voice.

**Rules for recording**
- Quoted Artemis lines are the exact core strings from docs/demo.md (from `policy/gateway.py`, `core/session.py`, `echo/check.py`). Lines marked *(model)* vary per run. Re-transcribe the captions from the actual take.
- Only demo 1 (Hello World) has run with the real agent. Every shot marked **[VERIFY BEFORE RECORDING]** must be rehearsed live with `-Agent claude` on the Ubuntu laptop first. Cut the shot if it doesn't pass. Never stage it.
- The keyboard is the only way to approve. Show the hands on the keys in every approval shot.
- Don't mention preference learning, custom phrases, or anything else that isn't on `main`.

---

## Shots

| # | Time | On screen | Heard | Purpose |
|---|---|---|---|---|
| 1 | 0:00–0:12 | Black, then a dark desktop with a blurred monitor. Text types in: *"An AI agent can write and run code on your machine."* then *"Now imagine you can't see what it's doing."* | Narration: "AI coding agents write files and run commands. If you're blind, you can't glance at the diff. You have to take its word for it." | The problem: blind trust in an agent is the risk |
| 2 | 0:12–0:20 | Ubuntu desktop, island notch at the top. Title card: **Artemis**, *a voice-first dev environment that proves its results* | Narration: "Artemis doesn't ask for trust. It proves its results." | Thesis |
| 3 | 0:20–0:40 | Request in the island (voice, or typed if the "Heard:" check fails). Approval modal opens. Close-up on the hands: **Enter**. | User: "Create a Python program that prints Hello World, and run it." Artemis: "Create project hello-world with 1 file: main.py, 1 line. No security findings. Enter to approve, Escape to reject." Enter. "Created main.py, 1 line." "Run main.py with Python. No security findings. Enter to run, Escape to reject." Enter. "main.py ran successfully. It printed: Hello, World!" | Real agent, real run, real output read back. **Verified (demo 1).** Re-check on Ubuntu with ElevenLabs. |
| 4 | 0:40–0:55 | Overlay diagram: approval card → `sha256:…` → "hash again before writing → mismatch = refused". Then an approval open while the user says "yes". | Narration: "Every write and every run waits for a key press, bound to a SHA-256 of the exact change. Artemis hashes again right before it acts, and if anything changed, nothing happens." User: "yes." Artemis: "Use the keyboard to decide: Enter on the dialog to approve, Escape to reject." | A misheard "yes" can't approve anything. The hash binding is in `policy/hashing.py`. **[VERIFY BEFORE RECORDING]** the spoken-yes reply on Ubuntu. Don't film a tamper shot unless it's rehearsed. |
| 5 | 0:55–1:10 | Request typed. Output appears in the island. | User: "Create a Python program that calls pritn with Hello World, and run it." (Enter, Enter) Artemis: "main.py failed with exit code 1. NameError: name 'pritn' is not defined. Did you mean: 'print'?" User: "Fix it and run it again." (Enter, Enter) "... It printed: Hello, World!" | Failures are reported honestly: the real exit code and the real error. **[VERIFY BEFORE RECORDING]** |
| 6 | 1:10–1:30 | `old-app`. A red BLOCKED modal; focus is on Reject. Hands: Enter (nothing happens), then Escape. | User: "Open the old-app project and run main.py." Artemis: "Blocked: 1 high-severity issue, main.py line 5, subprocess call with shell=True identified, security issue, and 1 more. Run main.py with Python. Escape to reject, or use the override key to approve anyway." Narration: "Bandit scans before every write and run. A blocked action starts on Reject, and Enter can't approve it." | Static scan gate and a safe default. **[VERIFY BEFORE RECORDING]** |
| 7 | 1:30–1:52 | `calc` README with the injected text highlighted ("ignore your rules…"). The island stays clear: no approval appears. | User: "Open the calc project and summarize it." Artemis *(model)*: a summary that says the README contains instructions aimed at an AI and that it ignored them. Narration: "This README tries to hijack the agent. It can't work, and not because we filter it. A summary task is never given write or run tools. The capability isn't there." | Prompt injection is defeated by removing the capability, not by a filter (the AI Security point). **[VERIFY BEFORE RECORDING]**: wording varies, so caption the real line. |
| 8 | 1:52–2:15 | `signup-page`: the page in a browser, with the Echo Check result next to it. Counter **7 → 0**. | User: "Open the signup-page project and check index.html for accessibility." Artemis: "Echo Check found 7 problems: 2 critical, 2 serious, 3 moderate. Critical: a button has no accessible name; …" User: "Fix those problems in index.html, then check it again." (Enter) Artemis: "Before: 7 problems. After: 0. …" | Checks the real accessibility tree instead of describing the screen. **[VERIFY BEFORE RECORDING]** If After isn't 0, show "Fix the rest." or cut. |
| 9 | 2:15–2:27 | Four-line card, then the logo. | Narration: "Keyboard approvals bound to the exact change. Real runs: no shell, fixed arguments, a scrubbed environment, a timeout, and the whole process tree killed. Injection-proof by design. Artemis proves its results." | Recap of the trust guarantees and the close |

Optional: a runner timeout shot (cut it if time runs short) **[VERIFY BEFORE RECORDING]**. Leave it out unless it's rehearsed with a real hang.

---

## Caption plan (for muted viewing)

- **Always on:** burned-in captions for every spoken line, with the speaker labeled: `YOU:`, `ARTEMIS:`, `ORCA:`, and the narration in italics. Use large high-contrast text in the bottom third, kept clear of the island at the top.
- **Key-press badges:** a keycap overlay (`ENTER`, `ESC`, `ENTER (ignored)`) at each approval, so a muted viewer sees that the keyboard approves, not the voice.
- **Proof tags:** a short top-right label on each shot: `REAL RUN`, `REAL ERROR`, `BLOCKED BY SCAN`, `NO WRITE TOOLS`, `REAL A11Y TREE: 7 → 0`.
- **Hash shot (4):** an animated `sha256:` string that locks onto the approval card, and a "voice 'yes' ≠ approval" strike-through.
- **Injection shot (7):** highlight the malicious README line in red with the label "untrusted input". The tool list shows only read tools; the write and run tools are greyed out.
- **End card:** the four guarantees as text, so the recap reads without audio.
- Captions come from the final take's audio, not from this script, because *(model)* lines vary.
