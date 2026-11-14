# "Hey Artemis" wake word: contract proposal

Status: **proposal for Avery (contracts), written 2026-11-14 by David's Opus 2 while David's Fable,
who owns the prototype, is away until about 3:20 PM.** Nothing is built. David asked for wake-word
work to start now and the measurements to run later. This only proposes the interface so that, if
the measurements pass, nothing waits on a contract. If they fail, push-to-talk stays the way to talk
and this document is dropped. Owners as in `AGENTS.md`.

## Where it stands

- `tools/wake_prototype` (branch `voice/wake-prototype`, David's Fable): listens, endpoints by
  loudness, uses the same local `small.en` model, and counts an utterance only when it starts with
  "Hey Artemis" (the matching now also exists in `voice/` as #98's close-spelling list). It starts no
  task, sends nothing and cannot approve anything. It has never run with a real voice.
- Its README defines go or no-go: 18 of 20 heard when meant at demo distance, 0 false wakes in 10
  minutes of conversation, 0 self-wakes in 5 minutes of Artemis speaking (SAPI and NVDA through the
  speakers), the slowest utterance under 3 s.
- Known risk: the self-wake guard drops audio while SAPI speaks, but **NVDA cannot tell us when it is
  speaking**, so with NVDA through the speakers the guard does nothing.
- Today the contract has `POST /voice/listen {start|stop}` and `voice.status {voice_ready,
  screen_reader}`. No wake field exists.

## What the contract would need (smallest version)

1. **State, not a command.** Extend `voice.status` with `wake`: `"off"` (default), `"listening"`,
   `"paused"` (the core is speaking, so wake audio is being dropped) or `"unavailable"` (no mic, the
   model not loaded). The client shows and announces it; it is the "always listening" indicator the
   spec requires, so the microphone is never open in secret.
2. **One control.** `POST /voice/wake {action: "on"|"off"}` → `204`. It is only reachable with the
   launch token like every route, and it does not persist across launches unless the developer sets
   `ARTEMIS_WAKE=on`. The default stays off.
3. **A wake uses the existing path.** When the phrase is heard the voice layer behaves exactly as
   `POST /voice/listen start` does today (the spoken cue, stop current speech, capture the request,
   the same Whisper transcript, the same `transcript.final`). The only new thing on the event is
   `intent: "wake"` for logging and tests. The request then goes through the conversation
   controller like any other, so "approve", "yes" and the rest are still never applied.
4. **No approvals by wake.** Saying "Hey Artemis" never approves, rejects or runs anything. A wake
   while an approval is open reads the approval again (the controller's existing behavior for the
   wake word alone).

## Rules for the voice layer (Yaovi's folder, behind the flag)

- **Mute while the core speaks.** The core already knows when it is announcing. It tells the voice
  layer "speaking" and "idle" so wake audio is dropped during speech and for a short tail after it.
  This also covers NVDA for the announcements Artemis makes through it (the core knows it sent them);
  it does not cover NVDA reading the screen on its own, which is why headset or close-mic use is
  the mitigation if test 4 fails with NVDA through the speakers.
- **No audio kept.** Audio lives in memory until transcribed, as in the prototype; nothing is
  written and nothing leaves the machine.
- **Push-to-talk and the Start listening button never depend on this.** If the wake detector fails
  to start, `wake` becomes `"unavailable"` and a fixed sentence says so once.
- **A conversational window** (spec step 3, later): after a wake the next utterances need no second
  "Hey Artemis" until a silence timeout (proposed 8 s, as in the prototype) or "stop listening".

## Client (Jun) and docs

- A visible and announced wake state, a toggle in the full window (never only in the notch), and a
  keyboard way to turn it off. The notch's listening state should read from `voice.status.wake`.
- `docs/demo.md` and the stage runbook get a step to turn wake on only after the measurements pass,
  and a line that push-to-talk is the fallback.

## Gate and order

1. Measurements on the demo laptop, with David or a person speaking (David's Fable owns the test;
   NVDA through the speakers is the deciding case).
2. If they pass: Avery accepts the contract change; Yaovi's `voice/` flag; Jun's indicator; then a
   by-ear rehearsal with NVDA on. If they fail: stop, keep push-to-talk, keep this as a record.
3. Never merged to the demo path before a by-ear pass on the stage laptop.

## Questions for Avery

- `voice.status` extension versus a separate event for wake state.
- Whether the "speaking/idle" signal to the voice layer is a core-internal call (proposed) or an event.
- Whether `intent: "wake"` on `transcript.final` is acceptable, or the wake should be invisible in
  the contract and only appear in logs.
