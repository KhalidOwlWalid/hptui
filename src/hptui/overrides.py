"""TOML parser files: corrections applied on top of what hptui reads from a program's live help.

    program = "mytool"
    values = "separate"            # the program wants --name VALUE, never --name=VALUE
    ignore = ["-h", "--version"]

    [options."--files"]            # fix an option hptui found (any of its spellings)...
    value = "OLD"
    help = "compare two files"

    [options."--until"]            # ...split one out of an entry it was lumped into, or add one
    value = "DATE"

    [positionals.REVISION]
    optional = true
    help = "OLD..NEW or REV"

    [subcommands.gui]
    help = "the desktop app"

    [[examples]]
    command = "mytool HEAD~3..HEAD"
    help = "the last three commits"

Unlike a marker (.txt) file, the program's --help is still read every time, so options a
new version adds show up on their own, and only the entries named here are changed.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - depends on the interpreter
    import tomli as tomllib

from .parser import Example, Option, Positional, Subcommand

VALUE_STYLES = ("separate", "any")

Parsed = tuple[list[Option], list[Subcommand], list[Example], list[Positional]]


class SpecError(ValueError):
    """A parser file that can't be used; the message says where and why."""


@dataclass
class Overrides:
    words: list[str]  # the program and subcommand path the file is for, e.g. ["git", "commit"]
    source: Path | None = None
    help: str | None = None  # help text to use instead of running the program's --help
    from_help: bool = True  # False: only the entries in this file
    values: str = "any"  # one of VALUE_STYLES, for every option
    ignore: set[str] = field(default_factory=set)
    options: dict[str, dict] = field(default_factory=dict)
    positionals: dict[str, dict] = field(default_factory=dict)
    subcommands: dict[str, dict] = field(default_factory=dict)
    examples: list[dict] = field(default_factory=list)

    @property
    def name(self) -> str:
        return " ".join(self.words)


# ---- reading a file ------------------------------------------------------------------

_STR, _BOOL, _STRS = "a string", "true or false", "a list of strings"
_TOP = {"program": _STR, "values": _STR, "ignore": _STRS, "help": _STR, "from_help": _BOOL,
        "options": "table", "positionals": "table", "subcommands": "table", "examples": "list"}
_OPTION = {"short": _STR, "long": _STR, "value": "a string or false", "optional_value": _BOOL,
           "help": _STR, "values": _STR, "hide": _BOOL}
_POSITIONAL = {"help": _STR, "optional": _BOOL, "repeatable": _BOOL, "hide": _BOOL}
_SUBCOMMAND = {"help": _STR, "aliases": _STRS, "hide": _BOOL}
_EXAMPLE = {"command": _STR, "help": _STR}
_FLAG_RE = re.compile(r"-\S+")


def _type_ok(value, want: str) -> bool:
    if want == _STR:
        return isinstance(value, str)
    if want == _BOOL:
        return isinstance(value, bool)
    if want == _STRS:
        return isinstance(value, list) and all(isinstance(v, str) for v in value)
    if want == "table":
        return isinstance(value, dict)
    if want == "list":
        return isinstance(value, list)
    return isinstance(value, (str, bool))  # "a string or false"


def _check_table(table, allowed: dict[str, str], where: str, errors: list[str]) -> dict:
    if not isinstance(table, dict):
        errors.append(f"{where}: must be a table")
        return {}
    for key, value in table.items():
        if key not in allowed:
            errors.append(f'{where}: unknown key "{key}"; use one of {", ".join(allowed)}')
        elif not _type_ok(value, allowed[key]):
            errors.append(f"{where}.{key}: must be {allowed[key]}")
    return {k: v for k, v in table.items() if k in allowed and _type_ok(v, allowed[k])}


def _check_flag(flag: str, where: str, errors: list[str]) -> None:
    if not _FLAG_RE.fullmatch(flag):
        errors.append(f'{where}: "{flag}" must start with "-" and have no spaces')


def parse_file(content: str, where: str = "parser file") -> tuple[Overrides, str | None]:
    """An Overrides (words still to be filled in) and the `program` it names. Raises SpecError."""
    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError as e:
        raise SpecError(f"{where}:\n  - {e}")
    errors: list[str] = []
    top = _check_table(data, _TOP, "file", errors)
    ov = Overrides([])
    values = top.get("values", "any")
    if values not in VALUE_STYLES:
        errors.append(f'values: must be one of {", ".join(VALUE_STYLES)}')
    ov.values = values
    ov.help = top.get("help")
    ov.from_help = top.get("from_help", True)
    ov.ignore = set(top.get("ignore", []))

    for flag, table in top.get("options", {}).items():
        label = f'options."{flag}"'
        _check_flag(flag, label, errors)
        fix = _check_table(table, _OPTION, label, errors)
        for key in ("short", "long"):
            if key in fix:
                _check_flag(fix[key], f"{label}.{key}", errors)
        if "short" in fix and fix["short"].startswith("--"):
            errors.append(f'{label}.short: "{fix["short"]}" is a long option; use long =')
        if fix.get("value") is True:
            errors.append(f'{label}.value: give the value\'s name, e.g. value = "FILE", or false for none')
        if fix.get("values", "any") not in VALUE_STYLES:
            errors.append(f'{label}.values: must be one of {", ".join(VALUE_STYLES)}')
        ov.options[flag] = fix
    for name, table in top.get("positionals", {}).items():
        ov.positionals[name] = _check_table(table, _POSITIONAL, f"positionals.{name}", errors)
    for name, table in top.get("subcommands", {}).items():
        ov.subcommands[name] = _check_table(table, _SUBCOMMAND, f"subcommands.{name}", errors)
    for i, table in enumerate(top.get("examples", [])):
        ex = _check_table(table, _EXAMPLE, f"examples[{i + 1}]", errors)
        if isinstance(table, dict) and "command" not in table:
            errors.append(f"examples[{i + 1}]: needs a command")
        elif "command" in ex:
            ov.examples.append(ex)
    if errors:
        raise SpecError(f"{where}:\n" + "\n".join(f"  - {e}" for e in errors))
    return ov, top.get("program") or None


# ---- applying it -----------------------------------------------------------------------

def _respec(opt: Option) -> None:
    """Rewrite the flag column shown in the list after its spellings or value changed."""
    flags = ", ".join(opt.aliases)
    if opt.metavar is None:
        opt.spec = flags
    elif opt.optional_value:
        opt.spec = flags + (f"[={opt.metavar}]" if opt.aliases[-1].startswith("--") else f"[{opt.metavar}]")
    else:
        opt.spec = f"{flags} {opt.metavar}"


def _set_flag(opt: Option, flag: str) -> None:
    if flag.startswith("--") or len(flag) > 2:  # -version counts as long, as in the help parser
        opt.long = flag
    else:
        opt.short = flag


def _fix_option(opt: Option, fix: dict) -> None:
    respec = False
    if "short" in fix or "long" in fix:
        old = {opt.short, opt.long}
        opt.short = fix.get("short", opt.short)
        opt.long = fix.get("long", opt.long)
        others = [a for a in opt.aliases if a not in old and a not in (opt.short, opt.long)]
        opt.aliases = [a for a in (opt.short, opt.long) if a] + others
        respec = True
    if "value" in fix:
        value = fix["value"]
        if value is False or value == "":
            opt.metavar, opt.optional_value = None, False
        else:
            opt.metavar = value
        respec = True
    if "optional_value" in fix:
        opt.optional_value = fix["optional_value"]
        if opt.optional_value and opt.metavar is None:
            opt.metavar = "VALUE"
        respec = True
    if "help" in fix:
        opt.description = fix["help"]
    if "values" in fix:
        opt.separate_value = fix["values"] == "separate"
    if respec:
        _respec(opt)


def _label(p: Positional) -> str:
    return (f"[{p.name}]" if p.optional else p.name) + ("..." if p.repeatable else "")


def apply(ov: Overrides, parsed: Parsed, report: list[str] | None = None) -> Parsed:
    """The entries parsed from the help, corrected by the file. `report` collects the file's
    entries that matched nothing in the help (added, or a hide/ignore that did nothing)."""
    note = report.append if report is not None else (lambda _msg: None)
    options, subcommands, examples, positionals = parsed if ov.from_help else ([], [], [], [])
    options = [replace(o, aliases=list(o.aliases)) for o in options]
    positionals = [replace(p) for p in positionals]
    subcommands = [replace(s, aliases=list(s.aliases)) for s in subcommands]
    separate = ov.values == "separate"

    hidden: set[int] = set()
    own_values: set[int] = set()  # options with their own `values`, which the file-wide one doesn't change
    for flag, fix in ov.options.items():
        opt = next((o for o in options if flag in (o.long, o.short)), None)
        if opt is None:
            owner = next((o for o in options if flag in o.aliases), None)
            if owner is not None:  # "--since DATE, --until DATE": split --until out of --since
                owner.aliases.remove(flag)
                _respec(owner)
                opt = replace(owner, short=None, long=None, aliases=[flag])
                options.insert(options.index(owner) + 1, opt)
            elif fix.get("hide"):
                note(f'options."{flag}": hide, but the help has no {flag}')
                continue
            else:
                note(f'options."{flag}": not in the help, added')
                opt = Option(aliases=[flag], separate_value=separate)
                options.append(opt)
            _set_flag(opt, flag)
            _respec(opt)
        _fix_option(opt, fix)
        if "values" in fix:
            own_values.add(id(opt))
        if fix.get("hide"):
            hidden.add(id(opt))
    options = [o for o in options if id(o) not in hidden]
    if separate:
        for o in options:
            if id(o) not in own_values:
                o.separate_value = True

    for name, fix in ov.positionals.items():
        p = next((p for p in positionals if name in (p.name, p.label)), None)
        if p is None:
            if fix.get("hide"):
                note(f"positionals.{name}: hide, but the help has no {name}")
                continue
            note(f"positionals.{name}: not in the help, added")
            p = Positional(name)
            positionals.append(p)
            p.label = _label(p)
        if fix.get("hide"):
            positionals.remove(p)
            continue
        if "help" in fix:
            p.description = fix["help"]
        if "optional" in fix or "repeatable" in fix:
            p.optional = fix.get("optional", p.optional)
            p.repeatable = fix.get("repeatable", p.repeatable)
            p.label = _label(p)

    for name, fix in ov.subcommands.items():
        s = next((s for s in subcommands if name in s.names), None)
        if s is None:
            if fix.get("hide"):
                note(f"subcommands.{name}: hide, but the help has no {name}")
                continue
            note(f"subcommands.{name}: not in the help, added")
            s = Subcommand(name)
            subcommands.append(s)
        if fix.get("hide"):
            subcommands.remove(s)
            continue
        if "help" in fix:
            s.description = fix["help"]
        if "aliases" in fix:
            s.aliases = [a for a in fix["aliases"] if a != s.name]

    ours = [Example(e["command"], e.get("help", "")) for e in ov.examples]
    taken = {e.command for e in ours}
    examples = ours + [e for e in examples if e.command not in taken]

    if ov.ignore:
        ig = ov.ignore
        everything = ({a for o in options for a in o.aliases} | {n for s in subcommands for n in s.names}
                      | {e.command for e in examples} | {p.name for p in positionals} | {p.label for p in positionals})
        for word in sorted(ig - everything):
            note(f"ignore: the help has no {word}")
        options = [o for o in options if not ig & set(o.aliases)]
        subcommands = [s for s in subcommands if not ig & set(s.names)]
        examples = [e for e in examples if e.command not in ig]
        positionals = [p for p in positionals if p.name not in ig and p.label not in ig]
    return options, subcommands, examples, positionals


# ---- `hptui parser --init` -----------------------------------------------------------------

def _q(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)  # a JSON string is a valid TOML basic string


def template(parsed: Parsed, words: list[str]) -> str:
    """A starting file: the format explained, and everything found in the help, commented out."""
    name = " ".join(words)
    options, subcommands, examples, positionals = parsed
    out = [
        f"# hptui parser for `{name}`: corrections to what hptui reads from `{name} --help`.",
        "# hptui still reads the help every time; this file only changes the entries named in it.",
        f"# Check it: hptui parser --check FILE    Install it: hptui parser --file FILE",
        f"program = {_q(name)}",
        "",
        '# values = "separate"           # the program wants --name VALUE, never --name=VALUE',
        '# ignore = ["-h", "--version"]  # hide entries by name',
        "# from_help = false             # use only the entries in this file",
        '# help = """..."""              # help text to use instead of running --help',
        "#",
        "# Each entry below is what hptui found. Uncomment one to correct it, or add one it missed:",
        '#   [options."--out"]    short = "-o"  long = "--out"  value = "DIR" (false: no value)',
        "#                        optional_value = true  help = \"...\"  values = \"separate\"  hide = true",
        "#   [positionals.FILE]   help = \"...\"  optional = true  repeatable = true  hide = true",
        "#   [subcommands.build]  help = \"...\"  aliases = [\"b\"]  hide = true",
        "#   [[examples]]         command = \"...\"  help = \"...\"",
        "#   Naming a flag that hptui lumped into another entry (\"--since DATE, --until DATE\")",
        "#   splits it out into its own entry.",
    ]

    def section(title: str, rows: list[tuple[str, str]]) -> None:
        if rows:
            out.extend(["", f"# ---- {title} ----"])
            out.extend(f"# {head}  # {desc}" if desc else f"# {head}" for head, desc in rows)

    section("options", [(f"[options.{_q(o.key)}]", o.spec) for o in options])
    section("positionals", [(f"[positionals.{_q(p.name)}]", p.description[:60]) for p in positionals])
    section("subcommands", [(f"[subcommands.{_q(s.name)}]", s.description[:60]) for s in subcommands])
    if examples:
        out.extend(["", "# ---- examples (listed first, before the ones in the help) ----"])
        for e in examples:
            out.extend(["# [[examples]]", f"# command = {_q(e.command)}"])
    return "\n".join(out) + "\n"
