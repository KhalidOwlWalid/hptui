"""Read and edit a shell command line in place.

The command text is the single source of truth: which options are "checked" and
what their values are is always worked out by scanning the text. Edits are made
by splicing the text so anything else the user typed is left alone.
"""

from __future__ import annotations

import os
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
    style: str  # "flag" | "eq" | "sep" | "attached" | "cluster" | "cluster_value"
    start: int  # span of the whole occurrence (flag + separate value token)
    end: int
    flag_end: int  # end of the flag token
    value: str | None = None
    value_start: int | None = None
    value_end: int | None = None
    cluster_pos: int | None = None  # index of the letter inside a "-abc" cluster


# POSIX shells vs Windows: on Windows backslashes are path separators (only \" is an
# escape) and single quotes are ordinary characters.
POSIX = os.name != "nt"


def _windows_token(text: str, i: int, buf: list[str]) -> int:
    """Read one argument using the MSVCRT rules; returns the index after it.

    Backslashes are literal unless they precede a quote: 2n backslashes + " give n
    backslashes and a quote that opens/closes; 2n+1 give n backslashes and a literal ".
    """
    n, in_quotes = len(text), False
    while i < n and (in_quotes or not text[i].isspace()):
        c = text[i]
        if c == "\\":
            j = i
            while j < n and text[j] == "\\":
                j += 1
            count = j - i
            if j < n and text[j] == '"':
                buf.append("\\" * (count // 2))
                if count % 2:
                    buf.append('"')
                    j += 1
            else:
                buf.append("\\" * count)
            i = j
        elif c == '"':
            in_quotes = not in_quotes
            i += 1
        else:
            buf.append(c)
            i += 1
    return i


def tokenize(text: str, posix: bool | None = None) -> list[Token]:
    """Split like the platform's shell, keeping each token's position. Unclosed quotes run to the end."""
    if posix is None:
        posix = POSIX
    tokens: list[Token] = []
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        start, buf = i, []
        if not posix:
            i = _windows_token(text, i, buf)
            tokens.append(Token(text[start:i], "".join(buf), start, i))
            continue
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


_WIN_SAFE_RE = re.compile(r"[\w@%+=:,./\\~-]+")


def _windows_quote(v: str) -> str:
    """Double-quote using the MSVCRT argv rules: backslashes only matter before a quote."""
    out, backslashes = ['"'], 0
    for ch in v:
        if ch == "\\":
            backslashes += 1
            continue
        out.append("\\" * (backslashes * 2 + 1) + '"' if ch == '"' else "\\" * backslashes + ch)
        backslashes = 0
    out.append("\\" * (backslashes * 2) + '"')
    return "".join(out)


def quote(value: str, posix: bool | None = None) -> str:
    """Quote a value for the shell, preferring double quotes and keeping a leading ~/ expandable."""
    if value == "":
        return '""'
    if not (POSIX if posix is None else posix):
        return value if _WIN_SAFE_RE.fullmatch(value) else _windows_quote(value)
    if _SAFE_RE.fullmatch(value):
        return value
    m = _TILDE_PREFIX_RE.fullmatch(value)
    if m:
        return m.group(1) + _double_quote(m.group(2))
    return _double_quote(value)


@dataclass(frozen=True)
class Scope:
    """The part of a command that belongs to one (sub)command's page.

    For `git -C dir commit -m x`, the git page owns `-C dir` and the commit page
    owns `-m x`. `path` is the subcommand words leading to the page ("commit"),
    `stop` the page's own subcommand names, which end its part of the command.
    """

    path: tuple[str, ...] = ()
    stop: frozenset[str] = frozenset()

    def locate(self, tokens: list[Token]) -> tuple[list[int], int, int]:
        """(indices of the path words, first token of this page, first token after it)."""
        found, i = [], 1  # token 0 is the program
        for word in self.path:
            k = next((k for k in range(i, len(tokens))
                      if tokens[k].value == word and not tokens[k].raw.startswith("-")), None)
            if k is None:  # the user edited the subcommand away: this page owns nothing
                return found, len(tokens), len(tokens)
            found.append(k)
            i = k + 1
        j = i
        while j < len(tokens) and not (tokens[j].value in self.stop and not tokens[j].raw.startswith("-")):
            if tokens[j].value == "--":
                j = len(tokens)
                break
            j += 1
        return found, i, j

    def subcommand_token(self, text: str) -> Token | None:
        """The token naming one of this page's subcommands, if the command has one."""
        tokens = tokenize(text)
        _, _, j = self.locate(tokens)
        return tokens[j] if j < len(tokens) else None


ROOT = Scope()


def _alias_map(options: list[Option]) -> dict[str, Option]:
    amap: dict[str, Option] = {}
    for opt in options:
        for alias in opt.aliases:
            amap.setdefault(alias, opt)
    return amap


def _looks_like_option(tok: Token) -> bool:
    return tok.raw.startswith("-") and len(tok.raw) > 1


def find_occurrences(text: str, options: list[Option], scope: Scope = ROOT) -> list[Occurrence]:
    """Find every known option in the part of the command that `scope` covers
    (by default everything after the program)."""
    amap = _alias_map(options)
    tokens = tokenize(text)
    _, i, end = scope.locate(tokens)
    tokens = tokens[:end]
    found: list[Occurrence] = []
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
            if tok.raw != t:
                continue  # quoted text, not a cluster of flags
            # getopt rules: -abc == -a -b -c, and a letter that takes a value
            # consumes the rest of the token (-tvfFILE) or the next one (-tvf FILE).
            cluster: list[Occurrence] = []
            for k in range(1, len(t)):
                o = amap.get("-" + t[k])
                if o is None:
                    cluster = []
                    break
                pos = tok.start + k
                if o.takes_value and not o.optional_value:
                    if k + 1 < len(t):
                        cluster.append(Occurrence(o, "cluster_value", tok.start, tok.end, tok.end,
                                                  t[k + 1:], pos + 1, tok.end, cluster_pos=pos))
                    elif i < len(tokens) and not _looks_like_option(tokens[i]):
                        val = tokens[i]
                        i += 1
                        cluster.append(Occurrence(o, "cluster_value", tok.start, val.end, tok.end,
                                                  val.value, val.start, val.end, cluster_pos=pos))
                    else:
                        cluster.append(Occurrence(o, "cluster_value", tok.start, tok.end, tok.end, cluster_pos=pos))
                    break
                cluster.append(Occurrence(o, "cluster", tok.start, tok.end, tok.end, cluster_pos=pos))
            found.extend(cluster)
    return found


def occurrences_by_key(text: str, options: list[Option], scope: Scope = ROOT) -> dict[str, Occurrence]:
    """First occurrence of each option, in the order they appear."""
    result: dict[str, Occurrence] = {}
    for occ in find_occurrences(text, options, scope):
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


def add_option(text: str, opt: Option, scope: Scope = ROOT, before: int | None = None) -> str:
    """Add an option at the end of the scope's part of the command (before any subcommand, or
    before position `before`, e.g. the first positional argument).
    Options that need a value get "--name=" (or "-n " for short-only options)."""
    flag = opt.long or opt.short or opt.spec
    piece = flag + "=" if opt.takes_value and not opt.optional_value and flag.startswith("--") else flag
    if before is not None:  # `hptui git commit` + -c -> `hptui -c git commit`
        return text[:before] + piece + " " + text[before:]
    sub = scope.subcommand_token(text)
    if sub is not None:  # `git commit` + --no-pager -> `git --no-pager commit`
        return text[:sub.start] + piece + " " + text[sub.start:]
    return _append(text, piece)


def remove_option(text: str, opt: Option, options: list[Option] | None = None, scope: Scope = ROOT) -> str:
    """Remove every occurrence of an option (and its value)."""
    occs = [o for o in find_occurrences(text, options or [opt], scope) if o.option is opt]
    for occ in sorted(occs, key=lambda o: (o.start, o.cluster_pos or 0), reverse=True):
        if occ.style == "cluster_value" and occ.value_start is not None:
            # drop the value first: it sits to the right, so the letter's position is unchanged
            if occ.value_start >= occ.flag_end:
                text = _cut(text, occ.value_start, occ.value_end)
            else:
                text = _splice(text, occ.value_start, occ.value_end, "")
        if occ.style in ("cluster", "cluster_value"):
            text = _splice(text, occ.cluster_pos, occ.cluster_pos + 1, "")
            token_end = occ.start + 1
            if token_end >= len(text) or text[token_end].isspace():
                text = _cut(text, occ.start, token_end)  # only a bare "-" is left
        else:
            text = _cut(text, occ.start, occ.end)
    return text


def set_value(text: str, opt: Option, value: str, options: list[Option] | None = None,
              scope: Scope = ROOT) -> str:
    """Set an option's value, adding the option if it is not in the command yet."""
    occ = occurrences_by_key(text, options or [opt], scope).get(opt.key)
    if occ is None:
        text = add_option(text, opt, scope)
        occ = occurrences_by_key(text, options or [opt], scope).get(opt.key)
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

    if occ.style == "cluster_value":
        if occ.value_start is None:
            return text if value == "" else _splice(text, occ.flag_end, occ.flag_end, " " + quote(value))
        if occ.value_start >= occ.flag_end:  # separate token: -tvf FILE
            if value == "":
                return _splice(text, occ.flag_end, occ.value_end, "")
            return _splice(text, occ.value_start, occ.value_end, quote(value))
        return _splice(text, occ.value_start, occ.value_end, quote(value) if value else "")  # -tvfFILE

    if occ.style == "attached":
        return _splice(text, occ.value_start, occ.value_end, quote(value) if value else "")

    if occ.style == "flag" and value != "":
        flag = text[occ.start:occ.end]
        if flag.startswith("--"):
            return _splice(text, occ.start, occ.end, f"{flag}={quote(value)}")
        return _splice(text, occ.start, occ.end, f"{flag}{quote(value)}")

    return text


# ---- positional arguments -------------------------------------------------------------

_STOP_TOKENS = {"|", "||", "&&", ";", "&", ">", ">>", "<", "2>", "2>&1", "&>", "|&"}


def positional_tokens(text: str, options: list[Option], scope: Scope = ROOT) -> list[Token]:
    """Words in the scope's part of the command that aren't options or option values."""
    tokens = tokenize(text)
    _, i, j = scope.locate(tokens)
    spans = [(o.start, o.end) for o in find_occurrences(text, options, scope)]
    out, after_dashes = [], False
    for tok in tokens[i:j]:
        if tok.raw in _STOP_TOKENS:
            break  # `cmd args | less`: the rest belongs to another program
        if tok.value == "--" and not after_dashes:
            after_dashes = True
            continue
        if any(a <= tok.start < b for a, b in spans):
            continue
        if not after_dashes and tok.raw.startswith("-") and len(tok.raw) > 1:
            continue  # an option hptui doesn't know
        out.append(tok)
    return out


def positional_spans(text: str, positionals: list, options: list[Option],
                     scope: Scope = ROOT) -> dict[str, list[Token]]:
    """Which words belong to which positional: in order, a repeatable one takes the rest."""
    toks = positional_tokens(text, options, scope)
    result: dict[str, list[Token]] = {}
    k = 0
    for p in positionals:
        if k >= len(toks):
            break
        if p.repeatable:
            result[p.key] = toks[k:]
            break
        result[p.key] = [toks[k]]
        k += 1
    return result


def positional_value(text: str, toks: list[Token], repeatable: bool) -> str:
    """What the value box shows: the word, or for a repeatable one the words as typed."""
    if not toks:
        return ""
    return text[toks[0].start:toks[-1].end] if repeatable else toks[0].value


def set_positional(text: str, p, value: str, positionals: list, options: list[Option],
                   scope: Scope = ROOT) -> str:
    """Set (or with "" remove) a positional's value. A repeatable one's value is used as typed,
    so several words can be given; a single one is quoted."""
    toks = positional_spans(text, positionals, options, scope).get(p.key)
    piece = value.strip() if p.repeatable else (quote(value) if value else "")
    if toks:
        if not piece:
            return _cut(text, toks[0].start, toks[-1].end)
        return _splice(text, toks[0].start, toks[-1].end, piece)
    if not piece:
        return text
    sub = scope.subcommand_token(text)
    if sub is not None:
        return text[:sub.start] + piece + " " + text[sub.start:]
    return _append(text.rstrip(), piece)
