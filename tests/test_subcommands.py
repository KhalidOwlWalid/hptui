import pytest

from hptui import cmdline, keys
from hptui.parser import parse_subcommands
from hptui.session import list_segments, new_session

GIT = """\
usage: git [-C <path>] <command> [<args>]

  -C <path>        run as if started in <path>
  -p, --paginate   pipe all output into less

These are common Git commands used in various situations:

start a working area (see also: git help tutorial)
   clone     Clone a repository into a new directory
   init      Create an empty Git repository

grow, mark and tweak your common history
   commit    Record changes to the repository
   remote    Manage set of tracked repositories

'git help -a' and 'git help -g' list available subcommands.
"""
COMMIT = """\
usage: git commit [<options>] [--] <pathspec>...

    -m, --message <message>  commit message
    -a, --all                commit all changed files
    -C, --reuse-message <commit>  reuse message from specified commit
"""
REMOTE = """\
usage: git remote add <name> <url>

Commands:
  add       Add a remote
  remove (rm)
            Remove a remote

    -v, --verbose         be verbose
"""
INIT = "usage: git init [-q]\n\n    -q, --quiet    be quiet\n"
HELP = {("git",): GIT, ("git", "commit"): COMMIT, ("git", "remote"): REMOTE, ("git", "init"): INIT,
        ("git", "remote", "add"): "    -f       fetch\n"}


@pytest.fixture(autouse=True)
def posix_shell(monkeypatch):
    monkeypatch.setattr(cmdline, "POSIX", True)


def make(command="git"):
    from hptui.parser import parse_help

    calls = []

    def loader(words):
        calls.append(words)
        return HELP.get(tuple(words), "")

    s = new_session(parse_help(GIT), command, subcommands=parse_subcommands(GIT), loader=loader)
    return s, calls


def press(s, *ks):
    for k in ks:
        s.handle(k)


def test_parse_subcommand_styles():
    assert [x.name for x in parse_subcommands(GIT)] == ["clone", "init", "commit", "remote"]
    remote = parse_subcommands(REMOTE)
    assert [(x.name, x.aliases, x.description) for x in remote] == [
        ("add", [], "Add a remote"), ("remove", ["rm"], "Remove a remote")]
    apt = "Most used commands:\n  list - list packages\n  install - install packages\n\nOptions:\n  -y  yes\n"
    assert [(x.name, x.description) for x in parse_subcommands(apt)] == [
        ("list", "list packages"), ("install", "install packages")]
    npm = "All commands:\n\n    access, adduser, audit,\n    bugs, cache\n\nSpecify configs\n"
    assert [x.name for x in parse_subcommands(npm)] == ["access", "adduser", "audit", "bugs", "cache"]
    cargo = "Commands:\n    build, b    Compile the current package\n    check, c    Analyze\n"
    assert [(x.name, x.aliases) for x in parse_subcommands(cargo)] == [("build", ["b"]), ("check", ["c"])]
    systemctl = "Unit Commands:\n  list-units [PATTERN...]   List units\n  kill UNIT...     Send signal\n"
    assert [x.name for x in parse_subcommands(systemctl)] == ["list-units", "kill"]
    argparse_style = "positional arguments:\n  {add,remove}\n    add         Add a thing\n    remove      Remove\n"
    assert [x.name for x in parse_subcommands(argparse_style)] == ["add", "remove"]
    vscode = ("Options\n  -h --help   Print usage.\n\nSubcommands\n  chat         Pass in a prompt to run in a chat\n"
              "               session.\n  tunnel       Make the machine accessible.\n")
    assert [(x.name, x.description) for x in parse_subcommands(vscode)] == [
        ("chat", "Pass in a prompt to run in a chat session."), ("tunnel", "Make the machine accessible.")]
    assert parse_subcommands("Run one of the commands\n  foo   bar\n") == []
    prose = "Usage: tool [OPTIONS]\n\nThis tool runs commands:\n  -v  verbose\n"
    assert parse_subcommands(prose) == []


def test_scope_splits_command_between_pages():
    from hptui.parser import parse_help

    root, commit = parse_help(GIT), parse_help(COMMIT)
    text = "git -C repo commit -C HEAD -m 'msg'"
    root_scope = cmdline.Scope((), frozenset({"commit", "init"}))
    commit_scope = cmdline.Scope(("commit",))
    assert {k: o.value for k, o in cmdline.occurrences_by_key(text, root, root_scope).items()} == {"-C": "repo"}
    assert {k: o.value for k, o in cmdline.occurrences_by_key(text, commit, commit_scope).items()} == {
        "--reuse-message": "HEAD", "--message": "msg"}
    # a root option is added before the subcommand, not after it
    paginate = next(o for o in root if o.long == "--paginate")
    assert cmdline.add_option(text, paginate, root_scope) == "git -C repo --paginate commit -C HEAD -m 'msg'"


def test_open_subcommand_page_and_back():
    s, calls = make()
    assert [type(i).__name__ for i in s.filtered()][:4] == ["Subcommand"] * 4
    s.list_index = 2  # commit
    press(s, "l")
    assert s.cmd.text == "git commit "
    assert s.path == ["commit"] and s.title() == "git commit"
    assert calls == [["git", "commit"]]
    assert [o.long for o in s.options] == ["--message", "--all", "--reuse-message"]

    press(s, " ")  # -m needs a value; space just adds it
    press(s, "j", "l")  # --all
    assert s.cmd.text == "git commit --message= --all"
    s.focus = "field:--message"
    press(s, *"first commit", "\n")
    assert s.cmd.text == 'git commit --message="first commit" --all'

    press(s, keys.KEY_BACKSPACE)  # back to git
    assert s.path == [] and s.cmd.text == 'git commit --message="first commit" --all'
    assert s.active_subcommand().name == "commit"
    s.list_index = 4  # -C on git's page goes before "commit"
    press(s, " ")
    assert s.cmd.text == 'git -C commit --message="first commit" --all'

    # going back into commit uses the cache, and keeps the text
    s.list_index = 2
    press(s, "l")
    assert calls == [["git", "commit"]] and s.path == ["commit"]
    assert "--message" in s.occurrences()


def test_switching_and_removing_subcommand():
    s, _ = make("git commit -m x")
    assert s.path == ["commit"]  # followed the command on startup
    press(s, keys.KEY_BACKSPACE)
    s.list_index = 1  # init
    press(s, "l")
    assert s.cmd.text == "git init " and s.path == ["init"]
    assert "switched from commit to init" in s.message
    press(s, "\x1b")
    s.list_index = 1
    press(s, "h")  # remove the active subcommand
    assert s.cmd.text == "git "


def test_nested_subcommands_and_aliases():
    s, calls = make("git remote rm origin")
    assert s.path == ["remote", "rm"]  # alias followed, nested
    assert calls == [["git", "remote"], ["git", "remote", "rm"]]
    press(s, "\x1b")
    assert s.path == ["remote"] and s.active_subcommand().name == "remove"
    rows = list_segments(s, 80)
    assert rows[1][0][0] == ("  ●  ", "check")  # the active subcommand is marked


def test_unknown_subcommand_page_is_empty_not_fatal():
    s, _ = make()
    s.list_index = 0  # clone: loader has no help for it
    press(s, "l")
    assert s.path == ["clone"] and s.options == [] and "no options found in the help" in s.message


def test_details_shows_the_current_pages_help_text():
    from hptui.session import details_segments, key_hints, reader_title

    s, _ = make()
    s.help_text = GIT
    press(s, "D")
    assert s.help_open and s.reader == "details" and reader_title(s) == "Details · git"
    lines = ["".join(t for t, _ in line) for line in details_segments(s, 80)]
    assert lines[0] == " usage: git [-C <path>] <command> [<args>]"
    kinds = dict((t.strip(), k) for line in details_segments(s, 80) for t, k in line if t.strip())
    assert kinds["usage:"] == "section" and kinds["-C"] == "flag" and kinds["<path>"] == "metavar"
    assert ("esc / D", "close") in key_hints(s)
    press(s, "?")  # switch to the key reference and back
    assert s.reader == "keys"
    press(s, "D")
    assert s.reader == "details"
    press(s, "\x1b")
    assert not s.help_open

    # on a subcommand page it shows that subcommand's help
    s.list_index = 2
    press(s, "l", "D")
    assert reader_title(s) == "Details · git commit"
    assert "usage: git commit" in "".join(t for line in details_segments(s, 80) for t, _ in line)
    press(s, "q", keys.KEY_BACKSPACE)
    assert s.help_text == GIT  # the parent's text comes back with its page


def test_details_wraps_long_lines_keeping_indent():
    from hptui.session import details_segments

    s, _ = make()
    s.help_text = "    " + "word " * 30
    lines = ["".join(t for t, _ in line) for line in details_segments(s, 40)]
    assert len(lines) > 1 and all(len(line) <= 40 for line in lines)
    assert all(line.startswith("     word") for line in lines)
