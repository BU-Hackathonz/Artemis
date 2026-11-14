"""Pure text helpers for voice input. No audio, no Windows calls."""

import re

# "Artemis, ...", "hey Artemis. ..." -> "..."; push-to-talk means no wake-word engine.
# Whisper often hears "Artemis" as a near miss (Artemus, Arthemis, Artimis...), so the middle accepts those,
# and it can split the end off ("Hey Artem, this is creative..."), so the -is/-us ending is optional.
_WAKE = re.compile(r"^\s*(?:(?:hey|ok|okay)\s+)?ar[td]h?[aei]m(?:[iu]s+)?\b[\s,.:;!?-]*", re.IGNORECASE)

# Whisper's initial_prompt is capped at about 224 tokens; stay well under it.
PROMPT_MAX_CHARS = 600

# Always first in the Whisper prompt, so the wake word and common demo words are primed even before a project
# opens. Whisper reads the prompt as the text just before the audio, so it is written like a spoken command
# rather than a bare word list. Deliberately not a demo sentence: a prompt Whisper can copy invites it to.
PROMPT_LEAD = "Hey Artemis, create a Python program, print, run it, main.py."
_LEAD_WORDS = set(re.findall(r"\w+(?:\.\w+)*", PROMPT_LEAD.lower()))


def strip_wake_word(text: str) -> str:
    """Remove a leading "Artemis" (and "hey") from a transcript."""
    return _WAKE.sub("", text, count=1).strip()


def vocabulary_prompt(words: list[str], max_chars: int = PROMPT_MAX_CHARS) -> str:
    """Build a Whisper initial_prompt: the spoken-style lead, then file names and symbols as one more
    sentence ("... main.py. LoginForm, utils."), deduped against the lead and each other, order kept."""
    seen = set(_LEAD_WORDS)
    out: list[str] = []
    size = len(PROMPT_LEAD)
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
    return f"{PROMPT_LEAD} {', '.join(out)}." if out else PROMPT_LEAD
