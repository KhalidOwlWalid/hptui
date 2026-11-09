"""hptui: turn a program's --help output into an interactive command builder."""

from __future__ import annotations

import argparse
import locale
import os
import subprocess
import sys

from . import compat, config, history, keys
from .cmdline import quote
from .parser import guess_command, parse_examples, parse_help, parse_subcommands

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
            "  hptui --frontend=textual          switch to the Textual frontend (remembered)"
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
    return _run_help(program)


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
        root = _run_help(program[:1])
        if any(program[1] in sub.names for sub in parse_subcommands(root)):
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


def main() -> None:
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

            help_text = _run_help([tokenize(command)[0].value])
        except SystemExit:
            help_text = ""  # program gone or no help: still let them edit the text
        options = parse_help(help_text)
        examples = parse_examples(help_text, history.program_of(command))
        app = _run_on_tty(lambda: run(options, command, examples=examples, subcommands=parse_subcommands(help_text),
                                      loader=load_subcommand_help, help_text=help_text))
        return _finish(app.result, run_it)

    text = _starting_help(args.program)
    options = parse_help(text)
    subcommands = parse_subcommands(text)
    if not options and not subcommands:
        raise SystemExit("hptui: no options or subcommands found in the help text")

    command = args.command or " ".join(quote(a) for a in args.program) or guess_command(text) or "command"
    examples = parse_examples(text, history.program_of(command))
    app = _run_on_tty(lambda: run(options, command, open_history=args.history, open_examples=args.examples,
                                  examples=examples, subcommands=subcommands, loader=load_subcommand_help,
                                  help_text=text))
    _finish(app.result, run_it)
