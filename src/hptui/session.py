"""Frontend-independent TUI state and key handling.

Keys use the curses vocabulary (curses.KEY_* constants and characters) so the
curses frontend can pass them straight through; other frontends translate.
"""

from __future__ import annotations

import curses
import os
import re
import textwrap
from dataclasses import dataclass, field

from . import cmdline, history
from .parser import Option

# Raw-mode control characters.
CTRL_A, CTRL_C, CTRL_D, CTRL_E, CTRL_K, CTRL_N = "\x01", "\x03", "\x04", "\x05", "\x0b", "\x0e"
CTRL_P, CTRL_R, CTRL_S, CTRL_U, CTRL_W = "\x10", "\x12", "\x13", "\x15", "\x17"
ESC = "\x1b"
ENTER_KEYS = ("\n", "\r", curses.KEY_ENTER)
BACKSPACE_KEYS = ("\x7f", "\x08", curses.KEY_BACKSPACE)


@dataclass
class LineEdit:
    """A single-line text input with a cursor and horizontal scrolling."""

    text: str = ""
    cursor: int = 0
    scroll: int = 0
    dirty: bool = False  # edited since last commit

    def set(self, text: str, cursor_to_end: bool = True) -> None:
        self.text = text
        if cursor_to_end or self.cursor > len(text):
            self.cursor = len(text)

    def handle(self, key) -> bool:
        """Apply an editing key. Returns True if the key was used."""
        t, c = self.text, self.cursor
        if isinstance(key, str) and key.isprintable():
            self.text = t[:c] + key + t[c:]
            self.cursor += len(key)
        elif key in BACKSPACE_KEYS:
            if c > 0:
                self.text = t[:c - 1] + t[c:]
                self.cursor -= 1
        elif key == curses.KEY_DC:
            self.text = t[:c] + t[c + 1:]
        elif key == curses.KEY_LEFT:
            self.cursor = max(0, c - 1)
            return True
        elif key == curses.KEY_RIGHT:
            self.cursor = min(len(t), c + 1)
            return True
        elif key in (curses.KEY_HOME, CTRL_A):
            self.cursor = 0
            return True
        elif key in (curses.KEY_END, CTRL_E):
            self.cursor = len(t)
            return True
        elif key == CTRL_U:
            self.text, self.cursor = t[c:], 0
        elif key == CTRL_K:
            self.text = t[:c]
        elif key == CTRL_W:
            i = c
            while i > 0 and t[i - 1] == " ":
                i -= 1
            while i > 0 and t[i - 1] != " ":
                i -= 1
            self.text, self.cursor = t[:i] + t[c:], i
        else:
            return False
        self.dirty = True
        return True

    def visible(self, width: int) -> tuple[str, int]:
        """Text slice to draw and the cursor column inside it."""
        width = max(1, width)
        if self.cursor < self.scroll:
            self.scroll = self.cursor
        elif self.cursor >= self.scroll + width:
            self.scroll = self.cursor - width + 1
        return self.text[self.scroll:self.scroll + width], self.cursor - self.scroll


@dataclass
class Session:
    options: list[Option]
    cmd: LineEdit
    fields: dict[str, LineEdit] = field(default_factory=dict)
    focus: str = "list"  # "cmd" | "list" | "field:<option key>"
    list_index: int = 0
    list_scroll: int = 0
    query: str = ""
    searching: bool = False  # "/" search mode in the option list
    message: str = ""
    result: str | None = None
    done: bool = False
    # history panel
    program: str = ""  # history is filtered to this program ("" = all programs)
    standalone: bool = False  # `hptui --history`: no help page, panel is the whole app
    hist_open: bool = False
    hist_entries: list[history.Entry] = field(default_factory=list)
    hist_index: int = 0
    hist_scroll: int = 0
    hist_query: str = ""
    hist_searching: bool = False
    # "?" key reference
    help_open: bool = False
    help_scroll: int = 0
    edit_request: str | None = None  # standalone: command the user wants to edit

    # ---- derived state -------------------------------------------------

    def occurrences(self) -> dict[str, cmdline.Occurrence]:
        return cmdline.occurrences_by_key(self.cmd.text, self.options)

    def value_options(self) -> list[Option]:
        """Checked options that take a value, in command order. Each gets an input box."""
        return [o.option for o in self.occurrences().values() if o.option.takes_value]

    def filtered(self) -> list[Option]:
        q = self.query.lower()
        if not q:
            return self.options
        return [o for o in self.options if q in f"{o.spec} {o.description}".lower()]

    def sync_fields(self) -> None:
        """Refresh value boxes from the command text (except the one being edited)."""
        occs = self.occurrences()
        for opt in self.value_options():
            box = self.fields.setdefault(opt.key, LineEdit())
            if self.focus != f"field:{opt.key}":
                box.set(occs[opt.key].value or "")
                box.dirty = False
        if self.focus.startswith("field:") and self.focus[6:] not in occs:
            self.focus = "list"

    def focus_order(self) -> list[str]:
        return ["cmd"] + [f"field:{o.key}" for o in self.value_options()] + ["list"]

    def option_by_key(self, key: str) -> Option:
        return next(o for o in self.options if o.key == key)

    # ---- actions ---------------------------------------------------------

    def commit_field(self) -> None:
        if not self.focus.startswith("field:"):
            return
        key = self.focus[6:]
        box = self.fields.get(key)
        if box is not None and box.dirty:
            self.cmd.set(cmdline.set_value(self.cmd.text, self.option_by_key(key), box.text, self.options))
            box.dirty = False

    def move_focus(self, step: int) -> None:
        self.commit_field()
        self.searching = False
        order = self.focus_order()
        idx = order.index(self.focus) if self.focus in order else len(order) - 1
        self.focus = order[(idx + step) % len(order)]
        self.sync_fields()

    def toggle(self, opt: Option, jump_to_field: bool) -> None:
        if opt.key in self.occurrences():
            self.cmd.set(cmdline.remove_option(self.cmd.text, opt, self.options))
        else:
            self.cmd.set(cmdline.add_option(self.cmd.text, opt))
            if opt.takes_value and jump_to_field:
                self.fields[opt.key] = LineEdit()
                self.focus = f"field:{opt.key}"
        self.sync_fields()

    def toggle_command_box(self) -> None:
        """Jump between the command box and the option list (skipping value boxes)."""
        self.commit_field()
        self.searching = False
        self.focus = "list" if self.focus == "cmd" else "cmd"
        self.sync_fields()

    def accept(self) -> None:
        self.commit_field()
        self.result = self.cmd.text.strip()
        self.done = True

    def cancel(self) -> None:
        self.result = None
        self.done = True

    # ---- key handling ----------------------------------------------------

    def handle(self, key) -> None:
        self.message = ""
        if key == CTRL_C:
            return self.cancel()
        if self.help_open:
            return self.handle_help(key)
        if self.hist_open:
            return self.handle_history(key)
        if key == CTRL_S:
            return self.accept()
        if key == CTRL_R:
            return self.open_history()
        if key == CTRL_P:
            return self.toggle_command_box()
        if key == "\t":
            return self.move_focus(1)
        if key == curses.KEY_BTAB:
            return self.move_focus(-1)

        if self.focus == "cmd":
            self.handle_cmd(key)
        elif self.focus == "list":
            self.handle_list(key)
        else:
            self.handle_field(key)

    def handle_cmd(self, key) -> None:
        if key in ENTER_KEYS:
            return self.accept()
        if key == ESC:
            self.focus = "list"
            return
        if key == curses.KEY_DOWN:
            return self.move_focus(1)
        if key == curses.KEY_UP:
            return self.move_focus(-1)
        if self.cmd.handle(key):
            self.sync_fields()

    def handle_field(self, key) -> None:
        box = self.fields[self.focus[6:]]
        if key in ENTER_KEYS:
            self.commit_field()
            self.focus = "list"
            self.sync_fields()
        elif key == ESC:  # throw away the edit
            box.dirty = False
            self.focus = "list"
            self.sync_fields()
        elif key == curses.KEY_DOWN:
            self.move_focus(1)
        elif key == curses.KEY_UP:
            self.move_focus(-1)
        else:
            box.handle(key)

    def _move_cursor(self, key, n: int) -> bool:
        """Cursor movement shared by normal and search mode. Returns True if handled."""
        half = 10
        if key == curses.KEY_DOWN:
            self.list_index = min(n - 1, self.list_index + 1)
        elif key == curses.KEY_UP:
            if self.list_index == 0 and not self.searching:
                self.move_focus(-1)
            self.list_index = max(0, self.list_index - 1)
        elif key in (curses.KEY_NPAGE, CTRL_D):
            self.list_index = min(n - 1, self.list_index + half)
        elif key in (curses.KEY_PPAGE, CTRL_U):
            self.list_index = max(0, self.list_index - half)
        else:
            return False
        return True

    def pick(self, opt: Option, jump_to_field: bool) -> None:
        """Toggle from search results, then drop the filter and land on that option."""
        self.searching = False
        self.query = ""
        self.list_index = self.options.index(opt)
        self.toggle(opt, jump_to_field)

    def handle_list(self, key) -> None:
        if self.searching:
            self.handle_search(key)
        else:
            self.handle_normal(key)
        self.list_index = max(0, min(self.list_index, len(self.filtered()) - 1))

    def handle_normal(self, key) -> None:
        """Vim-style keys for the option list."""
        items = self.filtered()
        n = len(items)
        current = items[self.list_index] if n else None
        if self._move_cursor(key, n):
            pass
        elif key == "j":
            self.list_index = min(n - 1, self.list_index + 1)
        elif key == "k":
            self._move_cursor(curses.KEY_UP, n)
        elif key in ("g", curses.KEY_HOME):
            self.list_index = 0
        elif key in ("G", curses.KEY_END):
            self.list_index = n - 1
        elif key == " " and current:
            self.toggle(current, jump_to_field=False)
        elif key in (*ENTER_KEYS, "l", curses.KEY_RIGHT) and current:
            self.toggle(current, jump_to_field=True)
        elif key in ("h", curses.KEY_LEFT) and current:
            if current.key in self.occurrences():
                self.toggle(current, jump_to_field=False)
        elif key == "/":
            self.searching = True
            self.list_index = 0
        elif key in ("i", "a"):
            self.focus = "cmd"
            self.cmd.cursor = len(self.cmd.text)
        elif key == "H":
            self.open_history()
        elif key == "?":
            self.open_help()
        elif key == "q":
            self.cancel()
        elif key == ESC and self.query:
            self.query = ""
            self.list_index = 0

    def handle_search(self, key) -> None:
        """Type to filter; Enter picks the highlighted match like fzf."""
        items = self.filtered()
        n = len(items)
        if self._move_cursor(key, n):
            pass
        elif key in ENTER_KEYS:
            if n:
                self.pick(items[self.list_index], jump_to_field=True)
            else:
                self.searching = False
        elif key == ESC:
            self.searching = False  # keep the filter so j/k can browse it
        elif key in BACKSPACE_KEYS:
            if self.query:
                self.query = self.query[:-1]
                self.list_index = 0
            else:
                self.searching = False
        elif key == CTRL_W:
            self.query = ""
            self.list_index = 0
        elif isinstance(key, str) and key.isprintable():
            self.query += key
            self.list_index = 0

    # ---- history panel -----------------------------------------------------

    def hist_filtered(self) -> list[history.Entry]:
        q = self.hist_query.lower()
        return [e for e in self.hist_entries if q in e.command.lower()]

    def open_history(self) -> None:
        self.commit_field()
        self.hist_entries = history.load(self.program or None)
        self.hist_open, self.hist_index, self.hist_query, self.hist_searching = True, 0, "", False

    def close_history(self) -> None:
        if self.standalone:
            return self.cancel()
        self.hist_open = False
        self.hist_searching = False

    def load_entry(self, entry: history.Entry) -> None:
        """Put a past command in the command box so it can be tweaked."""
        if self.standalone:
            self.edit_request = entry.command
            self.done = True
            return
        self.cmd.set(entry.command)
        self.close_history()
        self.focus = "cmd"
        self.sync_fields()

    def replay(self, entry: history.Entry) -> None:
        self.cmd.set(entry.command)
        self.fields.clear()
        self.focus = "cmd"
        self.accept()

    def handle_history(self, key) -> None:
        items = self.hist_filtered()
        n = len(items)
        current = items[self.hist_index] if n else None
        if key == CTRL_P and not self.standalone:
            self.close_history()
            self.focus = "cmd"
        elif key == curses.KEY_DOWN or (key == "j" and not self.hist_searching):
            self.hist_index = min(n - 1, self.hist_index + 1)
        elif key == curses.KEY_UP or (key == "k" and not self.hist_searching):
            self.hist_index = max(0, self.hist_index - 1)
        elif key in (curses.KEY_NPAGE, CTRL_D):
            self.hist_index = min(n - 1, self.hist_index + 10)
        elif key in (curses.KEY_PPAGE, CTRL_U):
            self.hist_index = max(0, self.hist_index - 10)
        elif key in ENTER_KEYS:
            self.hist_searching = False
            if current:
                self.load_entry(current)
        elif key == CTRL_S and current:
            self.replay(current)
        elif self.hist_searching:
            if key == ESC:
                self.hist_searching = False
            elif key in BACKSPACE_KEYS:
                if self.hist_query:
                    self.hist_query = self.hist_query[:-1]
                else:
                    self.hist_searching = False
            elif key == CTRL_W:
                self.hist_query = ""
            elif isinstance(key, str) and key.isprintable():
                self.hist_query += key
                self.hist_index = 0
        elif key == "l" and current:
            self.load_entry(current)
        elif key == "r" and current:
            self.replay(current)
        elif key == "d" and current:
            history.delete(current.command)
            self.hist_entries.remove(current)
            self.message = "deleted from history"
        elif key in ("g", curses.KEY_HOME):
            self.hist_index = 0
        elif key in ("G", curses.KEY_END):
            self.hist_index = n - 1
        elif key == "/":
            self.hist_searching = True
            self.hist_index = 0
        elif key == "?":
            self.open_help()
        elif key == ESC and self.hist_query:
            self.hist_query = ""
        elif key in (ESC, "q", "h", "H", CTRL_R):
            self.close_history()
        self.hist_index = max(0, min(self.hist_index, len(self.hist_filtered()) - 1))

    # ---- "?" help ------------------------------------------------------------

    def open_help(self) -> None:
        self.help_open, self.help_scroll = True, 0

    def handle_help(self, key) -> None:
        """Scroll with j/k etc.; frontends clamp help_scroll to the content height."""
        if key in (curses.KEY_DOWN, "j"):
            self.help_scroll += 1
        elif key in (curses.KEY_UP, "k"):
            self.help_scroll -= 1
        elif key in (curses.KEY_NPAGE, CTRL_D, " "):
            self.help_scroll += 10
        elif key in (curses.KEY_PPAGE, CTRL_U):
            self.help_scroll -= 10
        elif key in ("g", curses.KEY_HOME):
            self.help_scroll = 0
        elif key in ("G", curses.KEY_END):
            self.help_scroll = 10_000
        elif key in (ESC, "q", "?", "h", *ENTER_KEYS):
            self.help_open = False
        self.help_scroll = max(0, self.help_scroll)


# ---- layout helpers shared by the frontends ------------------------------------

def wrap(text: str, width: int) -> list[str]:
    return textwrap.wrap(text, max(10, width), break_on_hyphens=False) or [""]


def spec_width(items: list[Option], w: int) -> int:
    # Flags longer than the column get their own line, so keep the column modest.
    return min(26, w // 3, max((len(o.spec) for o in items), default=0))


def option_lines(opt: Option, checked: bool, spec_w: int, w: int) -> list[str]:
    """One option as screen lines, with the description wrapped under its own column."""
    head = f" {'[x]' if checked else '[ ]'} "
    indent = len(head) + spec_w + 2
    desc_w = w - 1 - indent
    if desc_w < 20:  # too narrow for two columns: description goes below the flags
        indent, desc_w = len(head) + 2, w - 1 - len(head) - 2
    if not opt.description:
        return [head + opt.spec]
    desc = wrap(opt.description, desc_w)
    if len(head) + len(opt.spec) + 2 > indent:  # flags don't fit the column: own line
        return [head + opt.spec] + [" " * indent + d for d in desc]
    first = (head + opt.spec).ljust(indent) + desc[0]
    return [first] + [" " * indent + d for d in desc[1:]]


HISTORY_INDENT = 12  # " 10m ago  " column


def history_lines(entry: history.Entry, w: int) -> list[str]:
    cmd = textwrap.wrap(entry.command, max(10, w - 1 - HISTORY_INDENT), break_on_hyphens=False,
                        drop_whitespace=False) or [""]
    return [f" {history.ago(entry.time).rjust(9)}  {cmd[0]}"] + [" " * HISTORY_INDENT + c for c in cmd[1:]]


def fit_scroll(heights: list[int], index: int, scroll: int, avail: int) -> int:
    """First item to show so that the item at `index` is fully on screen."""
    if not heights:
        return 0
    scroll = max(0, min(scroll, index))
    while scroll < index and sum(heights[scroll:index + 1]) > avail:
        scroll += 1
    return scroll


def options_header(s: Session) -> tuple[str, str]:
    """("Options (n/m selected)", search/filter hint)."""
    header = f"Options ({len(s.occurrences())}/{len(s.options)} selected)"
    if s.searching:
        return header, f"/{s.query}"
    if s.query:
        return header, f"filter: {s.query}  (esc clears)"
    return header, "/ to search" if s.focus == "list" else ""


def history_header(s: Session) -> tuple[str, str]:
    header = f"History · {s.program or 'all programs'} ({len(s.hist_filtered())})"
    if s.hist_searching:
        return header, f"/{s.hist_query}"
    if s.hist_query:
        return header, f"filter: {s.hist_query}  (esc clears)"
    return header, "/ to search"


def history_empty_message(s: Session) -> str:
    if s.hist_query:
        return "no matches"
    return f"no history yet for {s.program}" if s.program else "no history yet"


def history_status(s: Session) -> str:
    items = s.hist_filtered()
    if not items:
        return ""
    cwd = items[s.hist_index].cwd.replace(os.path.expanduser("~"), "~", 1)
    return f"ran in {cwd}" if cwd else ""


HELP: list[tuple[str, list[tuple[str, str]]]] = [
    ("Anywhere", [
        ("Tab / Shift-Tab", "move between command box, value boxes and options"),
        ("Ctrl-P", "jump between the command box and the options"),
        ("Ctrl-R", "open this program's history"),
        ("Ctrl-S", "done: run the command (prints it when output is captured)"),
        ("Ctrl-C", "quit without output"),
    ]),
    ("Option list", [
        ("j / k, ↑ / ↓", "move down / up (k at the top moves to the boxes)"),
        ("Ctrl-D / Ctrl-U", "half a page down / up"),
        ("g / G", "first / last option"),
        ("Space", "toggle option"),
        ("l / Enter", "toggle option and edit its value"),
        ("h", "uncheck option"),
        ("/", "search"),
        ("i / a", "edit the command box"),
        ("H", "history"),
        ("Esc", "clear the search filter"),
        ("?", "this help"),
        ("q", "quit without output"),
    ]),
    ("Search (after /)", [
        ("type, Backspace, Ctrl-W", "edit the query"),
        ("↑ / ↓, Ctrl-D / Ctrl-U", "move through matches"),
        ("Enter", "pick the match and edit its value"),
        ("Esc", "stop typing, keep the results to browse with j/k"),
    ]),
    ("Value box", [
        ("Enter", "write the value into the command"),
        ("Esc", "discard the edit"),
        ("↑ / ↓", "previous / next box"),
    ]),
    ("Command box", [
        ("Enter", "done"),
        ("Esc", "back to the options"),
        ("Ctrl-A / Ctrl-E", "start / end of line"),
        ("Ctrl-W / Ctrl-U / Ctrl-K", "delete word / to start / to end"),
    ]),
    ("History", [
        ("j / k, g / G", "move, first / last"),
        ("Enter / l", "load the command to edit it"),
        ("r / Ctrl-S", "use the command as-is"),
        ("d", "delete the entry"),
        ("/", "search"),
        ("?", "this help"),
        ("Esc / q / h", "back to the options"),
    ]),
]


def help_lines(w: int) -> list[tuple[str, str, int]]:
    """The key reference as (text, kind, key_end) lines.

    kind is "section", "key" or "" (a wrapped continuation); key_end is where the
    key column ends on "key" lines so frontends can colour it.
    """
    key_w = max(len(k) for _, rows in HELP for k, _ in rows) + 3
    if w - key_w < 24:  # narrow: action goes under the key
        key_w = 4
    out: list[tuple[str, str, int]] = []
    for title, rows in HELP:
        if out:
            out.append(("", "", 0))
        out.append((f" {title}", "section", 0))
        for k, action in rows:
            key_end = 3 + len(k)
            desc = wrap(action, w - key_w - 3)
            if key_w == 4:
                out.append((f"   {k}", "key", key_end))
            else:
                out.append((f"   {k}".ljust(key_w + 2) + desc[0], "key", key_end))
                desc = desc[1:]
            out += [(" " * (key_w + 2) + d, "", 0) for d in desc]
    return out


def key_hints(s: Session) -> list[tuple[str, str]]:
    """(key, what it does) pairs for the bottom bar."""
    if s.help_open:
        return [("j/k", "scroll"), ("esc / q / ?", "close")]
    if s.hist_open:
        if s.hist_searching:
            return [("type", "to filter"), ("↑↓", "move"), ("enter", "edit"), ("^S", "use as-is"), ("esc", "stop")]
        return [("j/k", "move"), ("enter", "edit (opens options)" if s.standalone else "edit"),
                ("r", "use as-is"), ("d", "delete"), ("/", "search"), ("?", "help"),
                ("esc", "quit" if s.standalone else "back")]
    if s.focus == "list":
        if s.searching:
            return [("type", "to filter"), ("↑↓", "move"), ("enter", "pick"), ("esc", "browse results")]
        return [("j/k", "move"), ("space", "toggle"), ("l", "edit"), ("/", "search"),
                ("^P", "command"), ("^S", "done"), ("?", "help")]
    if s.focus == "cmd":
        return [("enter", "done"), ("^P", "options"), ("tab/↓", "next"), ("esc", "back to list")]
    return [("enter", "set value"), ("esc", "discard"), ("tab/↑↓", "move"), ("^P", "command"), ("^S", "done")]


# ---- colour: frontend-neutral "kinds" that each frontend maps to a style ----------
#
# program  first word of a command      flag     -n, --name
# punct    = and , separators           value    an option's value
# string   quoted text                  arg      positional argument
# op       | && ; > and friends         metavar  STRING in "--name=STRING"
# check    [x]                          uncheck  [ ]
# match    search hit                   time     "5m ago"
# title    "hptui" in the title bar     ""       plain text

Segment = tuple[str, str]  # (text, kind)
_SHELL_OPS = {"|", "||", "&&", ";", "&", ">", ">>", "<", "2>", "2>&1", "&>", "|&"}
_FLAG_RE = re.compile(r"--?[A-Za-z0-9?#@][\w.+#@-]*")


def runs(text: str, kinds: list[str]) -> list[Segment]:
    """Group per-character kinds into (text, kind) segments."""
    out: list[Segment] = []
    for ch, kind in zip(text, kinds):
        if out and out[-1][1] == kind:
            out[-1] = (out[-1][0] + ch, kind)
        else:
            out.append((ch, kind))
    return out


def command_kinds(text: str, options: list[Option]) -> list[str]:
    """A kind for every character of a command line (syntax highlighting)."""
    kinds = [""] * len(text)

    def paint(a: int, b: int, kind: str) -> None:
        kinds[a:b] = [kind] * (b - a)

    after_op = True  # the first word, and the word after | && ; etc., is a program
    for tok in cmdline.tokenize(text):
        raw = tok.raw
        if raw in _SHELL_OPS:
            paint(tok.start, tok.end, "op")
            after_op = True
            continue
        if after_op:
            paint(tok.start, tok.end, "program")
            after_op = False
        elif raw.startswith("-") and len(raw) > 1:
            eq = raw.find("=")
            if eq > 1:
                paint(tok.start, tok.start + eq, "flag")
                paint(tok.start + eq, tok.start + eq + 1, "punct")
                paint(tok.start + eq + 1, tok.end, "string" if raw[eq + 1:eq + 2] in ("'", '"') else "value")
            else:
                paint(tok.start, tok.end, "flag")
        else:
            paint(tok.start, tok.end, "string" if raw[:1] in ("'", '"') or '"' in raw else "arg")
    # values of known options (e.g. the FILE in "-o FILE") are values, not positional args
    for occ in cmdline.find_occurrences(text, options):
        if occ.value_start is not None and occ.style in ("sep", "attached"):
            raw = text[occ.value_start:occ.value_end]
            paint(occ.value_start, occ.value_end, "string" if raw[:1] in ("'", '"') else "value")
    return kinds


def _mark_matches(line: str, kinds: list[str], query: str) -> None:
    if not query:
        return
    low, q = line.lower(), query.lower()
    i = low.find(q)
    while i != -1:
        kinds[i:i + len(q)] = ["match"] * len(q)
        i = low.find(q, i + len(q))


def option_segments(opt: Option, checked: bool, spec_w: int, w: int, query: str = "") -> list[list[Segment]]:
    """option_lines() with colour: checkbox, flags vs. metavars, and search hits."""
    lines = option_lines(opt, checked, spec_w, w)
    out = []
    for n, line in enumerate(lines):
        kinds = [""] * len(line)
        if n == 0:
            kinds[1:4] = ["check" if checked else "uncheck"] * 3
            start = 5  # after " [x] "
            for j, ch in enumerate(opt.spec):
                kinds[start + j] = "punct" if ch in ",|=" else ("" if ch == " " else "metavar")
            for m in _FLAG_RE.finditer(opt.spec):
                kinds[start + m.start():start + m.end()] = ["flag"] * (m.end() - m.start())
        _mark_matches(line, kinds, query)
        out.append(runs(line, kinds))
    return out


def history_segments(entry: history.Entry, w: int, query: str = "") -> list[list[Segment]]:
    """history_lines() with the command syntax-highlighted."""
    lines = history_lines(entry, w)
    ckinds = command_kinds(entry.command, [])
    out, pos = [], 0
    for n, line in enumerate(lines):
        body = line[HISTORY_INDENT:]
        kinds = ["time"] * HISTORY_INDENT + ckinds[pos:pos + len(body)]
        kinds += [""] * (len(line) - len(kinds))
        pos += len(body)
        body_kinds = kinds[HISTORY_INDENT:]
        _mark_matches(line[HISTORY_INDENT:], body_kinds, query)
        kinds = kinds[:HISTORY_INDENT] + body_kinds
        out.append(runs(line, kinds))
    return out


def title_segments(title: str) -> list[Segment]:
    """ "hptui", then the program name."""
    return [(" ", ""), ("hptui", "title"), ("  ·  ", "punct"), (title, "program"), (" ", "")]


def new_session(
    options: list[Option],
    command: str,
    *,
    program: str | None = None,
    open_history: bool = False,
    standalone: bool = False,
) -> Session:
    s = Session(options=options, cmd=LineEdit(), standalone=standalone)
    s.program = history.program_of(command) if program is None else program
    s.cmd.set(command.rstrip() + " " if command else "")
    s.sync_fields()
    if open_history or standalone:
        s.open_history()
    return s
