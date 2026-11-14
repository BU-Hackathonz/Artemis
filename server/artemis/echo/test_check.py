import os
import shutil
from pathlib import Path

import pytest

from artemis.echo.check import (
    EchoError,
    EchoReport,
    Finding,
    compare,
    echo_check,
    findings_from_axe,
    outline_from_snapshot,
    resolve_target,
    speak_report,
)

FIXTURE = Path(__file__).resolve().parents[3] / "fixture" / "echo-page"

SNAPSHOT = """- banner:
  - img "Club logo"
- main:
  - heading "Join the club" [level=1]
  - paragraph: We never share your email.
  - form "Sign up":
    - textbox "Email"
    - button
"""


def report(findings, title="Join the club"):
    return EchoReport(url="file:///x.html", title=title, findings=findings, outline=[])


def test_outline_keeps_landmarks_headings_controls_and_flags_unnamed():
    assert outline_from_snapshot(SNAPSHOT) == [
        "banner",
        '  img "Club logo"',
        "main",
        '  heading 1 "Join the club"',
        '  form "Sign up"',
        '    textbox "Email"',
        "    button (no name)",
    ]


def test_findings_sorted_by_impact_with_counts():
    violations = [
        {"id": "region", "impact": "moderate", "help": "Content in landmarks", "nodes": [{"target": ["div"]}] * 3},
        {"id": "button-name", "impact": "critical", "help": "Buttons must have discernible text", "nodes": [{"target": ["button"]}]},
        {"id": "odd", "impact": None, "help": "Something", "nodes": []},
    ]
    found = findings_from_axe(violations)
    assert [(f.rule, f.impact, f.count) for f in found] == [
        ("button-name", "critical", 1), ("region", "moderate", 3), ("odd", "minor", 1)]
    assert found[0].target == "button"


def test_speak_report_counts_then_top_three_in_plain_words():
    findings = [
        Finding(rule="button-name", impact="critical", target="button", message="m"),
        Finding(rule="image-alt", impact="critical", target="img", message="m"),
        Finding(rule="color-contrast", impact="serious", target="p", message="m", count=2),
        Finding(rule="region", impact="moderate", target="div", message="m"),
    ]
    text = speak_report(report(findings))
    assert text.startswith("Echo Check found 4 problems: 2 critical, 1 serious, 1 moderate. ")
    assert "Critical: a button has no accessible name" in text
    assert "Serious: some text doesn't have enough contrast (2 places)" in text
    assert text.endswith("And 1 more; ask me to list them.")
    assert speak_report(report([])) == "Echo Check's automated checks found no problems on Join the club."
    unknown = speak_report(report([Finding(rule="x-rule", impact="minor", target="a", message="Links Must Work.")]))
    assert unknown == "Echo Check found 1 problem: 1 minor. Minor: links must work."


def test_compare_before_after():
    before = report([Finding(rule="button-name", impact="critical", target="b", message="m")])
    assert compare(before, report([])) == "Before: 1 problems. After: 0. Fixed: a button has no accessible name."


@pytest.mark.parametrize("bad", ["https://example.com/", "http://10.0.0.5/x", "ftp://127.0.0.1/x", "javascript:alert(1)"])
def test_only_local_targets(bad):
    with pytest.raises(EchoError):
        resolve_target(bad)


def test_local_targets(tmp_path):
    page = tmp_path / "p.html"
    page.write_text("<html></html>")
    assert resolve_target(str(page)) == page.resolve().as_uri()
    assert resolve_target(page.resolve().as_uri()) == page.resolve().as_uri()
    assert resolve_target("http://127.0.0.1:5173/") == "http://127.0.0.1:5173/"
    with pytest.raises(EchoError):
        resolve_target(str(tmp_path / "missing.html"))


def _browser_available() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    if os.environ.get("ARTEMIS_CHROME"):
        return True
    cache = Path.home() / ".cache" / "ms-playwright"
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    return cache.exists() or local.exists() or bool(shutil.which("google-chrome") or shutil.which("chrome") or shutil.which("msedge"))


@pytest.mark.skipif(not _browser_available(), reason="needs the echo group and a Chromium/Chrome/Edge")
async def test_fixture_before_and_after_in_a_real_browser():
    from artemis.echo import echo_check

    before = await echo_check(str(FIXTURE / "before.html"))
    rules = {f.rule for f in before.findings}
    assert {"button-name", "image-alt", "html-has-lang", "color-contrast"} <= rules
    assert "button (no name)" in before.outline
    after = await echo_check(str(FIXTURE / "after.html"))
    assert after.findings == []
    assert '    button "Sign up"' in after.outline


# ----- Jun's review of #21 -----


def test_spaced_file_uri_resolves(tmp_path):
    page = tmp_path / "My Pages" / "index.html"
    page.parent.mkdir()
    page.write_text("<html></html>")
    assert resolve_target(page.as_uri()) == page.resolve().as_uri()


def test_undecided_checks_are_never_reported_as_no_problems():
    review = [Finding(rule="color-contrast", impact="serious", target="h1", message="Elements must meet contrast")]
    report = EchoReport(url="file:///x", title="Join", findings=[], outline=[], needs_review=review)
    assert not report.ok
    spoken = speak_report(report)
    assert "no definite problems" in spoken and "manual check" in spoken and "no problems" not in spoken.replace("no definite problems", "")
    worse = report.model_copy(update={"findings": review})
    assert speak_report(worse).endswith("Also, 1 thing needs a manual check, because axe couldn't decide: whether some text has enough contrast.")


@pytest.mark.skipif(not _browser_available(), reason="needs the echo group and a Chromium/Chrome/Edge")
async def test_gradient_contrast_needs_review_in_a_real_browser(tmp_path):
    page = tmp_path / "gradient.html"
    page.write_text(
        '<!doctype html><html lang="en"><head><title>Gradient</title></head><body><main>'
        '<h1 style="background: linear-gradient(90deg, #777, #999); color: #888">Welcome</h1>'
        "</main></body></html>"
    )
    report = await echo_check(str(page))
    assert any(f.rule == "color-contrast" for f in report.needs_review), report
    assert not report.ok and "manual check" in speak_report(report)


@pytest.mark.skipif(not _browser_available(), reason="needs the echo group and a Chromium/Chrome/Edge")
async def test_websockets_never_reach_a_server(tmp_path):
    """An allowed http://127.0.0.1 page tries WebSockets to 127.0.0.1 and [::1]; no server sees one."""
    import asyncio
    import functools
    import http.server
    import socket
    import threading

    hits: list[str] = []

    def listener(family, host):
        try:
            s = socket.socket(family, socket.SOCK_STREAM)
            s.bind((host, 0))
        except OSError:
            return None
        s.listen()
        s.settimeout(0.2)

        def accept():
            end = threading.Event()
            for _ in range(60):
                try:
                    conn, _ = s.accept()
                    hits.append(host)
                    conn.close()
                except OSError:
                    pass
            end.set()

        threading.Thread(target=accept, daemon=True).start()
        return s.getsockname()[1]

    v4 = listener(socket.AF_INET, "127.0.0.1")
    v6 = listener(socket.AF_INET6, "::1")
    targets = [f"ws://127.0.0.1:{v4}/"] + ([f"ws://[::1]:{v6}/"] if v6 else [])
    script = "".join(f"try {{ new WebSocket({t!r}); }} catch (e) {{}}" for t in targets)
    (tmp_path / "index.html").write_text(
        f'<!doctype html><html lang="en"><head><title>WS</title></head><body><main><h1>Hi</h1></main>'
        f"<script>{script}</script></body></html>"
    )
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        await echo_check(f"http://127.0.0.1:{httpd.server_address[1]}/index.html")
        await asyncio.sleep(1.0)
    finally:
        httpd.shutdown()
    assert hits == [], f"WebSocket reached {hits}"


async def test_playwright_driver_and_browser_get_no_secrets(tmp_path, monkeypatch):
    """No browser is needed: the Node driver starts without the key or token, and the launch env has neither
    (audit finding 2). The launch is faked, so this checks the env the real code builds."""
    import playwright.async_api as pw_api

    from artemis.echo import check

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    monkeypatch.setenv("ARTEMIS_TOKEN", "token-should-not-leak")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    page = tmp_path / "index.html"
    page.write_text('<!doctype html><html lang="en"><title>x</title><body></body></html>')
    seen: dict = {}

    class Boom(Exception):
        pass

    class Browser:
        async def new_context(self, **kwargs):
            raise Boom()

        async def close(self):
            seen["browser_closed"] = True

    class Chromium:
        async def launch(self, **kwargs):
            seen["launch_env"] = kwargs["env"]
            return Browser()

    class FakePlaywright:
        chromium = Chromium()

        async def stop(self):
            seen["stopped"] = True

    class Manager:
        async def start(self):
            seen["driver_env"] = dict(os.environ)
            return FakePlaywright()

    monkeypatch.setattr(pw_api, "async_playwright", lambda: Manager())
    with pytest.raises(Boom):
        await check.echo_check(str(page))

    for env in (seen["driver_env"], seen["launch_env"]):
        assert "ANTHROPIC_API_KEY" not in env and "ARTEMIS_TOKEN" not in env
    assert seen["launch_env"]["PLAYWRIGHT_BROWSERS_PATH"] == "/opt/pw-browsers"
    assert seen["browser_closed"] and seen["stopped"]
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-should-not-leak"
