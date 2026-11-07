"""Interactive curses interface."""

from __future__ import annotations

import curses
import os
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
class App:
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
        elif key == ESC and self.hist_query:
            self.hist_query = ""
        elif key in (ESC, "q", "h", "H", CTRL_R):
            self.close_history()
        self.hist_index = max(0, min(self.hist_index, len(self.hist_filtered()) - 1))


# ---- drawing -----------------------------------------------------------------

class Theme:
    def __init__(self) -> None:
        self.accent = self.checked = self.dim = self.error = curses.A_BOLD
        if curses.has_colors():
            curses.start_color()
            try:
                curses.use_default_colors()
                bg = -1
            except curses.error:
                bg = curses.COLOR_BLACK
            curses.init_pair(1, curses.COLOR_CYAN, bg)
            curses.init_pair(2, curses.COLOR_GREEN, bg)
            curses.init_pair(3, curses.COLOR_YELLOW, bg)
            self.accent = curses.color_pair(1) | curses.A_BOLD
            self.checked = curses.color_pair(2) | curses.A_BOLD
            self.label = curses.color_pair(3)
            self.dim = curses.A_DIM
        else:
            self.label = curses.A_BOLD


def _put(win, y: int, x: int, text: str, attr: int = 0) -> None:
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    text = text[: max(0, w - x - (1 if y == h - 1 else 0))]
    try:
        win.addstr(y, x, text, attr)
    except curses.error:
        pass


def _wrap(text: str, width: int) -> list[str]:
    return textwrap.wrap(text, max(10, width), break_on_hyphens=False) or [""]


def _option_lines(opt: Option, checked: bool, spec_w: int, w: int) -> list[str]:
    """One option as screen lines, with the description wrapped under its own column."""
    head = f" {'[x]' if checked else '[ ]'} "
    indent = len(head) + spec_w + 2
    desc_w = w - 1 - indent
    if desc_w < 20:  # too narrow for two columns: description goes below the flags
        indent, desc_w = len(head) + 2, w - 1 - len(head) - 2
    if not opt.description:
        return [head + opt.spec]
    desc = _wrap(opt.description, desc_w)
    if len(head) + len(opt.spec) + 2 > indent:  # flags don't fit the column: own line
        return [head + opt.spec] + [" " * indent + d for d in desc]
    first = (head + opt.spec).ljust(indent) + desc[0]
    return [first] + [" " * indent + d for d in desc[1:]]


def _fit_scroll(heights: list[int], index: int, scroll: int, avail: int) -> int:
    """First item to show so that the item at `index` is fully on screen."""
    if not heights:
        return 0
    scroll = max(0, min(scroll, index))
    while scroll < index and sum(heights[scroll:index + 1]) > avail:
        scroll += 1
    return scroll


def _draw_rows(win, y: int, avail: int, rows: list[list[str]], scroll: int, cursor: int | None, attr_of) -> None:
    w = win.getmaxyx()[1]
    used = 0
    for i in range(scroll, len(rows)):
        if used >= avail:
            break
        attr = attr_of(i) | (curses.A_REVERSE if i == cursor else 0)
        for line in rows[i][: avail - used]:
            _put(win, y + used, 0, line.ljust(w - 1) if i == cursor else line, attr)
            used += 1


def draw(win, app: App, theme: Theme, title: str) -> None:
    win.erase()
    h, w = win.getmaxyx()
    cursor_pos: tuple[int, int] | None = None
    occs = app.occurrences()

    _put(win, 0, 0, f" hptui · {title} ".ljust(w), curses.A_REVERSE | curses.A_BOLD)

    # command box
    y = 2
    prompt = "❯ "
    focused = app.focus == "cmd"
    _put(win, y, 0, prompt, theme.accent if focused else theme.dim)
    text, col = app.cmd.visible(w - len(prompt) - 1)
    _put(win, y, len(prompt), text, curses.A_BOLD if focused else 0)
    if focused:
        cursor_pos = (y, len(prompt) + col)
    _put(win, y + 1, 0, "─" * (w - 1), theme.accent if focused else theme.dim)
    y += 3

    if app.hist_open:
        cursor_pos = draw_history(win, app, theme, y)
        return _finish(win, cursor_pos)

    # one input box per checked option that takes a value
    value_opts = app.value_options()
    if value_opts:
        label_w = min(24, max(len(o.name) for o in value_opts)) + 1
        max_rows = max(1, (h - y - 6) // 2)
        for opt in value_opts[:max_rows]:
            key = f"field:{opt.key}"
            box = app.fields.get(opt.key, LineEdit())
            focused = app.focus == key
            label = f"{opt.name}:".ljust(label_w + 1)
            _put(win, y, 2, label, theme.accent if focused else theme.label)
            x = 2 + len(label) + 1
            text, col = box.visible(w - x - 1)
            hint = "" if box.text or focused else f"<{opt.metavar}>"
            _put(win, y, x, text or hint, (curses.A_UNDERLINE if focused else 0) | (theme.dim if hint else 0))
            if focused:
                cursor_pos = (y, x + col)
            y += 1
        y += 1

    # option list
    items = app.filtered()
    header = f"Options ({len(occs)}/{len(app.options)} selected)"
    _put(win, y, 0, header, curses.A_BOLD)
    if app.searching:
        prompt = f"/{app.query}"
        _put(win, y, len(header) + 2, prompt, theme.accent)
        if app.focus == "list":
            cursor_pos = (y, len(header) + 2 + len(prompt))
    elif app.query:
        _put(win, y, len(header) + 2, f"filter: {app.query}  (esc clears)", theme.accent)
    elif app.focus == "list":
        _put(win, y, len(header) + 2, "/ to search", theme.dim)
    y += 1

    list_h = max(1, h - y - 2)
    # Flags longer than the column get their own line, so keep the column modest.
    spec_w = min(26, w // 3, max((len(o.spec) for o in items), default=0))
    rows = [_option_lines(o, o.key in occs, spec_w, w) for o in items]
    app.list_scroll = _fit_scroll([len(r) for r in rows], app.list_index, app.list_scroll, list_h)
    _draw_rows(
        win, y, list_h, rows, app.list_scroll,
        cursor=app.list_index if app.focus == "list" else None,
        attr_of=lambda i: theme.checked if items[i].key in occs else 0,
    )
    if not items:
        _put(win, y, 2, "no options match", theme.dim)

    if app.message:
        _put(win, h - 2, 0, app.message, theme.accent)
    keys = {
        "list": "j/k move · space toggle · l edit · / search · ^P command · H history · ^S done · q quit",
        "search": "type to filter · ↑↓ move · enter pick · esc browse results",
        "cmd": "enter done · ^P options · tab/↓ next · esc back to list",
    }.get("search" if app.searching and app.focus == "list" else app.focus, "enter set value · esc discard · tab/↑↓ move · ^P command · ^S done")
    _put(win, h - 1, 0, f" {keys} ".ljust(w), curses.A_REVERSE)
    _finish(win, cursor_pos)


def draw_history(win, app: App, theme: Theme, y: int) -> tuple[int, int] | None:
    h, w = win.getmaxyx()
    cursor_pos = None
    items = app.hist_filtered()
    header = f"History · {app.program or 'all programs'} ({len(items)})"
    _put(win, y, 0, header, curses.A_BOLD)
    x = len(header) + 2
    if app.hist_searching:
        _put(win, y, x, f"/{app.hist_query}", theme.accent)
        cursor_pos = (y, x + 1 + len(app.hist_query))
    elif app.hist_query:
        _put(win, y, x, f"filter: {app.hist_query}  (esc clears)", theme.accent)
    else:
        _put(win, y, x, "/ to search", theme.dim)
    y += 1

    list_h = max(1, h - y - 3)
    indent = 12  # " 10m ago  " column
    rows = []
    for entry in items:
        cmd = textwrap.wrap(entry.command, max(10, w - 1 - indent), break_on_hyphens=False,
                            drop_whitespace=False) or [""]
        rows.append([f" {history.ago(entry.time).rjust(9)}  {cmd[0]}"] + [" " * indent + c for c in cmd[1:]])
    app.hist_scroll = _fit_scroll([len(r) for r in rows], app.hist_index, app.hist_scroll, list_h)
    _draw_rows(win, y, list_h, rows, app.hist_scroll, cursor=app.hist_index, attr_of=lambda i: 0)
    if not items:
        msg = "no matches" if app.hist_query else f"no history yet for {app.program}" if app.program else "no history yet"
        _put(win, y, 2, msg, theme.dim)

    if items:
        cwd = items[app.hist_index].cwd.replace(os.path.expanduser("~"), "~", 1)
        _put(win, h - 2, 0, f"ran in {cwd}" if cwd else "", theme.dim)
    if app.message:
        _put(win, h - 2, 0, app.message, theme.accent)
    edit = "enter edit" if not app.standalone else "enter edit (opens options)"
    keys = (
        "type to filter · ↑↓ move · enter edit · ^S use as-is · esc stop"
        if app.hist_searching
        else f"j/k move · {edit} · r use as-is · d delete · / search · esc {'quit' if app.standalone else 'back'}"
    )
    _put(win, h - 1, 0, f" {keys} ".ljust(w), curses.A_REVERSE)
    return cursor_pos


def _finish(win, cursor_pos: tuple[int, int] | None) -> None:
    if cursor_pos:
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        win.move(*cursor_pos)
    else:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
    win.refresh()


def _read_key(win):
    key = win.get_wch()
    if key == ESC:
        # Alt+<key> and unknown escape sequences arrive as ESC followed by more
        # bytes; swallow them all so only a lone ESC counts.
        win.nodelay(True)
        extra = False
        try:
            while True:
                win.get_wch()
                extra = True
        except curses.error:
            pass
        finally:
            win.nodelay(False)
        if extra:
            return None
    return key


def run(
    options: list[Option],
    command: str,
    title: str | None = None,
    *,
    program: str | None = None,
    open_history: bool = False,
    standalone: bool = False,
) -> App:
    """Show the TUI. app.result is the final command (None if cancelled)."""
    app = App(options=options, cmd=LineEdit(), standalone=standalone)
    app.program = history.program_of(command) if program is None else program
    app.cmd.set(command.rstrip() + " " if command else "")
    app.sync_fields()
    if open_history or standalone:
        app.open_history()

    def main(stdscr) -> None:
        try:
            curses.set_escdelay(25)
        except AttributeError:
            pass
        curses.raw()
        stdscr.keypad(True)
        theme = Theme()
        while not app.done:
            draw(stdscr, app, theme, title or command or "history")
            try:
                key = _read_key(stdscr)
            except curses.error:
                continue
            if key is None or key == curses.KEY_RESIZE:
                continue
            app.handle(key)

    curses.wrapper(main)
    return app
