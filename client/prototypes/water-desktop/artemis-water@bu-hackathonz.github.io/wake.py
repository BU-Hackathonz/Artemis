"""Hands-free "Hey Artemis" for the island: a port of main's server/artemis/voice/wake.py.

Run by the extension (wakeword.js) with its own venv. It reads the microphone with pw-record,
cuts the stream into utterances by loudness (main's Segmenter), transcribes each one locally with
Whisper (tiny.en, CPU), and only reports speech that begins with Artemis's name:

  "Hey Artemis, open VS Code"  -> {"event": "request", "text": "open VS Code"}
  "Hey Artemis" (alone)         -> {"event": "armed"}, and the next utterance is the request

Speech that does not start with the name is dropped unread: nothing is printed, kept or sent.
Everything stays on this computer. stdin takes "mute" / "unmute" (Artemis speaking, or push to talk
owns the mic: frames are dropped unheard, with a short tail), "wake on" / "wake off", "listen" / "stop"
(push to talk: words stream as "partial" events and listening ends by itself on silence) and "quit".
"""

from __future__ import annotations

import json
import os
import re
import select
import subprocess
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import numpy as np

RATE = 16_000
FRAME = RATE // 10  # 100 ms
ARMED_S = 8.0       # after "Hey Artemis" alone, how long the next words count as the request
TAIL_S = 0.6        # stay deaf this long after Artemis stops speaking


def emit(**event):
    print(json.dumps(event), flush=True)


def rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(frame)))) if len(frame) else 0.0


class Segmenter:
    """Loudness endpointing, as in main: speech starts well above the room's noise floor and ends
    after a stretch of quiet; the floor follows the room; a cap ends speech that never stops."""

    def __init__(self, min_rms=0.004, over_floor=3.0, end_silence_s=0.8, min_s=0.4, max_s=12.0, pre_roll_s=0.5):
        self.min_rms, self.over_floor = min_rms, over_floor
        self.end_frames = int(end_silence_s * 10)
        self.min_frames, self.max_frames = int(min_s * 10), int(max_s * 10)
        self.pre = deque(maxlen=int(pre_roll_s * 10))
        self.floor = min_rms
        self.pauses = []  # quiet stretches inside speech that did not end it (seconds)
        self.v_start, self.v_stay = 0.55, 0.35  # voice-probability bars, raised above the room's own (adapt())
        self.reset()

    def reset(self):
        self.speech, self.quiet = [], 0
        self.pre.clear()

    def threshold(self):
        return max(self.min_rms, self.floor * self.over_floor)

    def feed(self, frame, voice=None):
        """voice: the neural VAD's speech probability for this frame (None = no VAD: loudness decides)."""
        level = rms(frame)
        if voice is None:
            loud = level > self.threshold()
        else:
            # hysteresis: starting takes a clear voice, staying in speech takes less (no chopped words)
            loud = voice > (self.v_stay if self.speech else self.v_start) and level > self.min_rms * 0.25
        if not self.speech:
            if not loud:
                self.floor = 0.95 * self.floor + 0.05 * level  # follow the room while it is quiet
                self.pre.append(frame)
                return None
            self.speech = list(self.pre) + [frame]
            self.quiet = 0
            return None
        self.speech.append(frame)
        if loud and self.quiet >= 2:
            self.pauses.append(self.quiet / 10)
        self.quiet = 0 if loud else self.quiet + 1
        if self.quiet >= self.end_frames or len(self.speech) >= self.max_frames:
            audio, n = np.concatenate(self.speech), len(self.speech) - self.quiet
            self.reset()
            return audio if n >= self.min_frames else None
        return None


_NAME = r"art[ae]m[iaeu]s|artimis|artimus|artemas|artemiss|artemise"
# main requires a greeting ("Hey Artemis"); the island also takes the bare name at the start ("Artemis, ...")
_FILLERS = r"(?:(?:um+|uh+|uhm|er+|erm|ah+|oh|so|okay|ok|well|yeah|and|like)\W+){0,3}"  # "um, hey Artemis", "okay so Artemis"
_WAKE = re.compile(rf"^\W*{_FILLERS}(?:(?:hey|hay|hi|hello|okay|ok)\W+)?(?:{_NAME})\b[\s,.:;!?-]*", re.IGNORECASE)


LEARNED: list[str] = []  # how Whisper spells this person's "Artemis" (from the profile)


def _lev(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


_LEAD = re.compile(rf"^\W*{_FILLERS}(?:(?:hey|hay|hi|hello|okay|ok)\W+)?([A-Za-z']+)\b[\s,.:;!?-]*", re.IGNORECASE)


# "Is this the last word?" (as Siri's endpointer weighs "the chance a given word is the final word"): a request
# that stops on one of these, or on a comma or a cut-off word, is not finished, so silence is given longer.
CONTINUES = set("""and or but nor so then because if when while that which who whose where the a an
to of for with without in on at by from into onto about as like than plus minus called named
my your our their its this these those is are was were be can could should would will
please also not""".split())
EXTEND_S = 0.8        # how much longer to wait when the words so far sound unfinished
SENSITIVE_S = 4.0     # after a near miss of the name: how long a looser match still wakes (Siri's second chance)


CANCEL = ("cancel", "cancel that", "stop", "stop that", "stop it", "stop listening", "never mind", "nevermind",
          "forget it", "forget that", "no stop", "no cancel", "abort", "quit", "shut up", "be quiet")
_CANCEL_END = re.compile(r"(?:^|[,.;!?-]\s*|\s)(?:never ?mind|cancel(?: that)?|forget (?:it|that)|stop(?: that| it)?)[\s.!?]*$", re.IGNORECASE)


def is_cancel(text: str) -> bool:
    """"Cancel", "stop", "never mind" on its own, or a request that ends by taking itself back
    ("open VS Code, never mind")."""
    t = re.sub(r"[^a-z ]", "", (text or "").lower()).strip()
    t = re.sub(r"^(please|okay|ok|no|oh)\s+", "", t)
    if t in CANCEL:
        return True
    words = t.split()
    return len(words) >= 2 and _CANCEL_END.search((text or "").strip()) is not None


def sounds_unfinished(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if t.endswith((",", "-", "\u2014", "...", "\u2026", ":")):
        return True
    words = re.findall(r"[A-Za-z']+", t.lower())
    return bool(words) and words[-1] in CONTINUES


def near_miss(text: str) -> bool:
    """Close to the name but not close enough to wake: worth listening harder for a few seconds."""
    m = _LEAD.match(text or "")
    if not m:
        return False
    word = m.group(1).lower()
    greeted = re.match(r"^\W*(hey|hay|hi|hello|okay|ok)\W", text or "", re.IGNORECASE) is not None
    return (4 <= len(word) <= 11 and _lev(word, "artemis") <= 4) or (greeted and len(word) >= 4)


def match_name(text: str, relaxed: bool = False):
    """(request or '' or None, variant to learn or None). A near miss of the name (one or two letters off,
    e.g. "Artimus") also counts; the caller learns it once it really leads to a request. relaxed: in the
    seconds after a near miss, three letters off still counts (said again, it is almost surely the name)."""
    text = text or ""
    m = _WAKE.match(text)
    if m:
        return text[m.end():].strip(), None
    m = _LEAD.match(text)
    if not m:
        return None, None
    word = m.group(1).lower()
    if word in LEARNED:
        return text[m.end():].strip(), None
    if 5 <= len(word) <= 10 and word[0] in "ae" and _lev(word, "artemis") <= 2:
        return text[m.end():].strip(), word
    if relaxed and 4 <= len(word) <= 11 and _lev(word, "artemis") <= 3:
        return text[m.end():].strip(), word
    return None, None


def strip_name(text: str) -> str:
    """Push to talk: "Artemis, open VS Code" and "open VS Code" mean the same request."""
    r = wake_request(text)
    return r if r else text


def wake_request(text: str):
    """None if the words don't start with the name; '' for the name alone; else the request."""
    return match_name(text)[0]


PARTIAL_EVERY_S = 0.5  # how often the words so far are re-transcribed while someone speaks to Artemis
SNIFF_EVERY_S = 0.25   # while someone starts speaking: how often the first moment is checked for the name
SNIFF_HEAD_S = 1.6     # only this much from the start is checked (the name comes first): fast

PROFILE = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "artemis-wake", "profile.json")
HALLUCINATIONS = {"you", "thank you", "thanks for watching", "thank you for watching", "bye", "so", "okay", ""}
STOP = set("a an the to and or of in on for with my me it is be this that please can you could would will i".split())


class Profile:
    """What Artemis learns about how this person speaks, kept on this computer only (profile.json, 0600):
    the requests they made to Artemis (never other speech), how Whisper spells their "Artemis", and how
    long they pause mid-sentence. Delete the file to forget it all."""

    def __init__(self):
        self.d = {"history": [], "names": [], "pauses": []}
        try:
            with open(PROFILE) as f:
                self.d.update(json.load(f))
        except (OSError, ValueError):
            pass
        LEARNED[:] = self.d["names"]

    def save(self):
        tmp = PROFILE + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.d, f)
        os.replace(tmp, PROFILE)

    def learn(self, request, variant=None, pauses=()):
        if request:
            self.d["history"] = (self.d["history"] + [request])[-200:]
        if variant and variant not in self.d["names"]:
            self.d["names"] = (self.d["names"] + [variant])[-20:]
            LEARNED[:] = self.d["names"]
        self.d["pauses"] = (self.d["pauses"] + [round(p, 1) for p in pauses])[-300:]
        self.save()

    def end_silence(self):
        """Stop listening a little after this person's usual longest pause inside a sentence."""
        p = sorted(self.d["pauses"])
        if len(p) < 15:
            return 0.8
        return min(1.4, max(0.6, p[int(len(p) * 0.9)] + 0.25))

    def prompt(self):
        """Whisper reads the prompt as the words just before the audio: their own words come out right."""
        counts = {}
        for r in self.d["history"]:
            for w in re.findall(r"[A-Za-z][\w.'-]+", r):
                if w.lower() not in STOP:
                    counts[w] = counts.get(w, 0) + 1
        top = [w for w, _ in sorted(counts.items(), key=lambda x: -x[1])[:40]]
        return ("Hey Artemis, Artemis. " + ", ".join(top))[:600]
SNIFF_S = 3.0         # without "listen", give up on partials if the name hasn't come by then


def main():
    from faster_whisper import WhisperModel

    threads = min(8, os.cpu_count() or 4)
    model = WhisperModel("tiny.en", device="cpu", compute_type="int8", cpu_threads=max(2, threads - 2))  # fast: the name, live words
    try:  # more accurate for the final request (falls back to tiny if base.en is not downloaded)
        final_model = WhisperModel("base.en", device="cpu", compute_type="int8", cpu_threads=threads)
    except Exception:
        final_model = model
    pool = ThreadPoolExecutor(max_workers=1)  # transcription runs beside the loop, so listening never falls behind
    job = None  # (kind, future) of the transcription under way
    final_pool = ThreadPoolExecutor(max_workers=1)  # the final transcript, started early (speculatively)
    spec = None  # (future, speech frames it covers): the final transcript begun as soon as you paused
    rec = subprocess.Popen(["pw-record", "--rate", str(RATE), "--channels", "1", "--format", "s16", "-"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    # Hearing the speaker, not the room: a neural voice detector (Silero, shipped with faster-whisper) decides
    # when someone starts and stops talking, so fans, hiss and music do not hold the mic open; and the audio
    # given to Whisper is cleaned first (rumble filtered out, the room's steady noise subtracted).
    try:
        from faster_whisper.vad import get_vad_model
        vad = get_vad_model()
    except Exception:
        vad = None  # loudness endpointing, as before
    ring = np.zeros(512 * 31, np.float32)
    room_voice = deque(maxlen=60)  # voice probability of the last 6 s of nobody talking
    room = deque(maxlen=15)  # the last 1.5 s of no-one-talking
    try:
        import noisereduce
    except ImportError:
        noisereduce = None

    try:
        from scipy.signal import butter, sosfilt
        hp = butter(4, 80, 'highpass', fs=RATE, output='sos')
    except ImportError:
        hp = None

    def clean(audio):
        x = audio - np.mean(audio)
        if hp is not None and len(x) > 64:
            x = sosfilt(hp, x)  # 80 Hz high-pass: rumble, desk thumps and mains hum out; the voice untouched
        if noisereduce is not None and len(room) >= 5:
            try:
                x = noisereduce.reduce_noise(y=x, sr=RATE, y_noise=np.concatenate(room), stationary=True, prop_decrease=0.75)
            except Exception:
                pass
        peak = float(np.max(np.abs(x))) or 1.0
        return (x / peak * 0.9).astype(np.float32)  # quiet speakers come out at a normal level

    prof = Profile()
    prompt = prof.prompt()

    def transcribe(audio, final=False):
        audio = clean(audio)
        segments, _ = (final_model if final else model).transcribe(audio, language="en", beam_size=1, vad_filter=False, temperature=0.0,
                                       without_timestamps=True, condition_on_previous_text=False,
                                       initial_prompt=prompt)
        kept = [x for x in segments if x.no_speech_prob < 0.6 and x.avg_logprob > -1.2]  # not noise read as words
        text = " ".join(x.text.strip() for x in kept).strip()
        return "" if text.lower().strip(" .!?,") in HALLUCINATIONS else text

    seg = Segmenter(end_silence_s=prof.end_silence())
    base_end = seg.end_frames  # the person's usual stop (learned); "unfinished" words add EXTEND_S on top
    tail_text = ""             # the latest words of the utterance under way (for "is this the last word?")
    sensitive_until = 0.0      # a near miss of the name opens a short, more forgiving window
    covered = 0                # frames of the utterance the latest live transcript covers
    last_end = 0               # where the speech ended, before the segmenter hands over the utterance

    def learned(request, variant=None):
        """A real request reached Artemis: learn from it, then tune the next one."""
        nonlocal prompt
        prof.learn(request, variant, seg.pauses)
        seg.pauses = []
        prompt = prof.prompt()
        nonlocal base_end
        base_end = int(prof.end_silence() * 10)
    muted, deaf_until, armed_until = False, 0.0, 0.0
    wake_on = True        # "wake on" / "wake off": whether the name starts a request
    manual = False        # "listen": push to talk, the next utterance is the request
    named = False         # the name was heard in the utterance under way
    last_partial, seg_frames = 0.0, 0
    pending = ""
    emit(event="ready")
    try:
        while True:
            # commands from the extension, read unbuffered (a buffered readline can hide a second line from select)
            while select.select([sys.stdin], [], [], 0)[0]:
                chunk = os.read(sys.stdin.fileno(), 4096)
                if not chunk:
                    return  # the extension went away
                pending += chunk.decode()
                if "\n" in pending:
                    break
            cmds, _, pending = pending.rpartition("\n")
            for cmd in [c.strip() for c in cmds.split("\n") if c.strip()]:
                if cmd == "quit":
                    return
                if cmd == "mute":
                    muted = True
                    seg.reset()
                elif cmd == "unmute":
                    muted, deaf_until = False, time.monotonic() + TAIL_S
                elif cmd in ("wake on", "wake off"):
                    wake_on = cmd == "wake on"
                elif cmd == "listen":  # push to talk pressed: listen now, stop on silence
                    manual, armed_until, named = True, time.monotonic() + ARMED_S, False
                    seg.reset()
                    emit(event="armed")
                elif cmd == "stop":    # push to talk released: end the utterance now
                    job = None  # a live-word transcription still running is ignored
                    if manual and seg.speech:
                        audio = np.concatenate(seg.speech)
                        seg.reset()
                        manual, armed_until = False, 0.0
                        text = strip_name(transcribe(audio, final=True))
                        if is_cancel(text):
                            emit(event="cancel")
                            continue
                        if text:
                            learned(text)
                            emit(event="request", text=text)
                        else:
                            emit(event="disarmed")
                    elif manual:
                        manual, armed_until = False, 0.0
                        emit(event="disarmed")
            if armed_until and time.monotonic() > armed_until and not seg.speech:
                armed_until, manual = 0.0, False
                emit(event="disarmed")  # pressed or named, then nothing was said
            raw = rec.stdout.read(FRAME * 2)
            if not raw or len(raw) < FRAME * 2:
                emit(event="error", message="the microphone stopped")
                return
            if muted or time.monotonic() < deaf_until:
                continue  # Artemis is speaking (or the core has the mic): heard by no one
            listening = manual or bool(armed_until)
            if not listening and not wake_on:
                seg.reset()
                continue  # nothing to listen for: frames are dropped unheard
            was_speech = bool(seg.speech)
            frame = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
            ring = np.concatenate([ring[len(frame):], frame])  # the last second, for the VAD
            voice = float(np.ravel(vad(ring))[-3:].max()) if vad else None
            if voice is not None and not seg.speech:
                room_voice.append(voice)
                if len(room_voice) >= 20 and len(room_voice) % 10 == 0:
                    hi = float(np.percentile(room_voice, 90))  # how voice-like the room's own noise gets
                    seg.v_start = min(0.70, max(0.55, hi + 0.2))   # capped: a noisy moment must not make it deaf
                    seg.v_stay = min(0.55, max(0.35, hi + 0.08))
            if not seg.speech and (voice is None or voice < 0.2):
                room.append(frame)  # what the room sounds like when nobody talks: the noise to remove
            # "Is this the last word?": stopping on "and", "the", "that" or a comma waits longer before it ends.
            # Until the words up to the pause are known, hold on (up to 1 s): the decision needs the last word.
            speech_end = len(seg.speech) - seg.quiet if seg.speech else 0
            # As soon as you pause, start the final transcript in the background: if you really are done, it is
            # ready (or nearly) when listening stops; if you go on, it is thrown away.
            if (listening or named) and seg.speech and seg.quiet == 3 and (spec is None or spec[1] < speech_end):
                spec = (final_pool.submit(transcribe, np.concatenate(seg.speech[:speech_end]), True), speech_end)
            last_end = speech_end
            if (listening or named) and seg.speech and covered < speech_end:
                seg.end_frames = base_end + 10
            else:
                unfinished = (listening or named) and sounds_unfinished(tail_text)
                seg.end_frames = base_end + (int(EXTEND_S * 10) if unfinished else 0)
            audio = seg.feed(frame, voice)
            if seg.speech and not was_speech:
                seg_frames, last_partial, named, tail_text, covered = 0, time.monotonic(), False, "", 0
                if listening:
                    emit(event="speech")
            if seg.speech:
                seg_frames += 1
                now = time.monotonic()
                if job and job[1].done():  # a background transcription finished: use it
                    kind, fut, upto = job
                    job = None
                    if kind == "partial":
                        covered = upto  # the words are known up to here
                    text = fut.result()
                    if kind == "sniff":
                        req, _ = match_name(text, relaxed=now < sensitive_until)
                        if req is not None and not named:
                            named = True
                            emit(event="armed")
                            if req:
                                tail_text = req
                                emit(event="partial", text=req)
                    else:
                        shown = strip_name(text) if listening else (match_name(text, relaxed=now < sensitive_until)[0] or "")
                        if shown:
                            tail_text = shown
                            emit(event="partial", text=shown)
                            if is_cancel(shown):  # "... never mind": stop right away, don't wait for silence
                                seg.reset()
                                armed_until, manual, named, tail_text = 0.0, False, False, ""
                                emit(event="cancel")
                if job is None:
                    if listening or named:
                        # words as they come: only for an utterance meant for Artemis
                        pausing = seg.quiet >= 2 and covered < len(seg.speech) - seg.quiet
                        if (now - last_partial >= PARTIAL_EVERY_S or pausing) and seg_frames >= 5:
                            last_partial = now  # a pause asks at once: is that the last word?
                            job = ("partial", pool.submit(transcribe, np.concatenate(seg.speech)), len(seg.speech))
                    elif 4 <= seg_frames <= SNIFF_S * 10 and now - last_partial >= SNIFF_EVERY_S:
                        # listen for the name fast: just the first moment of the utterance, often
                        last_partial = now
                        head = np.concatenate(seg.speech[: int(SNIFF_HEAD_S * 10) + len(seg.pre)])
                        job = ("sniff", pool.submit(transcribe, head), 0)
                continue
            if audio is None:
                continue
            # the utterance ended on silence: this is where listening stops by itself. The final transcript was
            # usually begun at the pause; a live-word transcription still running is simply ignored when it lands.
            job = None
            if spec is not None and spec[1] >= last_end:
                text = spec[0].result()  # nothing was said after the pause: the early transcript is the final one
            else:
                text = transcribe(audio, final=True)
            spec = None
            if listening:
                req, variant = match_name(text)
                text = req if req else text
                armed_until, manual = 0.0, False
                if is_cancel(text):
                    emit(event="cancel")
                elif text:
                    learned(text, variant)
                    emit(event="request", text=text)
                else:
                    emit(event="disarmed")
                continue
            req, variant = match_name(text, relaxed=time.monotonic() < sensitive_until)
            if req is None and named and text:
                req = text  # the name was already heard in this utterance: the final words need not repeat it
            if req is None:
                seg.pauses = []  # someone else's speech: nothing about it is kept
                if near_miss(text):
                    sensitive_until = time.monotonic() + SENSITIVE_S  # say it again and a looser match wakes it
                if named:
                    emit(event="disarmed")
                continue  # not for Artemis: dropped unread
            if req and is_cancel(req):
                emit(event="cancel")  # "Artemis, stop": cancel listening and whatever Artemis is doing
            elif req:
                learned(req, variant)
                emit(event="request", text=req)
            else:
                armed_until = time.monotonic() + ARMED_S
                emit(event="armed")
    finally:
        rec.terminate()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # report, don't crash silently
        emit(event="error", message=str(e))
