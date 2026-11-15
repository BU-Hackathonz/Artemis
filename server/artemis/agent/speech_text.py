"""Strip markdown before text is spoken (design A9). The log keeps the original text.

Code is never read out symbol by symbol: a fenced block becomes one spoken pointer to the log,
and inline code that isn't a plain name (a file, a function, a variable) becomes "a snippet".
A model answer is also shaped for the ear (spoken_answer): paths become file names, operator
symbols are dropped, run-on sentences get their full stop, and a long answer is cut to a few
sentences with a pointer to the log.
"""

import html
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
_EMPHASIS = re.compile(r"(\*\*\*|\*\*|~~)(?=\S)(.+?)(?<=\S)\1")
# A single star pair; "*args and **kwargs" has no closing star of its own, so it is left alone.
_STAR = re.compile(r"(?<![*\w])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![*\w])")
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
        text = _STAR.sub(r"\1", text)
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
    r"|(?P<cmp>(?<=[\w)\]])[ ]*(?:<=|>=|<|>)[ ]*(?=[\w(\[-]))"
    r"|(?P<and>(?<=\s)&(?=\s))"
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
    if match.group("cmp"):
        return {"<=": " at most ", ">=": " at least ", "<": " less than ", ">": " greater than "}[match.group("cmp").strip()]
    if match.group("and"):
        return "and"
    if match.group("about"):
        return "about "
    if match.group("stop"):
        before = match.string[: match.start()].rstrip()
        return "" if before[-1:] in ".!?,:;" or not before else ". "
    return " "


# A URL (or a host with a path) is kept whole, query string and fragment included, minus trailing punctuation.
_URL = re.compile(r"(?:https?://|www\.)\S*?(?=[.,;:!?)\"']*(?:\s|$))|[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}/\S*?(?=[.,;:!?)\"']*(?:\s|$))")


def _drop_symbols(spoken: str) -> str:
    out, last = [], 0
    for m in _URL.finditer(spoken):
        out += [_SYMBOLS.sub(_symbol, spoken[last : m.start()]), m.group(0)]
        last = m.end()
    out.append(_SYMBOLS.sub(_symbol, spoken[last:]))
    return "".join(out)


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
    spoken = for_speech(html.unescape(text), stops=True)
    spoken = " ".join(_shorten_path(w) for w in spoken.split(" "))
    spoken = _CALL.sub(r"\1", spoken)
    spoken = _drop_symbols(spoken)
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


# ----- reading code aloud (read_file with aloud true) -----
# A developer who asked to hear a file gets its code spoken on purpose: names as words, each symbol by
# name, the indentation level announced when it changes, and only a few lines at a time. The text is
# untrusted data and is redacted by the caller before it gets here; this only shapes it for the ear.

CHUNK_LINES = 20  # lines spoken per call; "say continue" reads the next ones
CHUNK_CHARS = 900  # a chunk ends early when its spoken text gets this long
_SYMBOL_NAMES = {
    "==": "equals equals", "!=": "not equals", "<=": "less than or equal", ">=": "greater than or equal",
    "->": "arrow", "=>": "fat arrow", "&&": "and and", "||": "or or", "**": "double star", "//": "double slash",
    "::": "double colon", "+=": "plus equals", "-=": "minus equals", "*=": "star equals", "/=": "slash equals",
    "(": "open paren", ")": "close paren", "[": "open bracket", "]": "close bracket", "{": "open brace",
    "}": "close brace", ":": "colon", ";": "semicolon", ",": "comma", ".": "dot", "=": "equals",
    "<": "less than", ">": "greater than", "+": "plus", "-": "minus", "*": "star", "/": "slash",
    "%": "percent", "&": "ampersand", "|": "pipe", "!": "bang", "\"": "quote", "'": "single quote",
    "`": "backtick", "#": "hash", "@": "at", "$": "dollar", "~": "tilde", "^": "caret", "\\": "backslash",
    "?": "question mark", "_": "underscore",
}
_CODE_TOKEN = re.compile(
    r"==|!=|<=|>=|->|=>|&&|\|\||\*\*|//|::|\+=|-=|\*=|/="
    r"|[A-Za-z]+(?=[0-9])|[A-Za-z]+|[0-9]+(?:\.[0-9]+)?|\s+|."
)


def code_line_to_speech(line: str) -> str:
    """One line of code as words: names stay, camelCase is split, every symbol is named."""
    out = []
    for tok in _CODE_TOKEN.findall(line.strip()):
        if tok.isspace():
            continue
        if tok in _SYMBOL_NAMES:
            out.append(_SYMBOL_NAMES[tok])
        elif tok[0].isalnum():
            out.append(re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", tok))
        else:
            out.append("symbol")
    return " ".join(out)


def _indent_unit(lines: list[str]) -> int:
    """The width of one indentation level: 2 when every indented line uses an even number of spaces
    and some use exactly 2, else 4. A tab counts as one level on its own."""
    widths = [len(s) - len(s.lstrip(" ")) for s in lines if s.strip() and not s.startswith("\t")]
    widths = [w for w in widths if w]
    return 2 if widths and all(w % 2 == 0 for w in widths) and 2 in widths else 4


def _level(line: str, unit: int) -> int:
    lead = line[: len(line) - len(line.lstrip(" \t"))]
    return lead.count("\t") + len(lead.replace("\t", "")) // unit


def speak_code(numbered: list[tuple[int, str]], numbers: bool = False, unit: int | None = None) -> str:
    """Spoken text for lines of code: [(line number, text)]. Blank lines are said once, the indentation
    level is announced when it changes, and with numbers=True each line starts with its number."""
    unit = unit or _indent_unit([t for _, t in numbered])
    parts: list[str] = []
    level: int | None = None
    blank = False
    for n, text in numbered:
        if not text.strip():
            if not blank:
                parts.append(f"Line {n}, blank." if numbers else "Blank line.")
            blank = True
            continue
        blank = False
        here = _level(text, unit)
        head = f"Line {n}." if numbers else ""
        if here != level:
            head = f"{head} {'Indent level ' + str(here) if here else 'No indent'}.".strip()
            level = here
        parts.append(f"{head} {code_line_to_speech(text)}.".strip())
    return " ".join(parts)


def next_chunk_end(numbered: list[tuple[int, str]], limit_lines: int = CHUNK_LINES, limit_chars: int = CHUNK_CHARS) -> int:
    """How many of these lines make one spoken chunk: at most limit_lines, ending early when the
    spoken text passes limit_chars (a single long line is still a chunk of its own)."""
    used = 0
    for i, (_, text) in enumerate(numbered[:limit_lines], 1):
        used += len(code_line_to_speech(text)) + 2
        if used >= limit_chars:
            return i
    return min(limit_lines, len(numbered))
