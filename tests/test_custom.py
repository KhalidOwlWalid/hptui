import pytest

from hptui import cmdline, custom, keys
from hptui.parser import builtin_positionals, parse_help
from hptui.session import list_segments, new_session

HPTUI_HELP = """\
usage: hptui [-h] [-c COMMAND] [-x | -p] [-H] [-E] [--frontend {curses,textual}]
             [program ...]

Build a command interactively from its --help output.

positional arguments:
  program               program to build a command for (runs `PROGRAM --help` if nothing is
                        piped in)

options:
  -h, --help            show this help message and exit
  -c, --command COMMAND
                        starting command text (default: program name from the help text)
  -x, --exec            always run the command
"""

# A CLI that ignores conventions: options without dashes in a heading hptui doesn't know,
# subcommands with no "Commands:" heading, examples without an "Examples:" heading.
ODD_HELP = """\
mytool 2.0

Things you can do
  build      make the thing
  clean      remove the thing

Flags
  -v, --verbose   say more
  -o, --out DIR   where to put it

Try these
  mytool build -o out    # build into out
  $ mytool clean

Notes
  build, clean and friends are documented online.
"""


@pytest.fixture(autouse=True)
def posix_shell(monkeypatch):
    monkeypatch.setattr(cmdline, "POSIX", True)


def write(folder, name, text):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def test_argparse_positionals_are_found_without_a_parser_file():
    [p] = builtin_positionals(HPTUI_HELP)
    assert (p.name, p.repeatable, p.optional) == ("program", True, True)
    assert p.description.startswith("program to build a command for") and p.description.endswith("piped in)")


def test_markers_point_hptui_at_each_section(tmp_path):
    path = write(tmp_path, "mytool.txt", ODD_HELP + """\
--- hptui ---
# comments and blank lines are fine
program: mytool
subcommands: after "Things you can do" before "Flags"
options: after "flags"
examples: after "Try these" before "Notes"
""")
    spec = custom.load_file(path)
    assert spec.words == ["mytool"]
    options, subs, examples, positionals = custom.parse_spec(spec)
    assert [o.key for o in options] == ["--verbose", "--out"]
    assert [(s.name, s.description) for s in subs] == [("build", "make the thing"), ("clean", "remove the thing")]
    assert [(e.command, e.description) for e in examples] == [
        ("mytool build -o out", "build into out"), ("mytool clean", "")]
    assert positionals == []


def test_unmarked_text_is_parsed_as_usual_and_ignore_hides(tmp_path):
    path = write(tmp_path, "hptui.txt", HPTUI_HELP + """\
--- hptui ---
ignore: -x
""")
    options, _, _, positionals = custom.parse_spec(custom.load_file(path))
    assert [o.key for o in options] == ["--help", "--command"]  # -x ignored, the rest as usual
    assert [p.name for p in positionals] == ["program"]
    assert custom.load_file(path).words == ["hptui"]  # taken from the usage line


def test_a_file_without_markers_is_just_the_help(tmp_path):
    path = write(tmp_path, "thing.txt", "  -q, --quiet   hush\n")
    options, *_ = custom.parse_spec(custom.load_file(path))
    assert [o.key for o in options] == ["--quiet"]


@pytest.mark.parametrize("markers, message", [
    ('options: after "Nope"', 'no line starts with "Nope"'),
    ('options: after "Flags" before "Nope"', 'no line starts with "Nope" after "Flags"'),
    ('flagz: after "Flags"', 'unknown "flagz:"'),
    ("options: Flags", 'expected options: after "<heading>"'),
])
def test_bad_markers_say_whats_wrong(tmp_path, markers, message):
    path = write(tmp_path, "mytool.txt", ODD_HELP + "--- hptui ---\n" + markers + "\n")
    with pytest.raises(custom.SpecError) as e:
        custom.load_file(path)
    assert message in str(e.value) and "line " in str(e.value)


def test_installed_file_replaces_live_help_for_the_program(tmp_path, isolated_parsers):
    src = write(tmp_path, "anything.txt", ODD_HELP + '--- hptui ---\nprogram: mytool\noptions: after "Flags"\n')
    spec, dest, replaced = custom.install(src)
    assert dest == isolated_parsers / "mytool.txt" and not replaced
    assert custom.help_text("mytool") == spec.text
    options, *_ = custom.parse_page("live help is ignored", "mytool")
    assert [o.key for o in options] == ["--verbose", "--out"]
    assert custom.install(src)[2] is True  # installing again replaces it
    assert custom.remove(["mytool"]) == dest and custom.help_text("mytool") is None


def test_subcommand_page_file_is_used_instead_of_running_its_help(tmp_path, isolated_parsers):
    root = write(tmp_path, "root.txt", ODD_HELP + '--- hptui ---\nprogram: mytool\n'
                 'subcommands: after "Things" before "Flags"\noptions: after "Flags"\n')
    page = write(tmp_path, "build.txt", "  --release   optimised build\n--- hptui ---\nprogram: mytool build\n")
    custom.install(root)
    custom.install(page)
    assert (isolated_parsers / "mytool build.txt").exists()
    options, subs, examples, positionals = custom.parse_page("", "mytool")
    calls = []
    s = new_session(options, "mytool", subcommands=subs, examples=examples, positionals=positionals,
                    loader=lambda words: calls.append(words) or "")
    s.list_index = 0
    s.handle("l")  # open build
    assert s.path == ["build"] and [o.key for o in s.options] == ["--release"]
    assert calls == []  # the pasted page was used, nothing was run


def test_positional_rows_and_value_boxes():
    options = parse_help(HPTUI_HELP)
    [prog] = builtin_positionals(HPTUI_HELP)
    s = new_session(options, "hptui", positionals=[prog])
    assert [type(i).__name__ for i in s.filtered()][0] == "Positional"
    s.list_index = 0
    s.handle("l")  # opens its box (nothing is added until there's a value)
    assert s.focus == "field:arg:program" and s.cmd.text == "hptui "
    for ch in 'git commit -m "x"':
        s.handle(ch)
    s.handle("\n")
    assert s.cmd.text == 'hptui git commit -m "x"'  # repeatable: several words, as typed
    assert s.is_checked(prog)
    rows = list_segments(s, 80)
    assert rows[0][0][1] == ("[x]", "check")

    s.list_index = 2  # -c: options go before the positional words
    s.handle("l")
    assert s.cmd.text == 'hptui --command= git commit -m "x"'
    for ch in "ls":
        s.handle(ch)
    s.handle("\n")
    assert s.cmd.text == 'hptui --command=ls git commit -m "x"'

    s.list_index = 0
    s.handle("h")  # clear the positional
    assert "git" not in s.cmd.text and not s.is_checked(prog)


def test_single_positional_is_quoted_and_found_between_options():
    from hptui.parser import Positional

    options = parse_help("  -o FILE   out\n  -v   loud\n")
    src, dst = Positional("src"), Positional("dst")
    t = cmdline.set_positional("cp -v", src, "my file", [src, dst], options)
    assert t == 'cp -v "my file"'
    t = cmdline.set_positional(t + " -o out.txt", dst, "b", [src, dst], options)
    assert t == 'cp -v "my file" -o out.txt b'
    spans = cmdline.positional_spans(t, [src, dst], options)
    assert {k: v[0].value for k, v in spans.items()} == {"arg:src": "my file", "arg:dst": "b"}


def test_parser_cli(tmp_path, monkeypatch, capsys, isolated_parsers):
    import hptui

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(hptui, "_help_output", lambda words, flag: ODD_HELP)
    hptui.parser_main(["--init", "mytool"])
    made = (tmp_path / "mytool.txt").read_text()
    assert made.startswith("mytool 2.0") and "--- hptui ---\nprogram: mytool\n" in made
    with pytest.raises(SystemExit, match="already exists"):
        hptui.parser_main(["--init", "mytool"])

    with open(tmp_path / "mytool.txt", "a") as f:
        f.write('options: after "Flags"\nsubcommands: after "Things" before "Flags"\n')
    hptui.parser_main(["--check", "mytool.txt"])
    out = capsys.readouterr().out
    assert "mytool: 2 options, 0 positionals, 2 subcommands, 0 examples" in out
    assert 'options after "Flags": help lines' in out
    assert not (isolated_parsers / "mytool.txt").exists()  # --check doesn't install

    hptui.parser_main(["--file", "mytool.txt"])
    assert "installed" in capsys.readouterr().out
    hptui.parser_main(["--list"])
    assert "mytool" in capsys.readouterr().out
    hptui.parser_main(["--remove", "mytool"])
    with pytest.raises(SystemExit, match="no custom parser"):
        hptui.parser_main(["--remove", "mytool"])

    hptui.parser_main(["--init", "git", "commit"])  # subcommand pages get their own file
    assert "program: git commit" in (tmp_path / "git-commit.txt").read_text()
