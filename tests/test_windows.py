"""Windows shell rules and platform helpers. These run on every OS."""

import subprocess
import sys

from hptui import compat
from hptui.cmdline import occurrences_by_key, quote, set_value, tokenize
from hptui.parser import parse_help

OPTS = parse_help(" -o, --output=FILE   write to FILE\n -n, --name=STRING   a name\n")
BY = {o.name: o for o in OPTS}


def test_tokenize_keeps_backslashes_in_paths():
    toks = tokenize(r'tool --output=C:\Users\me\out.txt "C:\Program Files\x" it\'s', posix=False)
    assert [t.value for t in toks] == ["tool", r"--output=C:\Users\me\out.txt", r"C:\Program Files\x", "it\\'s"]


def test_tokenize_escaped_quote():
    assert [t.value for t in tokenize(r'tool "say \"hi\""', posix=False)] == ["tool", 'say "hi"']


def test_windows_quote():
    assert quote(r"C:\Users\me\out.txt", posix=False) == r"C:\Users\me\out.txt"
    assert quote(r"C:\Program Files\app", posix=False) == r'"C:\Program Files\app"'
    assert quote('say "hi"', posix=False) == r'"say \"hi\""'
    assert quote("it's here", posix=False) == '"it\'s here"'  # single quotes mean nothing to cmd.exe
    assert quote("C:\\My Dir\\", posix=False) == '"C:\\My Dir\\\\"'  # trailing backslash must not eat the quote
    assert quote("", posix=False) == '""'


def test_windows_quote_roundtrips_through_tokenize():
    for value in [r"C:\Program Files\app", 'say "hi"', "C:\\My Dir\\", "a & b", "it's"]:
        assert tokenize("x " + quote(value, posix=False), posix=False)[1].value == value


def test_set_value_with_windows_rules(monkeypatch):
    from hptui import cmdline

    monkeypatch.setattr(cmdline, "POSIX", False)
    text = set_value("tool --output=", BY["output"], r"C:\My Docs\out.txt", OPTS)
    assert text == r'tool --output="C:\My Docs\out.txt"'
    assert occurrences_by_key(text, OPTS)["--output"].value == r"C:\My Docs\out.txt"


def test_program_name_on_windows(monkeypatch):
    monkeypatch.setattr(compat, "WINDOWS", True)
    assert compat.program_name("Git.EXE") == "git"
    assert compat.program_name("npm.cmd") == "npm"
    monkeypatch.setattr(compat, "WINDOWS", False)
    assert compat.program_name("/usr/bin/help2man") == "help2man"


def test_textual_frontend_works_without_curses():
    """Windows without windows-curses: keys fall back and the Textual UI still imports."""
    code = (
        "import sys; sys.modules['curses'] = None\n"
        "from hptui import keys; assert not keys.AVAILABLE\n"
        "import hptui.textual_ui, hptui.session\n"
        "print('ok')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.stdout.strip() == "ok", out.stderr
