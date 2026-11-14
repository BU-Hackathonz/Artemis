"""The spoken status: what was actually observed, and sentence endings."""

from artemis.contracts import EventDraft
from artemis.conversation import Controller


def event(type, task_id="tsk_1", **data):
    return EventDraft.model_construct(task_id=task_id, type=type, announce=None, data=data)


def started(request="create a program", state="working"):
    c = Controller()
    c.begin("tsk_1", request)
    c.observe(event("task.state", state=state))
    return c


# ----- sentence endings -----


def test_a_question_keeps_its_mark_and_is_never_followed_by_a_full_stop():
    c = started("can you add a docstring?")
    assert c.status() == "I'm working on: can you add a docstring?"
    assert ".?" not in c.status() and "?." not in c.status()


def test_an_exclamation_keeps_its_mark():
    assert started("make it work!").status() == "I'm working on: make it work!"


def test_an_ordinary_request_still_gets_one_full_stop():
    assert started("create a program.").status() == "I'm working on: create a program."
    assert started("create a program").status() == "I'm working on: create a program."


def test_a_waiting_follow_up_that_is_a_question_has_no_double_stop():
    c = started("do the first thing")
    c.decide("also, add a docstring?")
    assert c.status().endswith("Next up: also, add a docstring?")
    said = c.decide("and one more thing?").say
    assert said == "I already have one waiting: also, add a docstring? Say cancel to drop it."


def test_a_dropped_follow_up_that_is_a_question_has_no_double_stop():
    c = started("do the first thing")
    c.decide("add a docstring?")
    c.observe(event("task.state", state="failed"))
    follow = c.next_request()
    assert follow.say == "Dropped the follow-up, because the task before it didn't finish: add a docstring?"
    c2 = started("do the first thing")
    c2.decide("add a docstring?")
    assert c2.decide("cancel").say.startswith("Dropped: add a docstring? ")


# ----- what was actually observed -----


def test_a_running_task_says_nothing_about_checks_that_have_not_reported():
    c = started("create a program")
    assert c.status() == "I'm working on: create a program."
    c.observe(event("file.changed", path="main.py", action="created", lines_added=1, lines_removed=0, op_id="o1"))
    assert c.status() == "I'm working on: create a program. So far I changed main.py."


def test_the_last_run_is_reported_by_exit_code():
    c = started("run it")
    c.observe(event("run.result", op_id="o2", preset="python", exit_code=0, timed_out=False, duration_ms=5,
                    stdout_tail="", stderr_tail="", truncated=False))
    assert "The last run exited with code 0." in c.status()


def test_a_failed_run_is_reported_as_a_failure_never_as_a_success():
    c = started("run it")
    c.observe(event("run.result", op_id="o2", preset="python", exit_code=1, timed_out=False, duration_ms=5,
                    stdout_tail="", stderr_tail="NameError", truncated=False))
    c.observe(event("task.state", state="failed"))
    said = c.status()
    assert "failed" in said and "The last run exited with code 1." in said
    assert "success" not in said.lower() and "passed" not in said.lower()


def test_a_run_that_timed_out_says_so():
    c = started("run it")
    c.observe(event("run.result", op_id="o2", preset="python", exit_code=None, timed_out=True, duration_ms=10000,
                    stdout_tail="", stderr_tail="", truncated=False))
    assert "The last run timed out." in c.status()


def test_the_security_scan_is_reported_only_when_it_reported():
    c = started("create a program")
    assert "security" not in c.status()
    c.observe(event("security.findings", op_id="o3", tool="bandit", findings=[]))
    assert "The security scan reported no findings." in c.status()


def test_scan_findings_are_counted_with_the_high_ones_called_out():
    c = started("create a program")
    finding = {"rule": "B602", "path": "main.py", "line": 5, "message": "shell=True"}
    c.observe(event("security.findings", op_id="o3", tool="bandit",
                    findings=[dict(finding, severity="high"), dict(finding, severity="low")]))
    assert "The security scan reported 2 findings, 1 high severity." in c.status()
    c2 = started("create a program")
    c2.observe(event("security.findings", op_id="o3", tool="bandit", findings=[dict(finding, severity="low")]))
    assert "The security scan reported 1 finding." in c2.status()


def test_a_finished_task_reports_files_then_run_then_scan():
    c = started("create a program")
    c.observe(event("file.changed", path="main.py", action="created", lines_added=1, lines_removed=0, op_id="o1"))
    c.observe(event("security.findings", op_id="o3", tool="bandit", findings=[]))
    c.observe(event("run.result", op_id="o2", preset="python", exit_code=0, timed_out=False, duration_ms=5,
                    stdout_tail="Hello", stderr_tail="", truncated=False))
    c.observe(event("task.state", state="succeeded"))
    assert c.status() == (
        "I finished: create a program. I changed main.py. The last run exited with code 0. "
        "The security scan reported no findings."
    )


def test_when_waiting_for_a_decision_the_decision_is_said_alone():
    c = started("create a program", state="awaiting_approval")
    c.observe(event("approval.requested", approval_id="apr_1", summary="Create main.py. Enter to approve."))
    c.observe(event("run.result", op_id="o2", preset="python", exit_code=0, timed_out=False, duration_ms=5,
                    stdout_tail="", stderr_tail="", truncated=False))
    assert c.status() == "I'm waiting for your decision. Create main.py. Enter to approve."


def test_a_new_task_starts_with_no_remembered_checks():
    c = started("first")
    c.observe(event("run.result", op_id="o2", preset="python", exit_code=0, timed_out=False, duration_ms=5,
                    stdout_tail="", stderr_tail="", truncated=False))
    c.observe(event("task.state", state="succeeded"))
    c.begin("tsk_2", "second")
    c.observe(event("task.state", task_id="tsk_2", state="working"))
    assert c.status() == "I'm working on: second."
