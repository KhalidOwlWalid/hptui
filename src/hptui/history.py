"""Command history, stored as JSON lines (newest last)."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from . import compat

MAX_ENTRIES = 1000


@dataclass
class Entry:
    program: str
    command: str
    time: float
    cwd: str = ""


def path() -> Path:
    if os.environ.get("HPTUI_HISTORY"):
        return Path(os.environ["HPTUI_HISTORY"])
    return compat.state_dir() / "history.jsonl"


def program_of(command: str) -> str:
    """The program a command runs, e.g. "help2man" for "/usr/bin/help2man -n x"."""
    from .cmdline import tokenize

    tokens = tokenize(command)
    return compat.program_name(tokens[0].value) if tokens else ""


def _read_all() -> list[Entry]:
    try:
        lines = path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries = []
    for line in lines:
        try:
            entries.append(Entry(**json.loads(line)))
        except (ValueError, TypeError):
            continue  # skip corrupt lines rather than failing
    return entries


def _write_all(entries: list[Entry]) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(asdict(e)) + "\n" for e in entries[-MAX_ENTRIES:]), encoding="utf-8")
    tmp.replace(p)


def load(program: str | None = None) -> list[Entry]:
    """Entries newest first, one per distinct command. Pass a program to only get its commands."""
    seen: set[str] = set()
    result = []
    for e in reversed(_read_all()):
        if e.command in seen or (program and e.program != program):
            continue
        seen.add(e.command)
        result.append(e)
    return result


def add(command: str) -> None:
    command = command.strip()
    if not command:
        return
    entries = [e for e in _read_all() if e.command != command]
    entries.append(Entry(program_of(command), command, time.time(), os.getcwd()))
    try:
        _write_all(entries)
    except OSError:
        pass  # history is a convenience; never block the user over it


def delete(command: str) -> None:
    try:
        _write_all([e for e in _read_all() if e.command != command])
    except OSError:
        pass


def ago(t: float) -> str:
    s = max(0, time.time() - t)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= size:
            return f"{int(s // size)}{unit} ago"
    return "just now"
