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


# ----- spoken_answer: what a model answer sounds like (measured on David's laptop, 2026-11-15) -----

from artemis.agent.speech_text import MAX_SENTENCES, spoken_answer  # noqa: E402
from artemis.voice.speechlint import run_ons  # noqa: E402

# A real answer from the Celsius run: five sentences, 70 words, after the run had failed.
CELSIUS_ANSWER = (
    "I've made convert.py and it works. In my test run, 25 Celsius came out as 77 Fahrenheit and minus 40 "
    "stayed minus 40. The program also skipped a word I put in on purpose, which is why it ended with exit "
    "code 1. You give it one or more Celsius temperatures after the file name, and it prints one line for "
    "each. With no temperatures, it shows a few examples."
)


def test_a_long_answer_is_cut_with_a_pointer_to_the_log():
    spoken = spoken_answer(CELSIUS_ANSWER)
    assert spoken.endswith("The rest is in the log.")
    assert spoken.count(". ") + 1 == MAX_SENTENCES + 1  # four of its sentences, then the pointer
    assert "With no temperatures" not in spoken


def test_a_closing_question_is_never_cut():
    spoken = spoken_answer("One. Two. Three. Four. Five. Should I fix it?")
    assert spoken == "One. Two. Three. The rest is in the log. Should I fix it?"


def test_a_short_answer_is_left_alone():
    for text in ["main.py ran successfully. It printed: Hello, World!", "2 * 3 * 4 is 24.", "I made C++ notes."]:
        assert spoken_answer(text) == text


@pytest.mark.parametrize(
    "answer, spoken",
    [
        ("The file is at ~/projects/hello-world/src/main.py now.", "The file is at main.py now."),
        ("It opens src/ops.py and ./data/cfg.json.", "It opens ops.py and cfg.json."),
        (r"Open C:\Users\me\a.py first.", "Open a.py first."),
        ("25/40 passed and/or failed.", "25/40 passed and/or failed."),  # not paths
        ("See https://example.com/a/b for more.", "See https://example.com/a/b for more."),
        ("It checks if x == 5 && y -> z.", "It checks if x 5 y z."),
        ("Call main() to run it.", "Call main to run it."),
    ],
)
def test_paths_and_symbols_are_not_read_aloud(answer, spoken):
    assert spoken_answer(answer) == spoken


def test_speechlint_runs_on_every_answer():
    # for_speech turns a markdown heading into a run-on; the answer check puts the full stop back.
    assert for_speech("## Result\nIt worked.") == "Result It worked."
    assert spoken_answer("## Result\nIt worked.") == "Result. It worked."
    assert run_ons(spoken_answer("## No input device\nCheck the microphone.")) == []


@pytest.mark.parametrize(
    "answer",
    [
        "Should I answer Yes or No?",
        "Press Escape to stop.",
        "Hold Shift Enter to add a line.",
        "Hold Ctrl Enter to send.",
        "The status is Done.",
        "No input device Check the microphone.",  # a real run-on, but not from markdown: only logged
    ],
)
def test_run_ons_not_made_by_markdown_get_no_stop(answer, caplog):
    with caplog.at_level("INFO", logger="artemis.agent.speech_text"):
        assert spoken_answer(answer) == answer
    assert answer not in caplog.text  # counts and word positions only, never the text


def test_a_logged_run_on_gives_its_word_position_only(caplog):
    with caplog.at_level("INFO", logger="artemis.agent.speech_text"):
        spoken_answer("The status is Done.")
    assert "1 possible run-on(s)" in caplog.text and "[3]" in caplog.text
    assert "status" not in caplog.text


@pytest.mark.parametrize(
    "markdown, spoken",
    [
        ("## Result\nIt worked.", "Result. It worked."),
        ("# No input device\nCheck the microphone.", "No input device. Check the microphone."),
        ("Done:\n- Try again\n- Use the mic", "Done: Try again. Use the mic."),
        ("1. Press Escape\n2. Say Done", "Press Escape. Say Done."),
        ("**Result**\n\nIt worked.", "Result. It worked."),
        ("Heard nothing\n\nTry again.", "Heard nothing. Try again."),
        ("## Result.\nIt worked.", "Result. It worked."),  # already punctuated: no double stop
        ("This line wraps\nonto the next. Done.", "This line wraps onto the next. Done."),
    ],
)
def test_stops_where_markdown_stripping_made_the_boundary(markdown, spoken):
    assert spoken_answer(markdown) == spoken


def test_say_shapes_answers_and_questions_but_keeps_the_log_whole():
    import asyncio

    from artemis.agent.base import say

    seen = []

    async def emit(draft):
        seen.append(draft)

    asyncio.run(say(emit, "tsk_1", CELSIUS_ANSWER))
    asyncio.run(say(emit, "tsk_1", "## Note\nThe file is src/app.py. Should I run it?", "question"))
    asyncio.run(say(emit, "tsk_1", "Reading src/app.py.", "progress"))
    assert seen[0].data["text"] == CELSIUS_ANSWER and seen[0].announce.text.endswith("The rest is in the log.")
    assert seen[1].announce.text == "Note. The file is app.py. Should I run it?"
    assert seen[2].announce.text == "Reading src/app.py." and seen[2].announce.priority == "low"  # progress: markdown only


@pytest.mark.parametrize(
    "text,spoken",
    [
        # Symbols that carry meaning stay.
        ("I wrote it in C# and F#.", "I wrote it in C# and F#."),
        ("It costs $20 a month.", "It costs $20 a month."),
        ("Mail user@example.com today.", "Mail user@example.com today."),
        ("AT&T and R&D agree.", "AT&T and R&D agree."),
        ("It takes ~5 minutes.", "It takes about 5 minutes."),
        ("Read it; then run it.", "Read it. then run it."),
        ("Done;", "Done."),
        ("Done.;", "Done."),
        # Noise symbols still go.
        ("a == b and c -> d", "a b and c d"),
        ("x && y", "x y"),
        # Dates, lists and product names are not paths.
        ("It is due 11/15/2026.", "It is due 11/15/2026."),
        ("The date is 2026/11/15.", "The date is 2026/11/15."),
        ("It uses HTML/CSS/JS.", "It uses HTML/CSS/JS."),
        ("Permissions are read/write/execute.", "Permissions are read/write/execute."),
        ("The stack is React/Next.js.", "The stack is React/Next.js."),
        ("See github.com/BU-Hackathonz/Artemis now.", "See github.com/BU-Hackathonz/Artemis now."),
        ("Score is 25/40 and/or more.", "Score is 25/40 and/or more."),
        # Real paths still shorten.
        ("Open ~/Artemis/server/main.py now.", "Open main.py now."),
        ("Open ./src/app.tsx now.", "Open app.tsx now."),
        ("Open ../lib/util.py now.", "Open util.py now."),
        ("Open C:\\Users\\me\\notes.txt now.", "Open notes.txt now."),
        ("Open /usr/local/bin now.", "Open bin now."),
        ("Open src/app.tsx now.", "Open app.tsx now."),
        ("Open server/artemis/agent/speech_text.py now.", "Open speech_text.py now."),
    ],
)
def test_symbols_and_paths_keep_their_meaning(text, spoken):
    from artemis.agent.speech_text import spoken_answer

    assert spoken_answer(text) == spoken
