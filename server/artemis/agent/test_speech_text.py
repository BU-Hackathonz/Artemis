import pytest

from artemis.agent.speech_text import for_speech


@pytest.mark.parametrize(
    "markdown, spoken",
    [
        ("I created `main.py`.", "I created main.py."),
        ("It **failed** on *line 3*.", "It failed on line 3."),
        ("__Done__ and _fine_.", "Done and fine."),
        ("## Result\nIt worked.", "Result It worked."),
        ("- one\n- two\n* three", "one two three"),
        ("1. first\n2) second", "first second"),
        ("See [the docs](https://example.com/a_b) now.", "See the docs now."),
        ("![diagram](x.png) shown", "diagram shown"),
        ("> quoted\ntext", "quoted text"),
        ("before\n---\nafter", "before after"),
        ("```python\nprint(1)\n```\nThat prints 1.", "The code is in the log. That prints 1."),
        ("***very* important**", "very important"),
        ("~~old~~ new", "old new"),
    ],
)
def test_markdown_is_stripped(markdown, spoken):
    assert for_speech(markdown) == spoken


@pytest.mark.parametrize(
    "text",
    [
        "main.py ran successfully. It printed: Hello, World!",
        "The variable my_file_name is on line 2.",
        "2 * 3 * 4 is 24.",
        "__init__.py is empty.",
    ],
)
def test_plain_text_and_code_names_survive(text):
    assert for_speech(text) == text


# Jun's sample from main (2026-11-15): the code inside the backticks used to be read out.
JUN_SAMPLE = (
    'Line 4 does `path = os.path.join("..", "//data", f"{name}.json")`:\n'
    "```python\nfor k, v in cfg.items():\n    out[k] = v or {}\n```\nThen it returns `out`."
)


def test_code_is_never_read_out_symbol_by_symbol():
    spoken = for_speech(JUN_SAMPLE)
    assert spoken == "Line 4 does a snippet: The code is in the log. Then it returns out."
    for noise in ("//", "../", "..", "{", "}", "=", "(", '"', "cfg.items"):
        assert noise not in spoken


@pytest.mark.parametrize(
    "markdown, spoken",
    [
        # Several blocks in a row get one pointer, not one each.
        ("```\na = 1\n```\n```\nb = 2\n```\nDone.", "The code is in the log. Done."),
        # A block that never closes still never reaches speech.
        ("Here it is:\n```python\nimport os\nos.remove(x)", "Here it is: The code is in the log."),
        ("Call `main()` to start.", "Call main to start."),
        ("It opens `src/ops.py` and `.env.example`.", "It opens src/ops.py and .env.example."),
        ("Set `x = 5` first.", "Set a snippet first."),
        ("It reads `../secrets.txt`.", "It reads a snippet."),
        ("The URL is `http://127.0.0.1:8000`.", "The URL is a snippet."),
    ],
)
def test_code_in_speech(markdown, spoken):
    assert for_speech(markdown) == spoken


def test_the_log_keeps_the_code(tmp_path):
    import asyncio

    from artemis.agent.base import say

    seen = []

    async def emit(draft):
        seen.append(draft)

    asyncio.run(say(emit, "tsk_1", JUN_SAMPLE))
    assert seen[0].data["text"] == JUN_SAMPLE  # the log, read with the screen reader
    assert "//" not in seen[0].announce.text  # what is spoken
