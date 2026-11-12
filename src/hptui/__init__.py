"""hptui: turn a program's --help output into an interactive command builder."""

from __future__ import annotations

import argparse
import locale
import os
import subprocess
import sys

from pathlib import Path

from . import compat, config, custom, history, keys
from .cmdline import quote
from .parser import guess_command, parse_help, parse_subcommands

__all__ = ["main"]


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hptui",
        description="Build a command interactively from its --help output.",
        epilog=(
            "examples:\n"
            "  help2man --help | hptui           build a command, then run it\n"
            "  hptui help2man                    same, runs `help2man --help` for you\n"
            "  hptui -p help2man                 print the command instead of running it\n"
            '  eval "$(hptui help2man)"          run it in the current shell (output captured, so it prints)\n'
            "  hptui --history                   pick a past command from any program\n"
            "  hptui --history help2man          start in help2man's history\n"
            "  hptui --examples tar              start in the examples from `tar --help`\n"
            "  hptui --frontend=textual          switch to the Textual frontend (remembered)\n"
            "  hptui parser --help               teach hptui how to read a program's help (custom parsers)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("program", nargs="*", help="program to build a command for (runs `PROGRAM --help` if nothing is piped in)")
    p.add_argument("-c", "--command", help="starting command text (default: program name from the help text)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("-x", "--exec", action="store_true",
                      help="always run the command when done (default when output goes to a terminal)")
    mode.add_argument("-p", "--print", action="store_true",
                      help="always print the command instead of running it (default when output is captured)")
    p.add_argument("-H", "--history", action="store_true", help="start in the command history (all programs if none given)")
    p.add_argument("-E", "--examples", action="store_true", help="start in the examples from the help page")
    p.add_argument(
        "--frontend", choices=config.FRONTENDS,
        help="UI to use, remembered for future runs (default: curses). "
             "curses is faster to start and has no dependencies; "
             "textual adds mouse support, paste and nicer styling but starts slower",
    )
    return p


def _read_help(program: list[str]) -> str:
    if not sys.stdin.isatty():
        return sys.stdin.read()
    if not program:
        raise SystemExit("hptui: pipe some --help output in, e.g. `help2man --help | hptui`, or pass a program name")
    pasted = custom.help_text(program[0], program[1:])
    return pasted if pasted is not None else _run_help(program)


# Help must come back as text: never wait on a pager, and don't let `git x --help`
# open a man page (or a browser on Windows).
_NO_PAGER = {"PAGER": "cat", "MANPAGER": "cat", "GIT_PAGER": "cat"}


def _help_output(words: list[str], flag: str) -> str:
    proc = subprocess.run([compat.resolve_program(words[0]), *words[1:], flag], capture_output=True, text=True,
                          errors="replace", stdin=subprocess.DEVNULL, env={**os.environ, **_NO_PAGER}, timeout=15)
    return proc.stdout or proc.stderr


def _run_help(program: list[str]) -> str:
    try:
        return _help_output(program, "--help")
    except FileNotFoundError:
        raise SystemExit(f"hptui: command not found: {program[0]}")
    except subprocess.TimeoutExpired:
        raise SystemExit(f"hptui: `{' '.join(program)} --help` took too long")


def load_subcommand_help(words: list[str]) -> str:
    """Help text for a subcommand page, e.g. ["git", "commit"]. Returns "" if there is none."""
    git = compat.program_name(words[0]) == "git"
    text = ""
    for flag in (["-h"] if git else ["--help", "-h"]):
        try:
            text = _help_output(words, flag)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        if parse_help(text) or parse_subcommands(text):
            break
    return text


def _starting_help(program: list[str]) -> str:
    """`hptui git commit` starts from git's help (commit's page opens on top of it, so Esc
    leads back to git); anything that isn't a listed subcommand gets `... --help` as before."""
    if len(program) > 1 and sys.stdin.isatty() and not program[1].startswith("-"):
        pasted = custom.help_text(program[0])
        root = pasted if pasted is not None else _run_help(program[:1])
        if any(program[1] in sub.names for sub in custom.parse_page(root, program[0])[1]):
            return root
    return _read_help(program)


def _run_on_tty(fn):
    """Run fn with stdin/stdout pointed at the terminal, even if they are pipes."""
    try:
        tty_in, tty_out = compat.open_terminal()
    except OSError:
        raise SystemExit("hptui: no terminal available")
    sys.stdout.flush()
    saved_in, saved_out = os.dup(0), os.dup(1)
    os.dup2(tty_in, 0)
    os.dup2(tty_out, 1)
    try:
        return fn()
    finally:
        os.dup2(saved_in, 0)
        os.dup2(saved_out, 1)
        for fd in {saved_in, saved_out, tty_in, tty_out}:
            os.close(fd)


def _finish(result: str | None, run_it: bool) -> None:
    if result is None:
        sys.exit(130)
    history.add(result)
    if not run_it:
        print(result)
        return
    compat.run_command(result)


def _parser_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hptui parser",
        description="Custom parsers: paste a program's help into a file, mark where its options, "
                    "arguments, subcommands and examples are, and hptui uses that file for the program.",
        epilog=(
            "file format (the help, pasted exactly, then markers):\n"
            "  usage: mytool [-h] [FILE ...]\n"
            "  ...\n"
            "  --- hptui ---\n"
            '  positionals: after "positional arguments:" before "options:"\n'
            '  options: after "options:"\n'
            '  subcommands: after "Commands"\n'
            '  examples: after "Examples"\n'
            "  ignore: --bogus, help\n"
            "\n"
            "examples:\n"
            "  hptui parser --init mytool        write mytool.txt from `mytool --help`, to add markers to\n"
            "  hptui parser --check mytool.txt   show what the file gives, without installing it\n"
            "  hptui parser --file mytool.txt    install it: `hptui mytool` uses it from now on\n"
            '  hptui parser --file c.txt --program "git commit"   a file for a subcommand page\n'
            "  hptui parser --list               installed parsers\n"
            "  hptui parser --remove mytool      back to hptui's own parsing"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    action = p.add_mutually_exclusive_group(required=True)
    action.add_argument("-f", "--file", type=Path, metavar="FILE", help="check a parser file and install it")
    action.add_argument("--check", type=Path, metavar="FILE", help="show what a parser file gives, without installing it")
    action.add_argument("--init", nargs="+", metavar="PROGRAM", help="write PROGRAM.txt from PROGRAM's help, ready for markers")
    action.add_argument("-l", "--list", action="store_true", help="list installed parsers")
    action.add_argument("--remove", nargs="+", metavar="PROGRAM", help="delete the installed parser for PROGRAM")
    action.add_argument("--path", action="store_true", help="print the folder installed parsers live in")
    p.add_argument("--program", help='what the file is for, e.g. "mytool" or "git commit" '
                                     "(default: its program: line, else the usage line, else the file name)")
    p.add_argument("-o", "--output", help="with --init: file to write (default: PROGRAM.txt here; - for stdout)")
    p.add_argument("--force", action="store_true", help="with --init: overwrite an existing file")
    return p


def parser_main(argv: list[str]) -> None:
    """`hptui parser ...`: manage custom parser files."""
    args = _parser_argparser().parse_args(argv)
    words = args.program.split() if args.program else None
    if args.path:
        print(custom.parsers_dir())
    elif args.list:
        files = custom.installed()
        if not files:
            print(f"no custom parsers installed (folder: {custom.parsers_dir()})")
        for path in files:
            print(f"{path.stem:20} {path}")
    elif args.remove:
        path = custom.remove(args.remove)
        if path is None:
            raise SystemExit(f"hptui: no custom parser installed for {' '.join(args.remove)}")
        print(f"removed {path}")
    elif args.init:
        out = args.output or "-".join([compat.program_name(args.init[0]), *args.init[1:]]) + custom.SUFFIX
        if out != "-" and Path(out).exists() and not args.force:
            raise SystemExit(f"hptui: {out} already exists (use --force to overwrite)")
        text = load_subcommand_help(args.init) if len(args.init) > 1 else _quiet_help(args.init[0])
        if not text.strip():
            print(f"hptui: `{' '.join(args.init)} --help` printed nothing; paste the help into the file yourself",
                  file=sys.stderr)
        content = custom.template(text, args.init)
        if out == "-":
            print(content, end="")
        else:
            Path(out).write_text(content, encoding="utf-8")
            print(f"wrote {out}: add markers at the bottom, check it with `hptui parser --check {out}`, "
                  f"then install it with `hptui parser --file {out}`")
    else:
        source = args.file or args.check
        spec = custom.load_file(source, words)
        for line in custom.summary(spec):
            print(line)
        if args.file:
            _, dest, replaced = custom.install(source, words)
            print(f"{'replaced' if replaced else 'installed'} {dest}")


def _quiet_help(program: str) -> str:
    try:
        return _help_output([program], "--help")
    except (OSError, subprocess.TimeoutExpired):
        return ""


def main() -> None:
    try:
        if sys.argv[1:2] == ["parser"]:
            return parser_main(sys.argv[2:])
        _main()
    except custom.SpecError as e:
        raise SystemExit(f"hptui: bad parser file {e}")


def _main() -> None:
    args = _build_argparser().parse_args()
    # Run when used directly in a terminal; print when captured, e.g. "$(hptui ls)".
    run_it = args.exec or (not args.print and sys.stdout.isatty())

    if args.frontend:
        config.save(frontend=args.frontend)
        if sys.stdin.isatty() and not args.program and not args.history:
            print(f"hptui: frontend set to {args.frontend} (saved in {config.path()})", file=sys.stderr)
            return
    frontend = args.frontend or config.load()["frontend"]
    if frontend == "curses" and not keys.AVAILABLE:
        hint = " (pip install windows-curses)" if compat.WINDOWS else ""
        print(f"hptui: curses isn't available{hint}; using the textual frontend", file=sys.stderr)
        frontend = "textual"
    # UI imports are deferred so --help stays fast; textual is only imported when chosen.
    if frontend == "textual":
        from .textual_ui import run
    else:
        from .curses_ui import run

    locale.setlocale(locale.LC_ALL, "")

    if args.history and sys.stdin.isatty() and not args.program:
        # History across every program; no help page needed unless the user edits an entry.
        app = _run_on_tty(lambda: run([], "", program="", standalone=True))
        if app.edit_request is None:
            return _finish(app.result, run_it)
        command = app.edit_request
        try:
            from .cmdline import tokenize

            program = tokenize(command)[0].value
            pasted = custom.help_text(program)
            help_text = pasted if pasted is not None else _run_help([program])
        except SystemExit:
            help_text = ""  # program gone or no help: still let them edit the text
        options, subcommands, examples, positionals = custom.parse_page(help_text, history.program_of(command))
        app = _run_on_tty(lambda: run(options, command, examples=examples, subcommands=subcommands,
                                      loader=load_subcommand_help, help_text=help_text, positionals=positionals))
        return _finish(app.result, run_it)

    text = _starting_help(args.program)
    command = args.command or " ".join(quote(a) for a in args.program) or guess_command(text) or "command"
    options, subcommands, examples, positionals = custom.parse_page(text, history.program_of(command))
    if not options and not subcommands and not positionals:
        hint = f" (teach hptui with `hptui parser --init {history.program_of(command)}`)"
        raise SystemExit(f"hptui: no options or subcommands found in the help text{hint}")
    app = _run_on_tty(lambda: run(options, command, open_history=args.history, open_examples=args.examples,
                                  examples=examples, subcommands=subcommands, loader=load_subcommand_help,
                                  help_text=text, positionals=positionals))
    _finish(app.result, run_it)
