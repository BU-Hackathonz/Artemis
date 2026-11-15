"""Strip markdown before text is spoken (design A9). The log keeps the original text.

Code is never read out symbol by symbol: a fenced block becomes one spoken pointer to the log,
and inline code that isn't a plain name (a file, a function, a variable) becomes "a snippet".
A model answer is also shaped for the ear (spoken_answer): paths become file names, operator
symbols are dropped, run-on sentences get their full stop, and a long answer is cut to a few
sentences with a pointer to the log.
"""

import logging
import re

from artemis.voice.speechlint import run_ons

log = logging.getLogger(__name__)

# A whole fenced block, up to its closing fence or the end of the text if it never closes.
_BLOCK = re.compile(r"^[ \t]*```.*?(?:^[ \t]*```[ \t]*$|\Z)", re.MULTILINE | re.DOTALL)
_INLINE = re.compile(r"`([^`\n]+)`")
# Inline code that sounds fine: a name or a relative path made of names, optionally called.
_NAME = re.compile(r"[\w.-]+(?:/[\w.-]+)*(?:\(\))?")
_IN_LOG = "The code is in the log."
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_BULLET = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+", re.MULTILINE)
_QUOTE = re.compile(r"^\s*>\s?", re.MULTILINE)
_RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$", re.MULTILINE)
_EMPHASIS = re.compile(r"(\*\*\*|\*\*|\*|~~)(?=\S)(.+?)(?<=\S)\1")
# Underscores only count at word edges, so my_file_name and __init__.py are left alone.
_UNDERSCORE = re.compile(r"(?<![\w.])(_{1,3})(?=[^\s_])(.+?)(?<=[^\s_])\1(?!\w|\.\w)")


def _inline(match: re.Match) -> str:
    code = match.group(1).strip()
    if _NAME.fullmatch(code) and not code.startswith(("..", "/")) and "//" not in code:
        return code.removesuffix("()")
    return "a snippet"


_MARK = "\x01"  # private: put in front of a line whose markdown marker for_speech removed
_ENDS_SENTENCE = re.compile(r"[.!?\u2026:;,][\"')\]]*$")
# A Python traceback: its header and the indented frames. The exception line after it stays.
_TRACEBACK = re.compile(r"^[ \t]*Traceback \(most recent call last\):[ \t]*\n(?:[ \t]+\S.*\n?)*", re.MULTILINE)
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_RULE = re.compile(r"^\s*\|?[\s:|-]*-{2,}[\s:|-]*\|?\s*$")
# Emoji and pictographs: a screen reader names each one ("party popper"), which is noise.
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B50\u2B55\uFE0F\u200D]")


def _table(text: str, stops: bool) -> str:
    """A table is read by row, cells joined by commas, and the rule line under the header is dropped."""
    out = []
    for line in text.split("\n"):
        if _TABLE_RULE.match(line):
            continue
        if _TABLE_ROW.match(line):
            line = ", ".join(c.strip() for c in line.strip().strip("|").split("|") if c.strip())
            if stops:
                line = _MARK + line
        out.append(line)
    return "\n".join(out)


def _stops(text: str) -> str:
    """Give a full stop to each line that stripping made the end of a sentence: a heading, a list item,
    a table row or the last line of a paragraph with no punctuation of its own. A line that merely wraps is left."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        marked = line.startswith(_MARK)
        line = line.removeprefix(_MARK).rstrip()
        ends_paragraph = i + 1 < len(lines) and not lines[i + 1].strip()
        if line and (marked or ends_paragraph) and not _ENDS_SENTENCE.search(line):
            line += "."
        lines[i] = line
    return "\n".join(lines)


def for_speech(text: str, stops: bool = False) -> str:
    """Plain text for the ear. With stops=True a heading, a list item, a table row and a paragraph end get a full stop."""
    text = _BLOCK.sub(f" {_IN_LOG} ", text)
    # One pointer is enough when several blocks follow each other.
    text = re.sub(rf"(?:{re.escape(_IN_LOG)}\s*){{2,}}", f"{_IN_LOG} ", text)
    text = _TRACEBACK.sub(f" {_IN_LOG} " if stops else " ", text)
    if stops:
        text = _table(text, stops)
    text = _INLINE.sub(_inline, text)
    text = _EMOJI.sub("", text)
    text = _IMAGE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _RULE.sub("", text)
    text = _HEADING.sub(_MARK if stops else "", text)
    text = _QUOTE.sub("", text)
    text = _BULLET.sub(_MARK if stops else "", text)
    for _ in range(3):  # nested emphasis such as **bold with *italic* inside**
        text = _EMPHASIS.sub(r"\2", text)
    text = _UNDERSCORE.sub(r"\2", text)
    text = text.replace("`", "")
    if stops:
        text = _stops(text)
    return re.sub(r"\s+", " ", text).strip()


# The prompt asks for two sentences unless the developer wants detail; this is the ceiling even then.
MAX_SENTENCES = 4
_REST_IN_LOG = "The rest is in the log."
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])")
# A full stop after these does not end a sentence: "see Dr. Smith", "files, etc. The".
_ABBREVIATION = re.compile(r"(?:\b(?:Dr|Mr|Mrs|Ms|Prof|St|vs|No|Inc|Ltd|Jr|Sr)|\be\.g|\bi\.e|\betc)\.$", re.IGNORECASE)
# A relative or absolute path: keep its last part, the file or folder name. URLs are left alone.
_PATH = re.compile(r"(~/|/|\.{1,2}/|[A-Za-z]:\\)?((?:[\w.-]+[/\\])+)([\w-]+(?:\.[\w-]+)*)")
# A word that starts like a host name (github.com/org/repo): protected like a URL.
_HOST_LIKE = re.compile(r"[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}/")
# "Next.js", "Node.js": a product name, not a file.
_PRODUCT = re.compile(r"(?:next|node|vue|nuxt|three|d3|p5|ember|alpine|backbone|express|chart|socket)\.(?:js|io)", re.I)
# Symbols that are noise when spoken: ==, !=, ->, &&, {}, [], and so on. ! ends a sentence, so it stays.
# Kept: C#/F#, $ before a digit, @ inside a word, & between letters. "~5" is "about 5"; ";" is a full stop.
_SYMBOLS = re.compile(
    r"(?P<keep>(?<!\w)[A-Za-z]#(?!\w)|\$(?=\d)|(?<=\w)@(?=\w)|(?<=[A-Za-z])&(?=[A-Za-z]))"
    r"|(?P<about>~(?=\d))"
    r"|(?P<stop>;+)"
    r"|(?:==|!=|<=|>=|->|=>|&&|\|\||[{}\[\]~^$#@\\=<>|&])+"
)
_CALL = re.compile(r"\b(\w+)\(\)")


def _file_name(match: re.Match) -> str:
    # Only a real path: a root (~/ / ./ ../ C:\) in front, or a last part with a real file extension and
    # no all-digit part. "25/40", "11/15/2026", "and/or", "HTML/CSS/JS", "React/Next.js" stay.
    root, folders, name = match.groups()
    if root:
        return name
    parts = re.split(r"[/\\]", folders)[:-1] + [name]
    if any(p.isdigit() for p in parts) or _PRODUCT.fullmatch(name):
        return match.group(0)
    if re.search(r"\.[A-Za-z]\w{0,5}$", name):
        return name
    return match.group(0)


def _shorten_path(word: str) -> str:
    if "://" in word or _HOST_LIKE.match(word):
        return word
    return _PATH.sub(_file_name, word)


def _symbol(match: re.Match) -> str:
    if match.group("keep"):
        return match.group("keep")
    if match.group("about"):
        return "about "
    if match.group("stop"):
        before = match.string[: match.start()].rstrip()
        return "" if before[-1:] in ".!?,:;" or not before else ". "
    return " "


def _sentences(text: str) -> list[str]:
    out: list[str] = []
    for part in (s for s in _SENTENCE.split(text) if s):
        if out and _ABBREVIATION.search(out[-1]):
            out[-1] += " " + part
        else:
            out.append(part)
    return out


def _log_run_ons(spoken: str) -> None:
    """speechlint hits that stripping did not cause are only counted: "Yes or No" and "press Escape" are
    fine, so no stop is added. Log the word positions, never the text (it may hold the developer's code)."""
    positions, cursor = [], 0
    for hit in run_ons(spoken):
        found = spoken.find(hit, cursor)
        if found >= 0:
            positions.append(len(spoken[:found].split()) + 1)
            cursor = found + len(hit)
    if positions:
        log.info("Spoken answer has %d possible run-on(s), left as is, at word(s) %s", len(positions), positions)


def spoken_answer(text: str) -> str:
    """What the developer hears for a model answer or question. The log keeps the original."""
    spoken = for_speech(text, stops=True)
    spoken = " ".join(_shorten_path(w) for w in spoken.split(" "))
    spoken = _CALL.sub(r"\1", spoken)
    spoken = _SYMBOLS.sub(_symbol, spoken)
    _log_run_ons(spoken)
    spoken = re.sub(r"\s+([.,!?;:])", r"\1", re.sub(r"\s+", " ", spoken)).strip()
    sentences = _sentences(spoken)
    if len(sentences) > MAX_SENTENCES:
        log.info("Spoken answer cut from %d to %d sentences; the log has all of it", len(sentences), MAX_SENTENCES)
        kept = sentences[:MAX_SENTENCES]
        # Their next request answers a question: never cut it, even when a closing remark follows it.
        questions = [s for s in sentences[MAX_SENTENCES - 1 :] if s.endswith("?")]
        if questions:
            return " ".join(sentences[: MAX_SENTENCES - 1] + [_REST_IN_LOG, questions[-1]])
        spoken = " ".join(kept + [_REST_IN_LOG])
    return spoken
