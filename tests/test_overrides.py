"""TOML parser files: corrections hptui applies on top of a program's live help."""

from pathlib import Path

import pytest

from hptui import cmdline, custom, overrides
from hptui.session import new_session

from test_custom import HPTUI_HELP, ODD_HELP, write

DATA = Path(__file__).resolve().parent / "data"
PARSERS = Path(__file__).resolve().parent.parent / "parsers"
KIGIT_HELP = (DATA / "kigit-help.txt").read_text()  # `kigit --help` of the version parsers/kigit.toml is for


@pytest.fixture(autouse=True)
def posix_shell(monkeypatch):
    monkeypatch.setattr(cmdline, "POSIX", True)


def toml(tmp_path, text, name="mytool.toml"):
    return custom.load_file(write(tmp_path, name, text))


def test_kigit_toml_corrects_the_live_help():
    options, subs, examples, positionals = custom.parse_overrides(custom.load_file(PARSERS / "kigit.toml"), KIGIT_HELP)
    by_key = {o.key: o for o in options}
    files = by_key["--files"]
    assert (files.aliases, files.metavar, files.spec) == (["-f", "--files"], "OLD", "-f, --files OLD")
    assert files.description.startswith("Compare two board files")
    since, until = by_key["--since"], by_key["--until"]
    assert (since.aliases, since.spec, since.metavar) == (["--since"], "--since DATE", "DATE")
    assert (until.aliases, until.spec, until.metavar) == (["--until"], "--until DATE", "DATE")
    keys = [o.key for o in options]
    assert keys.index("--until") == keys.index("--since") + 1
    assert "--help" not in by_key and "--version" not in by_key
    assert {"--3d", "--log-level", "--console-level", "--rewritten", "--render"} <= set(by_key)  # newer than the .txt
    assert all(o.separate_value for o in options)
    assert [(p.name, p.optional, p.label) for p in positionals] == [
        ("REVISION", True, "[REVISION]"), ("BOARD", True, "[BOARD]")]
    assert [s.name for s in subs] == ["gui", "bom", "example", "uninstall"]
    assert examples[0].command == "kigit" and examples[-1].command == "kigit gui"
    assert len({e.command for e in examples}) == len(examples)  # the help's own example isn't repeated


def test_kigit_toml_in_a_session(isolated_parsers):
    custom.install(PARSERS / "kigit.toml")
    assert custom.help_text("kigit") is None  # the live help is still run
    options, subs, examples, positionals = custom.parse_page(KIGIT_HELP, "kigit")
    calls = []
    s = new_session(options, "kigit HEAD~1..HEAD", subcommands=subs, examples=examples, positionals=positionals,
                    loader=lambda words: calls.append(words) or "  --json   JSON\n")
    output = next(o for o in s.options if o.key == "--output")
    s.toggle(output, jump_to_field=True)
    assert s.cmd.text == 'kigit --output "" HEAD~1..HEAD '
    assert s.field_values()["arg:REVISION"] == "HEAD~1..HEAD"
    s.cmd.set("kigit ")
    s.focus = "list"  # toggling --output moved the focus to its value box
    s.list_index = next(i for i, item in enumerate(s.filtered()) if getattr(item, "name", "") == "bom")
    s.handle("l")  # open bom: its page comes from `kigit bom --help`
    assert s.path == ["bom"] and calls == [["kigit", "bom"]] and [o.key for o in s.options] == ["--json"]


def test_fixes_adds_splits_and_hides(tmp_path):
    ov = toml(tmp_path, """
program = "mytool"
ignore = ["clean"]
[options."--verbose"]
hide = true
[options."-o"]
long = "--output"
help = "where it goes"
[options."--color"]
value = "WHEN"
optional_value = true
[options."-x"]
[positionals.FILE]
repeatable = true
help = "inputs"
[subcommands.build]
aliases = ["b"]
""")
    options, subs, examples, positionals = custom.parse_overrides(ov, ODD_HELP)
    assert [(o.spec, o.description) for o in options] == [
        ("-o, --output DIR", "where it goes"), ("--color[=WHEN]", ""), ("-x", "")]
    assert options[0].key == "--output" and options[0].aliases == ["-o", "--output"]
    assert cmdline.add_option("mytool", options[0]) == "mytool --output="  # values default to "any"
    assert [(s.name, s.aliases) for s in subs] == [("build", ["b"])]
    assert [(p.name, p.label, p.repeatable, p.description) for p in positionals] == [
        ("FILE", "FILE...", True, "inputs")]


def test_split_keeps_the_rest_of_the_entry():
    ov = overrides.Overrides(["t"], options={"--until": {}})
    text = "  --since, --until DATE   only commits in this range\n"
    options, *_ = overrides.apply(ov, custom.builtin(text, "t"))
    assert [(o.aliases, o.spec, o.metavar, o.description) for o in options] == [
        (["--since"], "--since DATE", "DATE", "only commits in this range"),
        (["--until"], "--until DATE", "DATE", "only commits in this range")]


def test_the_parsed_entries_are_not_changed_in_place():
    parsed = custom.builtin("Commands:\n  build   make it\n\n  -o, --out DIR   where\n", "mytool")
    ov = overrides.Overrides(["mytool"], options={"-o": {"long": "--dest"}}, subcommands={"build": {"aliases": ["b"]}})
    fixed = overrides.apply(ov, parsed)
    assert fixed[0][0].aliases == ["-o", "--dest"] and fixed[1][0].aliases == ["b"]
    assert parsed[0][0].aliases == ["-o", "--out"] and parsed[0][0].spec == "-o, --out DIR"
    assert parsed[1][0].aliases == []


def test_reports_entries_the_help_doesnt_have(tmp_path):
    ov = toml(tmp_path, """
ignore = ["--nope"]
[options."--sicne"]
value = "DATE"
[options."--gone"]
hide = true
[subcommands.ghost]
hide = true
""")
    report = []
    options, *_ = custom.parse_overrides(ov, ODD_HELP, report=report)
    assert [o.key for o in options] == ["--verbose", "--out", "--sicne"]  # a typo is added, and reported
    assert report == ['options."--sicne": not in the help, added', 'options."--gone": hide, but the help has no --gone',
                      "subcommands.ghost: hide, but the help has no ghost", "ignore: the help has no --nope"]


def test_own_help_and_from_help(tmp_path, isolated_parsers):
    ov = toml(tmp_path, 'help = """\nusage: mytool [-q]\n  -q, --quiet   hush\n"""\n')
    assert ov.words == ["mytool"]  # from the usage line in its help
    options, *_ = custom.parse_overrides(ov, "  --live   ignored, the file has its own help\n")
    assert [o.key for o in options] == ["--quiet"]
    custom.install(ov.source)
    assert "--quiet" in custom.help_text("mytool")

    only = toml(tmp_path, 'from_help = false\n[options."--only"]\nhelp = "just me"\n', "only.toml")
    assert only.words == ["only"]  # from the file name
    options, subs, examples, positionals = custom.parse_overrides(only, ODD_HELP)
    assert [o.key for o in options] == ["--only"] and subs == examples == positionals == []


def test_per_option_values_beat_the_file_wide_one(tmp_path):
    ov = toml(tmp_path, 'values = "separate"\n[options."-o"]\nlong = "--dest"\nvalues = "any"\n'
                        '[options."--new"]\nvalue = "X"\n')
    options, *_ = custom.parse_overrides(ov, ODD_HELP)
    by_key = {o.key: o for o in options}
    assert by_key["--dest"].separate_value is False
    assert by_key["--verbose"].separate_value is True and by_key["--new"].separate_value is True


def test_value_false_makes_a_plain_flag(tmp_path):
    ov = toml(tmp_path, '[options."--out"]\nvalue = false\n')
    options, *_ = custom.parse_overrides(ov, ODD_HELP)
    out = next(o for o in options if o.key == "--out")
    assert (out.metavar, out.spec, out.takes_value) == (None, "-o, --out", False)


@pytest.mark.parametrize("text, message", [
    ("program = ", "Invalid"),  # TOML syntax, as tomllib says it
    ("flags = 1", 'file: unknown key "flags"'),
    ('values = "sometimes"', "values: must be one of separate, any"),
    ('[options."--out"]\nvalu = "DIR"', 'options."--out": unknown key "valu"'),
    ('[options."--out"]\nvalue = true', "give the value's name"),
    ('[options."--out"]\nhelp = 3', 'options."--out".help: must be a string'),
    ('[options.out]\nhelp = "x"', '"out" must start with "-"'),
    ('[options."--out"]\nshort = "--o"', "is a long option"),
    ('[options."--out"]\nvalues = "nope"', 'options."--out".values: must be one of'),
    ('[positionals.FILE]\noptional = "yes"', "positionals.FILE.optional: must be true or false"),
    ('[subcommands.build]\naliases = "b"', "subcommands.build.aliases: must be a list of strings"),
    ('[[examples]]\nhelp = "x"', "examples[1]: needs a command"),
    ('ignore = "-h"', "ignore: must be a list of strings"),
    ('options = 3', "options: must be table"),
])
def test_bad_toml_says_whats_wrong(tmp_path, text, message):
    with pytest.raises(custom.SpecError) as e:
        toml(tmp_path, text)
    assert message in str(e.value) and "mytool.toml" in str(e.value)


def test_toml_and_txt_for_the_same_page_replace_each_other(tmp_path, isolated_parsers):
    txt = write(tmp_path, "a.txt", ODD_HELP + '--- hptui ---\nprogram: mytool\noptions: after "Flags"\n')
    tom = write(tmp_path, "b.toml", 'program = "mytool"\n[options."--out"]\nhelp = "from toml"\n')
    custom.install(txt)
    spec, dest, replaced = custom.install(tom)
    assert dest == isolated_parsers / "mytool.toml" and replaced
    assert not (isolated_parsers / "mytool.txt").exists()
    options, *_ = custom.parse_page(ODD_HELP, "mytool")
    assert next(o for o in options if o.key == "--out").description == "from toml"
    assert custom.install(txt)[2] and not (isolated_parsers / "mytool.toml").exists()
    assert [p.name for p in custom.installed()] == ["mytool.txt"]

    (isolated_parsers / "mytool.toml").write_text('[options."--out"]\nhelp = "toml wins"\n')  # both, by hand
    custom.clear_cache()
    options, *_ = custom.parse_page(ODD_HELP, "mytool")
    assert next(o for o in options if o.key == "--out").description == "toml wins"
    assert custom.remove(["mytool"]) == isolated_parsers / "mytool.toml"
    assert custom.installed() == [] and custom.remove(["mytool"]) is None


def test_init_template_is_valid_and_changes_nothing_until_edited(tmp_path):
    for help_text, words in ((ODD_HELP, ["mytool"]), (KIGIT_HELP, ["kigit"]), (HPTUI_HELP, ["hptui"]),
                             ("", ["git", "commit"])):
        parsed = custom.builtin(help_text, words[0])
        ov = toml(tmp_path, overrides.template(parsed, words))
        assert ov.words == words and ov.options == ov.positionals == ov.subcommands == {} and ov.examples == []
        assert custom.parse_overrides(ov, help_text) == parsed
    # uncommenting an entry's line gives a valid table to fill in
    text = overrides.template(custom.builtin(ODD_HELP, "mytool"), ["mytool"])
    text = text.replace('# [options."--out"]', '[options."--out"]') + 'value = "PATH"\n'
    options, *_ = custom.parse_overrides(toml(tmp_path, text), ODD_HELP)
    assert next(o for o in options if o.key == "--out").spec == "-o, --out PATH"


def test_parser_cli_toml(tmp_path, monkeypatch, capsys, isolated_parsers):
    import hptui

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(hptui, "_help_output", lambda words, flag: ODD_HELP)
    hptui.parser_main(["--init", "mytool"])
    assert "wrote mytool.toml" in capsys.readouterr().out
    made = (tmp_path / "mytool.toml").read_text()
    assert 'program = "mytool"' in made and '# [options."--out"]  # -o, --out DIR' in made
    with open(tmp_path / "mytool.toml", "a") as f:
        f.write('[options."--out"]\nhelp = "fixed"\n[subcommands.extra]\n')
    hptui.parser_main(["--check", "mytool.toml"])
    out = capsys.readouterr().out
    assert "mytool: 2 options, 0 positionals, 1 subcommands, 0 examples" in out
    assert "note: subcommands.extra: not in the help, added" in out
    assert not (isolated_parsers / "mytool.toml").exists()
    hptui.parser_main(["--file", "mytool.toml"])
    assert "installed" in capsys.readouterr().out and (isolated_parsers / "mytool.toml").exists()
    hptui.parser_main(["--list"])
    assert "mytool.toml" in capsys.readouterr().out
    hptui.parser_main(["--remove", "mytool"])
    assert custom.installed() == []


def test_main_uses_the_toml_for_a_bad_file_message(tmp_path, isolated_parsers, monkeypatch):
    import sys

    import hptui

    write(isolated_parsers, "mytool.toml", "nonsense = 1\n")
    monkeypatch.setattr(sys, "argv", ["hptui", "parser", "--check", str(isolated_parsers / "mytool.toml")])
    with pytest.raises(SystemExit, match='(?s)bad parser file .*unknown key "nonsense"'):
        hptui.main()
