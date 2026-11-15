"""Path policy for workspaces on Windows and Linux (design D1).

Every path a tool receives is relative to the project root and goes through `safe_resolve`
before anything touches the disk. The rules are the same on both: a backslash is a separator
everywhere, and names Windows can't hold (device names, colons, trailing dots) are refused on
Linux too, so a project stays portable.
"""

import os
import re
from pathlib import Path

MAX_READ_BYTES = 1024 * 1024
MAX_WRITE_BYTES = 256 * 1024
MAX_FILES_PER_BATCH = 20

# Text and source a project is made of. Nothing here runs by itself: run_program only runs .py, and
# a script in any other language needs run_command, which the developer approves line by line.
WRITE_EXTENSIONS = frozenset(
    {".py", ".txt", ".md", ".json", ".toml", ".csv", ".html", ".css", ".js",
     ".htm", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".svg", ".yaml", ".yml", ".xml",
     ".ini", ".cfg", ".sql", ".scss", ".vue"}
)
# Extensionless project files that are plain text (matched on the whole name, lower case).
WRITE_NAMES = frozenset({".gitignore", ".gitattributes", ".editorconfig", ".prettierrc", "license", "readme"})

# Folders that are never listed, sent to the model or scanned.
SKIP_DIRS = frozenset({".git", "__pycache__", ".venv", "node_modules"})

_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
_DRIVE = re.compile(r"^[A-Za-z]:")


class PathRejected(Exception):
    """The reason is written so the agent can read it back to the user."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def is_reserved_name(component: str) -> bool:
    return component.split(".")[0].rstrip(" ").upper() in _RESERVED


def is_protected_name(component: str) -> bool:
    """.git and .env* are never read, written or listed, whatever case they are in."""
    lowered = component.lower()
    return lowered == ".git" or lowered.startswith(".env")


def _check_protected(part: str) -> None:
    lowered = part.lower()
    if lowered == ".git":
        raise PathRejected("The .git folder is off limits.")
    if lowered.startswith(".env"):
        raise PathRejected(".env files are off limits because they hold secrets.")


def safe_resolve(root: Path, rel: str, *, for_write: bool = False) -> Path:
    """Return the absolute path for `rel` under `root`, or raise PathRejected."""
    if not rel or not rel.strip():
        raise PathRejected("The path is empty.")
    if "\x00" in rel:
        raise PathRejected("The path contains a null character.")
    if rel.startswith(("\\\\?\\", "//?/")):
        raise PathRejected(f"{rel} uses the \\\\?\\ prefix, which isn't allowed.")
    if rel.startswith(("\\\\", "//")):
        raise PathRejected(f"{rel} is a network path. Paths must be inside the project.")
    if _DRIVE.match(rel):
        raise PathRejected(f"{rel} has a drive letter. Paths must be relative to the project.")
    if rel.startswith(("/", "\\")):
        raise PathRejected(f"{rel} is an absolute path. Paths must be relative to the project.")
    # On Linux a backslash is an ordinary file name character, so `src\main.py` would be one
    # file in the project root and `..\x.py` a file named that. Read it as Windows does.
    if os.sep == "/":
        rel = rel.replace("\\", "/")

    for part in re.split(r"[\\/]+", rel):
        if part in ("", ".", ".."):
            continue
        if ":" in part:
            raise PathRejected(f"{rel} contains a colon, which isn't allowed in a file name.")
        if part.endswith((".", " ")):
            raise PathRejected(f"{rel} has a name that ends with a dot or a space.")
        if is_reserved_name(part):
            raise PathRejected(f"{part} is a reserved Windows device name.")
        _check_protected(part)

    root_resolved = root.resolve()
    resolved = Path(root_resolved, rel).resolve(strict=False)
    # normcase because Windows is case-insensitive (on Linux it changes nothing, and paths are
    # case-sensitive there); the trailing separator so that
    # a sibling such as `root2` doesn't pass as being under `root`.
    prefix = os.path.normcase(str(root_resolved)).rstrip("\\/") + os.sep
    if not os.path.normcase(str(resolved)).startswith(prefix):
        raise PathRejected(f"{rel} is outside the project folder.")
    # A link inside the project (public.txt -> .env.local, docs -> .git) must not reach a protected
    # file under another name: check the names of where the path really lands too (issue #158).
    for part in resolved.relative_to(root_resolved).parts:
        _check_protected(part)

    if for_write and resolved.suffix.lower() not in WRITE_EXTENSIONS and resolved.name.lower() not in WRITE_NAMES:
        kind = resolved.suffix or "no extension"
        raise PathRejected(
            f"{rel} has {kind}, and I can only write "
            + ", ".join(sorted(WRITE_EXTENSIONS))
            + " files."
        )
    return resolved


def check_read_size(path: Path, rel: str) -> None:
    if path.stat().st_size > MAX_READ_BYTES:
        raise PathRejected(f"{rel} is larger than 1 megabyte, which is too big to read.")


def check_write_size(content: str, rel: str) -> None:
    if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
        raise PathRejected(f"{rel} is larger than 256 kilobytes, which is too big to write.")


def _is_link(path: str) -> bool:
    return os.path.islink(path) or os.path.isjunction(path)


def project_files(root: Path, top: Path | None = None) -> list[str]:
    """Every file under `top` (default: the whole project) as a sorted path relative to `root`.

    Links and junctions are never followed, and anything that resolves outside the project
    is left out, so a link inside the project can't expose files elsewhere. Skips SKIP_DIRS
    and .env files, also when a link points at them.
    """
    base = root.resolve()
    prefix = os.path.normcase(str(base)).rstrip("\\/") + os.sep
    names: list[str] = []
    for folder, dirs, files in os.walk(top or root, followlinks=False):
        dirs[:] = sorted(
            d for d in dirs if d not in SKIP_DIRS and not _is_link(os.path.join(folder, d))
        )
        for file in sorted(files):
            if file.lower().startswith(".env"):
                continue
            resolved = Path(folder, file).resolve()
            if not os.path.normcase(str(resolved)).startswith(prefix):
                continue
            inside = resolved.relative_to(base)
            # A file link to .env.local or into .git is left out like the file itself (issue #158).
            if any(is_protected_name(part) for part in inside.parts):
                continue
            names.append(inside.as_posix())
    return names
