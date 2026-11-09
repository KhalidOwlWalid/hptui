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


_USAGE_RE = re.compile(r"^\s*usage:\s*(\S+)", re.IGNORECASE)

# Brackets nest one level deep: keyword[[:]=value].
_BR = r"\[(?:[^\[\]]|\[[^\]]*\])*\]"

# One piece of the flag column of an option line.
_SPEC_TOKEN_RE = re.compile(
    r"""(?P<flag>--?[A-Za-z0-9?#@][\w.+#@-]*)            # -n, --name, -?, --dry-run, --verbose...
          (?P<attached>=?BR(?=[\s,|]|$)                   # --color[=WHEN], -O[LEVEL]
                      |=?<[^>]*>(?:\.\.\.)?               # --file=<path>
                      |=(?:BR|[^\s,|\[]|\|(?!-))+)?        # --name=STRING, --lines=[-]NUM, --stderr=e|a|c
      | (?P<angle>=?<[^>]*>)                              # <file>, <on | off>, <var>=<value>
      | (?P<square>BR)                                    # [BAR]
      | (?P<brace>\{[^}]*\})                              # {fast,slow}
      | (?P<winflag>/[A-Za-z?]\w*)                        # /V (Windows spelling, skipped)
      | (?P<sep>[,|/])
      | (?P<word>(?:[^\s,|]|\|(?!-))+)                    # FILE, a|b|c, or the first word of the description
    """.replace("BR", _BR),
    re.VERBOSE,
)
_CAPS_RE = re.compile(r"[A-Z][A-Z0-9_.:-]*")
_WS_RE = re.compile(r"\s*")
_OVERSTRIKE_RE = re.compile(r".\x08")  # bold/underline the way man and less print it


def _metavar(text: str) -> tuple[str, bool]:
    """"=STRING" -> ("STRING", False), "[=WHEN]" -> ("WHEN", True), "[<file>...]" -> ("file", True)."""
    text = text.lstrip("=")
    optional = text.startswith("[") and text.endswith("]")
    if optional:
        text = text[1:-1].lstrip("=").strip()
    text = text.removesuffix("...")
    if text.startswith("<") and text.endswith(">"):
        text = text[1:-1]
    return text.strip() or "VALUE", optional  # "--title=..." still takes a value


def _set_metavar(opt: Option, metavar: str, optional: bool) -> None:
    if opt.metavar is None:
        opt.metavar = metavar
    opt.optional_value = opt.optional_value or optional  # "-C NUM, --context[=NUM]"


def _parse_option_line(line: str) -> Option | None:
    """Parse an option line like "-n, --name=STRING   description".

    The flag column ends at a wide gap (2+ spaces or a tab), at " : " or " = ",
    or at the first word that can't be a flag or an argument name. A description
    after a single space is only accepted after an argument like <json>, so prose
    that happens to start with a dash isn't mistaken for an option.
    """
    opt = Option()
    pos, n = 0, len(line)
    last = None  # previous piece: "flag" | "arg" | "sep"
    desc = ""
    while pos < n:
        ws = _WS_RE.match(line, pos).end()
        if ws > pos and opt.aliases and ("\t" in line[pos:ws] or ws - pos >= 2):
            desc = line[ws:]
            break
        spaced = ws > pos
        pos = ws
        if pos >= n:
            break
        if opt.aliases and line[pos] in ":=" and (pos + 1 == n or line[pos + 1].isspace()):
            desc = line[pos + 1:]
            break
        m = _SPEC_TOKEN_RE.match(line, pos)
        end = m.end()
        at_boundary = end >= n or line[end].isspace() or line[end] in ",|"

        if m.group("flag") and at_boundary:
            flag = m.group("flag").removesuffix("...")  # "--verbose..." means repeatable
            if last in ("flag", "arg") and spaced:
                # Spellings joined only by a space ("-n --new-window") must be short then long;
                # anything else is a list of separate flags in prose (find's help).
                if not (last == "flag" and len(opt.aliases) == 1 and opt.short and flag.startswith("--")):
                    return None
            opt.aliases.append(flag)
            if flag.startswith("--") or len(flag) > 2:  # -version (java, find) counts as long
                opt.long = opt.long or flag
            else:
                opt.short = opt.short or flag
            if m.group("attached"):
                _set_metavar(opt, *_metavar(m.group("attached")))
                last = "arg"
            else:
                last = "flag"
        elif not opt.aliases:
            return None
        elif m.group("sep"):
            last = "sep"
        elif m.group("winflag"):
            pass
        elif m.group("angle") or m.group("square") or m.group("brace"):
            text = m.group(0)
            if line.startswith("...", end):  # <file>...
                end += 3
            if text.startswith("{"):
                _set_metavar(opt, text, False)
            elif text.startswith("[") and not spaced and last == "arg":
                pass  # "<var>[:<type>]" describes the same value, it doesn't make it optional
            else:
                _set_metavar(opt, *_metavar(text))
            last = "arg"
        else:
            if last == "sep":
                return None  # "--unicode, respectively." is a wrapped sentence, not an option
            word = m.group(0).removesuffix("...")
            after = _WS_RE.match(line, end).end()
            ends_column = (after >= n or after - end >= 2 or "\t" in line[end:after]
                           or line[end:end + 1] == ","
                           or (line[after:after + 1] in (":", "=") and line[after + 1:after + 2] in ("", " ")))
            if opt.metavar is None and last == "flag" and at_boundary and (_CAPS_RE.fullmatch(word) or ends_column):
                _set_metavar(opt, word, False)  # -o FILE, -o file
                last = "arg"
            else:
                # The description starts after a single space.
                if last != "arg" and not (word[:1].isupper() and opt.long):
                    return None
                desc = line[pos:]
                break
        pos = end

    if not opt.aliases:
        return None
    opt.spec = line[:n - len(desc)].strip() if desc else line.strip()
    opt.description = desc.strip()
    return opt


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def parse_help(text: str) -> list[Option]:
    """Extract options from help text. Order is preserved, duplicates are dropped."""
    options: list[Option] = []
    seen: set[str] = set()
    current: Option | None = None
    current_indent = 0

    for raw in _OVERSTRIKE_RE.sub("", text).expandtabs(8).splitlines():
        stripped = raw.strip()
        if not stripped:
            current = None
            continue

        if stripped.startswith("-"):
            opt = _parse_option_line(stripped)
            if opt is not None:
                opt.description = re.sub(r"^[:=](\s|$)", "", opt.description).strip()
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


@dataclass
class Example:
    command: str
    description: str = ""


_EXAMPLES_RE = re.compile(r"^\s*(?:usage\s+)?examples?(?:\s+of\s+use)?\s*:?\s*$", re.IGNORECASE)
_PROMPT_RE = re.compile(r"^[$%>]\s+")
_INLINE_COMMENT_RE = re.compile(r"\s+#\s+")


def parse_examples(text: str, program: str | None = None) -> list[Example]:
    """Commands listed under "Examples:" headings.

    Only lines that start with the program's name (optionally after a "$ "
    prompt) count, so surrounding prose is never mistaken for a command. A
    description comes from an inline "# comment", or from the line just above
    (a "# comment" line or a "Do something:" line).
    """
    from . import compat

    prog = compat.program_name(program) if program else None
    examples: list[Example] = []
    seen: set[str] = set()
    in_section, header_indent, pending = False, 0, ""

    def as_command(line: str) -> str | None:
        body = _PROMPT_RE.sub("", line)
        prompted = body != line
        first = body.split(maxsplit=1)[0] if body else ""
        if (prog and compat.program_name(first) == prog) or (prompted and not prog):
            return body
        return None

    for raw in text.expandtabs(8).splitlines():
        stripped = raw.strip()
        if _EXAMPLES_RE.match(raw):
            in_section, header_indent, pending = True, _indent(raw), ""
            continue
        if not in_section:
            continue
        if not stripped:
            pending = ""
            continue
        command = as_command(stripped)
        if command is None:
            # An option list or a new "Section:" heading ends the examples.
            if stripped.startswith("-") or (stripped.endswith(":") and _indent(raw) <= header_indent + 1
                                            and not stripped.startswith("#")):
                in_section = False
            elif stripped.startswith("#"):
                pending = stripped.lstrip("#").strip()
            elif stripped.endswith(":"):
                pending = stripped[:-1].strip()
            continue
        description = pending
        parts = _INLINE_COMMENT_RE.split(command, maxsplit=1)
        if len(parts) == 2:
            command, description = parts[0].rstrip(), parts[1].strip()
        if command.endswith(".") and len(command) > 1 and not command[-2].isspace():
            command = command[:-1]  # sentence full stop, not part of the command
        pending = ""
        if command not in seen:
            seen.add(command)
            examples.append(Example(command, description))
    return examples


@dataclass
class Subcommand:
    name: str  # e.g. "commit"
    description: str = ""
    aliases: list[str] = field(default_factory=list)  # other spellings, e.g. ["b"] for cargo's "build, b"

    @property
    def names(self) -> list[str]:
        return [self.name, *self.aliases]


_SUB_NAME = r"[A-Za-z][\w.:+-]*"
_SUB_NAME_RE = re.compile(_SUB_NAME)
# "Commands:", "Management Commands:", "Most used commands:",
# "These are common Git commands used in various situations:"
_COMMANDS_HEADER_RE = re.compile(r"^(?P<title>(?!-).*\b(?:sub-?)?commands?\b.*?)\s*:\s*$", re.IGNORECASE)
# Headings without a colon (VS Code's "Subcommands") must be short and title-like,
# so a sentence ending in "commands" isn't taken for one.
_BARE_COMMANDS_HEADER_RE = re.compile(r"^(?:[A-Z][\w-]*(?: [\w-]+){0,2} )?(?:[Ss]ub-?)?[Cc]ommands?$")
_CHOICES_RE = re.compile(r"^\{(?P<names>[\w.:+-]+(?:,[\w.:+-]+)*)\}(?:\s{2,}.*)?$")  # argparse "{add,remove}"
_ENTRY_SPLIT_RE = re.compile(r"\s{2,}|\t|\s+-\s+")
_ENTRY_HEAD_RE = re.compile(rf"(?P<names>{_SUB_NAME}\*?(?:,\s*{_SUB_NAME})*)(?P<rest>.*)")
# What may follow a subcommand's name in its column: [PATTERN...], UNIT..., <file>, (rm), KEY=VALUE
_SUB_ARG_RE = re.compile(r"[\[<({|].*|[A-Z][A-Z0-9_.|=\[\]-]*(?:\.\.\.)?|\.\.\.")


def parse_subcommands(text: str) -> list[Subcommand]:
    """Subcommands listed under a "Commands:"-style heading (git, docker, uv, pip, apt, systemctl,
    argparse, cargo), including comma-separated lists (npm, snap)."""
    subs: list[Subcommand] = []
    seen: set[str] = set()
    in_section, header_indent, allowed = False, 0, None
    current: Subcommand | None = None
    current_indent = 0

    def add(names: list[str], desc: str) -> Subcommand | None:
        names = [n.rstrip("*") for n in names]  # docker marks plugins as "buildx*"
        if allowed is not None and names[0] not in allowed:
            return None
        if names[0] in seen or (names[0].isupper() and len(names[0]) > 1):  # "COMMAND" placeholder
            return None
        seen.update(names)
        sub = Subcommand(names[0], desc, names[1:])
        subs.append(sub)
        return sub

    for raw in _OVERSTRIKE_RE.sub("", text).expandtabs(8).splitlines():
        stripped = raw.strip()
        indent = _indent(raw)
        if not stripped:
            current = None
            continue
        if indent <= 4 and (_COMMANDS_HEADER_RE.match(stripped)
                            or (indent == 0 and _BARE_COMMANDS_HEADER_RE.match(stripped))):
            in_section, header_indent, allowed, current = True, indent, None, None
            continue
        m = _CHOICES_RE.match(stripped)
        if m:
            in_section, header_indent, current = True, indent, None
            allowed = set(m.group("names").split(","))
            continue
        if not in_section:
            continue
        if stripped.startswith("-"):  # options: the command list is over
            in_section = False
            continue
        if indent <= header_indent:
            if stripped.endswith(":") and not re.search(r":\s", stripped):
                in_section = False  # "Options:", "Examples:"
            current = None  # git's group headings, prose
            continue
        if current is not None and indent > current_indent:
            current.description = f"{current.description} {stripped}".strip()  # wrapped description
            continue

        # "Basics: find, info, install" (snap) or "access, adduser, audit," (npm)
        listing = re.sub(r"^[^:,]*:\s+", "", stripped).rstrip(",")
        items = [i.strip() for i in listing.split(",")]
        if len(items) >= 2 and all(_SUB_NAME_RE.fullmatch(i) for i in items):
            for item in items:
                add([item], "")
            current = None
            continue

        parts = _ENTRY_SPLIT_RE.split(stripped, maxsplit=1)
        head, desc = parts[0], (parts[1].strip() if len(parts) > 1 else "")
        m = _ENTRY_HEAD_RE.fullmatch(head)
        rest = m.group("rest").split() if m else []
        if not m or not all(_SUB_ARG_RE.fullmatch(t) for t in rest):
            current = None  # prose, not an entry
            continue
        names = [n.strip() for n in m.group("names").split(",")]
        for t in rest:  # argparse aliases: "remove (rm)"
            if t.startswith("(") and t.endswith(")") or t.startswith("(") and t.endswith("),"):
                names += [a for a in re.split(r"[\s,()]+", t) if a]
        current = add(names, desc)
        current_indent = indent
    return subs
