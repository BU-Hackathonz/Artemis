"""Per-task context for the model (design A6) and vocabulary for speech recognition.

Only the file tree is sent; file contents reach the model through read_file, so every read
is announced and wrapped as untrusted.
"""

import os
import re
from pathlib import Path

from artemis.contracts import ProjectRef, TaskContext

from .untrusted import wrap

MAX_TREE_ENTRIES = 200
SKIP_DIRS = frozenset({".git", "__pycache__", ".venv", "node_modules"})
_SYMBOL = re.compile(r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_]\w*)", re.MULTILINE)
_MAX_SYMBOL_FILE_BYTES = 256 * 1024


def _files(project: ProjectRef) -> list[Path]:
    root = Path(project.root)
    found: list[Path] = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if not name.lower().startswith(".env"):
                found.append(Path(folder, name))
    return found


def file_tree(project: ProjectRef) -> list[str]:
    root = Path(project.root)
    return [path.relative_to(root).as_posix() for path in _files(project)]


def task_message(task: TaskContext) -> str:
    """The user message that starts a task: a short header, then the request.

    The request is the developer's own words, so it is not wrapped. File names are data.
    """
    if task.project is None:
        header = f'<task id="{task.task_id}" project="none">\nNo project is open.\n</task>'
    else:
        tree = file_tree(task.project)
        listing = "\n".join(tree[:MAX_TREE_ENTRIES]) or "(no files yet)"
        if len(tree) > MAX_TREE_ENTRIES:
            listing += f"\n[… {len(tree) - MAX_TREE_ENTRIES} more files not shown …]"
        header = (
            f'<task id="{task.task_id}" project="{task.project.slug}">\n'
            f"{wrap('file names', listing)}\n</task>"
        )
    return f"{header}\n\n{task.request}"


def vocabulary(project: ProjectRef) -> list[str]:
    """Words the developer is likely to say, most likely first: file stems, then the names of
    functions and classes. Handed to Voice.set_vocabulary()."""
    files = _files(project)
    words = [path.stem for path in files] + [path.name for path in files]
    for path in files:
        if path.suffix.lower() == ".py" and path.stat().st_size <= _MAX_SYMBOL_FILE_BYTES:
            text = path.read_text(encoding="utf-8", errors="replace")
            words.extend(_SYMBOL.findall(text))
    seen: set[str] = set()
    return [w for w in words if not (w.casefold() in seen or seen.add(w.casefold()))]
