"""Parse `--help` output into a list of options."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field


@dataclass
class Option:
    short: str | None = None  # e.g. "-n"
    long: str | None = None  # e.g. "--name"
    metavar: str | None = None  # e.g. "STRING"; None means the option is a plain flag
    optional_value: bool = False  # True for "--color[=WHEN]" style options
    description: str = ""
    spec: str = ""  # the flag text exactly as it appeared, e.g. "-n, --name=STRING"
    aliases: list[str] = field(default_factory=list)  # every flag spelling, e.g. ["-n", "--name"]

    @property
    def takes_value(self) -> bool:
        return self.metavar is not None

    @property
    def name(self) -> str:
        flag = self.long or self.short or "?"
        return flag.lstrip("-")

    @property
    def key(self) -> str:
        return self.long or self.short or self.spec


# One flag spelling plus whatever describes its argument.
_FLAG_RE = re.compile(
    r"""^(?P<flag>--?[A-Za-z0-9?#@][\w.+#@-]*)   # -n, --name, -?, --dry-run
        (?P<rest>.*)$""",
    re.VERBOSE,
)
_USAGE_RE = re.compile(r"^\s*usage:\s*(\S+)", re.IGNORECASE)
_COLUMN_GAP_RE = re.compile(r"\s{2,}|\t|\s:\s")


def _parse_arg(rest: str) -> tuple[str | None, bool] | None:
    """Parse the text after a flag name. Returns (metavar, optional) or None if unparseable."""
    rest = rest.strip() if not rest.startswith(("=", "[")) else rest
    if not rest:
        return None, False
    m = re.fullmatch(r"\[=?\s*<?([^\]>]+?)>?\s*\](?:\.\.\.)?", rest)
    if m:  # --color[=WHEN], -O[LEVEL], --foo [BAR]
        return m.group(1).strip(), True
    m = re.fullmatch(r"[= ]?\s*<([^>]+)>(?:\.\.\.)?", rest)
    if m:  # --file <path>, --file=<path>
        return m.group(1).strip(), False
    m = re.fullmatch(r"[= ]?\s*([^\s\[\]<>=]+)", rest)
    if m:  # --name=STRING, -o FILE
        return m.group(1), False
    return "__bad__", False


def _parse_spec(spec: str) -> Option | None:
    """Parse the flag column of an option line, e.g. "-n, --name=STRING"."""
    # Split on ", " / " | " but not inside choice lists like {fast,slow}.
    pieces = [p.strip() for p in re.split(r"(?:,\s*|\s+\|\s+|\s+/\s+)(?![^{]*\})", spec) if p.strip()]
    if not pieces:
        return None
    opt = Option(spec=spec.strip())
    for piece in pieces:
        m = _FLAG_RE.match(piece)
        if not m:
            return None
        flag, rest = m.group("flag"), m.group("rest")
        if flag.endswith("..."):  # "--verbose..." means repeatable
            flag, rest = flag[:-3], rest
        parsed = _parse_arg(rest)
        if parsed is None:
            metavar, optional = None, False
        else:
            metavar, optional = parsed
            if metavar == "__bad__":
                return None
        opt.aliases.append(flag)
        if flag.startswith("--"):
            opt.long = opt.long or flag
        elif len(flag) == 2:
            opt.short = opt.short or flag
        else:  # single-dash long option like -version (java, find)
            opt.long = opt.long or flag
        if metavar is not None:
            opt.metavar = opt.metavar or metavar
            opt.optional_value = opt.optional_value or optional
    return opt


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def parse_help(text: str) -> list[Option]:
    """Extract options from help text. Order is preserved, duplicates are dropped."""
    options: list[Option] = []
    seen: set[str] = set()
    current: Option | None = None
    current_indent = 0

    for raw in text.expandtabs(8).splitlines():
        stripped = raw.strip()
        if not stripped:
            current = None
            continue

        if stripped.startswith("-"):
            parts = _COLUMN_GAP_RE.split(stripped, maxsplit=1)
            spec = parts[0]
            desc = parts[1].strip().lstrip(":").strip() if len(parts) > 1 else ""
            opt = _parse_spec(spec)
            if opt is not None:
                opt.description = desc
                if opt.key not in seen:
                    seen.add(opt.key)
                    options.append(opt)
                current, current_indent = opt, _indent(raw)
                continue

        # Wrapped description lines are indented deeper than their option line.
        if current is not None and _indent(raw) > current_indent:
            current.description = f"{current.description} {stripped}".strip()
        else:
            current = None

    return options


def guess_command(text: str) -> str | None:
    """Find the program name from a "Usage: prog ..." line."""
    for line in text.splitlines():
        m = _USAGE_RE.match(line)
        if m:
            return os.path.basename(m.group(1).strip("`'\""))
    return None
