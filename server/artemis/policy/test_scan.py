"""The Bandit scan: report parsing offline, plus real Bandit runs when it is installed."""

import asyncio
import json

import pytest

from artemis.contracts import Decision
from artemis.policy import scan as scan_module
from artemis.policy.gateway import ToolGateway
from artemis.policy.scan import ScanError, parse_bandit, scan
from artemis.policy.testing import EventLog, FakeApprovals, FakeRunner

SHELL = "import subprocess\n\n\ndef run(command):\n    return subprocess.call(command, shell=True)\n"
CLEAN = 'print("Hello, World!")\n'

needs_bandit = pytest.mark.skipif(not scan_module.available(), reason="bandit is not installed")


def report(tmp_path, *items):
    results = [
        {
            "filename": str(tmp_path / name), "line_number": line, "test_id": rule,
            "issue_severity": severity, "issue_confidence": confidence, "issue_text": text,
        }
        for name, line, rule, severity, confidence, text in items
    ]
    return json.dumps({"errors": [], "results": results})


@pytest.mark.parametrize(
    "severity, confidence, level",
    [
        ("HIGH", "HIGH", "high"), ("HIGH", "MEDIUM", "high"), ("HIGH", "LOW", "medium"),
        ("MEDIUM", "HIGH", "medium"), ("MEDIUM", "MEDIUM", "medium"), ("MEDIUM", "LOW", "low"),
        ("LOW", "HIGH", "low"), ("LOW", "MEDIUM", "low"), ("LOW", "LOW", "low"),
    ],
)
def test_severity_and_confidence_map_to_a_level(tmp_path, severity, confidence, level):
    out = report(tmp_path, ("main.py", 4, "B602", severity, confidence, "subprocess call with shell=True"))
    finding = parse_bandit(out, tmp_path)[0]
    assert finding.severity == level
    assert (finding.rule, finding.path, finding.line) == ("B602", "main.py", 4)


def test_findings_are_relative_and_worst_first(tmp_path):
    out = report(
        tmp_path,
        ("z.py", 1, "B105", "LOW", "MEDIUM", "hardcoded password"),
        ("src/a.py", 9, "B602", "HIGH", "HIGH", "shell=True"),
    )
    assert [(f.path, f.severity) for f in parse_bandit(out, tmp_path)] == [("src/a.py", "high"), ("z.py", "low")]


@pytest.mark.parametrize("output", ["", "No module named bandit", "{}", "[]"])
def test_a_missing_report_is_an_error_not_a_clean_scan(tmp_path, output):
    with pytest.raises(ScanError):
        parse_bandit(output, tmp_path)


def test_bandit_gets_no_secrets_in_its_environment(tmp_path, monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs, argv=argv)

        class Done:
            returncode = 0
            stdout = '{"results": []}'

        return Done()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-FAKEfakeFAKE")
    monkeypatch.setattr(scan_module.subprocess, "run", fake_run)
    assert asyncio.run(scan(tmp_path)) == []
    assert [p.name for p in tmp_path.iterdir()] == ["main.py"]  # the scan leaves nothing behind
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert set(seen["env"]) <= {"SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA"}
    assert seen["cwd"] != str(tmp_path)  # Bandit never runs inside the project folder
    assert seen["argv"][1:] == ["-m", "bandit", "-r", str(tmp_path), "-f", "json", "-q"]
    assert seen["timeout"] == 20 and "shell" not in seen


def test_a_crashed_scan_is_an_error(tmp_path, monkeypatch):
    class Done:
        returncode = 2
        stdout = ""

    monkeypatch.setattr(scan_module.subprocess, "run", lambda *a, **k: Done())
    with pytest.raises(ScanError):
        asyncio.run(scan(tmp_path))


@needs_bandit
def test_real_bandit_flags_shell_true_and_blocks_the_write(tmp_path):
    """Design E3: a Bandit finding makes a blocking approval, before anything is written."""
    projects = tmp_path / "projects"
    projects.mkdir()

    async def go():
        log, approvals = EventLog(), FakeApprovals([Decision.reject])
        gateway = ToolGateway(FakeRunner(projects), approvals, log, projects_dir=projects, scan=scan)
        await gateway.call("tsk_1", "create_project", {"name": "shelly"})
        result = await gateway.call(
            "tsk_1", "write_files", {"files": [{"path": "main.py", "content": SHELL}]}
        )
        return log, approvals, result

    log, approvals, result = asyncio.run(go())
    approval = approvals.requests[0]
    assert approval.blocking is True
    assert approval.summary.startswith("Blocked: 1 high-severity issue, main.py line 5, ")
    assert approval.findings[0].rule == "B602" and approval.findings[0].severity == "high"
    assert log.of("security.findings")[0].data["tool"] == "bandit"
    assert result.error == "scan_blocked"
    assert list(projects.iterdir()) == []


@needs_bandit
def test_real_bandit_on_clean_code_finds_nothing(tmp_path):
    (tmp_path / "main.py").write_text(CLEAN)
    assert asyncio.run(scan(tmp_path)) == []
    assert [p.name for p in tmp_path.iterdir()] == ["main.py"]  # the scan leaves nothing behind
