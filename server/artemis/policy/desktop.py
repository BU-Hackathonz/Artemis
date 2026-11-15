"""Desktop tools (contracts.md §9) and the plan (§10), mixed into ToolGateway.

Artemis may act on the whole desktop, but every action that changes something, and every read
outside the project, waits for the guarded approval dialog. What the dialog shows is written from
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
import os
import re
import signal
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

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

# The plan action each tool carries out, for the ring.
TOOL_ACTIONS = {
    "create_project": "think", "open_project": "think", "list_files": "read", "read_file": "read",
    "read_path": "read", "inspect_page": "read", "scan_project": "read", "write_files": "write",
    "edit_file": "write", "run_program": "run", "run_command": "run", "open_app": "open",
    "open_path": "open", "type_text": "type", "press_keys": "type",
}
# Tools that change something or reach outside the project: the plan comes first (§10).
NEEDS_PLAN = {"write_files", "edit_file", "run_program", "run_command", "open_app", "open_path",
              "read_path", "type_text", "press_keys"}

# Opening one of these would run it, not open it.
_RUNNABLE_SUFFIXES = {".desktop", ".sh", ".bash", ".zsh", ".appimage", ".run", ".jar", ".bin",
                      ".exe", ".msi", ".deb", ".rpm", ".flatpakref", ".py", ".pl", ".rb"}
# Windows that run what is typed into them.
_TERMINALS = re.compile(r"ptyxis|gnome-terminal|org\.gnome\.terminal|org\.gnome\.console|kgx|kitty|"
                        r"alacritty|xterm|konsole|terminator|tilix|wezterm|foot|code|vscode", re.I)
# Keys that open a launcher or a shell rather than act in the focused window.
_LAUNCHER_KEYS = re.compile(r"^((ctrl|shift|alt)\+)*(super|super_l|super_r|meta)$|super\+|"
                            r"^alt\+f2$|^ctrl\+alt\+(t|delete|backspace)$|^(ctrl\+)?alt\+f[1-9]$", re.I)
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
    (re.compile(r"\|\s*(sudo\s+|env\s+|exec\s+)*(" + _SHELL + "|" + _INTERP + r")\b|(^|[\s;&|])(source|\.)\s+<\(|"
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
_ENV_APP = ("XDG_RUNTIME_DIR", "DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP",
            "DBUS_SESSION_BUS_ADDRESS")
_ENV_ALLOW = _ENV_COMMAND + _ENV_APP
RUN_MARKER = "ARTEMIS_RUN_ID"  # in a command's environment, to find what it left behind


def desktop_env(for_command: bool = False) -> dict[str, str]:
    """The allowlisted environment: for the apps Artemis opens (with the session's bus and display)
    or, with `for_command`, for a shell command (without them)."""
    allow = _ENV_COMMAND if for_command else _ENV_ALLOW
    env = {k: v for k, v in os.environ.items() if k in allow or k.startswith("LC_")}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
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


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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

def _app_dirs() -> list[Path]:
    data = [os.environ.get("XDG_DATA_HOME") or str(home() / ".local/share")]
    data += (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    data += ["/var/lib/snapd/desktop", "/var/lib/flatpak/exports/share", str(home() / ".local/share/flatpak/exports/share")]
    return [Path(d) / "applications" for d in data if d]


# What people say for an app whose real name differs.
_ALIASES = {"vs code": "visual studio code", "vscode": "visual studio code", "code": "visual studio code",
            "browser": "firefox", "web browser": "firefox", "file manager": "files", "editor": "text editor",
            "terminal": "terminal", "calculator": "calculator", "settings": "settings"}


def find_app(query: str) -> tuple[str, str] | None:
    """(desktop id, display name) of the installed app that best matches `query`: an exact desktop
    id, then an exact name, then a name or id that contains it. Hidden and NoDisplay apps are skipped."""
    want = query.strip().lower().removesuffix(".desktop")
    want = _ALIASES.get(want, want)
    if not want:
        return None
    best: tuple[int, str, str] | None = None
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
                parser.read(file, encoding="utf-8")
                entry = parser["Desktop Entry"]
                if entry.get("Type") != "Application" or entry.getboolean("NoDisplay", False) or entry.getboolean("Hidden", False):
                    continue
            except (configparser.Error, KeyError, UnicodeDecodeError, OSError, ValueError):
                continue
            name = entry.get("Name", file.stem)
            app_id = file.stem.lower()
            score = 3 if want == app_id else 2 if want == name.lower() else 1 if (want in name.lower() or want in app_id) else 0
            if score and (best is None or score > best[0]):
                best = (score, file.name, name)
    return (best[1], best[2]) if best else None


async def _spawn_detached(argv: list[str]) -> None:
    """Start a desktop program and don't wait for it (gtk-launch and gio return once it started)."""
    proc = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE, env=desktop_env(), start_new_session=True)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=15)
    except asyncio.TimeoutError:
        return  # still starting: the app is on its way
    if proc.returncode:
        raise RuntimeError(err.decode(errors="replace").strip()[:200] or f"exit code {proc.returncode}")


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
    app = app.removesuffix(".desktop").split(".")[-1] or "the focused window"
    return f"{title} in {app}" if title else app


# The island's side (contracts.md §9): (task_id, op_id, kind, text, window) -> (ok, message, window).
# kind "focus" asks which window has the focus; "type" and "keys" act in `window` only. None = no island.
DesktopBridge = Callable[[str, str, str, str, str | None], Awaitable[tuple[bool, str | None, str | None]]]


class DesktopTools:
    """The §9 tools and the §10 plan, as ToolGateway methods (see gateway.py for _ask and friends)."""

    _desktop: DesktopBridge | None
    _plans: dict

    # ----- the plan and the ring -----

    async def _plan(self, task_id: str, a: PlanArgs) -> dict:
        self._plans[task_id] = a
        self._cued.pop(task_id, None)
        await self._emit(task_id, "agent.plan", AgentPlanData(goal=a.goal, steps=[s.model_dump() for s in a.steps]))
        return {"planned": len(a.steps), "note": "Now carry out the steps. Each action still asks the developer."}

    def _plan_check(self, task_id: str, name: str) -> None:
        if self._require_plan and name in NEEDS_PLAN and task_id not in self._plans:
            raise self._tool_error("plan_required", "Call plan first: say the goal and the steps, then act.")

    async def _cue(self, task_id: str, name: str, target: str | None) -> None:
        """The ring's cue for a real tool call, with the plan step it carries out when one fits."""
        action = TOOL_ACTIONS.get(name)
        plan = self._plans.get(task_id)
        if action is None or plan is None:
            return  # the ring acts out a plan: without one, it keeps its working look
        step = None
        if plan is not None:
            done = self._cued.setdefault(task_id, set())
            for i, s in enumerate(plan.steps):
                if s.action == action and i not in done:
                    step = i
                    done.add(i)
                    target = target or s.target
                    break
        await self._emit(task_id, "ring.cue", RingCueData(step=step, action=action, target=target))

    async def ring_done(self, task_id: str) -> None:
        """The task ended: the ring goes home. Called by the core when the task's state is final."""
        await self._emit(task_id, "ring.cue", RingCueData(action="done"))
        self._plans.pop(task_id, None)
        self._cued.pop(task_id, None)

    # ----- one approval for a desktop action -----

    async def _approve_desktop(self, task_id: str, kind: str, spec: DesktopSpec, action: str, nothing: str,
                               findings: list[Finding] = (), still=None) -> None:
        """Ask, and check after the decision that `still()` (the current spec) matches what was approved."""
        workspace = self.project.slug if self.project else "desktop"
        payload = lambda s: [s.action, s.target, s.text, s.sha256]  # noqa: E731
        digest = approval_hash(kind, workspace, payload(spec))
        blocking = any(f.severity == "high" for f in findings)
        reasons = " ".join(f.message for f in findings)
        summary = (f"Blocked: {reasons} {action} Escape to reject, or use the override key to approve anyway."
                   if blocking else f"{action} Enter to approve, Escape to reject.")
        approval = Approval(approval_id=f"apr_{uuid.uuid4().hex[:12]}", kind=kind, approval_hash=digest,
                            summary=summary, desktop=spec, findings=list(findings), blocking=blocking)
        decision = await self._ask(task_id, approval, nothing)
        if decision is not Decision.approve:
            await self._rejected(task_id, approval, nothing)
        now = still() if still else spec
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

    def _openable(self, raw: str) -> Path:
        path = home_resolve(raw, self._base(), self._roots())
        if not path.exists():
            raise self._tool_error("not_found", f"There is no file or folder {raw}.")
        if path.is_file() and (path.suffix.lower() in _RUNNABLE_SUFFIXES or os.access(path, os.X_OK)):
            raise PathRejected(f"{path.name} is a program or a script, so opening it would run it. I only open documents and folders.")
        return path

    def _readable(self, raw: str) -> Path:
        path = home_resolve(raw, self._base(), self._roots())
        if not path.is_file():
            raise self._tool_error("not_found", f"There is no file {raw}.")
        if path.stat().st_size > MAX_READ_BYTES:
            raise PathRejected(f"{raw} is larger than 1 megabyte, which is too big to read.")
        return path

    # ----- the tools -----

    async def _open_app(self, task_id: str, a: OpenAppArgs) -> dict:
        found = find_app(a.app)
        if found is None:
            raise self._tool_error("not_found", f"I couldn't find an app called {a.app}.")
        app_id, name = found
        await self._approve_desktop(task_id, "open", DesktopSpec(action="open_app", target=app_id), f"Open {name}.", "Nothing was opened.")
        await self._cue(task_id, "open_app", name)
        try:
            await _spawn_detached(["gtk-launch", app_id])
        except Exception as exc:
            raise self._tool_error("tool_failed", f"{name} could not be opened: {type(exc).__name__}.") from None
        await self._say(task_id, f"Opened {name}.")
        return {"opened": name, "app_id": app_id}

    async def _open_path(self, task_id: str, a: OpenPathArgs) -> dict:
        path = self._openable(a.path)
        app = find_app(a.with_app) if a.with_app else None
        if a.with_app and app is None:
            raise self._tool_error("not_found", f"I couldn't find an app called {a.with_app}.")
        where = f" in {app[1]}" if app else ""

        def spec() -> DesktopSpec:
            p = self._openable(a.path)
            return DesktopSpec(action="open_path", target=str(p), text=app[0] if app else None,
                               sha256=file_sha256(p) if p.is_file() else None)

        await self._approve_desktop(task_id, "open", spec(), f"Open {spoken_path(path)}{where}.", "Nothing was opened.", still=spec)
        await self._cue(task_id, "open_path", app[1] if app else path.name)
        try:
            await _spawn_detached(["gtk-launch", app[0], str(path)] if app else ["gio", "open", str(path)])
        except Exception as exc:
            raise self._tool_error("tool_failed", f"{path.name} could not be opened: {type(exc).__name__}.") from None
        await self._say(task_id, f"Opened {path.name}{where}.")
        return {"opened": spoken_path(path), "with": app[1] if app else "its default app"}

    async def _read_path(self, task_id: str, a: ReadPathArgs) -> dict:
        path = self._readable(a.path)

        def spec() -> DesktopSpec:
            p = self._readable(a.path)
            return DesktopSpec(action="read_path", target=str(p), sha256=file_sha256(p))

        approved = spec()
        await self._approve_desktop(task_id, "read_outside", approved, f"Read {spoken_path(path)}.", "Nothing was read.", still=spec)
        await self._cue(task_id, "read_path", path.name)
        data = path.read_bytes()
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
        ok, message, window = await self._desktop(task_id, f"op_{uuid.uuid4().hex[:12]}", "focus", "", None)
        if not ok or not window:
            raise self._tool_error("desktop_unavailable", message or "The island couldn't tell which window has the focus.")
        return window

    async def _type_into(self, task_id: str, kind: str, text: str, what: str) -> dict:
        window = await self._focused(task_id)
        action = "type_text" if kind == "type" else "press_keys"
        findings = []
        if _TERMINALS.search(window.partition(": ")[0]):
            findings.append(Finding(rule="desktop-terminal", severity="high", path="window", line=1,
                                    message="The focused window is a terminal, so what is typed may run as a command."))
        await self._approve_desktop(task_id, "type", DesktopSpec(action=action, target=window, text=text),
                                    f"{what} into {_window_name(window)}.", "Nothing was typed.", findings=findings)
        await self._cue(task_id, action, _window_name(window))
        ok, message, _ = await self._desktop(task_id, f"op_{uuid.uuid4().hex[:12]}", kind, text, window)
        if not ok:
            raise self._tool_error("tool_failed", message or "The typing stopped.")
        return {"typed": len(text) if kind == "type" else text, "window": _window_name(window)}

    async def _type_text(self, task_id: str, a: TypeTextArgs) -> dict:
        return await self._type_into(task_id, "type", a.text, f"Type “{_short(a.text)}”")

    async def _press_keys(self, task_id: str, a: PressKeysArgs) -> dict:
        if _LAUNCHER_KEYS.search(a.keys):
            raise PathRejected(f"{a.keys} opens a launcher or a shell, so I don't press it.")
        return await self._type_into(task_id, "keys", a.keys, f"Press {a.keys.replace('+', ' ')}")
