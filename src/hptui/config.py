"""Persistent settings, stored as JSON in ~/.config/hptui/config.json."""

from __future__ import annotations

import json
import os
from pathlib import Path

FRONTENDS = ("curses", "textual")
DEFAULTS = {"frontend": "curses"}


def path() -> Path:
    if os.environ.get("HPTUI_CONFIG"):
        return Path(os.environ["HPTUI_CONFIG"])
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "hptui" / "config.json"


def load() -> dict:
    try:
        data = json.loads(path().read_text())
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {**DEFAULTS, **data}


def save(**changes) -> None:
    p = path()
    try:
        current = json.loads(p.read_text())
        if not isinstance(current, dict):
            current = {}
    except (OSError, ValueError):
        current = {}
    current.update(changes)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, indent=2) + "\n")
    tmp.replace(p)
