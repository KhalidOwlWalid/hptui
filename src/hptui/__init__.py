"""hptui: turn a program's --help output into an interactive command builder."""

from __future__ import annotations

import argparse
import locale
import os
import shlex
import subprocess
import sys

from . import config, history
from .parser import guess_command, parse_help

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


def _run_help(program: list[str]) -> str:
    try:
        proc = subprocess.run([*program, "--help"], capture_output=True, text=True)
    except FileNotFoundError:
        raise SystemExit(f"hptui: command not found: {program[0]}")
    return proc.stdout or proc.stderr


def _run_on_tty(fn):
    """Run fn with stdin/stdout pointed at the terminal, even if they are pipes."""
    try:
        tty = os.open("/dev/tty", os.O_RDWR)
    except OSError:
        raise SystemExit("hptui: no terminal available")
    sys.stdout.flush()
    saved_in, saved_out = os.dup(0), os.dup(1)
    os.dup2(tty, 0)
    os.dup2(tty, 1)
    try:
        return fn()
    finally:
        os.dup2(saved_in, 0)
        os.dup2(saved_out, 1)
        for fd in (saved_in, saved_out, tty):
            os.close(fd)


def _finish(result: str | None, run_it: bool) -> None:
    if result is None:
        sys.exit(130)
    history.add(result)
    if not run_it:
        print(result)
        return
    # Show what is about to run, then hand the process over to the user's shell.
    print(f"\033[2m❯\033[0m {result}" if sys.stderr.isatty() else f"> {result}", file=sys.stderr, flush=True)
    shell = os.environ.get("SHELL") or "/bin/sh"
    try:
        os.execvp(shell, [shell, "-c", result])
    except OSError:
        os.execvp("/bin/sh", ["/bin/sh", "-c", result])


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

            options = parse_help(_run_help([tokenize(command)[0].value]))
        except SystemExit:
            options = []  # program gone or no help: still let them edit the text
        app = _run_on_tty(lambda: run(options, command))
        return _finish(app.result, run_it)

    text = _read_help(args.program)
    options = parse_help(text)
    if not options:
        raise SystemExit("hptui: no options found in the help text")

    command = args.command or shlex.join(args.program) or guess_command(text) or "command"
    app = _run_on_tty(lambda: run(options, command, open_history=args.history))
    _finish(app.result, run_it)
