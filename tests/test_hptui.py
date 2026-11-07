from hptui.cmdline import add_option, occurrences_by_key, quote, remove_option, set_value, tokenize
from hptui.parser import guess_command, parse_help

HELP = """\
`help2man' generates a man page out of `--help' and `--version' output.

Usage: help2man [OPTION]... EXECUTABLE

 -n, --name=STRING       description for the NAME paragraph
 -s, --section=SECTION   section number for manual page (1, 6, 8)
 -o, --output=FILE       send output to `FILE'
 -N, --no-info           suppress pointer to Texinfo manual
 -l, --libtool           exclude the `lt-' from the program name
     --color[=WHEN]      colorize output
 -f FILE                 read from FILE
     --mode {fast,slow}  pick a mode
  --very-long-option-name VALUE
                         description on the next line
                         and wrapped
"""

OPTS = parse_help(HELP)
BY = {o.name: o for o in OPTS}


def test_parse_basic():
    assert guess_command(HELP) == "help2man"
    name = BY["name"]
    assert (name.short, name.long, name.metavar) == ("-n", "--name", "STRING")
    assert name.description == "description for the NAME paragraph"
    assert not BY["no-info"].takes_value
    assert BY["color"].optional_value and BY["color"].metavar == "WHEN"
    assert BY["f"].short == "-f" and BY["f"].metavar == "FILE"
    assert BY["mode"].metavar == "{fast,slow}"
    assert BY["very-long-option-name"].description == "description on the next line and wrapped"


def test_tokenize_quotes():
    toks = tokenize('cmd --name="a b" \'c d\' e\\ f')
    assert [t.value for t in toks] == ["cmd", "--name=a b", "c d", "e f"]
    assert toks[1].raw == '--name="a b"'


def test_quote():
    assert quote("plain") == "plain"
    assert quote("a b") == '"a b"'
    assert quote('say "hi" $HOME') == '"say \\"hi\\" \\$HOME"'
    assert quote("~/My Docs/x") == '~/"My Docs/x"'
    assert quote("") == '""'


def test_add_and_set_value_eq_style():
    t = add_option("help2man ", BY["name"])
    assert t == "help2man --name="
    t = set_value(t, BY["name"], "KhalidOwlWalid", OPTS)
    assert t == "help2man --name=KhalidOwlWalid"
    t = set_value(t, BY["name"], "Khalid Owl", OPTS)
    assert t == 'help2man --name="Khalid Owl"'
    assert occurrences_by_key(t, OPTS)["--name"].value == "Khalid Owl"


def test_set_value_short_separate():
    t = add_option("help2man", BY["f"])
    assert t == "help2man -f"
    t = set_value(t, BY["f"], "/tmp/my file", OPTS)
    assert t == 'help2man -f "/tmp/my file"'
    t = set_value(t, BY["f"], "x", OPTS)
    assert t == "help2man -f x"


def test_typed_options_are_detected():
    t = 'help2man -n foo -Nl --output="out file" -s8 prog'
    occ = occurrences_by_key(t, OPTS)
    assert occ["--name"].value == "foo"
    assert occ["--output"].value == "out file"
    assert occ["--section"].value == "8"
    assert "--no-info" in occ and "--libtool" in occ


def test_remove_option_keeps_rest():
    t = "help2man --name=x -o out prog"
    assert remove_option(t, BY["name"], OPTS) == "help2man -o out prog"
    assert remove_option(t, BY["output"], OPTS) == "help2man --name=x prog"
    assert remove_option("help2man -Nl prog", BY["no-info"], OPTS) == "help2man -l prog"
    assert remove_option("help2man -N", BY["no-info"], OPTS) == "help2man"


def test_optional_value():
    t = add_option("ls", BY["color"])
    assert t == "ls --color"
    t = set_value(t, BY["color"], "always", OPTS)
    assert t == "ls --color=always"
    assert set_value(t, BY["color"], "", OPTS) == "ls --color"


def test_options_after_double_dash_ignored():
    assert "--name" not in occurrences_by_key("help2man -- --name=x", OPTS)
