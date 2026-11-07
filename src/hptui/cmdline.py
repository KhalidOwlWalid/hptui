"""Read and edit a shell command line in place.

The command text is the single source of truth: which options are "checked" and
what their values are is always worked out by scanning the text. Edits are made
by splicing the text so anything else the user typed is left alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .parser import Option


@dataclass
class Token:
    raw: str  # text as written, quotes included
    value: str  # text after shell unquoting
    start: int
    end: int


@dataclass
class Occurrence:
    option: Option
    style: str  # "flag" | "eq" | "sep" | "attached" | "cluster"
    start: int  # span of the whole occurrence (flag + separate value token)
    end: int
    flag_end: int  # end of the flag token
    value: str | None = None
    value_start: int | None = None
    value_end: int | None = None
    cluster_pos: int | None = None  # index of the letter inside a "-abc" cluster


def tokenize(text: str) -> list[Token]:
    """Split like a POSIX shell, keeping each token's position. Unclosed quotes run to the end."""
    tokens: list[Token] = []
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        start, buf = i, []
        while i < n and not text[i].isspace():
            c = text[i]
            if c == "'":
                i += 1
                while i < n and text[i] != "'":
                    buf.append(text[i])
                    i += 1
                i = min(i + 1, n)
            elif c == '"':
                i += 1
                while i < n and text[i] != '"':
                    if text[i] == "\\" and i + 1 < n and text[i + 1] in '\\"$`':
                        buf.append(text[i + 1])
                        i += 2
                    else:
                        buf.append(text[i])
                        i += 1
                i = min(i + 1, n)
            elif c == "\\" and i + 1 < n:
                buf.append(text[i + 1])
                i += 2
            else:
                buf.append(c)
                i += 1
        tokens.append(Token(text[start:i], "".join(buf), start, i))
    return tokens


_SAFE_RE = re.compile(r"~?[\w@%+=:,./-]+")
_TILDE_PREFIX_RE = re.compile(r"(~[\w.-]*/)(.+)", re.S)


def _double_quote(v: str) -> str:
    if "!" in v:  # "!" triggers history expansion inside double quotes in bash
        return "'" + v.replace("'", "'\\''") + "'"
    return '"' + re.sub(r'([\\"$`])', r"\\\1", v) + '"'


def quote(value: str) -> str:
    """Quote a value for the shell, preferring double quotes and keeping a leading ~/ expandable."""
    if value == "":
        return '""'
    if _SAFE_RE.fullmatch(value):
        return value
    m = _TILDE_PREFIX_RE.fullmatch(value)
    if m:
        return m.group(1) + _double_quote(m.group(2))
    return _double_quote(value)


def _alias_map(options: list[Option]) -> dict[str, Option]:
    amap: dict[str, Option] = {}
    for opt in options:
        for alias in opt.aliases:
            amap.setdefault(alias, opt)
    return amap


def _looks_like_option(tok: Token) -> bool:
    return tok.raw.startswith("-") and len(tok.raw) > 1


def find_occurrences(text: str, options: list[Option]) -> list[Occurrence]:
    """Find every known option in the command. The first token (the program) is skipped."""
    amap = _alias_map(options)
    tokens = tokenize(text)
    found: list[Occurrence] = []
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        t = tok.value
        i += 1
        if t == "--":
            break  # everything after "--" is positional
        if not tok.raw.startswith("-") or len(t) < 2:
            continue

        if "=" in t and t.split("=", 1)[0] in amap:
            name, value = t.split("=", 1)
            eq = tok.raw.find("=")
            found.append(Occurrence(
                amap[name], "eq", tok.start, tok.end, tok.end, value,
                tok.start + eq + 1, tok.end,
            ))
            continue

        if t in amap:
            opt = amap[t]
            if opt.takes_value and not opt.optional_value:
                if i < len(tokens) and not _looks_like_option(tokens[i]):
                    val = tokens[i]
                    i += 1
                    found.append(Occurrence(
                        opt, "sep", tok.start, val.end, tok.end, val.value, val.start, val.end,
                    ))
                else:
                    found.append(Occurrence(opt, "sep", tok.start, tok.end, tok.end))
            else:
                found.append(Occurrence(opt, "flag", tok.start, tok.end, tok.end))
            continue

        if not t.startswith("--") and len(t) > 2:
            head = t[:2]
            opt = amap.get(head)
            if opt is not None and opt.takes_value and tok.raw.startswith(head):
                # -sVALUE
                found.append(Occurrence(
                    opt, "attached", tok.start, tok.end, tok.end, t[2:], tok.start + 2, tok.end,
                ))
                continue
            letters = [amap.get("-" + c) for c in t[1:]]
            if tok.raw == t and all(o is not None and not o.takes_value for o in letters):
                # -abc  ==  -a -b -c
                for k, o in enumerate(letters, start=1):
                    found.append(Occurrence(
                        o, "cluster", tok.start, tok.end, tok.end, cluster_pos=tok.start + k,
                    ))
    return found


def occurrences_by_key(text: str, options: list[Option]) -> dict[str, Occurrence]:
    """First occurrence of each option, in the order they appear."""
    result: dict[str, Occurrence] = {}
    for occ in find_occurrences(text, options):
        result.setdefault(occ.option.key, occ)
    return result


def _splice(text: str, start: int, end: int, new: str) -> str:
    return text[:start] + new + text[end:]


def _cut(text: str, start: int, end: int) -> str:
    """Remove text[start:end] together with the whitespace that separated it."""
    after = end
    while after < len(text) and text[after] == " ":
        after += 1
    if after < len(text):
        return text[:start] + text[after:]
    before = start
    while before > 0 and text[before - 1] == " ":
        before -= 1
    return text[:before] + text[after:]


def _append(text: str, piece: str) -> str:
    if text and not text.endswith(" "):
        text += " "
    return text + piece


def add_option(text: str, opt: Option) -> str:
    """Append an option. Options that need a value get "--name=" (or "-n " for short-only options)."""
    flag = opt.long or opt.short or opt.spec
    if opt.takes_value and not opt.optional_value and flag.startswith("--"):
        return _append(text, flag + "=")
    return _append(text, flag)


def remove_option(text: str, opt: Option, options: list[Option] | None = None) -> str:
    """Remove every occurrence of an option (and its value)."""
    occs = [o for o in find_occurrences(text, options or [opt]) if o.option is opt]
    for occ in sorted(occs, key=lambda o: (o.start, o.cluster_pos or 0), reverse=True):
        if occ.style == "cluster":
            text = _splice(text, occ.cluster_pos, occ.cluster_pos + 1, "")
            token_end = occ.start + 1
            if token_end >= len(text) or text[token_end].isspace():
                text = _cut(text, occ.start, token_end)  # only a bare "-" is left
        else:
            text = _cut(text, occ.start, occ.end)
    return text


def set_value(text: str, opt: Option, value: str, options: list[Option] | None = None) -> str:
    """Set an option's value, adding the option if it is not in the command yet."""
    occ = occurrences_by_key(text, options or [opt]).get(opt.key)
    if occ is None:
        text = add_option(text, opt)
        occ = occurrences_by_key(text, options or [opt]).get(opt.key)
        assert occ is not None

    if occ.style == "eq":
        if value == "" and opt.optional_value:
            return _splice(text, occ.value_start - 1, occ.value_end, "")
        return _splice(text, occ.value_start, occ.value_end, quote(value) if value else "")

    if occ.style == "sep":
        if occ.value_start is not None:
            if value == "":
                return _splice(text, occ.flag_end, occ.value_end, "")
            return _splice(text, occ.value_start, occ.value_end, quote(value))
        if value == "":
            return text
        return _splice(text, occ.flag_end, occ.flag_end, " " + quote(value))

    if occ.style == "attached":
        return _splice(text, occ.value_start, occ.value_end, quote(value) if value else "")

    if occ.style == "flag" and value != "":
        flag = text[occ.start:occ.end]
        if flag.startswith("--"):
            return _splice(text, occ.start, occ.end, f"{flag}={quote(value)}")
        return _splice(text, occ.start, occ.end, f"{flag}{quote(value)}")

    return text
