"""Tabs: Options · --help · Examples · History, switched with [ and ]."""

import pytest

from hptui import history, keys
from hptui.parser import Example, parse_help
from hptui.session import ESC, key_hints, new_session, tab_segments, tab_spans

HELP = """\
usage: tool [-o FILE] [-v]

  -o, --out FILE   where to write
  -v, --verbose    say more
""" + "".join(f"line {i}\n" for i in range(60))


@pytest.fixture(autouse=True)
def own_history(tmp_path, monkeypatch):
    monkeypatch.setenv("HPTUI_HISTORY", str(tmp_path / "h.jsonl"))
    history.add("tool -v")


def make(examples=True, **kw):
    ex = [Example("tool -o a.txt", "write a")] if examples else []
    return new_session(parse_help(HELP), "tool", examples=ex, help_text=HELP, **kw)


def press(s, *ks):
    for k in ks:
        s.handle(k)


def test_brackets_cycle_through_the_tabs_both_ways():
    s = make()
    assert s.tabs() == ["options", "help", "examples", "history"] and s.current_tab() == "options"
    seen = []
    for _ in range(4):
        press(s, "]")
        seen.append(s.current_tab())
    assert seen == ["help", "examples", "history", "options"]
    for _ in range(4):
        press(s, "[")
        seen.append(s.current_tab())
    assert seen[4:] == ["history", "examples", "help", "options"]


def test_help_tab_is_the_whole_help_text_and_keeps_its_place():
    s = make()
    press(s, "]")
    assert s.help_open and s.reader == "details" and s.help_text == HELP
    press(s, "j", "j", "j")
    press(s, "]", "[")  # to examples and back
    assert s.current_tab() == "help" and s.help_scroll == 3
    press(s, ESC)  # esc still goes back to the options
    assert s.current_tab() == "options" and not s.help_open


def test_examples_tab_is_left_out_without_examples_and_lists_them_when_there():
    s = make(examples=False)
    assert s.tabs() == ["options", "help", "history"]
    press(s, "[")
    assert s.current_tab() == "history" and [e.command for e in s.hist_filtered()] == ["tool -v"]
    s = make()
    press(s, "]", "]")
    assert s.hist_open and s.hist_kind == "examples" and [e.command for e in s.hist_filtered()] == ["tool -o a.txt"]


def test_brackets_are_typed_where_text_is_being_typed():
    s = make()
    press(s, "i")  # command box
    press(s, "[", "]")
    assert s.cmd.text == "tool []" and s.current_tab() == "options"
    s.cmd.set("tool ")
    s.focus = "list"
    press(s, "/", "[")  # option search
    assert s.query == "[" and s.current_tab() == "options"
    press(s, ESC, ESC)
    s.list_index = 0
    press(s, "l")  # --out's value box
    assert s.focus == "field:--out"
    press(s, "[", "x", "]", "\r")
    assert s.cmd.text == 'tool --out="[x]"' and s.current_tab() == "options"
    s.focus = "list"
    press(s, "[")  # history tab: typing in its search box too
    press(s, "/", "]")
    assert s.current_tab() == "history" and s.hist_query == "]"


def test_clicking_a_tab_while_editing_a_value_keeps_it():
    s = make()
    s.list_index = 0
    press(s, "l", "a")  # editing --out
    s.show_tab("help")  # what a click on the tab bar does
    assert s.cmd.text == "tool --out=a" and s.current_tab() == "help"
    s.show_tab("options")
    assert s.focus == "list"


def test_key_reference_switches_from_the_tab_it_was_opened_over():
    s = make()
    press(s, "?")
    assert s.help_open and s.reader == "keys" and s.current_tab() == "options"
    press(s, "]")
    assert s.current_tab() == "help"
    press(s, "]", "]", "?", "[")  # ? over history, then [ -> examples
    assert s.current_tab() == "examples" and not s.help_open


def test_standalone_history_has_no_tabs():
    s = new_session([], "", standalone=True)
    assert s.tabs() == [] and tab_segments(s) == [(" ", "")]
    press(s, "]")
    assert s.hist_open and not s.done and s.current_tab() == "history"


def test_tab_bar_marks_the_current_tab_and_spans_match_it():
    s = make()
    press(s, "]")
    segs = tab_segments(s)
    text = "".join(t for t, _ in segs)
    assert text.startswith("  Options   --help   Examples 1   History ")
    assert [t.strip() for t, k in segs if k == "tab"] == ["--help"]
    for name, start, end in tab_spans(s):
        label = text[start:end].strip()
        assert label.split()[0] in ("Options", "--help", "Examples", "History")
    assert key_hints(s)[0] == ("[ ]", "tabs")
