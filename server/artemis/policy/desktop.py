"""Desktop tools (contracts.md §9) and the plan (§10), mixed into ToolGateway.

Artemis may act on the whole desktop, but every action that changes something, and every read
outside the project, waits for the guarded approval dialog. Opening waits only when it could run
something (a terminal, an interpreter, a launcher the developer's own folder could have been planted
in) or when the file is outside the developer's own folders. What the dialog shows is written from
the arguments (never by the model), and the approval hash binds that exact action: the app, the
resolved path and its content, the command and its folder, the text and the window it goes into.
The gate checks the hash again after the decision, so a swapped file or a moved focus fails.

- Paths: under the home folder (or the projects folder) only, links resolved, and a denylist no
  approval overrides: every dot-entry at the top of home, .env and .git anywhere, the runtime folder.
- Opening never runs anything: executables, .desktop files, scripts and AppImages are refused.
- Commands: one `bash -c` line, 30 s, the tail of the output, an allowlisted environment (no keys, no
  ssh agent, no session bus, runtime folder or display: only apps get those), and nothing it started
  outlives it. The denylist can't bind what a command does, so risky commands are high findings and
  need the blocking key (Shift+Enter).
- Typing: only the GNOME shell can type on Wayland. The island reports the focused window, the
  developer approves typing into that window, and the island types only while it has the focus.
  Typing into a terminal is a high finding.
- The plan: the agent's first acting call for a request. Every tool call is announced as a ring
  cue from what really happens, matched to the plan's step when one fits.
"""

import asyncio
import configparser
import hashlib
import inspect
import os
import re
import shlex
import shutil
import signal
import stat
import tempfile
import time
import unicodedata
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from artemis.agent.speech_text import for_speech
from artemis.agent.untrusted import wrap
from artemis.contracts import (
    AgentPlanData,
    Approval,
    Decision,
    DesktopSpec,
    Finding,
    OpenAppArgs,
    OpenPathArgs,
    PlanArgs,
    PressKeysArgs,
    ReadPathArgs,
    RingCueData,
    RunCommandArgs,
    TypeTextArgs,
)

from .hashing import approval_hash
from .paths import MAX_READ_BYTES, PathRejected

COMMAND_TIMEOUT_S = 30
MAX_COMMAND_OUTPUT = 16 * 1024
SPOKEN_TEXT_CHARS = 120
# A slow VS Code start: typing waits this long for the app just opened to have the focus, asking the island
# every FOCUS_POLL_S; and a focus question the island didn't answer is asked again (FOCUS_TRIES times).
FOCUS_WAIT_S = 20.0
FOCUS_POLL_S = 0.5
FOCUS_TRIES = 3
RECENT_OPEN_S = 120.0  # an app opened longer ago than this is no longer the one typing is meant for

# How long the ring gets to reach its target before a window opens or typing starts: the island's
# flight (CUE_FLY_MS, 600 ms) plus a beat. Tests set it to 0.
RING_LEAD_S = 0.8

# The plan action each tool carries out, for the ring.
TOOL_ACTIONS = {
    "create_project": "think", "open_project": "think", "list_files": "read", "read_file": "read",
    "read_path": "read", "inspect_page": "read", "scan_project": "read", "write_files": "write",
    "edit_file": "write", "run_program": "run", "run_command": "run", "open_app": "open",
    "open_path": "open", "open_url": "open", "type_text": "type", "press_keys": "type", "publish_github": "run",
}
# Where the ring flies for each acting tool, when the call itself names no more (the app, the window):
# the editor, the terminal, the browser or the file. Every acting tool cues, with or without a plan.
TOOL_TARGETS = {
    "create_project": "editor", "open_project": "editor", "write_files": "editor", "edit_file": "editor",
    "list_files": "file", "read_file": "file", "read_path": "file", "scan_project": "file",
    "inspect_page": "browser", "run_program": "terminal", "run_command": "terminal", "open_url": "browser",
}
# Tools that change something or reach outside the project: the plan comes first (§10).
# Opening an app or one of the developer's own files is not here: "open VS Code" must just open it
# (it asks nothing and changes nothing), not bounce off plan_required first.
NEEDS_PLAN = {"write_files", "edit_file", "run_program", "run_command", "read_path", "type_text", "press_keys",
              "publish_github"}

# Opening one of these would run it, not open it.
_RUNNABLE_SUFFIXES = {".desktop", ".sh", ".bash", ".zsh", ".appimage", ".run", ".jar", ".bin",
                      ".exe", ".msi", ".deb", ".rpm", ".flatpakref", ".py", ".pl", ".rb", ".bat", ".cmd",
                      ".ps1", ".vbs", ".command", ".jnlp", ".svgz", ".mht", ".mhtml"}
# The pages the agent writes for the developer (a website, a picture): they open in a browser, in the
# default app or in an editor, from the developer's own folders, with no dialog. A browser running a
# local page is what "open the website you made" means; the file is hashed and bound all the same.
_WEB_SUFFIXES = {".html", ".htm", ".xhtml", ".svg"}
# Source and markup a code editor shows and never runs: what the agent writes for the developer, which
# may open in VS Code (and only there; in the default app these are refused as above).
# .html and .svg are here too: an editor shows their source (a browser would run them, so in any other
# app they are refused as above).
_EDITABLE_SUFFIXES = {".py", ".pyi", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".css",
                      ".sh", ".bash", ".zsh", ".bat", ".cmd", ".ps1", ".rb", ".pl", ".lua", ".go", ".rs", ".java",
                      ".c", ".h", ".cpp", ".hpp", ".cs", ".kt", ".swift", ".php", ".sql", ".json", ".yaml", ".yml",
                      ".toml", ".md", ".txt", ".html", ".htm", ".xhtml", ".svg", ".xml", ".scss", ".less", ".vue",
                      ".svelte", ".ini", ".cfg", ".csv", ".ipynb", ".rst"}
# Terminal apps, which run what they are given (editors are not, see _EDITORS).
_TERMINAL_APPS = re.compile(r"ptyxis|gnome-terminal|org\.gnome\.console|kgx|kitty|alacritty|xterm|konsole|"
                            r"terminator|tilix|wezterm|\bfoot\b|terminal", re.I)
# An app that would run the file it is given, not show it.
_INTERPRETERS = re.compile(r"^(env|sh|bash|dash|zsh|fish|python[\d.]*|perl|ruby|node|nodejs|deno|bun|php|java|"
                           r"lua|gjs|pwsh|powershell)$")
# Terminal apps: what is typed into them runs as a command. Substring match on the app id (the part
# before ": "), then whole-word matches for short names.
_TERMINALS = re.compile(
    r"ptyxis|gnome-terminal|org\.gnome\.terminal|org\.gnome\.console|kgx|kitty|alacritty|xterm|konsole|"
    r"terminator|tilix|wezterm|ghostty|warp|tabby|guake|yakuake|blackbox|terminology|terminal|console|"
    r"rxvt|sakura|contour|cool-retro-term|tmux|putty|termius", re.I)
_TERMINAL_WORDS = re.compile(r"(?<![a-z0-9])(foot|st|term|rio|hyper|screen|tty)(?![a-z0-9])", re.I)
# Editors (VS Code, Sublime, JetBrains, Emacs, vim). Typing into them is a normal approval, unless the
# window's title says that their integrated terminal has the focus (_EDITOR_TERMINAL_TITLE).
_EDITORS = re.compile(
    r"vscode|vscodium|codium|cursor|windsurf|jetbrains|intellij|pycharm|webstorm|clion|goland|rider|"
    r"phpstorm|rubymine|datagrip|android-?studio|sublime|emacs|neovide|nvim|gvim|"
    r"(?<![a-z0-9])(code|idea|zed|vim)(?![a-z0-9])", re.I)
# An editor's title when its terminal has the focus: "Terminal" in VS Code, "vterm" or "eshell" in Emacs.
_EDITOR_TERMINAL_TITLE = re.compile(r"(?<![a-z0-9])(terminal|vterm|eshell)(?![a-z0-9])", re.I)
# A terminal's title often says so: "user@host: ~/dir", or the shell's name. A shell name that follows a
# dot is a file ("install.sh", ".zshrc"), not a shell.
_TERMINAL_TITLE = re.compile(r"^[\w.-]+@[\w.-]+[: ]|(?<![a-z0-9.])(bash|zsh|fish|sh|tmux|ssh|sudo)(?![a-z0-9])", re.I)
# Keys that open a launcher or a shell rather than act in the focused window (see _launcher_key).
_LAUNCHER_FKEY = re.compile(r"^f([1-9]|1[0-2])$")
# What pastes the clipboard: the dialog can't show what that is.
_PASTE_KEYS = {("ctrl", "v"), ("ctrl", "shift", "v"), ("shift", "insert"), ("ctrl", "y")}


def _key_parts(keys: str) -> tuple[frozenset[str], str]:
    parts = [p.strip().lower() for p in keys.split("+")]
    return frozenset(parts[:-1]), parts[-1]


def _launcher_key(keys: str) -> bool:
    """True for keys that open a launcher, the overview, a console or a power dialog, whatever the
    order of the modifiers (alt+ctrl+t is ctrl+alt+t)."""
    mods, key = _key_parts(keys)
    if {"super", "meta", "hyper"} & mods or key.startswith(("super", "meta", "hyper", "xf86", "iso_")):
        return True
    if "alt" in mods and _LAUNCHER_FKEY.match(key):
        return True  # alt+F2 (run), ctrl+alt+F1..F12 (another console)
    return {"ctrl", "alt"} <= mods and key in {"t", "delete", "backspace"}


def _pastes(keys: str) -> bool:
    mods, key = _key_parts(keys)
    return any(set(c[:-1]) == mods and c[-1] == key for c in _PASTE_KEYS)


def _clean(text: str, limit: int) -> str:
    """Printable, one line, short: a window title is written by whatever is in the window, so it
    can't carry line breaks or invisible and right-to-left marks into the dialog."""
    text = "".join(c if unicodedata.category(c) not in ("Cc", "Cf", "Zl", "Zp") else " " for c in text)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


SPOKEN_PLAN_STEPS = 4  # a longer plan is shown in full but only its first steps are said


def spoken_plan(plan: PlanArgs, again: bool = False) -> str:
    """What the developer hears when the plan is made (§10: each step is spoken): the steps' own
    sentences in order, so they know what is coming before the first approval. A re-plan says so."""
    lines = []
    for step in plan.steps[:SPOKEN_PLAN_STEPS]:
        line = _clean(for_speech(step.say), 200)
        if line:
            lines.append(line if line[-1] in ".?!" else line + ".")
    rest = len(plan.steps) - SPOKEN_PLAN_STEPS
    if rest > 0:
        lines.append(f"Then {rest} more step{'s' if rest > 1 else ''}, in the panel.")
    if not lines:
        lines.append(_clean(for_speech(plan.goal), 200).rstrip(".") + ".")
    return ("New plan. " if again else "") + " ".join(lines)


def _is_terminal(window: str) -> bool:
    app, sep, title = window.partition(": ")
    if not sep:  # no app id was reported: the whole string is all there is
        app, title = "", window
    if _TERMINALS.search(app) or _TERMINAL_WORDS.search(app) or _TERMINAL_TITLE.search(title):
        return True
    if not app and _TERMINALS.search(title):
        return True
    # An editor is typed into like a text editor, unless its integrated terminal seems to have the focus.
    editor = _EDITORS.search(app or title)
    return bool(editor and _EDITOR_TERMINAL_TITLE.search(title))


# What a command may not do without the blocking key. A heuristic, not a boundary: a shell can name
# anything in a way no pattern reads, so every rule is checked on the line as written and on the line
# with its quotes and backslashes removed (r''m, "rm", \rm). The environment and the group kill in
# run_shell are the other layers.
_B = r"(?:^|[^\w.-])"  # start of a word: also after / so that /usr/bin/sudo counts
_SHELL = r"(?:\S*/)?(?:ba|z|da|k|c|fi|a)?sh"
_INTERP = r"(?:\S*/)?(?:python[\d.]*|perl|ruby|node|nodejs|php|lua|deno|bun)"
_HOME_DOT = r"(?:~|\$HOME|\$\{HOME\}|/home/[^/\s]+|/Users/[^/\s]+|/root)/+\."
_RISKY = [
    (re.compile(_B + r"(sudo|su|pkexec|doas|run0)\b"), "it asks for administrator rights"),
    (re.compile(r"\b(curl|wget|fetch)\b[^;&]*(\|\s*(sudo\s+)?(" + _SHELL + "|" + _INTERP + r")\b|<\()|"
                r"\b(curl|wget)\b.*(&&|;)\s*(sudo\s+)?(" + _SHELL + r"|chmod\s+\+?[ugoa]*x)\b|\$\(\s*(curl|wget)\b"),
     "it runs a script straight from the internet"),
    (re.compile(r"\|\s*(sudo\s+|(?:\S*/)?env\s+|exec\s+)*(" + _SHELL + "|" + _INTERP + r")\b|(^|[\s;&|])(source|\.)\s+<\(|"
                + _SHELL + r"\s+<\(|" + _B + r"eval\b|" + _B + _SHELL + r"\s+(-[a-zA-Z]*c|<)"),
     "it feeds text to a shell or interpreter, so the check can't read what runs"),
    (re.compile(_B + _INTERP + r"(\s+-[A-Za-z-]+)*\s+(-[A-Za-z]*[ceErp]\b|--eval|--print)"), "it runs code the check can't read"),
    (re.compile(r"\b(base64|base32)\s+(-\w*[dD]|--decode)|\bxxd\s+-\w*r|\bopenssl\s+(enc|base64)\b[^;&|]*-d\b|\$'[^']*\\[xuU0-7]"),
     "it hides what it runs"),
    (re.compile(r"(^|[;&|(]\s*|&&\s*)(\$\{?[A-Za-z_]\w*\}?|\$\(|`)|\{\s*(rm|sudo|su|dd)\s*,"), "it runs a command it builds at run time"),
    (re.compile(_B + r"rm\b[^;&|]*?\s(-[a-zA-Z]*[rRf][a-zA-Z]*|--recursive|--force|--no-preserve-root)(\s|$)|"
                r"\bfind\b[^;&|]*(-delete\b|-exec(dir)?\s+rm\b)|\bxargs\b[^;&|]*\brm\b|\bgit\s+clean\b|"
                r"\brsync\b[^;&|]*--(delete|remove-source)"), "it deletes files"),
    (re.compile(r"\b(dd|mkfs(\.\w+)?|shred|wipefs|fdisk|sfdisk|parted|cryptsetup)\b|/dev/(sd|nvme|mmc|vd|loop)"), "it can overwrite a disk"),
    (re.compile(r"(^|[\s;&|])(chmod|chown)\s+(-\w+\s+)*(-R|--recursive)\b"), "it changes permissions on many files"),
    (re.compile(r"(^|[\s'\"=:/])~?/?\.(ssh|gnupg|aws|kube|docker|netrc|password-store|pki|mozilla|config|local|artemis|"
                r"bash\w*|profile|zsh\w*|npmrc|pypirc|gitconfig|git-credentials|cargo|gem|mc|var|cache|thunderbird)\b|" + _HOME_DOT +
                r"|(^|[\s/])\.[^\s/]*[*?\[]"),
     "it names a private folder"),
    (re.compile(r"\.env|(^|[\s'\"=:/])\.git\b|launch\.json|XDG_RUNTIME_DIR|/run/user/|/var/run/user/"), "it names a secret or private file"),
    (re.compile(r"/proc/(?!(cpuinfo|meminfo|version|loadavg|uptime|stat|sys/))|\bps\s+[a-zA-Z]*e[a-zA-Z]*\b"),
     "it can read other programs' memory and environment"),
    (re.compile(r"\b(printenv|env|export\s+-p|declare\s+-[xp]+)\b\s*($|[|;&>])"), "it prints the environment"),
    (re.compile(_B + r"(secret-tool|gdbus|dbus-send|dbus-monitor|dbus-run-session|busctl|qdbus\d*|gsettings|dconf|keyctl|"
                r"gnome-keyring(-daemon)?|kwallet\w*|seahorse|loginctl)\b|(DBUS_SESSION_BUS_ADDRESS|DISPLAY|WAYLAND_DISPLAY)="),
     "it talks to the desktop session, keyring or settings"),
    (re.compile(_B + r"(xdotool|ydotool|dotool|wtype|xte|xclip|xsel|wl-copy|wl-paste|wmctrl|xinput|grim|scrot|gnome-screenshot|import)\s"),
     "it can type, read the clipboard or watch the screen without the typing approval"),
    (re.compile(_B + r"(ssh|scp|sftp|ssh-add|ssh-keygen|ssh-agent|gpg2?|aws|gcloud|az|kubectl|helm|doctl|flyctl|heroku)\s"),
     "it uses saved keys or logins"),
    (re.compile(_B + r"(setsid|nohup|disown|daemonize|crontab|systemd-run|systemctl|machinectl|launchctl|batch)\b|(^|[;&|(]\s*|&&\s*)(at|tmux|screen)\s"),
     "it can keep running, or come back, after Artemis stops it"),
]
# The variables a command gets (Avery's review point 3): no keys, tokens or ssh agent, and no way to
# the desktop session: no session bus, no runtime folder (where the bus and the Wayland socket live)
# and no display, so a command can't reach the keyring, GNOME Shell or the screen.
_ENV_COMMAND = ("PATH", "HOME", "LANG", "LANGUAGE", "TERM", "USER", "LOGNAME", "SHELL")
# Apps Artemis opens (gtk-launch, gio) do need the session.
# XDG_DATA_DIRS matters most: gtk-launch finds snap's code_code.desktop only through it. XAUTHORITY is
# how an Xwayland app (VS Code is Electron) reaches the display under GDM; without it the app dies silently.
_ENV_APP = ("XDG_RUNTIME_DIR", "DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP",
            "DBUS_SESSION_BUS_ADDRESS", "XDG_DATA_DIRS", "XDG_DATA_HOME", "XDG_CONFIG_DIRS", "XDG_CONFIG_HOME",
            "XDG_CACHE_HOME", "XDG_STATE_HOME", "XDG_SESSION_DESKTOP", "XDG_SESSION_ID", "DESKTOP_SESSION",
            "XAUTHORITY", "GDK_BACKEND", "QT_QPA_PLATFORM", "GNOME_SETUP_DISPLAY")
# Where launchers put their programs when a service's PATH is short (snap, flatpak, the VS Code deb).
_APP_BIN_DIRS = ("/usr/local/bin", "/usr/bin", "/bin", "/snap/bin", "/usr/share/code/bin",
                 "/var/lib/flatpak/exports/bin")
_ENV_ALLOW = _ENV_COMMAND + _ENV_APP
RUN_MARKER = "ARTEMIS_RUN_ID"  # in a command's environment, to find what it left behind


def desktop_env(for_command: bool = False) -> dict[str, str]:
    """The allowlisted environment: for the apps Artemis opens (with the session's bus and display)
    or, with `for_command`, for a shell command (without them)."""
    allow = _ENV_COMMAND if for_command else _ENV_ALLOW
    env = {k: v for k, v in os.environ.items() if k in allow or k.startswith("LC_")}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    if not for_command:
        # Started from a service or a bare shell, the session variables may be missing: fill in what
        # a GNOME session always has, when it is really there.
        uid = os.getuid()
        runtime = env.get("XDG_RUNTIME_DIR") or (f"/run/user/{uid}" if os.path.isdir(f"/run/user/{uid}") else "")
        if runtime:
            env.setdefault("XDG_RUNTIME_DIR", runtime)
            if "DBUS_SESSION_BUS_ADDRESS" not in env and os.path.exists(f"{runtime}/bus"):
                env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime}/bus"
            if "WAYLAND_DISPLAY" not in env and "DISPLAY" not in env and os.path.exists(f"{runtime}/wayland-0"):
                env["WAYLAND_DISPLAY"] = "wayland-0"
        have = env["PATH"].split(":")
        extra = [d for d in (*_APP_BIN_DIRS, str(home() / ".local/share/flatpak/exports/bin")) if d not in have]
        env["PATH"] = ":".join(have + extra)
    return env


def home() -> Path:
    return Path(os.environ.get("HOME") or Path.home()).resolve()


def home_resolve(raw: str, base: Path | None = None, extra_roots: tuple[Path, ...] = ()) -> Path:
    """`raw` (absolute, ~/..., or relative to `base`) as a real path under the home folder or one of
    `extra_roots` (the projects folder, which may be a link to another drive), or PathRejected.
    Links are resolved first, so a link can't reach a denied place under another name."""
    if not raw or not raw.strip() or "\x00" in raw:
        raise PathRejected("The path is empty or invalid.")
    path = Path(raw.strip()).expanduser()
    if not path.is_absolute():
        path = (base or home()) / path
    real = path.resolve(strict=False)
    top = home()
    inside = real.relative_to(top) if real.is_relative_to(top) else None
    if inside is not None and inside.parts and inside.parts[0].startswith("."):
        # Every dot-entry at the top of home: keys, keyrings, browser profiles, app configs.
        raise PathRejected(f"{raw} is in a private folder that I never touch.")
    if inside is None:
        for root in extra_roots:
            if real.is_relative_to(root.resolve()):
                inside = real.relative_to(root.resolve())
                break
    if inside is None:
        raise PathRejected(f"{raw} is outside your home folder, and I can only work inside it.")
    parts = inside.parts
    for part in parts:
        low = part.lower()
        if low == ".git":
            raise PathRejected("The .git folder is off limits.")
        if low.startswith(".env"):
            raise PathRejected(".env files are off limits because they hold secrets.")
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and real.is_relative_to(Path(runtime).resolve()):
        raise PathRejected(f"{raw} is in the session's private runtime folder.")
    return real


def spoken_path(path: Path) -> str:
    try:
        return "~/" + path.relative_to(home()).as_posix()
    except ValueError:
        return path.name


def _open_regular(path: Path) -> int:
    """A read-only descriptor for a regular file, never blocking on a pipe or a device, never
    following a link in the last place (the path is already resolved)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        raise PathRejected(f"{path.name} can't be read.") from None
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise PathRejected(f"{path.name} is not an ordinary file.")
    return fd


def read_capped(path: Path, limit: int = MAX_READ_BYTES) -> bytes:
    """The file's bytes, or PathRejected when it is not a regular file or holds more than `limit`
    (a file can grow after it was checked, so the size is enforced while reading)."""
    fd = _open_regular(path)
    with os.fdopen(fd, "rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise PathRejected(f"{path.name} is larger than 1 megabyte, which is too big to read.")
    return data


def file_sha256(path: Path) -> str:
    """Streamed, so opening a big video doesn't load it into memory."""
    digest = hashlib.sha256()
    with os.fdopen(_open_regular(path), "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def command_findings(command: str) -> list[Finding]:
    """High-severity findings for a command line (the blocking key is then needed to approve it)."""
    plain = re.sub(r"""['"\\]""", "", command)
    found = []
    for pattern, why in _RISKY:
        if pattern.search(command) or pattern.search(plain):
            found.append(Finding(rule="desktop-command", severity="high", path="command", line=1,
                                 message=f"This command needs a second look: {why}."))
    return found


# ----- apps -----

# The system's menu folders, searched even when XDG_DATA_DIRS leaves them out (snap and flatpak export
# their launchers here). A module constant so tests can point it away from the machine they run on.
SYSTEM_APP_DATA: tuple[str, ...] = ("/usr/local/share", "/usr/share", "/var/lib/snapd/desktop",
                                    "/var/lib/flatpak/exports/share")


def _app_dirs() -> list[Path]:
    data = [os.environ.get("XDG_DATA_HOME") or str(home() / ".local/share")]
    data += (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    data += [*SYSTEM_APP_DATA, str(home() / ".local/share/flatpak/exports/share")]
    folders: list[Path] = []
    for d in data:
        if d and (path := Path(d) / "applications") not in folders:
            folders.append(path)
    return folders


# VS Code and the builds that are VS Code for the developer, by the desktop id or the name each one
# ships under: deb (code.desktop), snap (code_code.desktop, exported by snapd), flatpak
# (com.visualstudio.code.desktop), VSCodium. Only these exact matches count, so "code" never picks
# some other app whose name merely contains it.
_VSCODE_LOOKUP = ("code", "code_code", "com.visualstudio.code", "code-oss", "visual studio code",
                  "codium", "com.vscodium.codium", "vscodium")
_VSCODIUM_LOOKUP = ("codium", "com.vscodium.codium", "vscodium")
_TEXT_EDITOR_LOOKUP = ("org.gnome.texteditor", "text editor")
_CODE_IDS = {"code", "code_code", "com.visualstudio.code", "code-oss", "codium", "com.vscodium.codium", "vscodium"}
# What the developer says for an app: each lookup list is tried in order, exact matches only (desktop
# id or name), so a family never picks some other app that merely contains the word.
_FIREFOX = ("firefox", "firefox_firefox", "org.mozilla.firefox", "firefox-esr", "mozilla firefox", "firefox web browser")
_CHROME = ("google-chrome", "google chrome", "google-chrome-stable", "com.google.chrome", "chromium",
           "chromium_chromium", "chromium-browser", "org.chromium.chromium", "brave-browser", "com.brave.browser",
           "microsoft-edge", "com.microsoft.edge")
_TERMINAL = ("org.gnome.terminal", "gnome-terminal", "org.gnome.ptyxis", "ptyxis", "org.gnome.console", "kgx",
             "terminal", "konsole", "org.kde.konsole", "xfce4-terminal", "tilix", "kitty", "alacritty", "xterm")
_FILES = ("org.gnome.nautilus", "nautilus", "files", "org.gnome.files", "nemo", "thunar", "org.kde.dolphin", "dolphin")
_GEDIT = ("org.gnome.gedit", "gedit", "text editor")
_FAMILIES = {
    "vs code": _VSCODE_LOOKUP, "vscode": _VSCODE_LOOKUP, "visual studio code": _VSCODE_LOOKUP,
    "visual studio": _VSCODE_LOOKUP, "code": _VSCODE_LOOKUP, "code editor": _VSCODE_LOOKUP,
    "microsoft vs code": _VSCODE_LOOKUP, "microsoft visual studio code": _VSCODE_LOOKUP,
    "editor": _VSCODE_LOOKUP + _TEXT_EDITOR_LOOKUP + _GEDIT, "text editor": _TEXT_EDITOR_LOOKUP + _GEDIT,
    "gedit": _GEDIT, "gnome text editor": _TEXT_EDITOR_LOOKUP,
    "codium": _VSCODIUM_LOOKUP, "vscodium": _VSCODIUM_LOOKUP,
    "firefox": _FIREFOX, "browser": _FIREFOX + _CHROME, "web browser": _FIREFOX + _CHROME,
    "chrome": _CHROME, "google chrome": _CHROME, "chromium": _CHROME[4:],
    "terminal": _TERMINAL, "gnome terminal": _TERMINAL, "console": _TERMINAL, "command line": _TERMINAL,
    "files": _FILES, "file manager": _FILES, "file browser": _FILES, "nautilus": _FILES, "finder": _FILES,
}
# Families that, when none of their builds is installed, may still match an app whose name contains the word.
_FUZZY = {"firefox", "browser", "web browser", "chrome", "google chrome", "chromium", "terminal", "gnome terminal",
          "console", "files", "file manager", "file browser", "nautilus"}
# Other names for an app whose real name differs; matched by name or id that contains it.
_ALIASES = {"calculator": "calculator", "settings": "settings"}


def _normal(query: str) -> str:
    """What the developer said, as a lookup key: lower case, no ".desktop", one space between words
    ("VS-Code", "vs  code" and "VS Code." are all "vs code")."""
    want = query.strip().lower().removesuffix(".desktop")
    if want in _FAMILIES or (" " not in want and ("." in want or "_" in want)):
        return want
    return " ".join(re.sub(r"[-_.,!?\"']+", " ", want).split())


_MAX_DESKTOP_FILE = 256 * 1024

# The developer's own folders under home: a document or folder there opens without the dialog.
_OWN_FOLDERS = ("Documents", "Desktop", "Downloads", "Projects")


def _find_app_file(query: str, exact: bool = False) -> tuple[Path, str, configparser.SectionProxy] | None:
    """(file, display name, entry) of the installed app that best matches `query`: an exact desktop
    id, then an exact name, then (unless `exact`) a name or id that contains it. Hidden and NoDisplay
    apps are skipped."""
    want = query.strip().lower().removesuffix(".desktop")
    if not want:
        return None
    best: tuple[int, Path, str, configparser.SectionProxy] | None = None
    seen: set[str] = set()
    for folder in _app_dirs():
        if not folder.is_dir():
            continue
        for file in sorted(folder.glob("*.desktop")):
            if file.name in seen:
                continue
            seen.add(file.name)
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                # A pipe or a device named *.desktop must not hang the search, nor a huge file fill memory.
                if not file.is_file() or file.stat().st_size > _MAX_DESKTOP_FILE:
                    continue
                parser.read(file, encoding="utf-8")
                entry = parser["Desktop Entry"]
                if entry.get("Type") != "Application" or entry.getboolean("NoDisplay", False) or entry.getboolean("Hidden", False):
                    continue
            except (configparser.Error, KeyError, UnicodeDecodeError, OSError, ValueError):
                continue
            name = entry.get("Name", file.stem)
            app_id = file.stem.lower()
            if want == app_id:
                score = 3
            elif want == name.lower():
                score = 2
            else:
                score = 0 if exact else 1 if (want in name.lower() or want in app_id) else 0
            if score and (best is None or score > best[0]):
                best = (score, file, name, entry)
    return best[1:] if best else None


def _lookup(query: str) -> tuple[Path, str, configparser.SectionProxy] | None:
    """The installed app for what the developer said or for a desktop id: a family ("VS Code", "editor")
    tries each of its builds in order, exact matches only; anything else is `_find_app_file`."""
    want = _normal(query)
    if want in _FAMILIES:
        for candidate in _FAMILIES[want]:
            found = _find_app_file(candidate, exact=True)
            if found is not None:
                return found
        if want not in _FUZZY:
            return None  # "code" must never open some other app whose name contains it
    found = _find_app_file(_ALIASES.get(want, want))
    raw = query.strip().lower()
    return found or (_find_app_file(raw) if raw != want else None)


def find_app(query: str) -> tuple[str, str] | None:
    """(desktop id, display name) of the app `_lookup` finds."""
    found = _lookup(query)
    return (found[0].name, found[1]) if found else None


def app_digest(query: str) -> str | None:
    """The sha256 of the .desktop file that would launch, so the approval binds what it runs
    (a user file in ~/.local/share/applications can shadow a system app of the same name)."""
    found = _lookup(query)
    return file_sha256(found[0]) if found else None


def _program_words(exec_line: str) -> list[str]:
    """The program and its first argument in an Exec line, past `env` and VAR=value prefixes: snap's
    VS Code launches as `env BAMF_DESKTOP_FILE_HINT=... /snap/bin/code`, which is not an interpreter."""
    words = exec_line.split()
    while words and (os.path.basename(words[0]) == "env" or re.match(r"[A-Za-z_]\w*=", words[0])):
        words = words[1:]
    return words[:2]


def app_runs_files(query: str) -> bool:
    """True for an app that would execute a file it is given: a terminal, or an interpreter."""
    found = _lookup(query)
    return found is not None and _runs_files(*found)


def _runs_files(file: Path, name: str, entry: configparser.SectionProxy) -> bool:
    """True for an app that would execute a file it is given: a terminal, or an interpreter."""
    if entry.getboolean("Terminal", False) or _TERMINAL_APPS.search(file.stem) or _TERMINAL_APPS.search(name):
        return True
    return any(_INTERPRETERS.match(os.path.basename(w)) for w in _program_words(entry.get("Exec", "")))


def app_is_editor(query: str) -> bool:
    """True for an app that shows source code and never runs it: VS Code or a build of it, or a text
    editor or IDE by its categories. Only meaningful after `app_runs_files` is False."""
    found = _lookup(query)
    if found is None:
        return False
    file, _name, entry = found
    categories = {c for c in entry.get("Categories", "").split(";") if c}
    return file.stem.lower() in _CODE_IDS or bool(categories & {"TextEditor", "IDE"})


def _in_user_apps(file: Path) -> bool:
    """True for a launcher in the developer's own applications folder (~/.local/share/applications by
    default)."""
    return file.parent.resolve() == _app_dirs()[0].resolve()


def _planted(file: Path, entry: configparser.SectionProxy) -> bool:
    """True for a launcher in the developer's own applications folder that looks planted: it shadows a
    system launcher of the same name with another content, or its program sits in a folder the agent
    writes into (Documents, Desktop, Downloads, ArtemisProjects). A user-level install (a tarball VS Code
    or a Chrome app in ~/.local/share/applications) is the developer's own and opens without asking."""
    if not _in_user_apps(file):
        return False
    for folder in _app_dirs()[1:]:
        twin = folder / file.name
        try:
            if twin.is_file() and twin.read_bytes() != file.read_bytes():
                return True
        except OSError:
            continue
    words = _program_words(entry.get("Exec", ""))
    if not words:
        return False
    program = Path(words[0]).expanduser()
    if not program.is_absolute():
        return False
    places = [home() / name for name in (*_OWN_FOLDERS, "ArtemisProjects")]
    return any(program.resolve().is_relative_to(p.resolve()) for p in places)


def _app_findings(found: tuple[Path, str, configparser.SectionProxy]) -> list[Finding]:
    """High findings (the blocking key is then needed) for an app whose launcher may have been planted.
    Any other installed app opens without asking: opening a terminal or an editor runs nothing, and
    typing into one has its own approval."""
    file, _name, entry = found
    if not _planted(file, entry):
        return []
    return [Finding(rule="desktop-app", severity="high", path="app", line=1,
                    message="This app needs a second look: its launcher is in your own applications folder and "
                            "looks planted, because it replaces a system app or starts a program from a folder I write into.")]


def _entry_argv(entry: configparser.SectionProxy, paths: list[str]) -> list[str] | None:
    """The Exec line of a launcher as an argv, with the file codes (%f %F %u %U) filled in with `paths`
    and the other codes dropped. None when it can't be read."""
    try:
        words = shlex.split(entry.get("Exec", ""))
    except ValueError:
        return None
    argv: list[str] = []
    used = False
    for word in words:
        if word in ("%f", "%F", "%u", "%U"):
            argv += paths[:1] if word in ("%f", "%u") else paths
            used = True
        else:
            word = re.sub(r"%[ickdDnNvm]", "", word).replace("%%", "%")
            if word:
                argv.append(word)
    if paths and not used:
        argv += paths
    return argv or None


def _code_binaries(file: Path) -> list[str]:
    """The programs to try by name when the launcher itself can't start: VS Code's `code`, VSCodium's."""
    stem = file.stem.lower()
    if stem in {"codium", "com.vscodium.codium", "vscodium"}:
        names = ["codium", "vscodium"]
    elif stem in _CODE_IDS:
        names = ["code", "code-oss"]
    else:
        return []
    path = desktop_env()["PATH"]
    return [found for n in names if (found := shutil.which(n, path=path))]


class LaunchFailed(Exception):
    """Every way of starting the app failed; `reasons` says what each one reported."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


async def _launch_app(found: tuple[Path, str, configparser.SectionProxy], paths: list[str] = ()) -> None:
    """Start an installed app (with `paths` to open in it): gtk-launch by desktop id, then `gio launch`
    on the launcher file (which needs no data-dir lookup), then the launcher's own Exec line, then, for
    VS Code, `code` on the PATH (`code --new-window <path>`). The first that starts it wins; if none does,
    LaunchFailed says why, so the developer hears a reason instead of nothing happening."""
    file, _name, entry = found
    paths = list(paths)
    attempts: list[list[str]] = [["gtk-launch", file.name, *paths], ["gio", "launch", str(file), *paths]]
    exec_argv = _entry_argv(entry, paths)
    if exec_argv:
        attempts.append(exec_argv)
    for binary in _code_binaries(file):
        attempts.append([binary, "--new-window", *paths] if paths else [binary, "--new-window"])
    reasons: list[str] = []
    for argv in attempts:
        try:
            await _spawn_detached(argv)
            return
        except Exception as exc:  # FileNotFoundError (no such program), RuntimeError (it exited with an error)
            reason = _clean(str(exc), 100) if str(exc) else type(exc).__name__
            reasons.append(f"{os.path.basename(argv[0])}: {reason}")
    raise LaunchFailed(reasons)


async def _spawn_detached(argv: list[str]) -> None:
    """Start a desktop program and don't wait for it (gtk-launch and gio return once it started).

    The launched app inherits our stderr. A pipe would stay open as long as the app lives (so we
    would wait out the timeout every time) and, unread, would freeze the app once it filled. A
    temporary file has neither problem."""
    with tempfile.TemporaryFile() as err:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=err, env=desktop_env(), start_new_session=True)
        try:
            await asyncio.wait_for(proc.wait(), timeout=15)
        except asyncio.TimeoutError:
            return  # still starting: the app is on its way
        if proc.returncode:
            err.seek(0)
            raise RuntimeError(err.read(200).decode(errors="replace").strip() or f"exit code {proc.returncode}")


async def _open_default(path: Path | str) -> None:
    """Open a document, a folder or a web address in its default app: gio open, then xdg-open."""
    reasons = []
    for argv in (["gio", "open", str(path)], ["xdg-open", str(path)]):
        try:
            await _spawn_detached(argv)
            return
        except Exception as exc:
            reasons.append(f"{argv[0]}: {_clean(str(exc), 100) or type(exc).__name__}")
    raise LaunchFailed(reasons)


# ----- commands -----

class _Tail:
    """The last `cap` bytes of a stream and how many bytes there were: memory stays bounded however
    much a command prints."""

    def __init__(self, cap: int = MAX_COMMAND_OUTPUT) -> None:
        self.cap, self.total, self.data = cap, 0, b""

    async def drain(self, stream: asyncio.StreamReader) -> None:
        while chunk := await stream.read(65536):
            self.total += len(chunk)
            self.data = (self.data + chunk)[-self.cap:]


def _marked(run_id: str) -> list[int]:
    """Processes that carry this run's marker, wherever they went (setsid, nohup, a daemon)."""
    needle = f"{RUN_MARKER}={run_id}".encode() + b"\0"
    found = []
    for entry in os.scandir("/proc"):
        if entry.name.isdigit() and int(entry.name) != os.getpid():
            try:
                with open(f"/proc/{entry.name}/environ", "rb") as f:
                    if needle in f.read():
                        found.append(int(entry.name))
            except OSError:
                continue
    return found


def _kill_run(pgid: int, run_id: str) -> None:
    """Kill the command's group, then whatever still carries its marker (a child that left the group
    with setsid). Children of children that are racing to fork get a few passes."""
    for _ in range(4):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            left = _marked(run_id)
        except OSError:  # no /proc: the group kill is all there is
            return
        if not left:
            return
        for pid in left:
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


async def run_shell(command: str, cwd: Path, timeout_s: int = COMMAND_TIMEOUT_S) -> dict:
    """`bash -c command`: a timeout, the last 16 KB of each stream kept (never the whole output), and
    nothing the command started survives it: when the shell ends or is stopped, its group and anything
    that left it with setsid are killed, and the pipes are not waited on, so a child that keeps them
    open can't hold the tool past its time."""
    run_id = uuid.uuid4().hex
    env = desktop_env(for_command=True)
    env[RUN_MARKER] = run_id
    proc = await asyncio.create_subprocess_exec(
        "bash", "-c", command, cwd=str(cwd), stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env, start_new_session=True)
    out, err = _Tail(), _Tail()
    readers = [asyncio.ensure_future(out.drain(proc.stdout)), asyncio.ensure_future(err.drain(proc.stderr))]
    timed_out = False
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout_s)
        await asyncio.wait(readers, timeout=0.5)  # the last of the output, if nobody holds the pipes
    except asyncio.TimeoutError:
        timed_out = True
    finally:  # also when cancelled
        await asyncio.to_thread(_kill_run, proc.pid, run_id)
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass
        await asyncio.wait(readers, timeout=2)
        for reader in readers:
            reader.cancel()
    return {
        "exit_code": proc.returncode,
        "timed_out": timed_out,
        "stdout": out.data.decode(errors="replace"),
        "stderr": err.data.decode(errors="replace"),
        "truncated": out.total > MAX_COMMAND_OUTPUT or err.total > MAX_COMMAND_OUTPUT,
    }


def _short(text: str, limit: int = SPOKEN_TEXT_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _window_name(window: str) -> str:
    """'org.gnome.TextEditor.desktop: notes.txt' -> 'notes.txt in TextEditor', for the dialog."""
    app, _, title = window.partition(": ")
    app = _clean(app.removesuffix(".desktop").split(".")[-1], 40) or "the focused window"
    title = _clean(title, 60)
    return f"{title} in {app}" if title else app


def _window_is_app(window: str, stem: str, name: str) -> bool:
    """True when the focused window ("code.desktop: main.py - Visual Studio Code") belongs to the app
    started from the launcher `stem` (code, code_code, com.visualstudio.code) named `name`."""
    app = window.partition(": ")[0] if ": " in window else window
    app = app.lower().removesuffix(".desktop")
    stem = stem.lower()
    names = {stem, stem.rsplit(".", 1)[-1], stem.split("_")[0], name.lower(), name.lower().replace(" ", "")}
    if stem in _CODE_IDS:
        names |= {"code", "code-oss", "vscodium", "codium", "visual studio code", "com.visualstudio.code", "code_code"}
    return any(n and re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", app) for n in names)


# The island's side (contracts.md §9): (task_id, op_id, kind, text, window) -> (ok, message, window).
# kind "focus" asks which window has the focus; "type" and "keys" act in `window` only. None = no island.
DesktopBridge = Callable[[str, str, str, str, str | None], Awaitable[tuple[bool, str | None, str | None]]]


def _island_message(message: str | None) -> str:
    """The core's answers are codes ("desktop_unavailable", "canceled"): say them as a sentence."""
    if message in (None, "", "desktop_unavailable"):
        return "The Artemis island didn't answer, so nothing was typed."
    if message == "canceled":
        return "The task was canceled, so nothing was typed."
    return _clean(message, 200)


class DesktopTools:
    """The §9 tools and the §10 plan, as ToolGateway methods (see gateway.py for _ask and friends)."""

    _desktop: DesktopBridge | None
    _plans: dict

    # ----- the plan and the ring -----

    async def _plan(self, task_id: str, a: PlanArgs) -> dict:
        again = task_id in self._plans
        self._plans[task_id] = a
        self._cued.pop(task_id, None)
        await self._emit(task_id, "agent.plan", AgentPlanData(goal=a.goal, steps=[s.model_dump() for s in a.steps]),
                         spoken_plan(a, again), "normal")
        await self._emit(task_id, "ring.cue", RingCueData(action="think"))  # the plan is made: the ring thinks
        return {"planned": len(a.steps), "note": "Now carry out the steps. Each action still asks the developer."}

    def _plan_check(self, task_id: str, name: str) -> None:
        if self._require_plan and name in NEEDS_PLAN and task_id not in self._plans:
            raise self._tool_error("plan_required", "Call plan first: say the goal and the steps, then act.")

    async def _cue(self, task_id: str, name: str, target: str | None) -> None:
        """The ring's cue for a real tool call: where the work is (the call's own target, else the tool's),
        with the plan step it carries out when one fits. Without a plan the step is None."""
        action = TOOL_ACTIONS.get(name)
        if action is None:
            return
        step, plan_target = None, None
        plan = self._plans.get(task_id)
        if plan is not None:
            done = self._cued.setdefault(task_id, set())
            for i, s in enumerate(plan.steps):
                if s.action == action and i not in done:
                    step, plan_target = i, s.target
                    done.add(i)
                    break
        target = target or TOOL_TARGETS.get(name) or plan_target
        await self._emit(task_id, "ring.cue", RingCueData(step=step, action=action, target=target))
        if action in ("open", "type") and RING_LEAD_S > 0:
            # The ring flies to where the window will be (the island's dock press for a new app, or
            # the window itself) before it appears or is typed into, not after (David's test run:
            # windows opened suddenly while the ring was still on its way).
            await asyncio.sleep(RING_LEAD_S)

    async def ring_done(self, task_id: str) -> None:
        """The task ended: the ring goes home. Called by the core when the task's state is final."""
        await self._emit(task_id, "ring.cue", RingCueData(action="done"))
        self._plans.pop(task_id, None)
        self._cued.pop(task_id, None)

    # ----- one approval for a desktop action -----

    async def _approve_desktop(self, task_id: str, kind: str, spec: DesktopSpec, action: str, nothing: str,
                               findings: list[Finding] = (), still=None, files=()) -> None:
        """Ask, and check after the decision that `still()` (the current spec) matches what was approved."""
        workspace = self.project.slug if self.project else "desktop"
        payload = lambda s: [s.action, s.target, s.text, s.sha256]  # noqa: E731
        digest = approval_hash(kind, workspace, payload(spec))
        blocking = any(f.severity == "high" for f in findings)
        reasons = " ".join(f.message for f in findings)
        summary = (f"Blocked: {reasons} {action} Escape to reject, or use the override key to approve anyway."
                   if blocking else f"{action} Enter to approve, Escape to reject.")
        approval = Approval(approval_id=f"apr_{uuid.uuid4().hex[:12]}", kind=kind, approval_hash=digest,
                            summary=summary, desktop=spec, findings=list(findings), blocking=blocking, files=list(files))
        decision = await self._ask(task_id, approval, nothing)
        if decision is not Decision.approve:
            await self._rejected(task_id, approval, nothing)
        now = still() if still else spec
        if inspect.isawaitable(now):
            now = await now
        if approval_hash(kind, workspace, payload(now)) != digest:
            await self._resolved(task_id, approval, "reject", "invalidated")
            await self._state(task_id, "working")
            raise self._tool_error("invalidated", f"It changed while you were deciding. {nothing}")
        await self._resolved(task_id, approval, "approve", "user")
        await self._state(task_id, "working")

    def _roots(self) -> tuple[Path, ...]:
        return (self._projects_dir,)

    def _base(self) -> Path | None:
        return Path(self.project.root) if self.project and Path(self.project.root).is_dir() else None

    def _command_dir(self, cwd: str | None) -> Path:
        base = self._base() or home()
        folder = home_resolve(cwd, base, self._roots()) if cwd else home_resolve(str(base), None, self._roots())
        if not folder.is_dir():
            raise self._tool_error("not_found", f"There is no folder {cwd}.")
        return folder

    def _openable(self, raw: str, editor: bool = False) -> Path:
        """The path to open. `editor` is true when the app it goes to shows source code and never runs
        it (VS Code): then a program the agent wrote (.py, .js, .html) opens; launchers and executables
        still don't."""
        path = home_resolve(raw, self._base(), self._roots())
        if not path.exists():
            raise self._tool_error("not_found", f"There is no file or folder {raw} yet. Create it first with write_files "
                                                "(or create_project for a new project folder), then open it.")
        if path.is_file():
            suffix = path.suffix.lower()
            if suffix in _WEB_SUFFIXES or (editor and suffix in _EDITABLE_SUFFIXES):
                return path
            if suffix in _RUNNABLE_SUFFIXES or os.access(path, os.X_OK):
                raise PathRejected(f"{path.name} is a program or a script, so opening it would run it. I only open documents and folders.")
        return path

    def _readable(self, raw: str) -> Path:
        path = home_resolve(raw, self._base(), self._roots())
        if not path.is_file():
            raise self._tool_error("not_found", f"There is no file {raw}.")
        if path.stat().st_size > MAX_READ_BYTES:
            raise PathRejected(f"{raw} is larger than 1 megabyte, which is too big to read.")
        return path  # read_capped enforces the size again while reading

    # ----- the tools -----

    def _own_place(self, real: Path) -> bool:
        """True for a path in the developer's own folders (Documents, Desktop, Downloads, Projects), the
        projects folder or the current project: opening there needs no approval."""
        places = [home() / name for name in _OWN_FOLDERS] + list(self._roots())
        if self._base():
            places.append(self._base())
        return any(real.is_relative_to(p.resolve()) for p in places)

    def _not_installed(self, said: str) -> Exception:
        want = _normal(said)
        hint = ""
        if want in _FAMILIES and want in {"vs code", "vscode", "visual studio code", "code", "visual studio"}:
            hint = " Say editor to use any installed text editor."
        return self._tool_error("not_found", f"I couldn't find an app called {said}; it doesn't look installed.{hint}")

    def _launch_failed(self, name: str, exc: Exception) -> Exception:
        why = "; ".join(exc.reasons[-2:]) if isinstance(exc, LaunchFailed) else type(exc).__name__
        return self._tool_error(
            "tool_failed", f"I couldn't open {name}. I tried the desktop launcher, gio and starting the program itself ({why}).")

    def _note_opened(self, task_id: str, found: tuple[Path, str, configparser.SectionProxy]) -> None:
        """Remember the app just started, so typing waits for its window to have the focus."""
        self.__dict__.setdefault("_opened", {})[task_id] = (found[0].stem, found[1], time.monotonic())

    async def _open_app(self, task_id: str, a: OpenAppArgs) -> dict:
        found = _lookup(a.app)
        if found is None:
            raise self._not_installed(a.app)
        app_id, name = found[0].name, found[1]
        findings = _app_findings(found)  # decided now, before anything is launched

        def spec() -> DesktopSpec:
            # The launcher file's hash is bound too: swapped or shadowed, it would run something else.
            return DesktopSpec(action="open_app", target=app_id, sha256=app_digest(app_id))

        if findings:  # only a launcher that looks planted is asked about
            await self._approve_desktop(task_id, "open", spec(), f"Open {name}.", "Nothing was opened.",
                                        findings=findings, still=spec)
        await self._cue(task_id, "open_app", name)
        try:
            await _launch_app(found)
        except Exception as exc:
            raise self._launch_failed(name, exc) from None
        self._note_opened(task_id, found)
        await self._say(task_id, f"Opened {name}.")
        return {"opened": name, "app_id": app_id}

    async def _open_url(self, task_id: str, a) -> dict:
        """A web page in the default browser. http(s) only, with no user name or password in it, so it can
        never run anything local (file:, javascript:, ssh: and custom schemes are refused). Like opening
        one of the developer's own files it asks nothing: it changes nothing and the browser can close it."""
        from urllib.parse import urlsplit

        url = a.url.strip()
        try:
            parts = urlsplit(url)
            host = parts.hostname or ""
        except ValueError:
            raise PathRejected("That isn't a web address I can open.") from None
        if parts.scheme not in ("https", "http") or not host or parts.username or parts.password or any(c in url for c in "\n\r\t\x00 "):
            raise PathRejected("I only open web addresses that start with https:// or http://.")
        await self._cue(task_id, "open_url", "browser")
        try:
            await _open_default(url)
        except Exception as exc:
            raise self._launch_failed(host, exc) from None
        await self._say(task_id, f"Opened {host.removeprefix('www.')} in the browser.")
        return {"opened": url}

    async def _open_path(self, task_id: str, a: OpenPathArgs) -> dict:
        found = _lookup(a.with_app) if a.with_app else None
        if a.with_app and found is None:
            self._openable(a.path)  # a program is refused whichever app was named, before the app's absence
            raise self._not_installed(a.with_app)
        if found and _runs_files(*found):
            raise PathRejected(f"{found[1]} would run the file, not show it, so I won't open anything with it.")
        app = (found[0].name, found[1]) if found else None
        editor = bool(app) and app_is_editor(app[0])
        path = self._openable(a.path, editor)
        where = f" in {app[1]}" if app else ""
        findings = _app_findings(found) if found else []  # a planted launcher is blocking, as for open_app
        # Asked when the app's launcher may be planted, or the file is outside the developer's own folders.
        asked = bool(findings) or not self._own_place(path)

        def spec() -> DesktopSpec:
            p = self._openable(a.path, editor)
            return DesktopSpec(action="open_path", target=str(p), text=app[0] if app else None,
                               sha256=file_sha256(p) if p.is_file() else None)

        if asked:
            await self._approve_desktop(task_id, "open", spec(), f"Open {spoken_path(path)}{where}.", "Nothing was opened.",
                                        findings=findings, still=spec)
        await self._cue(task_id, "open_path", app[1] if app else "file")
        try:
            if found:
                await _launch_app(found, [str(path)])
            else:
                await _open_default(path)
        except Exception as exc:
            raise self._launch_failed(f"{path.name}{where}", exc) from None
        if found:
            self._note_opened(task_id, found)
        await self._say(task_id, f"Opened {path.name}{where}.")
        return {"opened": spoken_path(path), "with": app[1] if app else "its default app"}

    async def _read_path(self, task_id: str, a: ReadPathArgs) -> dict:
        path = self._readable(a.path)

        def spec() -> DesktopSpec:
            p = self._readable(a.path)
            return DesktopSpec(action="read_path", target=str(p), sha256=hashlib.sha256(read_capped(p)).hexdigest())

        approved = spec()
        await self._approve_desktop(task_id, "read_outside", approved, f"Read {spoken_path(path)}.", "Nothing was read.", still=spec)
        await self._cue(task_id, "read_path", "file")
        data = read_capped(path)
        if hashlib.sha256(data).hexdigest() != approved.sha256:
            raise self._tool_error("invalidated", f"{path.name} changed after you approved it. Nothing was read.")
        lines = data.decode("utf-8", errors="replace").splitlines()
        start = (a.start_line or 1) - 1
        end = a.end_line or len(lines)
        numbered = "\n".join(f"{i + 1}\t{line}" for i, line in enumerate(lines[start:end], start))
        await self._say(task_id, f"Reading {path.name}.")
        return {"path": spoken_path(path), "total_lines": len(lines), "content": wrap(f"file:{spoken_path(path)}", numbered)}

    async def _run_command(self, task_id: str, a: RunCommandArgs) -> dict:
        folder = self._command_dir(a.cwd)

        def spec() -> DesktopSpec:
            return DesktopSpec(action="run_command", target=str(self._command_dir(a.cwd)), text=a.command)

        await self._approve_desktop(task_id, "run_command", spec(), f"Run this command in {spoken_path(folder)}: {a.command}",
                                    "Nothing was run.", findings=command_findings(a.command), still=spec)
        await self._cue(task_id, "run_command", "terminal")
        await self._state(task_id, "running", "Running the command.")
        try:
            result = await run_shell(a.command, folder)
        finally:
            await self._state(task_id, "working")
        if result["timed_out"]:
            spoken = f"The command was stopped after {COMMAND_TIMEOUT_S} seconds."
        elif result["exit_code"] == 0:
            spoken = "The command finished."
        else:
            spoken = f"The command failed with exit code {result['exit_code']}."
        await self._say(task_id, spoken, "high" if result["exit_code"] else "normal")
        return {
            "exit_code": result["exit_code"], "timed_out": result["timed_out"], "truncated": result["truncated"],
            "stdout": wrap("command stdout", result["stdout"]), "stderr": wrap("command stderr", result["stderr"]),
        }

    async def _focused(self, task_id: str) -> str:
        if self._desktop is None:
            raise self._tool_error("desktop_unavailable", "Typing needs the Artemis island, and it isn't connected.")
        message = None
        for attempt in range(FOCUS_TRIES):
            ok, message, window = await self._desktop(task_id, f"op_{uuid.uuid4().hex[:12]}", "focus", "", None)
            if ok and window:
                return window
            if message == "canceled" or attempt == FOCUS_TRIES - 1:
                break
            await asyncio.sleep(FOCUS_POLL_S)  # the island may still be starting, or a window opening: ask again
        raise self._tool_error("desktop_unavailable", _island_message(message) if message else "The island couldn't tell which window has the focus.")

    async def _wait_for_opened_app(self, task_id: str, window: str) -> str:
        """After open_app or open_path, typing goes into that app: wait (up to FOCUS_WAIT_S) until its window
        has the focus, so a slow VS Code start doesn't send the text into the window it opened over."""
        opened = self.__dict__.get("_opened", {}).get(task_id)
        if opened is None or time.monotonic() - opened[2] > RECENT_OPEN_S:
            return window
        if _window_is_app(window, opened[0], opened[1]):
            self._opened.pop(task_id, None)
            return window
        deadline = time.monotonic() + FOCUS_WAIT_S
        while time.monotonic() < deadline:
            await asyncio.sleep(FOCUS_POLL_S)
            window = await self._focused(task_id)
            if _window_is_app(window, opened[0], opened[1]):
                self._opened.pop(task_id, None)
                return window
        self._opened.pop(task_id, None)
        raise self._tool_error("tool_failed", f"{opened[1]} didn't come to the front in {int(FOCUS_WAIT_S)} seconds, so nothing was typed. "
                                              "Open it again, or ask the developer to click its window, then type.")

    async def _type_into(self, task_id: str, kind: str, text: str, what: str) -> dict:
        window = await self._wait_for_opened_app(task_id, await self._focused(task_id))
        action = "type_text" if kind == "type" else "press_keys"
        findings = []
        if _is_terminal(window):
            findings.append(Finding(rule="desktop-terminal", severity="high", path="window", line=1,
                                    message="The focused window is a terminal, so what is typed may run as a command."))
        await self._approve_desktop(task_id, "type", DesktopSpec(action=action, target=window, text=text),
                                    f"{what} into {_window_name(window)}.", "Nothing was typed.", findings=findings)
        # The decision can take a while: ask again which window has the focus, so a focus that moved
        # (or a title that changed) stops here and not only in the island.
        if await self._focused(task_id) != window:
            raise self._tool_error("invalidated", "The focus moved while you were deciding. Nothing was typed.")
        await self._cue(task_id, action, _window_name(window))
        ok, message, _ = await self._desktop(task_id, f"op_{uuid.uuid4().hex[:12]}", kind, text, window)
        if not ok:
            raise self._tool_error("tool_failed", _island_message(message))
        return {"typed": len(text) if kind == "type" else text, "window": _window_name(window)}

    async def _type_text(self, task_id: str, a: TypeTextArgs) -> dict:
        # Escape, Ctrl-C and the like would act as keys, not text; only line breaks and tabs are text.
        if any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in a.text.replace("\r\n", "\n")):
            raise self._tool_error("invalid_args", "The text has control characters, so I won't type it.")
        text = a.text.replace("\r\n", "\n").replace("\r", "\n")
        # _short joins lines, so say out loud that each line break is an Enter key.
        breaks = text.count("\n")
        enter = f" with {breaks} line break{'s' if breaks != 1 else ''}, each one a press of the enter key" if breaks else ""
        return await self._type_into(task_id, "type", text, f"Type “{_short(text)}”{enter}")

    async def _press_keys(self, task_id: str, a: PressKeysArgs) -> dict:
        if _launcher_key(a.keys):
            raise PathRejected(f"{a.keys} opens a launcher or a shell, so I don't press it.")
        paste = " (this pastes the clipboard, which I can't show)" if _pastes(a.keys) else ""
        return await self._type_into(task_id, "keys", a.keys, f"Press {a.keys.replace('+', ' ')}{paste}")


# ----- publishing to GitHub (contracts.md §9.1, Proposed) -----
#
# The push never comes from the project folder: the approved files are copied byte for byte into a
# fresh private folder, committed there with hooks and fsmonitor off, and pushed from there. So an
# existing .git (its history, index, hooks or config) in the project can't add anything, an earlier
# publish's remote can't get in the way, and what is pushed is exactly what was hashed and shown.

PUBLISH_TIMEOUT_S = 60
MAX_PUBLISH_FILES = 200
SPOKEN_FILES = 8

# Never-run git: no hooks, no fsmonitor, no system config. gh's own push needs the global config for
# its credential helper, so it keeps that, with hooks and fsmonitor still forced off.
_GIT_SAFE = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_COUNT": "3",
             "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": "/dev/null",
             "GIT_CONFIG_KEY_1": "core.fsmonitor", "GIT_CONFIG_VALUE_1": "false",
             "GIT_CONFIG_KEY_2": "init.templateDir", "GIT_CONFIG_VALUE_2": ""}

# What a secret looks like, on top of redact(): tokens and keys people paste into code.
_SECRET_TEXT = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[abpr]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,}|"
    r"\bsk-[A-Za-z0-9_-]{20,}|\bsk_(live|test)_[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|aws_secret_access_key|_authToken\s*=|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s@/]+@", re.I)
_SECRET_NAMES = re.compile(r"^(id_(rsa|dsa|ecdsa|ed25519)|credentials(\.json)?|secrets?\.(json|ya?ml|toml)|\.npmrc|"
                           r"\.pypirc|\.netrc|.*\.(pem|key|p12|pfx|keystore|jks))$", re.I)


def _publish_env() -> dict[str, str]:
    """gh keeps its login in the GNOME keyring, so it needs the session bus; nothing else of ours."""
    return {**desktop_env(for_command=False), **_GIT_SAFE}


def _git_env() -> dict[str, str]:
    """Plain git in the private copy: no session, no global or system config, no hooks."""
    return {**desktop_env(for_command=True), **_GIT_SAFE, "GIT_CONFIG_GLOBAL": "/dev/null"}


async def _git(args: list[str], cwd: Path, env: dict[str, str], timeout: int = PUBLISH_TIMEOUT_S) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=str(cwd), stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT, env=env, start_new_session=True)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # the whole group: gh's own git push goes too
        except ProcessLookupError:
            pass
        await proc.wait()
        if isinstance(exc, asyncio.CancelledError):
            raise
        return 124, "timed out"
    return proc.returncode, out.decode(errors="replace")[-2000:]


def _last_line(out: str) -> str:
    from .redact import redact
    lines = [line for line in out.strip().splitlines() if line.strip()]
    return redact(lines[-1])[:200] if lines else "no details"


def _secret_files(root: Path, names: list[str]) -> list[str]:
    """Files that look like they hold a secret: they never leave the laptop."""
    from .redact import redact
    bad = []
    for name in names:
        f = root / name
        try:
            text = f.read_bytes()[:MAX_READ_BYTES].decode("utf-8", errors="replace")
        except OSError:
            continue
        if _SECRET_NAMES.match(f.name) or _SECRET_TEXT.search(text) or redact(text) != text:
            bad.append(name)
    return bad


class _Snapshot:
    """The project's publishable files, copied into a private folder with a fresh repository: the
    names git would commit (after .gitignore) and the hash of exactly those bytes."""

    def __init__(self, folder: Path, names: list[str], digest: str):
        self.folder, self.names, self.digest = folder, names, digest

    @classmethod
    async def take(cls, root: Path) -> "_Snapshot":
        import shutil
        import tempfile

        from .paths import project_files
        listed = project_files(root)
        if len(listed) > MAX_PUBLISH_FILES:
            raise ValueError("too_many")
        folder = Path(tempfile.mkdtemp(prefix="artemis-publish-"))
        os.chmod(folder, 0o700)
        try:
            for rel in listed:
                src = (root / rel).resolve()
                dst = folder / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(read_capped(src))  # contents only: no links, modes or attributes
            env = _git_env()
            for step in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"]):
                code, out = await _git(step, folder, env)
                if code:
                    raise RuntimeError(f"git {step[1]}: {_last_line(out)}")
            code, out = await _git(["git", "ls-files", "-z"], folder, env)
            if code:
                raise RuntimeError(f"git ls-files: {_last_line(out)}")
            names = sorted(n for n in out.split("\0") if n)
            digest = hashlib.sha256()
            for name in names:
                digest.update(name.encode() + b"\0" + (folder / name).read_bytes() + b"\0")
            return cls(folder, names, digest.hexdigest())
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def discard(self) -> None:
        import shutil
        shutil.rmtree(self.folder, ignore_errors=True)


class GithubTools:
    """publish_github, as a ToolGateway method (mixed in next to DesktopTools)."""

    async def _publish_github(self, task_id: str, a) -> dict:
        from artemis.contracts import FileSummary

        project = self._require_project()
        root = Path(project.root)
        if not root.is_dir():
            raise self._tool_error("not_found", "The project has no files yet. Write them first, then publish.")
        env = _publish_env()
        code, who = await _git(["gh", "api", "user", "--jq", r'"\(.login) \(.id)"'], root, env, 20)
        login, _, uid = who.strip().partition(" ")
        if code != 0 or not login or not all(c.isalnum() or c == "-" for c in login) or not uid.isdigit():
            raise self._tool_error("tool_failed", "GitHub isn't signed in on this computer. Run gh auth login, then try again.")
        full = f"{login}/{a.repo}"
        try:
            snap = await _Snapshot.take(root)
        except ValueError:
            raise self._tool_error("invalid_args", f"The project has more than {MAX_PUBLISH_FILES} files, which is too many to publish this way.") from None
        except RuntimeError as exc:
            raise self._tool_error("tool_failed", f"I couldn't prepare the files: {exc}.") from None
        try:
            if not snap.names:
                raise self._tool_error("not_found", "The project has no files to publish yet.")
            secrets = _secret_files(snap.folder, snap.names)
            if secrets:
                raise PathRejected("I won't publish this: " + ", ".join(secrets[:3]) +
                                   " looks like it holds a key or a password. Remove it first.")
            visibility = "public" if a.public else "private"
            about = f" Description: {a.description}." if a.description else ""

            def spec(s: "_Snapshot") -> DesktopSpec:
                return DesktopSpec(action="publish_github", target=full, text=f"{visibility}\n{a.description or ''}", sha256=s.digest)

            async def still() -> DesktopSpec:
                again = await _Snapshot.take(root)
                again.discard()
                return spec(again)

            shown = ", ".join(snap.names[:SPOKEN_FILES]) + (f" and {len(snap.names) - SPOKEN_FILES} more" if len(snap.names) > SPOKEN_FILES else "")
            findings = []
            if a.public:
                findings.append(Finding(rule="publish-public", severity="high", path="repository", line=1,
                                        message="This makes the code public: anyone on the internet can see it."))
            files = [FileSummary(path=n, action="created", lines_added=(snap.folder / n).read_bytes().count(b"\n"), lines_removed=0)
                     for n in snap.names]
            await self._approve_desktop(
                task_id, "publish", spec(snap),
                f"Publish {project.slug} to GitHub as a {visibility} repository, {full}, with {len(snap.names)} "
                f"file{'s' if len(snap.names) != 1 else ''}: {shown}.{about}",
                "Nothing was published.", findings=findings, still=still, files=files)
            await self._cue(task_id, "publish_github", "browser")
            await self._state(task_id, "running", "Publishing to GitHub.")
            try:
                git = _git_env()
                commit = ["git", "-c", f"user.name={login}", "-c", f"user.email={uid}+{login}@users.noreply.github.com",
                          "commit", "-q", "-m", "First version"]
                code, out = await _git(commit, snap.folder, git)
                if code:
                    raise self._tool_error("tool_failed", f"git commit failed: {_last_line(out)}.")
                create = ["gh", "repo", "create", full, f"--{visibility}", "--source", ".", "--remote", "origin", "--push"]
                if a.description:
                    create += ["--description", a.description]
                code, out = await _git(create, snap.folder, env)
                if code:
                    raise self._tool_error("tool_failed", f"GitHub said: {_last_line(out)}.")
            finally:
                await self._state(task_id, "working")
        finally:
            snap.discard()
        await self._say(task_id, f"Published to GitHub as {full}, {visibility}.", "normal")
        return {"repository": full, "url": f"https://github.com/{full}", "visibility": visibility, "files": len(snap.names)}
