"""The built-in phrases Artemis handles itself, without the coding agent.

A phrase only counts when it is the whole utterance (after the wake word, greetings and
"please" are removed). "Stop the server from crashing" is a request for the agent; "stop" is
not. There is no model here and no guessing: an utterance either is one of these phrases or
it goes to the agent unchanged.

Nothing in this table can approve a write or a run, hide a warning, or run code.
"""

import re

INTENTS: dict[str, frozenset[str]] = {
    # Ends the task. Deliberately needs the word "cancel" (or "never mind"): see stop_speaking.
    "cancel": frozenset(
        {
            "cancel", "cancel that", "cancel it", "cancel the task", "cancel this", "cancel everything",
            "never mind", "nevermind", "abort", "stop the task", "stop working", "stop what you are doing",
        }
    ),
    # Stops the voice only. The task keeps going.
    "stop_speaking": frozenset(
        {
            "stop", "stop talking", "stop speaking", "be quiet", "quiet", "hush",
            "shut up", "enough", "thats enough", "okay stop", "ok stop",
        }
    ),
    "repeat": frozenset(
        {
            "repeat", "repeat that", "repeat it", "say that again", "say it again", "again",
            "what did you say", "what was that", "come again", "pardon",
        }
    ),
    "status": frozenset(
        {
            "status", "whats the status", "what is the status", "what are you doing",
            "what are you working on", "whats happening",
            "what is happening", "whats going on", "what is going on", "where are you",
            "where are we", "are you done", "are you done yet", "are you finished",
            "are you still working", "is it done", "hows it going", "how is it going",
        }
    ),
    "changed_files": frozenset(
        {
            "which file did you change", "which files did you change", "what file did you change",
            "what files did you change", "what did you change", "what changed", "what have you changed",
            "which files changed", "what did you write",
        }
    ),
    "brief": frozenset(
        {"keep it brief", "be brief", "brief mode", "keep it short", "short answers", "be concise"}
    ),
    "detailed": frozenset(
        {"deep dive", "go deeper", "more detail", "more details", "explain in detail", "tell me more", "detailed mode"}
    ),
    "normal": frozenset({"normal mode", "default mode", "back to normal"}),
    "visual": frozenset({"visual mode"}),
    "code": frozenset({"code mode"}),
    "slower": frozenset({"slower", "slow down", "speak slower", "talk slower", "too fast"}),
    "faster": frozenset({"faster", "speed up", "speak faster", "talk faster", "too slow"}),
    # Only means something while an approval is open, and even then it never approves:
    # the developer is told to use the keyboard.
    "approval_word": frozenset({"approve", "approve it", "yes", "reject", "reject it", "no"}),
}

_BY_PHRASE: dict[str, str] = {phrase: name for name, phrases in INTENTS.items() for phrase in phrases}

# Whisper writes the wake word several ways.
_WAKE = r"(?:hey |hi |hello |ok |okay )?(?:artemis|artemus|artimis|artemas)"
_LEADING = re.compile(rf"^(?:{_WAKE}|um+|uh+|er+|so|well|please|hey|can you|could you|would you|will you)\s+")
_TRAILING = re.compile(rf"\s+(?:please|{_WAKE}|now|thanks|thank you|for me)$")


def simplify(text: str) -> str:
    """Lowercase, no punctuation, no wake word or politeness around the edges."""
    words = re.sub(r"['’]", "", text.lower())
    words = " ".join(re.sub(r"[^\w\s]", " ", words).split())
    if re.fullmatch(_WAKE, words):
        return ""
    previous = None
    while words != previous:
        previous = words
        words = _TRAILING.sub("", _LEADING.sub("", words))
    return words


def match(text: str) -> str | None:
    """The intent this utterance is, or None when it is a request for the agent."""
    return _BY_PHRASE.get(simplify(text))
