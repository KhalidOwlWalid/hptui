"""Platform differences (Linux vs Windows) kept in one place."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

WINDOWS = os.name == "nt"


def state_dir() -> Path:
    """Where history lives: ~/.local/state/hptui, or %LOCALAPPDATA%\\hptui on Windows."""
    if WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    else:
        base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "hptui"


def config_dir() -> Path:
    """Where settings live: ~/.config/hptui, or %APPDATA%\\hptui on Windows."""
    if WINDOWS:
        base = os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "hptui"


def open_terminal() -> tuple[int, int]:
    """File descriptors for reading keys from and drawing to the terminal, even when stdin/stdout are pipes."""
    if WINDOWS:
        return os.open("CONIN$", os.O_RDWR), os.open("CONOUT$", os.O_RDWR)
    fd = os.open("/dev/tty", os.O_RDWR)
    return fd, fd


def resolve_program(name: str) -> str:
    """Full path for a program on PATH (needed on Windows for .cmd/.bat shims like npm)."""
    return shutil.which(name) or name


def program_name(path: str) -> str:
    """Normalised program name for matching history: "C:\\bin\\Git.EXE" -> "git" on Windows."""
    name = os.path.basename(path)
    if WINDOWS:
        name = name.lower()
        for ext in (".exe", ".cmd", ".bat", ".com", ".ps1"):
            if name.endswith(ext):
                return name[: -len(ext)]
    return name


def run_command(command: str):
    """Run the finished command in the user's shell and exit with its status. Never returns."""
    if WINDOWS:
        # exec() on Windows returns to the prompt before the child finishes; wait instead.
        print(f"> {command}", file=sys.stderr, flush=True)
        sys.exit(subprocess.run(command, shell=True).returncode)  # uses %COMSPEC% (cmd.exe)
    arrow = "\033[2m❯\033[0m" if sys.stderr.isatty() else ">"
    print(f"{arrow} {command}", file=sys.stderr, flush=True)
    shell = os.environ.get("SHELL") or "/bin/sh"
    try:
        os.execvp(shell, [shell, "-c", command])
    except OSError:
        os.execvp("/bin/sh", ["/bin/sh", "-c", command])
