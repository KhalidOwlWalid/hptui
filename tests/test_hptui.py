import pytest

from hptui import cmdline
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


@pytest.fixture(autouse=True)
def posix_shell(monkeypatch):
    """These tests describe POSIX shell rules; pin them so they also hold when run on Windows."""
    monkeypatch.setattr(cmdline, "POSIX", True)


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


def test_parse_examples():
    from hptui.parser import parse_examples

    text = """Usage: mytool [OPTIONS]

Examples:
  mytool -cf archive.tar foo bar  # Create archive.tar from foo and bar.
  # Convert a file
  $ mytool --in a.txt --out "b c.txt"

  Show the version:
      mytool --version
  This sentence mentions mytool but is prose.

 Main options:
  -v, --verbose   be loud
  mytool --not-an-example   (after the section ended)
"""
    got = [(e.command, e.description) for e in parse_examples(text, "mytool")]
    assert got == [
        ("mytool -cf archive.tar foo bar", "Create archive.tar from foo and bar."),
        ('mytool --in a.txt --out "b c.txt"', "Convert a file"),
        ("mytool --version", "Show the version"),
    ]
    assert parse_examples("Usage: x\n -v  verbose\n", "x") == []


def test_cluster_ending_in_value_option():
    # getopt style, like `tar -tvf archive.tar`
    occ = occurrences_by_key("help2man -Nlf in.txt prog", OPTS)
    assert "--no-info" in occ and "--libtool" in occ and occ["-f"].value == "in.txt"
    assert occurrences_by_key("help2man -Nlfin.txt", OPTS)["-f"].value == "in.txt"

    assert set_value("help2man -Nlf in.txt prog", BY["f"], "my file", OPTS) == 'help2man -Nlf "my file" prog'
    assert set_value("help2man -Nlfin.txt", BY["f"], "x", OPTS) == "help2man -Nlfx"
    assert set_value("help2man -Nlf", BY["f"], "x", OPTS) == "help2man -Nlf x"

    assert remove_option("help2man -Nlf in.txt prog", BY["f"], OPTS) == "help2man -Nl prog"
    assert remove_option("help2man -Nlf in.txt prog", BY["no-info"], OPTS) == "help2man -lf in.txt prog"
    assert remove_option("help2man -fin.txt", BY["f"], OPTS) == "help2man"


def test_parse_varied_help_styles():
    text = """\
  -d --diff <file> <file>                    Compare two files with each
                                             other.
  -n --new-window                            Force to open a new window.
  -g --goto <file:line[:character]>          Open a file at the path.
  --sync <on | off>                       Turn sync on or off.
  --add-mcp <json> Adds a server definition to the user profile.
  -e script, --expression=script
                 add the script to the commands to be executed
-c cmd : program passed in as string
  -n, --lines=[-]NUM       print the first NUM lines
  -c, -C NUM, --context[=NUM]  output NUM lines of context
  -A|--record-avail  <.deb file name>...
      --stderr=e|a|c         change stderr output mode
  --title=...                 the process title
  -D <var>[:<type>]=<value>    = Create or update a cmake cache entry.

            --unicode, respectively.
-amin N -anewer FILE -atime N -cmin N
-readable -writable -executable
"""
    got = {o.key: (o.aliases, o.metavar, o.optional_value, o.description) for o in parse_help(text)}
    assert got == {
        "--diff": (["-d", "--diff"], "file", False, "Compare two files with each other."),
        "--new-window": (["-n", "--new-window"], None, False, "Force to open a new window."),
        "--goto": (["-g", "--goto"], "file:line[:character]", False, "Open a file at the path."),
        "--sync": (["--sync"], "on | off", False, "Turn sync on or off."),
        "--add-mcp": (["--add-mcp"], "json", False, "Adds a server definition to the user profile."),
        "--expression": (["-e", "--expression"], "script", False, "add the script to the commands to be executed"),
        "-c": (["-c"], "cmd", False, "program passed in as string"),
        "--lines": (["-n", "--lines"], "[-]NUM", False, "print the first NUM lines"),
        "--context": (["-c", "-C", "--context"], "NUM", True, "output NUM lines of context"),
        "--record-avail": (["-A", "--record-avail"], None, False, "<.deb file name>..."),
        "--stderr": (["--stderr"], "e|a|c", False, "change stderr output mode"),
        "--title": (["--title"], "VALUE", False, "the process title"),
        "-D": (["-D"], "var", False, "Create or update a cmake cache entry."),
    }
