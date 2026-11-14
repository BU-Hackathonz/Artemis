# "Hey Artemis" wake-word prototype

A standalone experiment. It is **not connected to Artemis**: it starts no task, sends nothing
anywhere, and cannot approve anything. It listens, and prints what it would have done, so we can
decide with numbers whether hands-free activation is dependable enough for the demo.
Push-to-talk stays the way to talk to Artemis.

## How it works

Microphone, then loudness-based endpointing, then the same local Whisper model as push-to-talk
(`small.en`), then a text match: an utterance counts only if its transcript starts with
"Hey Artemis" (or "Hi", "Okay"; Whisper's spellings of the name are accepted).

- "Hey Artemis, check my codebase" in one breath is captured as one request.
- "Hey Artemis" alone opens an 8 second window; the next utterance is the request.
- Anything else is ignored and shown only as a word count, because it is other people's talk.
- While SAPI is speaking, audio is dropped unheard, so Artemis can't wake itself. **NVDA can't
  tell us when it is speaking**, so with NVDA through the laptop speakers this guard does nothing;
  that case has to be measured (step 4 below).
- No custom model, no new dependency, no account, nothing sent to a cloud service. Audio is held
  in memory only until it has been transcribed.

Files: `wake.py` (pure logic, tested offline), `run.py` (microphone and Whisper),
`test_wake.py`.

## Run it

From `server/`, on Windows, with the voice group installed:

```
uv run --group voice python ../tools/wake_prototype/run.py --minutes 2
```

Useful options: `--device <name or index>` to pick the microphone, `--show-ignored` to see what
ignored speech was transcribed as, `--say "The task is finished."` to have Artemis's SAPI voice
speak every 20 seconds, `--guard never` to switch the self-wake guard off, `--min-rms` to change
the loudness floor (default 0.004).

Offline tests (synthetic audio, scripted transcripts; CI does not run this folder):

```
uv run --group dev --with numpy pytest ../tools/wake_prototype -q
```

## The test that decides go or no-go

Use the microphone and the room conditions planned for the demo. A headset or other close
microphone is expected to pass; the laptop microphone with the speaker turned away is the worst
case and should be tried too.

1. **Heard when meant.** Say "Hey Artemis, check my codebase for security vulnerabilities"
   20 times at demo distance, with people talking nearby. Count `REQUEST` lines whose text is
   right. **Pass: 18 or more.**
2. **Whole request.** In those lines, the text must be the complete sentence, not cut short.
3. **Quiet when not meant.** Run 10 minutes of ordinary conversation near the microphone,
   including people saying "Artemis" while talking about the project. **Pass: 0 wakes.**
4. **Doesn't wake itself.** Run 5 minutes with `--say "Hey Artemis, this is a test."`, once with
   NVDA off (SAPI) and once with NVDA on reading aloud through the speakers. **Pass: 0 wakes.**
5. **Speed.** Read the summary line: the slowest single utterance should be under 3 seconds, or
   the cue comes too late to feel responsive.
6. **The cap.** "cut at the length cap" should be 0 or close to it. A high number means the room
   never goes quiet, and requests will be cut off or delayed.

If any of 1, 3 or 4 fails, the demo uses push-to-talk, and this stays a prototype.

## What it does not do

- It is not in the app: there is no toggle, no "always listening" indicator and no contract
  field yet. Those are for Yaovi (`voice/`), Jun (client) and Avery (contracts) only if the
  test passes.
- The cue plays after the utterance has been transcribed, not the instant the phrase is spoken.
- It was written and tested with synthetic audio only. Nobody has run it with a real voice yet.
