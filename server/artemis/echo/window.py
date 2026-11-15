"""What is in the focused window, read from the Linux accessibility bus (AT-SPI), read-only.

A blind developer asks "what's on my screen?". This walks the accessibility tree of the window that has
the focus and returns its title and its text, the same tree Orca reads. It never clicks, types or sets
anything. Everything in it is data from another program: the text is redacted, wrapped as untrusted by
the caller, and can never approve or start anything. Password fields are skipped.

Nothing here is wired to a tool yet: asking for another app's text needs the developer's approval, and
that gate lives in policy/desktop.py. `read_focused_window` and `describe_window` are what that tool calls.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from artemis.policy.redact import redact

MAX_NODES = 600  # accessibility nodes visited
MAX_DEPTH = 14
MAX_CHARS = 6000  # text kept; the rest is cut
_SKIP_ROLES = frozenset({"password text"})  # never read out


class WindowError(Exception):
    """A problem the developer should hear (no accessibility bus, no focused window). The message is spoken."""


@dataclass
class Node:
    """One accessibility node, whatever backend produced it."""

    role: str
    name: str = ""
    text: str = ""
    children: list["Node"] = field(default_factory=list)


@dataclass
class WindowText:
    app: str
    title: str
    lines: list[str]
    cut: bool = False


def collect(root: Node) -> tuple[list[str], bool]:
    """The text of a window as lines, in reading order: a node's own text, else its name when it is a
    label, heading, button, link, menu item or similar. Capped in nodes, depth and characters."""
    lines: list[str] = []
    seen = 0
    size = 0
    cut = False
    stack: list[tuple[Node, int]] = [(root, 0)]
    while stack:
        node, depth = stack.pop()
        seen += 1
        if seen > MAX_NODES:
            cut = True
            break
        if node.role in _SKIP_ROLES:
            continue
        said = (node.text or node.name or "").strip()
        if said and (node.text or node.role not in ("frame", "window", "filler", "panel", "scroll pane")):
            said = re.sub(r"\s+", " ", said)
            label = "" if node.role in ("text", "label", "static", "paragraph", "terminal") else f"{node.role}: "
            if size + len(said) > MAX_CHARS:
                cut = True
                break
            lines.append(label + said)
            size += len(said) + len(label)
        if depth < MAX_DEPTH:
            stack.extend((c, depth + 1) for c in reversed(node.children))
    return lines, cut


def read_focused_window(backend: Callable[[], tuple[str, str, Node]] | None = None) -> WindowText:
    """The focused window's app, title and text, secrets redacted. `backend` returns (app, title, tree);
    the default reads AT-SPI through PyGObject and raises WindowError when that is not there."""
    app, title, tree = (backend or _atspi_focused)()
    lines, cut = collect(tree)
    # Redact the whole text at once, so a secret that spans lines is found.
    body = redact("\n".join(lines))
    return WindowText(app=redact(app), title=redact(title), lines=body.split("\n") if body else [], cut=cut)


def describe_window(w: WindowText, limit: int = 12) -> str:
    """Spoken summary: which app and window, then the first lines. Text from the window is read as data."""
    head = f"The focused window is {w.title or 'untitled'}" + (f" in {w.app}." if w.app else ".")
    if not w.lines:
        return head + " I found no readable text in it."
    shown = [ln.rstrip(".") + "." for ln in w.lines[:limit]]
    more = len(w.lines) - limit
    tail = f" And {more} more lines; ask me to continue." if more > 0 else (" The text was cut short." if w.cut else "")
    return head + " " + " ".join(shown) + tail


def _atspi_focused() -> tuple[str, str, Node]:  # pragma: no cover - needs a Linux desktop session
    try:
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except Exception:
        raise WindowError("I can't read windows here: the accessibility service is not available.") from None
    desktop = Atspi.get_desktop(0)

    def to_node(acc, depth: int = 0) -> Node:
        role = acc.get_role_name() or ""
        text = ""
        try:
            iface = acc.get_text_iface()
            if iface is not None and role not in _SKIP_ROLES:
                text = iface.get_text(0, min(iface.get_character_count(), MAX_CHARS))
        except Exception:
            text = ""
        node = Node(role=role, name=acc.get_name() or "", text=text)
        if depth < MAX_DEPTH:
            for i in range(min(acc.get_child_count(), 200)):
                try:
                    node.children.append(to_node(acc.get_child_at_index(i), depth + 1))
                except Exception:
                    continue
        return node

    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        for j in range(app.get_child_count()):
            win = app.get_child_at_index(j)
            if win is not None and win.get_state_set().contains(Atspi.StateType.ACTIVE):
                return app.get_name() or "", win.get_name() or "", to_node(win)
    raise WindowError("I couldn't find a focused window.")

