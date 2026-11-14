"""Per-task context for the model (design A6) and vocabulary for speech recognition.

Only the file tree is sent; file contents reach the model through read_file, so every read
is announced and wrapped as untrusted.
"""

import re
from pathlib import Path

from artemis.contracts import ProjectRef, TaskContext
from artemis.policy.paths import project_files

from .untrusted import wrap

MAX_TREE_ENTRIES = 200
_SYMBOL = re.compile(r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_]\w*)", re.MULTILINE)
_MAX_SYMBOL_FILE_BYTES = 256 * 1024


def file_tree(project: ProjectRef) -> list[str]:
    """Relative paths of the project's files. Links are not followed, so nothing outside the
    project is listed or read."""
    root = Path(project.root)
    return project_files(root) if root.is_dir() else []


def _files(project: ProjectRef) -> list[Path]:
    root = Path(project.root).resolve()
    return [root / rel for rel in file_tree(project)]


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
    return f"{header}\n\n{_style_note(task)}{task.request}"


_VERBOSITY = {
    "brief": "Keep the answer to one or two short sentences.",
    "detailed": "They asked for detail: explain step by step, in as many sentences as it takes.",
}
_FOCUS = {
    "visual": "Describe what the page looks like and how accessible it is. Say what you "
    "actually checked: changing the CSS is not the same as inspecting the rendered page.",
    "code": "Explain which files and functions you changed and why.",
}


def _style_note(task: TaskContext) -> str:
    """One line for the answer style the developer asked for, or nothing for the defaults."""
    parts = [_VERBOSITY.get(task.style.verbosity), _FOCUS.get(task.style.focus)]
    note = " ".join(part for part in parts if part)
    return f"[Answer style: {note}]\n\n" if note else ""


def vocabulary(project: ProjectRef) -> list[str]:
    """Words the developer is likely to say, most likely first: file stems, then the names of
    functions and classes. Handed to Voice.set_vocabulary()."""
    files = _files(project)
    words = [path.stem for path in files] + [path.name for path in files]
    for path in files:
        if path.suffix.lower() != ".py":
            continue
        try:
            if path.stat().st_size > _MAX_SYMBOL_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue  # deleted or locked since the listing; keep the words from the rest
        words.extend(_SYMBOL.findall(text))
    seen: set[str] = set()
    return [w for w in words if not (w.casefold() in seen or seen.add(w.casefold()))]
