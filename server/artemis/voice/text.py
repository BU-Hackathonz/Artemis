"""Pure text helpers for voice input. No audio, no Windows calls."""

import re

# "Artemis, ...", "hey Artemis. ..." -> "..."; push-to-talk means no wake-word engine.
# Whisper often hears "Artemis" as a near miss (Artemus, Arthemis, Artimis...), so the middle accepts those,
# and it can split the end off ("Hey Artem, this is creative..."), so the -is/-us ending is optional.
# Whisper often punctuates the greeting ("Hey, Artemis," / "Okay. Artemis"). A possessive or a file name
# ("Artem's code", "artemis.py is broken") is part of the request, not the wake word.
_WAKE = re.compile(r"^\s*(?:(?:hey|ok|okay)[\s,.]+)?ar[td]h?[aei]m(?:[iu]s+)?\b(?!['’.]\w)[\s,.:;!?-]*", re.IGNORECASE)

# Whisper's initial_prompt is capped at about 224 tokens; stay well under it.
PROMPT_MAX_CHARS = 600

# Always first in the Whisper prompt, so the wake word and common demo words are primed even before a project opens.
PROMPT_LEAD = ["Hey Artemis", "Artemis", "Python", "main.py"]

# Opt-in (ARTEMIS_WHISPER_PROMPT=natural; for the stage only once demo.md row 3 passes with it on the demo laptop).
# Whisper reads the prompt as the text just before the audio, so this lead is written like a spoken command rather
# than a bare word list. Deliberately not a demo sentence: a prompt Whisper can copy invites it to. Even so, Whisper
# can echo a prompt on weak audio, which is why the word list above stays the default.
NATURAL_LEAD = "Hey Artemis, create a Python program, print, run it, main.py."
_NATURAL_WORDS = set(re.findall(r"\w+(?:\.\w+)*", NATURAL_LEAD.lower()))


def strip_wake_word(text: str) -> str:
    """Remove a leading "Artemis" (and "hey") from a transcript."""
    return _WAKE.sub("", text, count=1).strip()


def vocabulary_prompt(words: list[str], max_chars: int = PROMPT_MAX_CHARS, natural: bool = False) -> str:
    """Build a Whisper initial_prompt: the wake-word lead first, then file names and symbols, deduped, order kept
    ("Hey Artemis, Artemis, Python, main.py, LoginForm").

    natural=True (opt-in): the spoken-style lead, then the words as one more sentence
    ("... main.py. LoginForm, utils."), deduped against the lead and each other, order kept.
    """
    if natural:
        return _natural_prompt(words, max_chars)
    seen: set[str] = set()
    out: list[str] = []
    size = 0
    for w in PROMPT_LEAD + list(words):
        w = w.strip()
        if not w or w.lower() in seen:
            continue
        cost = len(w) + (2 if out else 0)
        if size + cost > max_chars:
            break
        seen.add(w.lower())
        out.append(w)
        size += cost
    return ", ".join(out)


def _natural_prompt(words: list[str], max_chars: int) -> str:
    seen = set(_NATURAL_WORDS)
    out: list[str] = []
    size = len(NATURAL_LEAD)
    for w in words:
        w = w.strip()
        if not w or w.lower() in seen:
            continue
        cost = len(w) + 2  # the space or ", " before it, or the closing "."
        if size + cost > max_chars:
            break
        seen.add(w.lower())
        out.append(w)
        size += cost
    return f"{NATURAL_LEAD} {', '.join(out)}." if out else NATURAL_LEAD
