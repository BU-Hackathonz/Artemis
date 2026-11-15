"""Strip markdown before text is spoken (design A9). The log keeps the original text.

Code is never read out symbol by symbol: a fenced block becomes one spoken pointer to the log,
and inline code that isn't a plain name (a file, a function, a variable) becomes "a snippet".
"""

import re

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


def for_speech(text: str) -> str:
    text = _BLOCK.sub(f" {_IN_LOG} ", text)
    # One pointer is enough when several blocks follow each other.
    text = re.sub(rf"(?:{re.escape(_IN_LOG)}\s*){{2,}}", f"{_IN_LOG} ", text)
    text = _INLINE.sub(_inline, text)
    text = _IMAGE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _RULE.sub("", text)
    text = _HEADING.sub("", text)
    text = _QUOTE.sub("", text)
    text = _BULLET.sub("", text)
    for _ in range(3):  # nested emphasis such as **bold with *italic* inside**
        text = _EMPHASIS.sub(r"\2", text)
    text = _UNDERSCORE.sub(r"\2", text)
    text = text.replace("`", "")
    return re.sub(r"\s+", " ", text).strip()
