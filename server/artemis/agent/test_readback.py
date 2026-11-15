"""Reading code aloud: symbols named, indentation announced, chunks."""

from artemis.agent.speech_text import code_line_to_speech, next_chunk_end, speak_code


def test_symbols_are_spoken_and_names_split():
    assert code_line_to_speech("def add(a, b):") == "def add open paren a comma b close paren colon"
    assert code_line_to_speech("x == y != z") == "x equals equals y not equals z"
    assert code_line_to_speech("my_var = getName()") == "my underscore var equals get Name open paren close paren"
    assert code_line_to_speech("items[0] += 1") == "items open bracket 0 close bracket plus equals 1"
    assert code_line_to_speech("é ✓") == "é symbol"


def test_indent_level_announced_on_change_only():
    pairs = [(1, "def f(x):"), (2, "    y = x"), (3, "    return y"), (4, ""), (5, ""), (6, "z = f(1)")]
    spoken = speak_code(pairs)
    assert spoken.startswith("No indent. def f open paren x close paren colon.")
    assert spoken.count("Indent level 1.") == 1
    assert spoken.count("Blank line.") == 1
    assert "No indent. z equals f open paren 1 close paren." in spoken


def test_two_space_and_tab_indents():
    assert "Indent level 2." in speak_code([(1, "a:"), (2, "  b:"), (3, "    c")])
    assert "Indent level 1." in speak_code([(1, "a {"), (2, "\tb")])


def test_line_numbers_on_request_only():
    pairs = [(10, "x = 1"), (11, ""), (12, "y = 2")]
    assert "Line 10." in speak_code(pairs, numbers=True)
    assert "Line 11, blank." in speak_code(pairs, numbers=True)
    assert "Line" not in speak_code(pairs)


def test_chunk_is_bounded_by_lines_and_spoken_length():
    pairs = [(i, "x = 1") for i in range(1, 100)]
    assert next_chunk_end(pairs) == 20
    assert next_chunk_end(pairs[:5]) == 5
    long = [(1, "a = " + "word " * 300), (2, "b")]
    assert next_chunk_end(long) == 1
