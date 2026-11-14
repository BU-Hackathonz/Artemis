"""Strip markdown before text is spoken (design A9). The log keeps the original text."""

import re

_FENCE = re.compile(r"^\s*```.*$", re.MULTILINE)
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_BULLET = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+", re.MULTILINE)
_QUOTE = re.compile(r"^\s*>\s?", re.MULTILINE)
_RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$", re.MULTILINE)
_EMPHASIS = re.compile(r"(\*\*\*|\*\*|\*|~~)(?=\S)(.+?)(?<=\S)\1")
# Underscores only count at word edges, so my_file_name and __init__.py are left alone.
_UNDERSCORE = re.compile(r"(?<![\w.])(_{1,3})(?=[^\s_])(.+?)(?<=[^\s_])\1(?!\w|\.\w)")


def for_speech(text: str) -> str:
    text = _FENCE.sub("", text)
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
