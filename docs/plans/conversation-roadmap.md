# Conversation, voice and desktop roadmap

Status: **proposal for review**, written 2026-11-14 by David's Opus 2 from the Drive plans
("Conversational Guidance Layer Product Goal", "Conversational Voice and Hey Artemis Implementation
Spec", "Hey Artemis Wake-Word Prototype Plan", "Team Handoff & Technical Context"), this repo's
`docs/architecture.md`, `contracts.md` section 5, `decisions.md` and `demo.md`, and a quick read of
two public voice products (VoiceOS and HeyClicky; public pages only). Nothing here is built unless
it says so. Owners are the ones in `AGENTS.md`; any contract change goes to Avery first.

## Where we are against those plans

| Plan item | State |
|---|---|
| Architecture phases 0 to 3 (stubs, real agent, voice, runner) | Built and merged |
| Phase 3 rehearsal with the real agent (demos 1 to 5) | Not run; deferred by David. `demo.md` rows 6 to 11 stay "not run" |
| Voice spec step 1: interpret what was said | The agent prompt handles self-corrections. Speech accuracy is the live problem (see below) |
| Step 2: follow-ups, interruption, built-in phrases | Built (`contracts.md` 5.4), behind `ARTEMIS_CONVERSATION`; the launcher turns it on |
| Step 3: "Hey Artemis" wake word | Standalone prototype only (`tools/wake_prototype`, branch `voice/wake-prototype`), never run with a real voice |
| Step 4: saved preferences and phrase aliases | Not built |
| Product goal: "Where are we?" guidance layer | Not built; status is one fixed sentence |
| Notch UI (#70) | Code reviewed and frozen; hardware pass pending |
| Startup line, SAPI voice and rate, NVDA voice path | Built; startup line confirmed by ear |

Evidence on speech accuracy (David's laptop, NVDA on): the demo sentence "Hey Artemis, create a
Python program that prints Hello World, and run it" was heard as "Hey Artem, this is creative
Python program with Prince Hello World." The fixes in flight are priming Whisper with the product
words, accepting "Artem", a short post-roll after release, and optional larger-model settings.

## Plan, in order

1. **Finish what is in flight, then rerun the same sentence.** Speech-accuracy fix, then a by-ear
   check with the transcript read from the session log. #70 notch hardware pass with a person using
   NVDA. Real-agent demos 1 to 5 on Avery's laptop. Nothing below starts building before these pass.
2. **Persist answer style (small).** The spec's step 4. Keep brief, deep dive, visual and code
   across sessions. One small local file holding only the style, loaded at session start and saved
   when it changes; a corrupt file is ignored; "reset my preferences" clears it; never any content or
   secrets. `conversation/` (David) plus a small storage hook in the core (Avery). Contract note only.
3. **Richer "Where are we?" (small to medium).** From the controller's own state: last verified
   action, pending approval, next step. Deterministic and spoken from fixed templates first. Event-
   driven narration comes later, behind a flag, and must never describe a failure or a skipped check
   as success.
4. **"Not now" cool-down (small).** After a rejected approval, do not re-propose the same action for
   a while. It only suppresses re-asking and must not touch the gate. Needs a read of how rejections
   reach the agent before the design is final (`agent/`, `policy/`).
5. **Wake word, behind a flag (medium, gated on measurements).** See below.
6. **Voice quality.** With NVDA running, the voice is NVDA's synthesizer (a setting, not code). The
   laptop currently uses the Windows OneCore "David" voice. Try other installed voices first, then a
   better synthesizer if one is found. Try the larger speech model setting once it lands.
7. **In-app key and status screen.** Needs a security design before any code: the app cannot set the
   core's environment after launch, and the key must never be in the UI bundle, logs or a repo file.
   Likely a Tauri command that talks to Windows Credential Manager and a protected core endpoint.
   Review by David (security owner) and Avery. It must fit the notch design (setup lives in the full
   window).
8. **Packaging.** The app starts the core itself (sidecar decision in `decisions.md` 3), then an
   installer. After the demo.

Considered and not copied from the research: cloud transcription, screen capture and drawing, mouse
and keyboard control, voice approvals, always-listening input, cloud memory of conversations, and
third-party tool servers. Each conflicts with local speech, keyboard-only approvals or no secrets in
prompts and logs. Also noted for later, not now: named jobs with a spoken task list, and resuming a
long job from notes (both touch the one-active-task rule).

## Wake word: how to pursue it now

The prototype (`tools/wake_prototype`) listens, endpoints by loudness, transcribes with the same
local `small.en` model, and counts an utterance only if it starts with "Hey Artemis". It starts no
task, sends nothing and cannot approve anything. Its README defines the go or no-go test, which has
never been run with a real voice:

1. Heard when meant: 20 tries at demo distance with people talking nearby; pass is 18 or more with
   the right text.
2. Whole request: the text is the full sentence, not cut short.
3. Quiet when not meant: 10 minutes of conversation near the microphone, including people saying
   "Artemis"; pass is 0 wakes.
4. Does not wake itself: 5 minutes of Artemis speaking, once with SAPI and once with NVDA through the
   speakers; pass is 0 wakes.
5. Speed: slowest utterance under 3 seconds.
6. Length cap rarely hit.

**The known risk:** the prototype's self-wake guard works only for SAPI, because NVDA cannot tell us
when it is speaking. With NVDA through the speakers the guard does nothing, so test 4 decides this.
Mitigations to try in order: a headset or close microphone for the demo; a short mute window after
any announcement the core makes; a longer confirmation window; and the always-available
push-to-talk and keyboard fallback, which stay no matter what.

**Integration, only if the test passes:** a feature flag in `voice/` (Yaovi's lane, covered by Avery's
Claude while Yaovi is out); a visible and audible "listening" state and a clear way to turn it off
(Jun's lane, client); a core field for the wake state (Avery, contract change first); a conversational
window so follow-ups need no second "Hey Artemis", with an inactivity timeout (spec step 3, P1).
Saying "Hey Artemis" never grants permission for a write or a run.

## Decisions needed from David

- Approve the answer-style plan (step 2) so the proposal and contract note can go to Avery.
- Wake word: run the measurements now, using the laptop microphone and a headset.
- The in-app key screen's design approach (step 7).
