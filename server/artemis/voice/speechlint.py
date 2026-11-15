"""Find spoken text that runs two sentences together ("Result It worked.", "No input device Check the mic").

A screen reader reads a missing full stop as one run-on sentence. The check is a heuristic: a word, a
space, then a word that usually starts a sentence. It catches the common slips without flagging names
such as "Hold Control Shift Space" or "Hello World".

Report every string literal in the server that trips it (read-only; changes nothing):
    python -m artemis.voice.speechlint
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

# Words that start a sentence in Artemis's spoken lines and rarely follow another word mid-sentence.
STARTERS = (
    "It", "The", "No", "Check", "Try", "You", "We", "This", "That", "Please", "Use", "Say", "Nothing",
    "Retrying", "Give", "Okay", "Done", "Created", "Heard", "Working", "Still", "Starting", "Opening",
    "Reading", "Scanning", "Running", "Waiting", "Canceled", "Blocked", "Enter", "Escape", "Result",
)
_RUN_ON = re.compile(r"\b([A-Za-z][\w']*) (" + "|".join(STARTERS) + r")\b")
NAMES = {"Echo Check", "Press Enter", "press Enter"}  # one name or one key instruction, not a run-on


def run_ons(text: str) -> list[str]:
    """Each place in `text` where a sentence seems to start with no punctuation before it, as 'word Starter'."""
    hits = (f"{m.group(1)} {m.group(2)}" for m in _RUN_ON.finditer(text))
    return [h for h in hits if h not in NAMES]


def _literals(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value
        elif isinstance(node, ast.JoinedStr):  # an f-string: check its fixed parts joined, with {} for values
            text = "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
            yield node.lineno, text


def scan(root: Path) -> list[tuple[str, int, str, list[str]]]:
    """(file, line, text, hits) for every non-test string literal under `root` that trips the check."""
    found = []
    for path in sorted(root.rglob("*.py")):
        if path.name.startswith("test_") or path.name == "speechlint.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line, text in _literals(tree):
            if "\n" in text:  # docstrings and comments in strings are not spoken
                continue
            hits = run_ons(text)
            if hits:
                found.append((str(path.relative_to(root.parent)), line, text, hits))
    return found


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]  # server/artemis
    results = scan(root)
    for file, line, text, hits in results:
        print(f"{file}:{line}: {', '.join(hits)}: {text!r}")
    print(f"{len(results)} string(s) to check by ear.")
    sys.exit(0)
