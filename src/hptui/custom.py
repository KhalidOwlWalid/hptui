"""Custom parsers: a program's help text, pasted into a file, plus simple markers.

    usage: mytool [-h] [FILE ...]
    ...the help, pasted exactly...
    --- hptui ---
    positionals: after "positional arguments:" before "options:"
    options: after "options:"
    ignore: --bogus
    values: separate

Everything above the `--- hptui ---` line is the help text hptui uses for that
program (instead of running `--help`). Each marker line says where one kind of
entry lives; inside a marked section every entry is read as that kind. Parts
that aren't marked are parsed as usual. `values: separate` is for programs that only take
"--name VALUE" (not "--name=VALUE" or "-nVALUE"). Installed files live in
<config dir>/parsers/<program>.txt ("git commit.txt" for a subcommand page).

A <program>.toml file (see overrides.py) is the other kind: it keeps reading the live help and
only corrects the entries it names. If both are installed for a page, the .toml one is used.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import compat, overrides
from .overrides import Overrides, SpecError
from .parser import (
    _INLINE_COMMENT_RE, _PROMPT_RE, Example, Option, Positional, Subcommand, apply_usage_hints,
    builtin_positionals, parse_examples, parse_help, parse_positionals, parse_subcommands, section_end,
)

SEPARATOR_RE = re.compile(r"^\s*-{3,}\s*hptui\s*-{3,}\s*$", re.IGNORECASE)
SEPARATOR = "--- hptui ---"
KINDS = ("options", "positionals", "subcommands", "examples")
VALUE_STYLES = ("separate", "any")  # `values:`; "any" (the default) writes --name=VALUE
SUFFIX = ".txt"  # marker files
TOML = ".toml"  # override files
SUFFIXES = (TOML, SUFFIX)  # the order lookups try them in
_MARKER_RE = re.compile(
    r"""^(?P<kind>\w+)\s*:\s*
        after\s+(?:"(?P<after_q>[^"]*)"|(?P<after>.+?))
        (?:\s+before\s+(?:"(?P<before_q>[^"]*)"|(?P<before>.+?)))?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)

Parsed = tuple[list[Option], list[Subcommand], list[Example], list[Positional]]


@dataclass
class Section:
    kind: str  # one of KINDS
    after: str  # heading text the section starts after
    before: str | None = None  # heading text it ends at (default: the end of that heading's block)
    line: int = 0  # line number of the marker, for messages


@dataclass
class Spec:
    words: list[str]  # the program and subcommand path the file is for, e.g. ["git", "commit"]
    text: str  # the pasted help
    sections: list[Section] = field(default_factory=list)
    ignore: set[str] = field(default_factory=set)
    source: Path | None = None
    values: str = "any"  # one of VALUE_STYLES

    @property
    def name(self) -> str:
        return " ".join(self.words)


# ---- reading a file ------------------------------------------------------------------

def parse_file(content: str, where: str = "parser file"
               ) -> tuple[str, list[Section], set[str], str | None, str]:
    """Split a parser file into (help text, sections, ignore, program, values). Raises SpecError."""
    lines = content.splitlines()
    sep = next((i for i, line in enumerate(lines) if SEPARATOR_RE.match(line)), None)
    if sep is None:
        return content, [], set(), None, "any"
    text = "\n".join(lines[:sep])
    sections, ignore, program, values, errors = [], set(), None, "any", []
    for n in range(sep + 1, len(lines)):
        line = lines[n].strip()
        if not line or line.startswith("#"):
            continue
        label = f"line {n + 1}"
        key, _, value = line.partition(":")
        key = key.strip().lower()
        if key == "ignore":
            ignore |= {w for w in re.split(r"[\s,]+", value) if w}
            continue
        if key == "program":
            program = value.strip() or None
            continue
        if key == "values":
            values = value.strip().lower()
            if values not in VALUE_STYLES:
                errors.append(f'{label}: values: must be one of {", ".join(VALUE_STYLES)}')
            continue
        m = _MARKER_RE.match(line)
        if key not in KINDS:
            errors.append(f'{label}: unknown "{key}:"; use one of {", ".join(KINDS)}, ignore, program, values')
        elif m is None:
            errors.append(f'{label}: expected {key}: after "<heading>" [before "<heading>"]')
        else:
            after = (m.group("after_q") if m.group("after_q") is not None else m.group("after")).strip()
            before = m.group("before_q") if m.group("before_q") is not None else m.group("before")
            sections.append(Section(key, after, before.strip() if before else None, n + 1))
    help_lines = text.splitlines()
    for sec in sections:
        try:
            locate(help_lines, sec)
        except SpecError as e:
            errors.append(f"line {sec.line}: {e}")
    if errors:
        raise SpecError(f"{where}:\n" + "\n".join(f"  - {e}" for e in errors))
    return text, sections, ignore, program, values


def _find_heading(lines: list[str], heading: str, start: int) -> int | None:
    want = heading.strip().lower()
    for i in range(start, len(lines)):
        if lines[i].strip().lower().startswith(want):
            return i
    return None


def locate(lines: list[str], sec: Section) -> tuple[int, int]:
    """(first line, line after the last) of a marked section in the help text."""
    start = _find_heading(lines, sec.after, 0)
    if start is None:
        raise SpecError(f'{sec.kind}: no line starts with "{sec.after}" in the pasted help')
    if sec.before is None:
        return start + 1, section_end(lines, start)
    end = _find_heading(lines, sec.before, start + 1)
    if end is None:
        raise SpecError(f'{sec.kind}: no line starts with "{sec.before}" after "{sec.after}"')
    return start + 1, end


def load_file(path: Path, words: list[str] | None = None) -> Spec | Overrides:
    """A parser file: a .toml one is an Overrides, anything else a marker Spec. Raises SpecError."""
    path = Path(path)
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as e:
        raise SpecError(f"{path}: {e.strerror or e}")
    if path.suffix.lower() == TOML:
        ov, program = overrides.parse_file(content, str(path))
        if words is None:
            from .parser import guess_command

            words = (program or (guess_command(ov.help) if ov.help else None) or path.stem).split()
        ov.words, ov.source = [compat.program_name(words[0]), *words[1:]], path
        return ov
    text, sections, ignore, program, values = parse_file(content, str(path))
    if words is None:
        from .parser import guess_command

        words = (program or guess_command(text) or path.stem).split()
    return Spec([compat.program_name(words[0]), *words[1:]], text, sections, ignore, path, values)


# ---- where files live -------------------------------------------------------------------

def parsers_dir() -> Path:
    return Path(os.environ.get("HPTUI_PARSERS") or compat.config_dir() / "parsers")


def file_for(words: list[str], suffix: str = SUFFIX) -> Path:
    return parsers_dir() / (" ".join([compat.program_name(words[0]), *words[1:]]) + suffix)


_cache: dict[str, Spec | Overrides | None] = {}


def load(words: list[str]) -> Spec | Overrides | None:
    """The installed parser for a program (or subcommand page), or None."""
    if not words or not words[0]:
        return None
    key = str(file_for(words, ""))
    if key not in _cache:
        path = next((p for p in (file_for(words, s) for s in SUFFIXES) if p.is_file()), None)
        _cache[key] = load_file(path, words) if path is not None else None
    return _cache[key]


def clear_cache() -> None:
    _cache.clear()


def installed() -> list[Path]:
    folder = parsers_dir()
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix in SUFFIXES and p.is_file())


def install(source: Path, words: list[str] | None = None) -> tuple[Spec | Overrides, Path, bool]:
    """Check a parser file and copy it into the parsers folder, replacing any file of the other
    kind for the same page. Returns (spec, destination, replaced)."""
    spec = load_file(source, words)
    suffix = TOML if isinstance(spec, Overrides) else SUFFIX
    dest = file_for(spec.words, suffix)
    dest.parent.mkdir(parents=True, exist_ok=True)
    replaced = dest.exists()
    if Path(source).resolve() != dest.resolve():
        shutil.copyfile(source, dest)
    for other in SUFFIXES:
        old = file_for(spec.words, other)
        if other != suffix and old.is_file():
            old.unlink()
            replaced = True
    clear_cache()
    return spec, dest, replaced


def remove(words: list[str]) -> Path | None:
    """Delete the installed parser files for a page; returns the one that was in use, or None."""
    paths = [p for p in (file_for(words, s) for s in SUFFIXES) if p.is_file()]
    for path in paths:
        path.unlink()
    clear_cache()
    return paths[0] if paths else None


# ---- parsing with markers -------------------------------------------------------------------

def builtin(text: str, program: str | None) -> Parsed:
    return parse_help(text), parse_subcommands(text), parse_examples(text, program), builtin_positionals(text)


def _forced_subcommands(text: str) -> list[Subcommand]:
    """Every "name  description" entry, no "Commands:" heading needed."""
    body = "\n".join("  " + line for line in text.splitlines())
    return parse_subcommands("Commands:\n" + body)


def _forced_examples(text: str) -> list[Example]:
    """Every line is an example command; "# ..." lines and lines ending in ":" describe the next one."""
    found, pending = [], ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            pending = ""
        elif line.startswith("#"):
            pending = line.lstrip("#").strip()
        elif line.endswith(":") and " " in line:
            pending = line[:-1].strip()
        else:
            command = _PROMPT_RE.sub("", line)
            parts = _INLINE_COMMENT_RE.split(command, maxsplit=1)
            description = parts[1].strip() if len(parts) == 2 else pending
            found.append(Example(parts[0].rstrip(), description))
            pending = ""
    return found


def parse_spec(spec: Spec, program: str | None = None) -> Parsed:
    """Options, subcommands, examples and positionals from a parser file's help text."""
    lines = spec.text.expandtabs(8).splitlines()
    marked: dict[str, list[str]] = {k: [] for k in KINDS}
    used = [False] * len(lines)
    for sec in spec.sections:
        start, end = locate(lines, sec)
        marked[sec.kind].append("\n".join(lines[start:end]))
        for i in range(start - 1, end):  # the heading belongs to its section too
            used[i] = True
    # what isn't marked is parsed as usual (marked lines are blanked, keeping the layout)
    rest = "\n".join("" if used[i] else line for i, line in enumerate(lines))
    options, subcommands, examples, positionals = builtin(rest, program or spec.words[0])
    def marked_first(kind, parse, names, found):
        """Entries of every `kind` marker, in file order, then the rest of what was found."""
        ours: list = []
        for block in marked[kind]:
            ours = _merge(ours, parse(block), names)
        return _merge(ours, found, names)

    options = marked_first("options", parse_help, lambda o: set(o.aliases), options)
    subcommands = marked_first("subcommands", _forced_subcommands, lambda s: set(s.names), subcommands)
    examples = marked_first("examples", _forced_examples, lambda e: {e.command}, examples)
    positionals = marked_first("positionals", lambda b: apply_usage_hints(parse_positionals(b), spec.text),
                               lambda p: {p.name}, positionals)
    if spec.ignore:
        ig = spec.ignore
        options = [o for o in options if not ig & set(o.aliases)]
        subcommands = [s for s in subcommands if not ig & set(s.names)]
        examples = [e for e in examples if e.command not in ig]
        positionals = [p for p in positionals if p.name not in ig and p.label not in ig]
    if spec.values == "separate":
        for o in options:
            o.separate_value = True
    return options, subcommands, examples, positionals


def _merge(first: list, rest: list, names) -> list:
    """`first`, then the items of `rest` that don't share a name with any of them."""
    taken = set().union(*(names(i) for i in first)) if first else set()
    return [*first, *(i for i in rest if not names(i) & taken)]


def parse_overrides(ov: Overrides, text: str, program: str | None = None,
                    report: list[str] | None = None) -> Parsed:
    """The page's help (the file's own `help`, if it has one), parsed as usual, then corrected."""
    source = ov.help if ov.help is not None else text
    return overrides.apply(ov, builtin(source, program or ov.words[0]), report)


def parse_page(text: str, program: str | None, names: list[str] | None = None) -> Parsed:
    """Parse a page's help. If a marker file is installed for it, its pasted help and markers win;
    an override file corrects what's parsed from `text`."""
    spec = load([program, *(names or [])]) if program else None
    if isinstance(spec, Overrides):
        return parse_overrides(spec, text, program)
    if spec is not None:
        return parse_spec(spec, program)
    return builtin(text, program)


def help_text(program: str | None, names: list[str] | None = None) -> str | None:
    """The help to use for a page instead of running the program, if a parser file has one."""
    spec = load([program, *(names or [])]) if program else None
    if isinstance(spec, Overrides):
        return spec.help
    return spec.text if spec is not None else None


# ---- `hptui parser` output ----------------------------------------------------------------------

def template(help_text: str, words: list[str]) -> str:
    """A parser file for --init: the help as it is now, plus commented markers to fill in."""
    return (help_text.rstrip("\n") + "\n" + SEPARATOR + "\n"
            f"program: {' '.join([compat.program_name(words[0]), *words[1:]])}\n"
            "# Everything above this line is the help hptui uses for this program; edit it freely.\n"
            "# Mark where each kind of entry is (headings match by how the line starts, any case):\n"
            '#   options: after "options:"\n'
            '#   positionals: after "positional arguments:" before "options:"\n'
            '#   subcommands: after "Commands"\n'
            '#   examples: after "Examples"\n'
            "# Hide wrongly found entries:\n"
            "#   ignore: --bogus, help\n"
            "# If the program rejects --name=VALUE and wants --name VALUE:\n"
            "#   values: separate\n")


def summary(spec: Spec | Overrides, live_help: str = "") -> list[str]:
    """What a parser file gives. An override file is applied to `live_help` (the program's
    current help), and entries in it that matched nothing there are listed."""
    report: list[str] = []
    if isinstance(spec, Overrides):
        options, subcommands, examples, positionals = parse_overrides(spec, live_help, report=report)
    else:
        options, subcommands, examples, positionals = parse_spec(spec)
    lines = [f"{spec.name}: {len(options)} options, {len(positionals)} positionals, "
             f"{len(subcommands)} subcommands, {len(examples)} examples"]

    def show(title, names):
        if names:
            shown = ", ".join(names[:12]) + (f", ... ({len(names) - 12} more)" if len(names) > 12 else "")
            lines.append(f"  {title}: {shown}")

    show("options", [o.spec for o in options])
    show("positionals", [p.name + (" ..." if p.repeatable else "") for p in positionals])
    show("subcommands", [s.name for s in subcommands])
    show("examples", [e.command for e in examples])
    if spec.values == "separate":
        lines.append("  values: separate (written as --name VALUE)")
    if isinstance(spec, Overrides):
        if not spec.from_help:
            lines.append("  from_help: false (only the entries in the file)")
        elif spec.help is None and not live_help.strip():
            lines.append(f"  warning: no help text to correct (`{spec.name} --help` printed nothing)")
        lines.extend(f"  note: {msg}" for msg in report)
        return lines
    for sec in spec.sections:
        start, end = locate(spec.text.expandtabs(8).splitlines(), sec)
        span = f'after "{sec.after}"' + (f' before "{sec.before}"' if sec.before else "")
        lines.append(f"  {sec.kind} {span}: help lines {start + 1}-{end}")
    return lines
