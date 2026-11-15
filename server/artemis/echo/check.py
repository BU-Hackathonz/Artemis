"""Echo Check (Avery + Jun): what a screen reader actually gets from a page, spoken.

Loads a local page in headless Chromium, reads the accessibility tree with Playwright's
aria_snapshot() (roles and accessible names, the same tree NVDA reads), runs axe-core on it, and
turns the result into one short spoken summary. Run it before and after a fix to prove the fix.

Only local pages: file:// URLs (or plain paths) and http://127.0.0.1 / localhost. Nothing on the
internet is ever loaded, so a generated page can't be used to reach the network through us.
"""

import os
import re
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlparse

from pydantic import BaseModel

from artemis.childenv import child_environ, secrets_hidden_from_environ

AXE_JS = Path(__file__).parent / "vendor" / "axe.min.js"
Impact = Literal["critical", "serious", "moderate", "minor"]
IMPACT_ORDER: list[str] = ["critical", "serious", "moderate", "minor"]
MAX_OUTLINE = 40  # lines of the aria outline kept in the report


class EchoError(Exception):
    """A problem the user should hear (bad target, no browser). The message is spoken."""


class Finding(BaseModel):
    rule: str  # axe rule id, e.g. "button-name"
    impact: Impact
    target: str  # CSS selector of the first offending element
    message: str  # axe's help text, e.g. "Buttons must have discernible text"
    count: int = 1  # how many elements fail this rule


class EchoReport(BaseModel):
    url: str
    title: str
    findings: list[Finding]
    outline: list[str]  # headings, landmarks and controls with their accessible names
    # axe "incomplete": checks axe couldn't decide (e.g. text on a gradient or image), so a
    # person has to look. Never reported as "no problems" (Jun's review of #21).
    needs_review: list[Finding] = []
    # What the page is made of, counted from the whole tree (the outline above is cut at MAX_OUTLINE).
    structure: "Structure | None" = None

    @property
    def ok(self) -> bool:
        return not self.findings and not self.needs_review


class Structure(BaseModel):
    """The page as a screen reader meets it, from the accessibility tree and not from pixels."""

    landmarks: list[str] = []  # "banner", "navigation Main menu", "main", ...
    headings: list[tuple[int, str]] = []  # (level, name)
    links: int = 0
    unnamed_links: int = 0
    buttons: int = 0
    unnamed_buttons: int = 0
    fields: list[tuple[str, str]] = []  # (role, name); an empty name means no label
    images: int = 0
    images_no_alt: int = 0  # images with no accessible name: no alt text


EchoReport.model_rebuild()


def resolve_target(url_or_file: str) -> str:
    """Return a URL Echo Check may load, or raise EchoError. Paths become file:// URLs."""
    text = url_or_file.strip()
    parsed = urlparse(text)
    if parsed.scheme in ("http", "https"):
        if parsed.hostname not in ("127.0.0.1", "localhost"):
            raise EchoError("Echo Check only opens pages on this computer, not on the internet.")
        return text
    if parsed.scheme == "file":
        raw = unquote(parsed.path)  # file:///C:/My%20Pages/x.html -> C:/My Pages/x.html
        path = Path(raw.lstrip("/") if os.name == "nt" else raw)
    elif parsed.scheme and len(parsed.scheme) > 1:  # "C:" parses as a one-letter scheme
        raise EchoError(f"Echo Check can't open {parsed.scheme} links.")
    else:
        path = Path(text)
    path = path.resolve()
    if not path.is_file():
        raise EchoError(f"I couldn't find the page {path.name}.")
    return path.as_uri()


# ----- the aria outline -----

_OUTLINE_ROLES = (
    "heading", "banner", "navigation", "main", "contentinfo", "region", "form", "search",
    "complementary", "button", "link", "textbox", "checkbox", "radio", "combobox", "img",
    "slider", "switch", "tab", "dialog", "alert",
)
_LINE = re.compile(r"^(\s*)-\s+([a-z]+)(?:\s+\"((?:[^\"\\]|\\.)*)\")?(.*)$")


def outline_from_snapshot(snapshot: str) -> list[str]:
    """Keep the lines a blind user navigates by: landmarks, headings and controls, with names.

    aria_snapshot() is YAML-like: '- heading "Sign up" [level=1]', '- button', '- textbox "Email"'.
    An unnamed control is written as 'button (no name)' so the gap is audible.
    """
    out: list[str] = []
    for raw in snapshot.splitlines():
        m = _LINE.match(raw)
        if not m:
            continue
        indent, role, name, rest = m.groups()
        if role not in _OUTLINE_ROLES:
            continue
        level = re.search(r"\[level=(\d)\]", rest or "")
        label = f"{role} {level.group(1)}" if role == "heading" and level else role
        depth = len(indent) // 2
        shown = f'"{name}"' if name else "(no name)"
        if role in ("banner", "navigation", "main", "contentinfo", "form", "search", "complementary") and not name:
            shown = ""
        out.append(("  " * depth + f"{label} {shown}").rstrip())
        if len(out) >= MAX_OUTLINE:
            out.append("…")
            break
    return out


_LANDMARKS = ("banner", "navigation", "main", "contentinfo", "complementary", "form", "search", "region")
_FIELD_ROLES = ("textbox", "checkbox", "radio", "combobox", "slider", "switch")


def structure_from_snapshot(snapshot: str) -> Structure:
    """Count and name the landmarks, headings, links, buttons, form fields and images of the whole tree."""
    st = Structure()
    for raw in snapshot.splitlines():
        m = _LINE.match(raw)
        if not m:
            continue
        _, role, name, rest = m.groups()
        name = (name or "").replace('\\"', '"').strip()
        if role in _LANDMARKS:
            if role in ("form", "region") and not name:
                continue  # an unnamed form or region is not a landmark a screen reader lists
            st.landmarks.append(f"{role} {name}".strip())
        elif role == "heading":
            level = re.search(r"\[level=(\d)\]", rest or "")
            st.headings.append((int(level.group(1)) if level else 2, name))
        elif role == "link":
            st.links += 1
            st.unnamed_links += not name
        elif role == "button":
            st.buttons += 1
            st.unnamed_buttons += not name
        elif role in _FIELD_ROLES:
            st.fields.append((role, name))
        elif role == "img":
            st.images += 1
            st.images_no_alt += not name
    return st


_ROLE_WORDS = {"textbox": "text box", "combobox": "drop-down", "checkbox": "check box", "radio": "radio button"}
_MAX_NAME = 60
_MAX_LISTED = 12


def _short(name: str) -> str:
    name = " ".join(name.split())
    return name if len(name) <= _MAX_NAME else name[:_MAX_NAME].rstrip() + "..."


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def describe_page(report: EchoReport) -> str:
    """The page described by its structure, for the ear: landmarks, headings in order, how many links and
    buttons, each form field and whether it has a label, and images with no alt text. Names come from the
    page, so they are data to read out, never instructions."""
    st = report.structure
    if st is None:
        return "I could not read the structure of this page."
    parts = [f"This page is titled {_short(report.title)}." if report.title else "This page has no title."]
    if st.landmarks:
        parts.append("Its landmarks are " + ", ".join(st.landmarks[:_MAX_LISTED]) + ".")
    else:
        parts.append("It has no landmarks, so a screen reader cannot jump to the main part.")
    if st.headings:
        shown = [f"level {lv} {_short(n) or 'with no text'}" for lv, n in st.headings[:_MAX_LISTED]]
        more = len(st.headings) - _MAX_LISTED
        tail = f", and {more} more" if more > 0 else ""
        parts.append(f"{_count(len(st.headings), 'heading')}: " + ", ".join(shown) + tail + ".")
        if st.headings[0][0] != 1:
            parts.append("The first heading is not level 1.")
    else:
        parts.append("It has no headings.")
    bits = []
    if st.links:
        bits.append(_count(st.links, "link"))
    if st.buttons:
        bits.append(_count(st.buttons, "button"))
    if bits:
        parts.append("It has " + " and ".join(bits) + ".")
    for noun, total, unnamed in (("link", st.links, st.unnamed_links), ("button", st.buttons, st.unnamed_buttons)):
        if unnamed:
            parts.append(f"{_count(unnamed, noun)} of {total} {'has' if unnamed == 1 else 'have'} no name.")
    if st.fields:
        named = [f"{_ROLE_WORDS.get(r, r)} {_short(n)}" for r, n in st.fields if n][:_MAX_LISTED]
        no_label = sum(1 for _, n in st.fields if not n)
        text = f"{_count(len(st.fields), 'form field')}"
        if named:
            text += ": " + ", ".join(named)
        parts.append(text + ".")
        if no_label:
            parts.append(f"{_count(no_label, 'field')} {'has' if no_label == 1 else 'have'} no label.")
    if st.images_no_alt:
        parts.append(f"{_count(st.images_no_alt, 'image')} of {st.images} {'has' if st.images_no_alt == 1 else 'have'} no alt text.")
    elif st.images:
        parts.append(f"All {_count(st.images, 'image')} have alt text.")
    return " ".join(parts)


# ----- axe results -> findings -----


def findings_from_axe(violations: list[dict]) -> list[Finding]:
    findings = []
    for v in violations:
        nodes = v.get("nodes") or []
        target = nodes[0].get("target", [""])[0] if nodes else ""
        impact = v.get("impact") or "minor"
        findings.append(
            Finding(
                rule=v.get("id", "unknown"),
                impact=impact if impact in IMPACT_ORDER else "minor",
                target=str(target),
                message=v.get("help", v.get("description", "")),
                count=max(1, len(nodes)),
            )
        )
    findings.sort(key=lambda f: (IMPACT_ORDER.index(f.impact), f.rule))
    return findings


# ----- speech -----

# Plain-words versions of the rules a generated page most often breaks.
_PLAIN = {
    "button-name": "a button has no accessible name",
    "label": "a form field has no label",
    "image-alt": "an image has no text alternative",
    "html-has-lang": "the page doesn't say which language it is in",
    "color-contrast": "some text doesn't have enough contrast",
    "link-name": "a link has no accessible name",
    "document-title": "the page has no title",
    "landmark-one-main": "the page has no main landmark",
    "page-has-heading-one": "the page has no top-level heading",
    "region": "some content is outside any landmark",
    "input-button-name": "an input button has no accessible name",
    "select-name": "a select box has no label",
}


def _plain(f: Finding) -> str:
    text = _PLAIN.get(f.rule) or f.message.rstrip(".").lower() or f.rule
    if f.count > 1:
        text += f" ({f.count} places)"
    return text


# What a person should check when axe couldn't decide.
_REVIEW = {
    "color-contrast": "whether some text has enough contrast",
    "color-contrast-enhanced": "whether some text has enough contrast",
}


def _review_sentence(report: EchoReport) -> str:
    n = len(report.needs_review)
    if not n:
        return ""
    what = "; ".join(_REVIEW.get(f.rule) or f"the {f.rule} rule" for f in report.needs_review[:2])
    noun = "thing needs" if n == 1 else "things need"
    return f"{n} {noun} a manual check, because axe couldn't decide: {what}."


def speak_report(report: EchoReport, top: int = 3) -> str:
    """One short spoken summary: total, counts by impact, then the worst few in plain words."""
    review = _review_sentence(report)
    if not report.findings:
        if review:
            return (
                f"Echo Check's automated checks found no definite problems on "
                f"{report.title or 'the page'}, but {review}"
            )
        return f"Echo Check's automated checks found no problems on {report.title or 'the page'}."
    n = len(report.findings)
    counts = {i: sum(1 for f in report.findings if f.impact == i) for i in IMPACT_ORDER}
    by_impact = ", ".join(f"{c} {i}" for i, c in counts.items() if c)
    worst = "; ".join(f"{f.impact.capitalize()}: {_plain(f)}" for f in report.findings[:top])
    more = f" And {n - top} more; ask me to list them." if n > top else ""
    noun = "problem" if n == 1 else "problems"
    tail = f" Also, {review}" if review else ""
    return f"Echo Check found {n} {noun}: {by_impact}. {worst}.{more}{tail}"


def compare(before: EchoReport, after: EchoReport) -> str:
    """Spoken before/after line for the demo."""
    fixed = {f.rule for f in before.findings} - {f.rule for f in after.findings}
    new = {f.rule for f in after.findings} - {f.rule for f in before.findings}
    parts = [f"Before: {len(before.findings)} problems. After: {len(after.findings)}."]
    if after.needs_review:
        parts.append(f"{len(after.needs_review)} still need a manual check.")
    if fixed:
        parts.append(f"Fixed: {', '.join(_PLAIN.get(r, r) for r in sorted(fixed))}.")
    if new:
        parts.append(f"New: {', '.join(_PLAIN.get(r, r) for r in sorted(new))}.")
    return " ".join(parts)


# ----- the browser part -----


# No DNS prefetch and no WebRTC to the network: neither goes through Playwright's request routing.
_BROWSER_ARGS = ["--dns-prefetch-disable", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"]


async def _block_socket(ws) -> None:
    """Every WebSocket is answered here and closed; it never reaches any server (Jun's review of
    #21: an allowed http page opened a socket to [::1], which request routing doesn't see)."""
    await ws.close()


async def _launch(p):
    """Bundled Chromium, else the installed Chrome or Edge (the demo laptop has Edge), else fail."""
    attempts = [{}, {"channel": "chrome"}, {"channel": "msedge"}]
    exe = os.environ.get("ARTEMIS_CHROME")
    if exe:
        attempts.insert(0, {"executable_path": exe})
    last: Exception | None = None
    for kwargs in attempts:
        try:
            return await p.chromium.launch(headless=True, args=_BROWSER_ARGS, env=child_environ(), **kwargs)
        except Exception as exc:  # each missing browser raises; try the next one
            last = exc
    raise EchoError(
        "Echo Check needs a browser. Run: uv run --group echo python -m playwright install chromium"
    ) from last


async def echo_check(url_or_file: str, timeout_s: float = 15) -> EchoReport:
    url = resolve_target(url_or_file)
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise EchoError("Echo Check isn't installed. Run: uv sync --group echo") from exc

    with secrets_hidden_from_environ():
        p = await async_playwright().start()  # the Node driver copies os.environ now, so no secrets in it
    try:
        browser = await _launch(p)
        try:
            context = await browser.new_context(
                java_script_enabled=True,
                offline=url.startswith("file:"),
                service_workers="block",  # a worker could fetch outside the page's routing
            )
            await context.route_web_socket(re.compile(".*"), _block_socket)

            async def only_local(route):
                target = urlparse(route.request.url)
                if target.scheme in ("file", "data", "blob") or target.hostname in ("127.0.0.1", "localhost"):
                    await route.continue_()
                else:
                    await route.abort()  # a page may not pull anything from the internet

            await context.route("**/*", only_local)
            page = await context.new_page()
            await page.goto(url, timeout=timeout_s * 1000, wait_until="load")
            title = await page.title()
            snapshot = await page.locator("body").aria_snapshot(timeout=timeout_s * 1000)
            await page.add_script_tag(content=AXE_JS.read_text(encoding="utf-8"))
            result = await page.evaluate(
                "async () => { const r = await axe.run(document, {resultTypes: ['violations', 'incomplete']});"
                " const pick = v => ({id: v.id, impact: v.impact, help: v.help,"
                " description: v.description, nodes: v.nodes.map(n => ({target: n.target}))});"
                " return {violations: r.violations.map(pick), incomplete: r.incomplete.map(pick)}; }"
            )
        finally:
            await browser.close()
    finally:
        await p.stop()

    return EchoReport(
        url=url,
        title=title,
        findings=findings_from_axe(result["violations"]),
        outline=outline_from_snapshot(snapshot),
        structure=structure_from_snapshot(snapshot),
        needs_review=findings_from_axe(result["incomplete"]),
    )
