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
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert set(seen["env"]) <= {"SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA"}
    assert seen["cwd"] != str(tmp_path)  # Bandit never runs inside the project folder
    assert seen["argv"][1:8] == ["-m", "bandit", "-r", str(tmp_path), "-f", "json", "-q"]
    # Virtual environments and node_modules are excluded, so a run isn't held up by them.
    assert seen["argv"][8] == "-x"
    excluded = set(seen["argv"][9].split(","))
    assert {str(tmp_path / ".venv"), str(tmp_path / "node_modules")} <= excluded
    assert {"*/.venv/*", "*/node_modules/*"} <= excluded  # and the same names at any depth
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


# ----- files Bandit can't parse, and folders it shouldn't crawl -----

UNPARSEABLE = "import subprocess\nsubprocess.call(input(), shell=True)\ndef (:\n"


def test_a_file_bandit_cannot_parse_is_a_finding_not_a_clean_scan(tmp_path):
    out = json.dumps(
        {
            "errors": [{"filename": str(tmp_path / "main.py"), "reason": "syntax error while parsing AST from file"}],
            "results": [],
        }
    )
    finding = parse_bandit(out, tmp_path)[0]
    assert (finding.rule, finding.severity, finding.path, finding.line) == ("SCAN", "medium", "main.py", 1)
    assert finding.message == "couldn't be scanned: syntax error while parsing AST from file"


@needs_bandit
def test_real_bandit_unparseable_file_is_said_in_the_approval(tmp_path):
    projects = tmp_path / "projects"
    projects.mkdir()

    async def go():
        log, approvals = EventLog(), FakeApprovals([Decision.reject])
        gateway = ToolGateway(FakeRunner(projects), approvals, log, projects_dir=projects, scan=scan)
        await gateway.call("tsk_1", "create_project", {"name": "broken"})
        await gateway.call("tsk_1", "write_files", {"files": [{"path": "main.py", "content": UNPARSEABLE}]})
        return approvals.requests[0]

    approval = asyncio.run(go())
    assert "No security findings" not in approval.summary
    assert "main.py line 1, couldn't be scanned: syntax error" in approval.summary
    assert approval.findings[0].rule == "SCAN"


@needs_bandit
def test_real_bandit_skips_virtual_environments_and_node_modules(tmp_path):
    risky = "import subprocess\nsubprocess.call(input(), shell=True)\n"
    for rel in [".venv/lib/v.py", "node_modules/x/n.py", "src/s.py"]:
        (tmp_path / rel).parent.mkdir(parents=True)
        (tmp_path / rel).write_text(risky)
    assert {f.path for f in asyncio.run(scan(tmp_path))} == {"src/s.py"}


# ----- the project can't switch the scan off (Avery's review of #35) -----


def test_bandit_ignores_project_settings_and_nosec(tmp_path, monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv

        class Done:
            returncode = 0
            stdout = '{"results": []}'

        return Done()

    monkeypatch.setattr(scan_module.subprocess, "run", fake_run)
    asyncio.run(scan(tmp_path))
    argv = seen["argv"]
    assert "--ignore-nosec" in argv
    ini = argv[argv.index("--ini") + 1]
    assert ini == str(scan_module._INI) and scan_module._INI.read_text().strip() == "[bandit]"


@needs_bandit
def test_real_bandit_a_planted_bandit_file_or_nosec_cannot_hide_findings(tmp_path):
    (tmp_path / ".bandit").write_text("[bandit]\nskips = B602,B404\n")
    (tmp_path / "main.py").write_text(
        "import subprocess  # nosec\nsubprocess.call(input(), shell=True)  # nosec\n"
    )
    rules = {f.rule for f in asyncio.run(scan(tmp_path))}
    assert "B602" in rules, rules


def test_a_finding_behind_an_outside_link_never_lands_on_a_project_file(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside" / "link.py"
    output = json.dumps({"errors": [], "results": [{
        "filename": str(outside), "line_number": 2, "test_id": "B602",
        "issue_severity": "HIGH", "issue_confidence": "HIGH", "issue_text": "shell=True",
    }]})
    (finding,) = parse_bandit(output, tmp_path)
    assert finding.path == "(outside the project) link.py"
