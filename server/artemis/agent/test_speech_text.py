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
        ("```python\nprint(1)\n```\nThat prints 1.", "print(1) That prints 1."),
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
