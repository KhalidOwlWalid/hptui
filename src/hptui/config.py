"""Persistent settings, stored as JSON in ~/.config/hptui/config.json (%APPDATA%\\hptui on Windows)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import compat

FRONTENDS = ("curses", "textual")
DEFAULTS = {"frontend": "curses"}


def path() -> Path:
    if os.environ.get("HPTUI_CONFIG"):
        return Path(os.environ["HPTUI_CONFIG"])
    return compat.config_dir() / "config.json"


def load() -> dict:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {**DEFAULTS, **data}


def save(**changes) -> None:
    p = path()
    try:
        current = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(current, dict):
            current = {}
    except (OSError, ValueError):
        current = {}
    current.update(changes)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)
