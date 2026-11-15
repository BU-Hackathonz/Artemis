# Artemis voice persona (draft, proposal)

Status: **proposal, not agreed.** Nothing in the code changes until the owners of each line agree,
and lines that `docs/contracts.md` fixes word for word change there first. Owner of this doc:
Yaovi (voice). Lines live in core (Avery), policy, agent and conversation (David) and client (Jun).

Goal: Artemis sounds like a voice that works with you while you code, not a form reading status.

## 1. Rules for every spoken line

1. **Artemis speaks as itself, in the first person.** "I created main.py." Never "Claude", never
   "the system", never "the user". The activity log may keep technical detail (model name, codes).
2. **Outcome first, in five words or fewer.** "Done." "It failed." "Blocked." "I need a decision."
3. **Then one fact.** A file, a line number, a count, an exit code. One, not three.
4. **Then, if there is more, offer it.** "Say repeat for the details." or "Say what changed to hear
   the files." Never read a list the user didn't ask for.
5. **Errors come word for word, first.** The exit code and the first error line exactly as the
   program wrote them, before any explanation. This rule beats rule 2.
6. **Plain words.** No jargon the user didn't say first. Numbers as digits. File names as written.
7. **One sentence per idea, each ending in a full stop.** A screen reader reads a missing stop as a
   run-on (`python -m artemis.voice.speechlint` checks the server's strings).
8. **No em dashes, no markdown, no emoji** in anything spoken (`agent/speech_text.py` strips markdown).
9. **Never over the user.** Push-to-talk stops speech first. Progress lines are `low` and may be
   dropped while Artemis is still talking.
10. **Approvals stay keyboard only.** Every approval line ends with the keys, and no spoken word
    approves anything.

## 2. Three detail levels

Tied to the existing answer-style setting (`TaskContext.style.verbosity`, contracts.md §3.1 and
§5.4: "keep it brief", "normal mode", "deep dive"). The level changes how much follows the outcome,
never the outcome itself, the error text or the approval keys.

| Level | What is spoken | Example after a run |
|---|---|---|
| brief | Outcome and the one fact | "Done. It printed Hello, World!" |
| normal (default) | Outcome, one fact, the offer | "Done. main.py printed Hello, World! Say repeat to hear it again." |
| detailed | Outcome, the facts, a short why | "Done. main.py ran in 81 milliseconds and printed Hello, World! No security findings." |

## 3. Current lines and proposed rewrites

`C` = fixed word for word in `docs/contracts.md` (needs a contract change first). Owner in brackets.
Lines not listed keep their current wording.

| Where | Current | Proposed | |
|---|---|---|---|
| core/app.py:367 (Avery) | Artemis is ready. Hold Control Shift Space and speak, or type a request. | Ready. Hold Control Shift Space and talk, or type. | C |
| core/app.py:368 (Avery) | Artemis is ready. Use the Start listening button and speak, or type a request. | Ready. Use Start listening and talk, or type. | C |
| core/session.py:62 (Avery) | Still working. | Still working. Reading main.py. (the last progress step, once, after about 8 s) | C |
| core/session.py:363 (Avery) | Still working on the last request. Say cancel to stop it, or wait. | I'm still on the last one. Say cancel to stop it. | C |
| core/session.py:326 (Avery) | Sorry, that didn't work. Please say it again. | I didn't catch that. Say it again, or type it. | |
| core/session.py:257 (Avery) | The task failed. {reason} You can try again. | That failed. {reason} Try again, or say it differently. | C |
| core/session.py:356 and conversation/controller.py:31 | Use the keyboard to decide: Enter on the dialog to approve, Escape to reject. | Decide on the keyboard: Enter approves, Escape rejects. | C |
| policy/gateway.py:152 (David) | main.py ran successfully. It printed: Hello, World! | Done. main.py printed: Hello, World! | C |
| policy/gateway.py:143 (David) | main.py ran successfully. It printed nothing. | Done. main.py printed nothing. | |
| policy/gateway.py:149 (David) | main.py ran successfully. It printed a lot, ending with: … | Done. main.py printed a lot. The last line: … Say repeat for more. | |
| policy/gateway.py:154 (David) | main.py failed with exit code 1. NameError: … | Unchanged: the error comes first, word for word (rule 5). | C |
| policy/gateway.py:139 (David) | main.py was stopped after 10 seconds because it took too long. | Stopped. main.py ran past 10 seconds. | |
| policy/gateway.py:536 (David) | Create project hello-world with 1 file: main.py, 1 line. No security findings. Enter to approve, Escape to reject. | I need a decision. Create main.py, 1 line, in hello-world. No security findings. Enter approves, Escape rejects. | C |
| policy/gateway.py:527 (David) | Blocked: … Escape to reject, or use the override key to approve anyway. | Blocked. … Escape rejects. The override key approves anyway. | C |
| policy/gateway.py:297, 320, 345 (David) | Starting project …. / Opening project …. / Reading …. | Unchanged (short progress lines, `low`). | |
| agent/claude.py:149 (David) | Claude is busy, retrying. | Give me a second, I'm busy. Retrying. | |
| agent/claude.py:86 (David) | Claude is still busy. Try again in a minute. | I'm still busy. Try again in a minute. | |
| agent/claude.py:88 (David) | No internet connection to Claude. | I can't reach the internet. Check the connection. | |
| agent/claude.py:91 (David) | Claude returned an error. | Something went wrong on my side. Try again. | |
| agent/claude.py:84 (David) | The Claude API key is missing or invalid. | I can't sign in. The API key is missing or wrong. | |
| core/app.py:373 and voice mic errors (Avery, Yaovi) | Microphone not available: … Check the microphone, or type the request. | I can't hear the microphone. Check it, or type. (device detail stays in the log) | |
| conversation/controller.py:226 (David) | I'll do that next. | Got it. That's next. | |
| conversation/controller.py:248 (David) | I finished: …. | Done: …. | |

## 4. Not changed by this doc

- The four Whisper settings and their defaults.
- NVDA first, never both, and never changing NVDA's voice or rate.
- Approvals are keypresses only.
- What is logged: the log keeps every line in full, including technical detail.

## 5. Order of work

1. Agree the rules (section 1) and levels (section 2).
2. Contract change for the `C` lines, owners on both sides tagged on the board.
3. One small PR per owner's folder, each listing every line it changes and a test for it.
4. Hardware pass with NVDA: every changed line heard once, with NVDA's own voice.
