"""curses frontend: fast to start, no dependencies."""

from __future__ import annotations

import curses

from .parser import Option
from .session import (
    ESC, LineEdit, Session, fit_scroll, help_lines, history_empty_message, history_header, history_lines,
    history_status, key_hints, new_session, option_lines, options_header, spec_width,
)


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


def draw(win, s: Session, theme: Theme, title: str) -> None:
    win.erase()
    h, w = win.getmaxyx()
    cursor_pos: tuple[int, int] | None = None
    occs = s.occurrences()

    _put(win, 0, 0, f" hptui · {title} ".ljust(w), curses.A_REVERSE | curses.A_BOLD)

    # command box
    y = 2
    prompt = "❯ "
    focused = s.focus == "cmd" and not s.hist_open and not s.help_open
    _put(win, y, 0, prompt, theme.accent if focused else theme.dim)
    text, col = s.cmd.visible(w - len(prompt) - 1)
    _put(win, y, len(prompt), text, curses.A_BOLD if focused else 0)
    if focused:
        cursor_pos = (y, len(prompt) + col)
    _put(win, y + 1, 0, "─" * (w - 1), theme.accent if focused else theme.dim)
    y += 3

    if s.help_open:
        cursor_pos = None
        draw_help(win, s, theme, y)
    elif s.hist_open:
        cursor_pos = draw_history(win, s, theme, y)
    else:
        cursor_pos = draw_options(win, s, theme, y, occs) or cursor_pos
        if s.message:
            _put(win, h - 2, 0, s.message, theme.accent)
    _put(win, h - 1, 0, f" {key_hints(s)} ".ljust(w), curses.A_REVERSE)
    _finish(win, cursor_pos)


def draw_options(win, s: Session, theme: Theme, y: int, occs) -> tuple[int, int] | None:
    h, w = win.getmaxyx()
    cursor_pos = None

    # one input box per checked option that takes a value
    value_opts = s.value_options()
    if value_opts:
        label_w = min(24, max(len(o.name) for o in value_opts)) + 1
        max_rows = max(1, (h - y - 6) // 2)
        for opt in value_opts[:max_rows]:
            box = s.fields.get(opt.key, LineEdit())
            focused = s.focus == f"field:{opt.key}"
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
    items = s.filtered()
    header, hint = options_header(s)
    _put(win, y, 0, header, curses.A_BOLD)
    _put(win, y, len(header) + 2, hint, theme.accent if (s.searching or s.query) else theme.dim)
    if s.searching and s.focus == "list":
        cursor_pos = (y, len(header) + 2 + len(hint))
    y += 1

    list_h = max(1, h - y - 2)
    spec_w = spec_width(items, w)
    rows = [option_lines(o, o.key in occs, spec_w, w) for o in items]
    s.list_scroll = fit_scroll([len(r) for r in rows], s.list_index, s.list_scroll, list_h)
    _draw_rows(
        win, y, list_h, rows, s.list_scroll,
        cursor=s.list_index if s.focus == "list" else None,
        attr_of=lambda i: theme.checked if items[i].key in occs else 0,
    )
    if not items:
        _put(win, y, 2, "no options match", theme.dim)
    return cursor_pos


def draw_help(win, s: Session, theme: Theme, y: int) -> None:
    h, w = win.getmaxyx()
    lines = help_lines(w)
    avail = max(1, h - y - 2)
    s.help_scroll = min(s.help_scroll, max(0, len(lines) - avail))
    _put(win, y, 0, "Keys", curses.A_BOLD)
    if len(lines) > avail:
        shown = min(len(lines), s.help_scroll + avail)
        _put(win, y, 6, f"lines {s.help_scroll + 1}-{shown} of {len(lines)} · j/k to scroll", theme.dim)
    for row, (text, kind, key_end) in enumerate(lines[s.help_scroll:s.help_scroll + avail]):
        if kind == "section":
            _put(win, y + 1 + row, 0, text, theme.accent)
        else:
            _put(win, y + 1 + row, 0, text)
            if kind == "key":
                _put(win, y + 1 + row, 0, text[:key_end], theme.label | curses.A_BOLD)


def draw_history(win, s: Session, theme: Theme, y: int) -> tuple[int, int] | None:
    h, w = win.getmaxyx()
    cursor_pos = None
    items = s.hist_filtered()
    header, hint = history_header(s)
    _put(win, y, 0, header, curses.A_BOLD)
    _put(win, y, len(header) + 2, hint, theme.accent if (s.hist_searching or s.hist_query) else theme.dim)
    if s.hist_searching:
        cursor_pos = (y, len(header) + 2 + len(hint))
    y += 1

    list_h = max(1, h - y - 3)
    rows = [history_lines(e, w) for e in items]
    s.hist_scroll = fit_scroll([len(r) for r in rows], s.hist_index, s.hist_scroll, list_h)
    _draw_rows(win, y, list_h, rows, s.hist_scroll, cursor=s.hist_index, attr_of=lambda i: 0)
    if not items:
        _put(win, y, 2, history_empty_message(s), theme.dim)
    _put(win, h - 2, 0, s.message or history_status(s), theme.accent if s.message else theme.dim)
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
) -> Session:
    """Show the curses TUI. The returned session's .result is the final command (None if cancelled)."""
    s = new_session(options, command, program=program, open_history=open_history, standalone=standalone)

    def main(stdscr) -> None:
        try:
            curses.set_escdelay(25)
        except AttributeError:
            pass
        curses.raw()
        stdscr.keypad(True)
        theme = Theme()
        while not s.done:
            draw(stdscr, s, theme, title or command or "history")
            try:
                key = _read_key(stdscr)
            except curses.error:
                continue
            if key is None or key == curses.KEY_RESIZE:
                continue
            s.handle(key)

    curses.wrapper(main)
    return s
