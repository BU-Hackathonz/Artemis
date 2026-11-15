"""ClaudeAgent against a fake client that returns real SDK message objects. Offline.

One live test at the bottom runs the real Hello World; it is skipped without an API key.
"""

import asyncio
import copy
import json
import os
import sys
import time

import pytest

anthropic = pytest.importorskip("anthropic")
import httpx2  # noqa: E402  (the SDK's HTTP library; only used to build fake error responses)
from anthropic.types.beta import BetaMessage  # noqa: E402

from artemis.agent import make_agent  # noqa: E402
from artemis.agent import claude as claude_module  # noqa: E402
from artemis.agent.claude import ClaudeAgent  # noqa: E402
from artemis.agent.config import Config  # noqa: E402
from artemis.agent.context import task_message, vocabulary  # noqa: E402
from artemis.agent.prompts import DESKTOP, SPOKEN_REQUESTS, SYSTEM_PROMPT, strict_schema, system_prompt, tool_definitions  # noqa: E402
from artemis.agent.stub import StubAgent  # noqa: E402
from artemis.contracts import DESKTOP_TOOL_ARGS, TOOL_ARGS, Answered, Decision, Failed, ProjectRef, TaskContext  # noqa: E402
from artemis.policy.gateway import ToolGateway  # noqa: E402
from artemis.policy.testing import EventLog, FakeApprovals, FakeRunner  # noqa: E402

HELLO = 'print("Hello, World!")\n'
REQUEST = "create a Python program that prints Hello World"


def message(*blocks, stop_reason="end_turn", **extra):
    return BetaMessage.model_validate(
        {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": list(blocks), "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5}, **extra,
        }
    )


def thinking(signature="sig_1"):
    return {"type": "thinking", "thinking": "", "signature": signature}


def text(value):
    return {"type": "text", "text": value}


def tool(id, name, /, **input):
    return {"type": "tool_use", "id": id, "name": name, "input": input}


def dump(messages) -> str:
    """The conversation as the SDK would send it, for byte-for-byte comparison."""
    def plain(value):
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, list):
            return [plain(v) for v in value]
        if isinstance(value, dict):
            return {k: plain(v) for k, v in value.items()}
        return value

    return json.dumps(plain(messages), sort_keys=True)


class FakeClient:
    """Stands in for AsyncAnthropic: `client.beta.messages.create(...)`. Each script step is
    a message to return or an exception to raise."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []
        self.sent: list[str] = []  # the conversation as sent on each call
        self.beta = self
        self.messages = self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        self.sent.append(dump(kwargs["messages"]))
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def api_error(cls, status):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("simulated", response=httpx2.Response(status, request=request), body=None)


def hello_script():
    return [
        message(thinking(), tool("toolu_1", "create_project", name="hello world"), stop_reason="tool_use"),
        message(
            thinking("sig_2"),
            tool("toolu_2", "write_files", files=[{"path": "main.py", "content": HELLO}]),
            stop_reason="tool_use",
        ),
        message(tool("toolu_3", "run_program", entry="main.py", args=[]), stop_reason="tool_use"),
        message(text("I created **hello-world** with `main.py` and ran it. It printed Hello, World!")),
    ]


class World:
    def __init__(self, tmp_path, script, approvals=None, max_iterations=12):
        self.projects = tmp_path
        self.log = EventLog()
        self.approvals = FakeApprovals(approvals)
        self.runner = FakeRunner(tmp_path)
        self.gateway = ToolGateway(self.runner, self.approvals, self.log, projects_dir=tmp_path)
        self.client = FakeClient(script)
        self.agent = ClaudeAgent(Config(max_iterations=max_iterations), client=self.client)

    async def run(self, request=REQUEST, task_id="tsk_1"):
        task = TaskContext(task_id=task_id, request=request, project=self.gateway.project)
        return await self.agent.run(task, self.gateway, self.log)


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    monkeypatch.setattr(claude_module, "RETRY_DELAY_S", 0)


# ----- the loop -----


def test_hello_world_tool_loop(tmp_path):
    w = World(tmp_path, hello_script())
    outcome = asyncio.run(w.run())
    assert isinstance(outcome, Answered)
    assert (tmp_path / "hello-world" / "main.py").read_text() == HELLO
    assert w.runner.runs == [("main.py", [], 10)]
    assert len(w.client.calls) == 4
    # The answer is spoken without markdown; the log keeps the original.
    answer = w.log.events[-1]
    assert answer.announce.text == "I created hello-world with main.py and ran it. It printed Hello, World!"
    assert answer.data == {"text": outcome.text, "kind": "answer"}
    assert "**hello-world**" in outcome.text
    # The deterministic run result is spoken before the model's explanation.
    spoken = w.log.spoken()
    assert spoken.index("main.py ran successfully. It printed: Hello, World!") < len(spoken) - 1


def test_request_shape(tmp_path):
    w = World(tmp_path, [message(text("Hello."))])
    asyncio.run(w.run())
    call = w.client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["max_tokens"] == 16000
    assert call["output_config"] == {"effort": "medium"}
    assert call["cache_control"] == {"type": "ephemeral"}
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["system"] == SYSTEM_PROMPT
    # Thinking is always on for this model, forced tool choice is a 400, and we don't stream.
    assert not {"thinking", "tool_choice", "stream", "temperature"} & set(call)
    offered = [n for n in TOOL_ARGS if sys.platform.startswith("linux") or n not in DESKTOP_TOOL_ARGS]
    assert [t["name"] for t in call["tools"]] == offered


def test_model_and_effort_come_from_config(tmp_path):
    w = World(tmp_path, [message(text("Hello."))])
    w.agent = ClaudeAgent(Config(model="claude-sonnet-5-5", effort="low"), client=w.client)
    asyncio.run(w.run())
    assert w.client.calls[0]["model"] == "claude-sonnet-5-5"
    assert w.client.calls[0]["output_config"] == {"effort": "low"}


def test_response_content_is_appended_exactly_as_returned(tmp_path):
    script = hello_script()
    w = World(tmp_path, list(script))
    asyncio.run(w.run())
    assistant = [m for m in w.agent.messages if m["role"] == "assistant"]
    assert [m["content"] for m in assistant] == [s.content for s in script]
    assert assistant[0]["content"][0].type == "thinking"  # thinking blocks go back unchanged
    roles = [m["role"] for m in w.agent.messages]
    assert roles == ["user", "assistant", "user", "assistant", "user", "assistant", "user", "assistant"]


def test_tool_results_are_json_with_untrusted_output(tmp_path):
    w = World(tmp_path, hello_script())
    asyncio.run(w.run())
    results = [m["content"] for m in w.agent.messages if m["role"] == "user" and isinstance(m["content"], list)]
    run_result = results[2][0]
    assert run_result["type"] == "tool_result" and run_result["tool_use_id"] == "toolu_3"
    assert run_result["is_error"] is False
    body = json.loads(run_result["content"])
    assert body["ok"] is True and body["data"]["exit_code"] == 0
    assert body["data"]["stdout"].startswith('<untrusted source="program:main.py stdout">')


def test_parallel_tool_calls_run_in_order_and_return_in_one_message(tmp_path):
    script = [
        message(
            tool("toolu_a", "create_project", name="multi"),
            tool("toolu_b", "write_files", files=[{"path": "a.py", "content": "x = 1\n"}]),
            tool("toolu_c", "read_file", path="a.py", start_line=None, end_line=None),
            stop_reason="tool_use",
        ),
        message(text("Done.")),
    ]
    w = World(tmp_path, script)
    asyncio.run(w.run())
    results = w.agent.messages[2]["content"]
    assert [r["tool_use_id"] for r in results] == ["toolu_a", "toolu_b", "toolu_c"]
    assert all(r["is_error"] is False for r in results)  # the read saw the file the write made
    assert len(w.approvals.requests) == 1
    assert len(w.agent.messages) == 4


# ----- stop reasons -----


def test_refusal(tmp_path):
    refused = message(stop_reason="refusal", stop_details={"type": "refusal", "category": "cyber", "explanation": "x"})
    w = World(tmp_path, [refused])
    outcome = asyncio.run(w.run())
    assert outcome == Failed(code="refused", message="I can't help with that request.")
    assert w.log.spoken() == []  # the core speaks the Failed outcome, once
    assert w.agent.messages == []  # the refused request is not replayed on the next task
    assert w.approvals.requests == []


def test_refusal_after_tool_calls_keeps_the_answered_history(tmp_path):
    w = World(tmp_path, hello_script()[:1] + [message(stop_reason="refusal")])
    outcome = asyncio.run(w.run())
    assert outcome.code == "refused"
    assert [m["role"] for m in w.agent.messages] == ["user", "assistant", "user"]


def test_max_tokens_repeatedly_fails_out_loud(tmp_path):
    cut = lambda: message(text("Here is a very long"), stop_reason="max_tokens")
    w = World(tmp_path, [cut(), cut(), cut()])
    outcome = asyncio.run(w.run())
    assert outcome.code == "too_long" and "too big" in outcome.message
    assert len(w.client.calls) == 3


def test_max_tokens_cut_off_tool_call_runs_nothing_and_asks_for_smaller_pieces(tmp_path):
    cut_off = message(tool("toolu_1", "create_project", name="x"), stop_reason="max_tokens")
    w = World(tmp_path, [cut_off, message(text("Split it."))])
    outcome = asyncio.run(w.run())
    assert outcome == Answered(text="Split it.")
    assert w.gateway.project is None  # the cut-off call was not executed
    result = w.agent.messages[2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "toolu_1" and result["is_error"]
    assert "cut off" in result["content"] and "did NOT run" in result["content"]
    assert any("too big" in t for t in w.log.spoken())


def test_max_tokens_text_only_reply_continues_in_smaller_pieces(tmp_path):
    w = World(tmp_path, [message(text("long"), stop_reason="max_tokens"), message(text("ok"))])
    assert asyncio.run(w.run()) == Answered(text="ok")
    assert "cut off" in w.agent.messages[2]["content"]


def test_a_slow_answer_is_a_timeout_not_no_internet(tmp_path):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    w = World(tmp_path, [anthropic.APITimeoutError(request=request)])
    outcome = asyncio.run(w.run())
    assert outcome.code == "timeout" and "internet" not in outcome.message


def test_unknown_tool_is_an_error_result_and_the_loop_continues(tmp_path):
    script = [
        message(tool("toolu_1", "delete_everything", path="."), stop_reason="tool_use"),
        message(text("I can't delete files.")),
    ]
    w = World(tmp_path, script)
    outcome = asyncio.run(w.run())
    result = w.agent.messages[2]["content"][0]
    assert result["is_error"] is True
    assert json.loads(result["content"])["error"] == "not_found"
    assert outcome == Answered(text="I can't delete files.")


@pytest.mark.parametrize(
    "bad_input",
    [{}, {"files": "main.py"}, {"files": [{"path": "main.py"}]}, {"files": [{"path": "a.py", "content": "", "x": 1}]}],
)
def test_malformed_args_are_an_error_result(tmp_path, bad_input):
    script = [
        message(tool("toolu_1", "create_project", name="x"), stop_reason="tool_use"),
        message({"type": "tool_use", "id": "toolu_2", "name": "write_files", "input": bad_input}, stop_reason="tool_use"),
        message(text("Sorry, let me try again.")),
    ]
    w = World(tmp_path, script)
    asyncio.run(w.run())
    result = w.agent.messages[4]["content"][0]
    assert result["is_error"] is True
    assert json.loads(result["content"])["error"] == "invalid_args"
    assert w.approvals.requests == [] and list(tmp_path.iterdir()) == []


def test_rejection_goes_back_to_the_model(tmp_path):
    script = hello_script()[:2] + [message(text("Okay, I didn't create anything."))]
    w = World(tmp_path, script, approvals=[Decision.reject])
    outcome = asyncio.run(w.run())
    result = w.agent.messages[4]["content"][0]
    assert json.loads(result["content"])["error"] == "rejected_by_user"
    assert outcome == Answered(text="Okay, I didn't create anything.")
    assert list(tmp_path.iterdir()) == []


def test_iteration_limit(tmp_path):
    endless = [message(tool(f"toolu_{i}", "list_files", dir=None), stop_reason="tool_use") for i in range(5)]
    w = World(tmp_path, endless, max_iterations=3)
    outcome = asyncio.run(w.run())
    assert outcome.code == "too_many_steps" and len(w.client.calls) == 3


def test_empty_answer_still_says_something(tmp_path):
    w = World(tmp_path, [message(thinking())])
    assert asyncio.run(w.run()) == Answered(text="Done.")


# ----- conversation -----


def test_conversation_is_append_only_across_two_tasks(tmp_path):
    second = [
        message(
            thinking("sig_3"),
            tool("toolu_4", "write_files", files=[{"path": "main.py", "content": 'print("Goodbye")\n'}]),
            stop_reason="tool_use",
        ),
        message(text("main.py now prints Goodbye.")),
    ]
    w = World(tmp_path, hello_script() + second)

    async def go():
        await w.run()
        after_first = copy.deepcopy(dump(w.agent.messages))
        count = len(w.agent.messages)
        outcome = await w.run("now make it say goodbye", task_id="tsk_2")
        return after_first, count, outcome

    after_first, count, outcome = asyncio.run(go())
    assert outcome == Answered(text="main.py now prints Goodbye.")
    # Everything from the first task is still there, byte for byte, and only new messages
    # were added after it.
    assert dump(w.agent.messages[:count]) == after_first
    assert len(w.agent.messages) == count + 4
    # Every request sent the previous request's conversation as an unchanged prefix.
    for earlier, later in zip(w.client.sent, w.client.sent[1:]):
        assert later.startswith(earlier[:-1])
    # The second task knows the project and its files.
    second_request = w.agent.messages[count]["content"]
    assert '<task id="tsk_2" project="hello-world">' in second_request
    assert "main.py" in second_request and second_request.endswith("now make it say goodbye")
    assert (tmp_path / "hello-world" / "main.py").read_text() == 'print("Goodbye")\n'
    assert w.approvals.requests[-1].files[0].action == "modified"


def test_system_prompt_and_tools_are_identical_on_every_call(tmp_path):
    w = World(tmp_path, hello_script())
    asyncio.run(w.run())
    first = w.client.calls[0]
    for call in w.client.calls[1:]:
        assert call["system"] == first["system"] and call["tools"] == first["tools"]
    assert tool_definitions() == tool_definitions()


def test_reset_starts_a_new_conversation(tmp_path):
    w = World(tmp_path, [message(text("Hi.")), message(text("Hi again."))])
    asyncio.run(w.run())
    w.agent.reset()
    asyncio.run(w.run())
    assert len(w.agent.messages) == 2


def test_cancel_during_a_tool_call_leaves_a_valid_conversation(tmp_path):
    async def go():
        waiting = asyncio.Event()

        async def never(approval):
            waiting.set()
            await asyncio.Event().wait()

        script = [
            message(
                tool("toolu_1", "create_project", name="x"),
                tool("toolu_2", "write_files", files=[{"path": "a.py", "content": "1\n"}]),
                tool("toolu_3", "list_files", dir=None),
                stop_reason="tool_use",
            )
        ]
        w = World(tmp_path, script, approvals=[never])
        running = asyncio.create_task(w.run())
        await waiting.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        return w

    w = asyncio.run(go())
    assert list(tmp_path.iterdir()) == []
    results = w.agent.messages[-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["toolu_1", "toolu_2", "toolu_3"]
    assert [r["is_error"] for r in results] == [False, True, True]


# ----- API errors -----


def test_authentication_error_never_echoes_the_key(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-FAKEfakeFAKE")
    w = World(tmp_path, [api_error(anthropic.AuthenticationError, 401)])
    outcome = asyncio.run(w.run())
    assert outcome == Failed(code="auth", message="The Claude API key is missing or invalid.")
    assert "FAKEfake" not in json.dumps([e.model_dump(mode="json") for e in w.log.events])
    assert w.agent.messages == []


def test_missing_credentials_are_a_spoken_failure(tmp_path):
    w = World(tmp_path, [anthropic.AnthropicError("Could not resolve authentication method")])
    assert asyncio.run(w.run()).code == "auth"


@pytest.mark.parametrize(
    "error", [(anthropic.RateLimitError, 429), (anthropic.OverloadedError, 529), (anthropic.InternalServerError, 500)]
)
def test_busy_is_spoken_and_retried_once(tmp_path, error):
    w = World(tmp_path, [api_error(*error), message(text("Hello."))])
    outcome = asyncio.run(w.run())
    assert outcome == Answered(text="Hello.")
    assert w.log.spoken() == ["Claude is busy, retrying.", "Hello."]
    assert len(w.client.calls) == 2


def test_busy_twice_fails(tmp_path):
    w = World(tmp_path, [api_error(anthropic.RateLimitError, 429), api_error(anthropic.RateLimitError, 429)])
    outcome = asyncio.run(w.run())
    assert outcome.code == "busy" and len(w.client.calls) == 2
    assert w.log.spoken() == ["Claude is busy, retrying."]


def test_connection_error(tmp_path):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    w = World(tmp_path, [anthropic.APIConnectionError(request=request)])
    outcome = asyncio.run(w.run())
    assert outcome == Failed(code="offline", message="No internet connection to Claude.")


def test_other_api_errors_fail_without_a_retry(tmp_path):
    w = World(tmp_path, [api_error(anthropic.BadRequestError, 400)])
    outcome = asyncio.run(w.run())
    assert outcome == Failed(code="api_error", message="Claude returned an error.")
    assert len(w.client.calls) == 1


@pytest.mark.parametrize("error", [
    lambda: anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")),
    lambda: api_error(anthropic.AuthenticationError, 401),
    lambda: api_error(anthropic.PermissionDeniedError, 403),
    lambda: anthropic.AnthropicError("Could not resolve authentication method"),
], ids=["offline", "bad_key", "forbidden", "no_key"])
@pytest.mark.parametrize("mid_task", [False, True])
def test_an_api_failure_is_left_to_the_core_to_speak(tmp_path, error, mid_task):
    """The core speaks every Failed once, as "The task failed. <message> You can try again."
    (high). The agent saying the message too put it in the log twice, the first time at normal
    priority, cut off at once by the high one (failure-paths review)."""
    w = World(tmp_path, (hello_script()[:1] if mid_task else []) + [error()])
    outcome = asyncio.run(w.run())
    assert isinstance(outcome, Failed)
    assert w.log.spoken() == (["Starting project hello-world."] if mid_task else [])


# ----- prompts, schemas, context -----


def test_tool_schemas_are_strict(tmp_path):
    unsupported = {"minLength", "maxLength", "minimum", "maximum", "minItems", "default", "title"}

    def check(node):
        if isinstance(node, dict):
            assert not unsupported & (set(node) - set(node.get("properties", {})))
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert node["required"] == list(node["properties"])
            for key, value in node.items():
                check(list(value.values()) if key == "properties" else value)
        elif isinstance(node, list):
            for item in node:
                check(item)

    tools = tool_definitions()
    for definition in tools:
        assert definition["strict"] is True and definition["description"]
        check(definition["input_schema"])
    names = [t["name"] for t in tools]
    desktop = ["open_app", "open_path", "read_path", "run_command", "type_text", "press_keys"]
    assert names == [
        "create_project", "open_project", "list_files", "read_file", "write_files", "run_program", "edit_file",
        "inspect_page", "scan_project",
        *(desktop if sys.platform.startswith("linux") else []), "plan",
    ]
    assert not {"shell", "delete_file", "ask_user", "scan", "install"} & set(names)


def test_strict_schema_keeps_a_property_named_like_a_keyword():
    schema = {"type": "object", "properties": {"title": {"type": "string", "minLength": 1}}}
    assert strict_schema(schema) == {
        "type": "object", "properties": {"title": {"type": "string"}},
        "required": ["title"], "additionalProperties": False,
    }


def test_system_prompt_is_frozen_and_covers_the_rules():
    for phrase in ["blind", "read aloud", "no markdown", "rejected_by_user", "scan_blocked", "<untrusted>", "Plan first", "waits for", "never touched",
                   "what it does as a whole", "never speak symbols", "in the log"]:
        assert phrase in SYSTEM_PROMPT
    assert "202" not in SYSTEM_PROMPT  # no dates or timestamps: the prompt must cache


# sha256 of main's SYSTEM_PROMPT (updated when the prompt itself changes, last when code went into files
# and VS Code was opened with open_path, not typed, on top of the plan and the desktop tools) as it
# is on Windows: with ARTEMIS_CONVERSATION off the agent must see exactly that prompt.
MAIN_SYSTEM_PROMPT_SHA256 = "ab7e5c13f676d7c2ce4937944d073af4344528b2aed9090bf61f258de58b9bd3"


def test_spoken_answers_are_short_and_true_to_the_tool_results():
    flat = " ".join(SYSTEM_PROMPT.split())
    assert "at most two short sentences unless they ask for more detail" in flat
    assert "if the last run failed, never say the program works" in flat
    assert "never by their path or how their name is spelled" in flat


def test_a_rejected_change_is_not_proposed_again_unless_asked():
    flat = " ".join(SYSTEM_PROMPT.split())
    # The original rule stays; the new one covers runs and blocked scans, lasts the whole
    # conversation, names what counts as new, and is the one place to ask before a tool call.
    assert "do not try the same thing again or look for another way to do it" in flat
    assert "said no to a change or a run" in flat
    assert "Both hold for the rest of the conversation" in flat
    assert "unless the developer asks for it in words" in flat
    assert "this is the one case where you ask before calling a tool" in flat


def test_code_goes_into_files_and_vs_code_is_opened_not_typed():
    flat = " ".join(SYSTEM_PROMPT.split())
    assert "Code goes into files, never into a window." in flat
    assert "Create and change code with write_files and edit_file" in flat
    assert "Use type_text and press_keys only for text the developer asked you to type into another app" in flat
    assert "with open_path and with_app set to VS Code" in flat
    assert "Opening an app or a file in the usual folders happens right away" in flat


def test_prompt_tells_the_model_to_build_complete_multi_file_projects():
    flat = " ".join(SYSTEM_PROMPT.split())
    assert "no TODO stubs" in flat and "index.html, style.css and script.js" in flat
    assert "under about three hundred lines" in flat and "inspect_page" in flat
    assert "what you built and how to use it" in flat


def test_flag_off_system_prompt_is_mains():
    import hashlib

    assert system_prompt() == system_prompt(False) == SYSTEM_PROMPT
    # Off Windows only the desktop's name differs from main's prompt.
    as_on_windows = SYSTEM_PROMPT.replace(f"developer on {DESKTOP}.", "developer on Windows.", 1)
    assert hashlib.sha256(as_on_windows.encode()).hexdigest() == MAIN_SYSTEM_PROMPT_SHA256
    assert "How to read a spoken request" not in SYSTEM_PROMPT


def test_system_prompt_names_the_desktop_it_runs_on():
    import sys

    expected = {"win32": "Windows", "darwin": "macOS"}.get(sys.platform, "Linux")
    assert DESKTOP == expected
    assert f"blind software developer on {expected}." in SYSTEM_PROMPT
    assert ("Windows" in SYSTEM_PROMPT) == (sys.platform == "win32")


def test_flag_on_system_prompt_adds_the_spoken_request_paragraph():
    on = system_prompt(True)
    # Spoken input: self-corrections, and what must never be lost from a transcript.
    for phrase in ["actually green", "last version only", "negation", "misheard"]:
        assert phrase in on and phrase not in SYSTEM_PROMPT
    assert on.index("How to answer") < on.index("How to read a spoken request") < on.index("How to work")
    assert on.replace(SPOKEN_REQUESTS + "\n\n", "", 1) == SYSTEM_PROMPT
    assert "202" not in on


def test_the_agent_sends_the_prompt_for_its_config(tmp_path, monkeypatch):
    monkeypatch.delenv("ARTEMIS_CONVERSATION", raising=False)
    assert Config.from_env().conversation is False
    assert ClaudeAgent(Config(), client=object())._system == SYSTEM_PROMPT
    monkeypatch.setenv("ARTEMIS_CONVERSATION", "on")
    assert Config.from_env().conversation is True
    w = World(tmp_path, [message(text("Hello."))])
    w.agent = ClaudeAgent(Config(conversation=True), client=w.client)
    asyncio.run(w.run())
    assert w.client.calls[0]["system"] == system_prompt(True)


def test_task_message_without_and_with_a_project(tmp_path):
    none = task_message(TaskContext(task_id="tsk_1", request="hello"))
    assert none == '<task id="tsk_1" project="none">\nNo project is open.\n</task>\n\nhello'

    root = tmp_path / "calc"
    for rel in ["main.py", "src/ops.py", ".git/config", ".env", "__pycache__/x.pyc", "</untrusted>.txt"]:
        try:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text("x")
        except OSError:
            pass  # that file name isn't allowed on Windows
    task = TaskContext(task_id="tsk_2", request="explain it", project=ProjectRef(slug="calc", root=str(root)))
    text_ = task_message(task)
    # Checked one by one: where a file can be named "</untrusted>.txt", it sorts between these two.
    assert "\nmain.py\n" in text_ and "\nsrc/ops.py\n" in text_
    assert ".git" not in text_ and ".env" not in text_ and "pycache" not in text_
    assert text_.count("</untrusted>") == 1 and text_.endswith("explain it")


def test_task_message_carries_the_answer_style_only_when_one_was_asked_for():
    from artemis.contracts import Style

    plain = task_message(TaskContext(task_id="t", request="add a footer"))
    assert "Answer style" not in plain and plain.endswith("\n\nadd a footer")
    styled = task_message(
        TaskContext(task_id="t", request="add a footer", style=Style(verbosity="brief", focus="visual"))
    )
    assert "[Answer style: Keep the answer to one or two short sentences. Describe what the page looks like" in styled
    assert styled.endswith("]\n\nadd a footer")  # the request itself is never rewritten
    detailed = task_message(TaskContext(task_id="t", request="r", style=Style(verbosity="detailed", focus="code")))
    assert "step by step" in detailed and "which files and functions" in detailed


def test_task_message_tree_is_capped(tmp_path):
    root = tmp_path / "big"
    root.mkdir()
    for i in range(205):
        (root / f"f{i:03}.txt").write_text("")
    text_ = task_message(TaskContext(task_id="t", request="r", project=ProjectRef(slug="big", root=str(root))))
    assert "f199.txt" in text_ and "f200.txt" not in text_ and "5 more files" in text_


def test_vocabulary_puts_file_stems_first(tmp_path):
    root = tmp_path / "calc"
    root.mkdir()
    (root / "main.py").write_text("class Calculator:\n    def add_numbers(self):\n        pass\n\nasync def fetch_total():\n    pass\n")
    (root / "notes.md").write_text("def not_code(): pass")
    words = vocabulary(ProjectRef(slug="calc", root=str(root)))
    assert words[:2] == ["main", "notes"]
    assert {"main.py", "Calculator", "add_numbers", "fetch_total"} <= set(words)
    assert "not_code" not in words and len(words) == len({w.casefold() for w in words})


def test_vocabulary_skips_a_file_that_cannot_be_read(tmp_path, monkeypatch):
    from artemis.agent import context

    root = tmp_path / "calc"
    root.mkdir()
    (root / "main.py").write_text("def add_numbers():\n    pass\n")
    # Listed, then deleted before it was read.
    monkeypatch.setattr(context, "_files", lambda project: [root / "gone.py", root / "main.py"])
    words = vocabulary(ProjectRef(slug="calc", root=str(root)))
    assert words[:2] == ["gone", "main"] and "add_numbers" in words


def test_make_agent_switches_on_one_setting():
    assert isinstance(make_agent(Config(agent="stub")), StubAgent)
    assert isinstance(make_agent(Config(agent="claude")), ClaudeAgent)


# ----- live -----


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")
def test_live_hello_world(tmp_path):
    """The real model, the real gateway, a fake runner. Costs a few cents."""
    log, approvals, runner = EventLog(), FakeApprovals(), FakeRunner(tmp_path)
    gateway = ToolGateway(runner, approvals, log, projects_dir=tmp_path)
    agent = ClaudeAgent(Config.from_env())
    stamps: list[float] = []
    start = time.monotonic()

    async def emit(event):
        stamps.append(time.monotonic() - start)
        await log(event)

    task = TaskContext(task_id="tsk_live", request=REQUEST)
    outcome = asyncio.run(agent.run(task, gateway, emit))
    total = time.monotonic() - start
    print(f"\nlive: first spoken progress {stamps[0]:.1f}s, total {total:.1f}s, model calls {len(agent.messages) // 2}")
    assert isinstance(outcome, Answered), outcome
    assert [a.kind for a in approvals.requests] == ["write_files", "run_program"]
    assert len(list(tmp_path.rglob("*.py"))) == 1
    assert runner.runs and "run.result" in log.types()


# ----- the real SDK client, with the network replaced -----


@pytest.mark.parametrize("workspace", ["wrkspc_01TESTnotreal", None])
def test_workspace_header_is_sent_only_when_a_workspace_is_set(tmp_path, monkeypatch, workspace):
    """The agent builds its own client here, so this covers the header end to end."""
    headers = []
    reply = {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": "Hello."}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }

    def handler(request):
        headers.append(request.headers)
        return httpx2.Response(200, json=reply)

    real = anthropic.AsyncAnthropic

    def offline(**kwargs):
        return real(
            api_key="test-key-not-real",
            max_retries=0,
            http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
            **kwargs,
        )

    monkeypatch.setattr(claude_module.anthropic, "AsyncAnthropic", offline)
    if workspace:
        monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", f"  {workspace}  ")
    else:
        monkeypatch.delenv("ANTHROPIC_WORKSPACE_ID", raising=False)
    config = Config.from_env()
    assert config.workspace_id == workspace

    async def go():
        w = World(tmp_path, [])
        w.agent = ClaudeAgent(config)
        return await w.run()

    assert asyncio.run(go()) == Answered(text="Hello.")
    assert headers[0].get("anthropic-workspace-id") == workspace


def test_an_empty_workspace_setting_means_no_workspace(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "")
    assert Config.from_env().workspace_id is None


def test_the_workspace_id_is_redacted_like_the_key(monkeypatch):
    from artemis.policy.redact import redact

    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_01TESTnotreal")
    assert "wrkspc_01TESTnotreal" not in redact("workspace wrkspc_01TESTnotreal failed")


def test_client_built_at_startup_keeps_key_and_workspace_while_secrets_are_hidden(monkeypatch):
    """A session's first call can run while Playwright's driver start hides the secrets. The client
    built at startup already has the key and workspace, and the request must not read os.environ again."""
    from artemis.agent.claude import make_client
    from artemis.childenv import secrets_hidden_from_environ

    key, workspace = "sk-ant-api03-FAKEfakeFAKE", "wrkspc_01TESTnotreal"
    monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", workspace)
    seen = []
    reply = {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": "Hello."}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }

    def handler(request):
        seen.append(request.headers)
        return httpx2.Response(200, json=reply)

    real = anthropic.AsyncAnthropic

    def offline(**kwargs):  # no api_key here: the SDK reads it from the environment, as in production
        return real(
            max_retries=0,
            http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
            **kwargs,
        )

    monkeypatch.setattr(claude_module.anthropic, "AsyncAnthropic", offline)
    config = Config.from_env()
    client = make_client(config)  # startup, secrets visible
    agent = ClaudeAgent(config, client=client)
    with secrets_hidden_from_environ():
        assert "ANTHROPIC_API_KEY" not in os.environ
        assert asyncio.run(agent._request()).content[0].text == "Hello."

    assert seen[0].get("authorization") == f"Bearer {key}"
    assert seen[0].get("anthropic-workspace-id") == workspace
    assert key not in repr(config)


def test_real_sdk_client_builds_the_request_and_replays_thinking_blocks(tmp_path):
    """Everything except the network: the real AsyncAnthropic serializes our request, and the
    second request carries the first response's blocks back unchanged."""
    bodies, headers = [], []
    replies = [
        {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [thinking("sig_real"), tool("toolu_1", "create_project", name="hello world")],
            "stop_reason": "tool_use", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
        {
            "id": "msg_2", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [text("Started hello-world.")],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    ]

    def handler(request):
        bodies.append(json.loads(request.content))
        headers.append(request.headers)
        return httpx2.Response(200, json=replies[len(bodies) - 1])

    async def go():
        client = anthropic.AsyncAnthropic(
            api_key="test-key-not-real",
            max_retries=0,
            http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
        )
        w = World(tmp_path, [])
        w.agent = ClaudeAgent(Config(), client=client)
        return await w.run()

    assert asyncio.run(go()) == Answered(text="Started hello-world.")
    first, second = bodies
    assert first["model"] == "claude-opus-5-5" and first["max_tokens"] == 16000
    assert first["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in headers[0]["anthropic-beta"]
    assert first["output_config"] == {"effort": "medium"}
    assert first["cache_control"] == {"type": "ephemeral"}
    assert "thinking" not in first and "tool_choice" not in first and "stream" not in first
    assert all(t["strict"] is True for t in first["tools"])
    assistant = second["messages"][1]["content"]
    assert assistant[0] == {"type": "thinking", "thinking": "", "signature": "sig_real"}
    assert assistant[1]["type"] == "tool_use" and assistant[1]["id"] == "toolu_1"
    result = second["messages"][2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "toolu_1"
    assert second["messages"][0] == first["messages"][0]


# ----- cancel while the model request is in flight -----


class BlockingClient(FakeClient):
    """Answers from the script, then blocks forever on the next request."""

    def __init__(self, script):
        super().__init__(script)
        self.blocked = asyncio.Event()

    async def create(self, **kwargs):
        if not self.script:
            self.blocked.set()
            await asyncio.Event().wait()
        return await super().create(**kwargs)


def cancel_in_flight(tmp_path, script):
    async def go():
        w = World(tmp_path, [])
        w.client = BlockingClient(script)
        w.agent = ClaudeAgent(Config(), client=w.client)
        running = asyncio.create_task(w.run("delete the logs"))
        await w.client.blocked.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        return w

    return asyncio.run(go())


def test_cancel_before_any_answer_removes_the_request(tmp_path):
    w = cancel_in_flight(tmp_path, [])
    assert w.agent.messages == []  # the next task won't replay "delete the logs"

    w.client.script = [message(text("Added a test."))]
    assert asyncio.run(w.run("add a test", task_id="tsk_2")) == Answered(text="Added a test.")
    sent = w.client.calls[-1]["messages"]
    assert len(sent) == 2 and "delete the logs" not in sent[0]["content"]


def test_cancel_after_tool_calls_keeps_history_and_says_it_was_canceled(tmp_path):
    w = cancel_in_flight(tmp_path, hello_script()[:1])
    roles = [m["role"] for m in w.agent.messages]
    assert roles == ["user", "assistant", "user", "user"]  # nothing answered was removed
    assert w.agent.messages[-1]["content"] == "The developer canceled this task. Do not continue it."
    assert w.agent.messages[2]["content"][0]["type"] == "tool_result"


# ----- links -----


def test_task_message_and_vocabulary_do_not_follow_a_link_out_of_the_project(tmp_path):
    import sys

    root, outside = tmp_path / "calc", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "main.py").write_text("def add():\n    pass\n")
    (outside / "secret.py").write_text("def launch_codes():\n    pass\n")
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(outside), str(root / "link"))
    else:
        os.symlink(outside, root / "link", target_is_directory=True)
    project = ProjectRef(slug="calc", root=str(root))
    text_ = task_message(TaskContext(task_id="t", request="r", project=project))
    assert "main.py" in text_ and "secret" not in text_
    assert "launch_codes" not in vocabulary(project) and "secret" not in vocabulary(project)


@pytest.mark.parametrize("error", [
    lambda: anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")),
    lambda: api_error(anthropic.AuthenticationError, 401),
    lambda: api_error(anthropic.PermissionDeniedError, 403),
    lambda: anthropic.AnthropicError("Could not resolve authentication method"),
], ids=["offline", "bad_key", "forbidden", "no_key"])
def test_a_failure_before_any_answer_does_not_leave_the_request_in_the_conversation(tmp_path, error):
    """The core now speaks the failure; the agent must still forget the request nobody answered,
    or the next task would replay it together with its own."""
    w = World(tmp_path, [error()])
    outcome = asyncio.run(w.run())
    assert isinstance(outcome, Failed)
    assert w.agent.messages == []
