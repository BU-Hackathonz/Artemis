"""Pure text helpers for voice input. No audio, no Windows calls."""

import re

# "Artemis, ...", "hey Artemis. ..." -> "..."; push-to-talk means no wake-word engine.
_WAKE = re.compile(r"^\s*(?:hey\s+)?artemis\b[\s,.:;!?-]*", re.IGNORECASE)

# Whisper's initial_prompt is capped at about 224 tokens; stay well under it.
PROMPT_MAX_CHARS = 600


def strip_wake_word(text: str) -> str:
    """Remove a leading "Artemis" (and "hey") from a transcript."""
    return _WAKE.sub("", text, count=1).strip()


def vocabulary_prompt(words: list[str], max_chars: int = PROMPT_MAX_CHARS) -> str:
    """Build a Whisper initial_prompt from file names and symbols, deduped, order kept."""
    seen: set[str] = set()
    out: list[str] = []
    size = 0
    for w in words:
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
