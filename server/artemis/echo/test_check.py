import os
import shutil
from pathlib import Path

import pytest

from artemis.echo.check import (
    EchoError,
    EchoReport,
    Finding,
    compare,
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
    assert text.endswith("And 1 more in the log.")
    assert speak_report(report([])) == "Echo Check found no problems on Join the club."
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
