"""The conversation controller, offline: phrases, state from events, and what gets decided."""

import pytest

from artemis.contracts import EventDraft, Style
from artemis.conversation import INTENTS, Controller, FollowUp, match, simplify
from artemis.conversation.controller import (
    HEARTBEAT,
    KEYBOARD_ONLY,
    NOTHING_TO_APPROVE,
    WAITING,
)

# ----- phrases -----


@pytest.mark.parametrize(
    "said, intent",
    [
        ("Stop.", "stop_speaking"),
        ("stop talking", "stop_speaking"),
        ("Hey Artemis, stop talking please", "stop_speaking"),
        ("Um, can you be quiet?", "stop_speaking"),
        ("That's enough.", "stop_speaking"),
        ("Cancel.", "cancel"),
        ("Cancel the task", "cancel"),
        ("never mind", "cancel"),
        ("Artemis, cancel that now", "cancel"),
        ("Repeat that.", "repeat"),
        ("What did you say?", "repeat"),
        ("What are you doing?", "status"),
        ("Hey Artemus, what's the status", "status"),
        ("Are you done yet?", "status"),
        ("Which file did you change?", "changed_files"),
        ("What did you change", "changed_files"),
        ("Keep it brief.", "brief"),
        ("Deep dive", "detailed"),
        ("visual mode", "visual"),
        ("Code mode, please.", "code"),
        ("Slower", "slower"),
        ("Could you slow down?", "slower"),
        ("yes", "approval_word"),
        ("Approve it.", "approval_word"),
    ],
)
def test_a_built_in_phrase_is_recognized(said, intent):
    assert match(said) == intent


@pytest.mark.parametrize(
    "said",
    [
        "Stop the server from crashing when the file is missing",
        "Cancel the subscription when the card is declined",
        "Add a stop button to the page",
        "Never mind the tests, just fix main.py",
        "Repeat the header on every page",
        "What are you doing in the parse function on line 12?",
        "Make it blue, actually green",
        "Keep it brief in the README",
        "Add a status endpoint",
        "Don't delete main.py",
        "No, don't use the shell",
        "Yes, and add dark mode",
        "",
        "Hey Artemis",
    ],
)
def test_a_request_that_only_contains_a_phrase_goes_to_the_agent(said):
    assert match(said) is None


def test_simplify_only_trims_the_edges():
    assert simplify("Hey Artemis, um, STOP talking, please!") == "stop talking"
    assert simplify("What's happening?") == "whats happening"
    # Words in the middle are never dropped, so a real request keeps its meaning.
    assert simplify("please don't stop the server please") == "dont stop the server"


def test_no_phrase_belongs_to_two_intents_and_none_can_approve():
    seen: dict[str, str] = {}
    for name, phrases in INTENTS.items():
        for phrase in phrases:
            assert phrase == simplify(phrase), phrase
            assert seen.setdefault(phrase, name) == name, phrase
    assert "stop" in INTENTS["stop_speaking"] and "stop" not in INTENTS["cancel"]
    assert not {"approve", "run", "write", "allow", "override"} & set(INTENTS)


# ----- a session, as the controller sees it -----


def event(type, task_id="tsk_1", **data):
    return EventDraft.model_construct(task_id=task_id, type=type, announce=None, data=data)


def working(request="build a to-do app"):
    c = Controller()
    c.begin("tsk_1", request)
    c.observe(event("task.state", state="working"))
    return c


APPROVAL = "Create project todo with 1 file: main.py, 12 lines. Enter to approve, Escape to reject."


def awaiting_approval():
    c = working()
    c.observe(event("agent.message", text="Starting project todo.", kind="progress"))
    c.observe(event("task.state", state="awaiting_approval"))
    c.observe(event("approval.requested", summary=APPROVAL, approval_id="apr_1"))
    return c


def finished(answer="I built it.", files=("main.py",)):
    c = working()
    for path in files:
        c.observe(event("file.changed", path=path, action="created", op_id="op", lines_added=1, lines_removed=0))
    c.observe(event("agent.message", text=answer, kind="answer"))
    c.observe(event("task.state", state="succeeded"))
    return c


def test_a_request_with_nothing_running_starts_a_task_with_the_exact_words():
    raw = "Um, make the header blue... actually, green. And don't touch main.py."
    d = Controller().decide(raw)
    assert (d.intent, d.action, d.text) == ("request", "submit", raw)  # the raw transcript, untouched
    assert d.say is None


def test_an_empty_transcript_does_nothing():
    assert Controller().decide("   ").action == "none"
    assert working().decide("").action == "none"


def test_an_empty_transcript_with_an_approval_open_says_the_approval_again():
    # The tap that recorded nothing may have cut the approval announcement off (§5.1).
    d = awaiting_approval().decide("")
    assert (d.intent, d.action, d.say, d.priority, d.reminder) == ("empty", "say", APPROVAL, "high", None)


@pytest.mark.parametrize("said", ["Artemis", "Hey Artemis.", "hey artemus", "OK Artemis!"])
def test_only_the_wake_word_is_never_a_task(said):
    c = working()
    d = c.decide(said)
    assert (d.intent, d.action, d.say, d.text) == ("wake_word", "none", None, None)
    assert c.queued is None
    assert Controller().decide(said).action == "none"
    assert awaiting_approval().decide(said).say == APPROVAL


def test_the_heartbeat_is_not_a_step_in_the_status_answer():
    c = working()
    c.observe(event("agent.message", text="Reading main.py.", kind="progress"))
    c.observe(event("agent.message", text=HEARTBEAT, kind="progress"))
    assert c.decide("status").say == "I'm working on: build a to-do app. Last step: Reading main.py."
    fresh = working()
    fresh.observe(event("agent.message", text=HEARTBEAT, kind="progress"))
    assert fresh.decide("status").say == "I'm working on: build a to-do app."


def test_the_heartbeat_text_matches_the_core():
    from artemis.core.session import STILL_WORKING

    assert HEARTBEAT == STILL_WORKING


def test_stop_talking_never_cancels_the_task():
    c = working()
    d = c.decide("Stop.")
    assert d.action == "stop_speech" and d.reminder is None
    assert c.task.active


def test_cancel_cancels_only_when_asked_in_those_words():
    c = working()
    assert c.decide("Cancel the task").action == "cancel"
    idle = Controller()
    d = idle.decide("cancel")
    assert (d.action, d.say) == ("say", "Nothing to cancel.")


def test_status_while_working_reports_progress_without_a_new_task():
    c = working()
    c.observe(event("agent.message", text="Reading main.py.", kind="progress"))
    d = c.decide("What are you doing?")
    assert d.action == "say" and d.text is None
    assert d.say == "I'm working on: build a to-do app. Last step: Reading main.py."


def test_status_in_every_other_state():
    assert Controller().decide("status").say == "I'm not working on anything yet."
    assert awaiting_approval().decide("status").say == f"I'm waiting for your decision. {APPROVAL}"
    running = working()
    running.observe(event("task.state", state="running"))
    assert running.decide("status").say == "I'm running the program for: build a to-do app."
    assert finished(files=("main.py", "util.py")).decide("are you done").say == (
        "I finished: build a to-do app. I changed main.py and util.py."
    )
    failed = working()
    failed.observe(event("task.state", state="failed"))
    assert failed.decide("status").say == "The last request failed: build a to-do app."


def test_which_file_did_you_change():
    assert Controller().decide("what did you change").say == "I haven't changed any files yet."
    assert finished().decide("Which file did you change?").say == "I changed 1 file in the last request: main.py."
    many = finished(files=("a.py", "b.py", "c.py", "d.py", "e.py"))
    assert many.decide("what changed").say == "I changed 5 files in the last request: a.py, b.py, c.py and 2 more."
    assert finished(files=()).decide("what changed").say == "I didn't change any files in the last request."


def test_events_for_another_task_are_ignored():
    c = working()
    c.observe(event("file.changed", task_id="tsk_old", path="old.py"))
    c.observe(event("task.state", task_id="tsk_old", state="failed"))
    assert c.task.active and c.task.files == []


# ----- approvals stay on the keyboard and survive interruptions -----


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def open_approval(kind="write_files", blocking=False):
    clock = Clock()
    c = Controller(clock=clock)
    c.begin("tsk_1", "build a to-do app")
    c.observe(event("task.state", state="awaiting_approval"))
    c.observe(event("approval.requested", summary=APPROVAL, approval_id="apr_1", approval_hash="sha256:x",
                    kind=kind, blocking=blocking))
    return c, clock


@pytest.mark.parametrize("said", ["yes", "Yes.", "no", "okay", "sure"])
def test_yes_and_no_never_decide_an_approval(said):
    c, clock = open_approval()
    clock.t += 5
    d = c.decide(said)
    assert d.action != "decide" and d.verdict is None and c.approval == APPROVAL


@pytest.mark.parametrize("said", ["approve", "Approve.", "approve it"])
def test_approve_by_voice_decides_a_clean_project_write(said):
    c, clock = open_approval()
    clock.t += 2
    d = c.decide(said)
    assert (d.action, d.verdict, d.approval_id, d.approval_hash) == ("decide", "approve", "apr_1", "sha256:x")


def test_approve_said_before_the_summary_was_heard_does_not_decide():
    c, clock = open_approval()
    clock.t += 0.5
    assert c.decide("approve").action == "say"


@pytest.mark.parametrize("kind,blocking", [("write_files", True), ("run_program", True), ("run_command", False),
                                           ("open", False), ("type", False), ("read_outside", False), ("publish", False)])
def test_approve_by_voice_is_refused_outside_the_project_or_when_blocked(kind, blocking):
    c, clock = open_approval(kind, blocking)
    clock.t += 5
    d = c.decide("approve")
    assert d.action == "say" and "keyboard" in d.say


@pytest.mark.parametrize("kind,blocking", [("write_files", False), ("run_command", False), ("type", True)])
def test_reject_by_voice_decides_any_approval(kind, blocking):
    c, clock = open_approval(kind, blocking)
    clock.t += 2
    d = c.decide("reject")
    assert (d.action, d.verdict) == ("decide", "reject")


def test_approval_words_with_an_old_approval_event_are_not_a_decision():
    c = awaiting_approval()  # no hash or kind: an older core
    assert c.decide("approve").say == KEYBOARD_ONLY


def test_yes_with_no_approval_open_is_an_ordinary_answer():
    c = finished(answer="Do you want a minimal layout or an interactive one?")
    assert c.expecting_reply is True
    d = c.decide("Yes, minimal. And dark mode.")
    assert (d.action, d.text) == ("submit", "Yes, minimal. And dark mode.")


@pytest.mark.parametrize("said", ["yes", "Approve.", "approve it", "no", "reject"])
def test_approval_words_with_nothing_to_answer_are_never_a_task(said):
    for c in (Controller(), working(), finished(answer="Done. It printed Hello, World.")):
        d = c.decide(said)
        assert (d.intent, d.action, d.say, d.text) == ("approval_word", "say", NOTHING_TO_APPROVE, None)
        assert c.queued is None


@pytest.mark.parametrize("said", ["yes", "No.", "yes please"])
def test_a_bare_yes_or_no_answers_the_agents_question(said):
    # Design A4: the agent asks by ending its turn; the next utterance is the answer. Anything
    # that answer leads to still has to be approved on the keyboard.
    c = finished(answer="Shall I add tests?")
    d = c.decide(said)
    assert (d.action, d.text) == ("submit", said)


def test_an_interruption_never_hides_a_pending_approval():
    c = awaiting_approval()
    assert c.decide("stop talking").reminder == WAITING
    assert c.decide("keep it brief").reminder == WAITING
    # "Repeat" says the decision again, at high priority, whatever was spoken since.
    d = c.decide("repeat that")
    assert (d.action, d.say, d.priority) == ("say", APPROVAL, "high")
    assert c.approval == APPROVAL and c.task.state == "awaiting_approval"


def test_repeat_without_an_approval_is_the_voices_last_line():
    assert working().decide("repeat").action == "repeat"


def test_the_approval_is_forgotten_once_resolved():
    c = awaiting_approval()
    c.observe(event("approval.resolved", approval_id="apr_1", decision="approve", by="user"))
    assert c.approval is None and c.decide("stop").reminder is None


# ----- follow-ups while a task is running -----


def test_a_request_during_a_task_is_kept_for_next_and_read_back_when_it_starts():
    c = working()
    d = c.decide("Also add a dark mode toggle.")
    assert (d.action, d.say, d.text) == ("queue", "I'll do that next.", "Also add a dark mode toggle.")
    assert c.decide("status").say.endswith(" Next up: Also add a dark mode toggle.")
    assert c.queued == "Also add a dark mode toggle."  # asking for status doesn't use it up
    c.observe(event("task.state", state="succeeded"))
    assert c.next_request() == FollowUp("Starting the follow-up.", start="Also add a dark mode toggle.")
    assert c.next_request() is None


def test_a_second_request_never_replaces_the_one_waiting():
    c = working()
    c.decide("add dark mode")
    d = c.decide("actually add a footer")
    assert d.action == "say" and d.text is None and c.queued == "add dark mode"
    assert d.say == "I already have one waiting: add dark mode. Say cancel to drop it."


def test_a_follow_up_during_an_approval_mentions_the_decision():
    d = awaiting_approval().decide("then add tests")
    assert d.action == "queue"
    assert d.say == "I'll do that next. A decision is still waiting: Enter to approve, Escape to reject."


def test_cancel_drops_the_follow_up_first_and_the_task_second():
    c = working()
    c.decide("add dark mode")
    first = c.decide("never mind")
    assert first.action == "say" and c.queued is None and c.task.active
    assert first.say == "Dropped: add dark mode. The current task is still going. Say cancel again to stop it."
    assert c.decide("cancel").action == "cancel"


@pytest.mark.parametrize("state", ["failed", "canceled"])
def test_a_follow_up_is_dropped_out_loud_when_the_task_before_it_did_not_finish(state):
    c = working()
    c.decide("then add tests")
    c.observe(event("task.state", state=state))
    follow = c.next_request()
    assert follow.start is None  # never started: it may have depended on the task that failed
    assert follow.say == "Dropped the follow-up, because the task before it didn't finish: then add tests."
    assert c.next_request() is None


def test_stop_with_a_task_running_says_how_to_cancel_instead_of_staying_silent():
    d = working().decide("stop")
    assert (d.action, d.say) == ("stop_speech", "Say cancel to stop the task.")
    assert Controller().decide("stop").say is None  # nothing running: nothing to add
    assert awaiting_approval().decide("stop").say is None  # the approval reminder is said instead


# ----- preferences -----


def test_style_phrases_change_how_the_next_task_is_answered():
    c = Controller()
    assert c.style == Style()
    assert c.decide("keep it brief").say == "Okay, I'll keep it brief."
    assert c.decide("visual mode").say.startswith("Visual mode.")
    assert c.style == Style(verbosity="brief", focus="visual")
    c.decide("deep dive")
    c.decide("code mode")
    assert c.style == Style(verbosity="detailed", focus="code")
    c.decide("normal mode")
    assert c.style == Style(focus="code")


def test_slower_is_honest_about_what_it_can_change():
    sapi = Controller()
    d = sapi.decide("slower")
    assert (d.action, d.delta) == ("rate", -1) and sapi.decide("faster").delta == 1
    nvda = Controller()
    nvda.observe(event("voice.status", task_id=None, voice_ready=True, screen_reader="nvda"))
    d = nvda.decide("slow down")
    assert d.action == "say" and "NVDA" in d.say
