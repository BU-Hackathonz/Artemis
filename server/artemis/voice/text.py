"""Pure text helpers for voice input. No audio, no Windows calls."""

import re

# "Artemis, ...", "hey Artemis. ..." -> "..."; push-to-talk means no wake-word engine.
# Whisper often hears "Artemis" as a near miss (Artemus, Arthemis, Artimis...), so the middle accepts those.
_WAKE = re.compile(r"^\s*(?:(?:hey|ok|okay)\s+)?ar[td]h?[aei]m[iu]s+\b[\s,.:;!?-]*", re.IGNORECASE)

# Whisper's initial_prompt is capped at about 224 tokens; stay well under it.
PROMPT_MAX_CHARS = 600

# Always first in the Whisper prompt, so the wake word and common demo words are primed even before a project opens.
PROMPT_LEAD = ["Artemis", "Python", "main.py"]


def strip_wake_word(text: str) -> str:
    """Remove a leading "Artemis" (and "hey") from a transcript."""
    return _WAKE.sub("", text, count=1).strip()


def vocabulary_prompt(words: list[str], max_chars: int = PROMPT_MAX_CHARS) -> str:
    """Build a Whisper initial_prompt: the wake-word lead first, then file names and symbols, deduped, order kept."""
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
