"""curses frontend: fast to start, no dependencies."""

from __future__ import annotations

import curses

from .parser import Option
from .session import (
    ESC, LineEdit, Segment, Session, command_kinds, fit_scroll, help_lines, history_empty_message,
    history_header, history_segments, history_status, key_hints, new_session, option_segments,
    options_header, runs, spec_width, title_segments,
)

# kind -> (256-colour fg, 8-colour fg, extra attrs). None = terminal default (dimmed in 8-colour mode).
# The 256-colour values approximate the Dracula palette.
PALETTE = {
    "program": (212, curses.COLOR_MAGENTA, curses.A_BOLD),
    "flag": (117, curses.COLOR_CYAN, 0),
    "punct": (61, None, 0),
    "value": (228, curses.COLOR_YELLOW, 0),
    "string": (84, curses.COLOR_GREEN, 0),
    "op": (203, curses.COLOR_RED, curses.A_BOLD),
    "metavar": (141, curses.COLOR_MAGENTA, 0),
    "check": (84, curses.COLOR_GREEN, curses.A_BOLD),
    "uncheck": (61, None, 0),
    "time": (61, None, 0),
    "dim": (61, None, 0),
    "section": (212, curses.COLOR_MAGENTA, curses.A_BOLD),
    "key": (228, curses.COLOR_YELLOW, curses.A_BOLD),
    "accent": (117, curses.COLOR_CYAN, curses.A_BOLD),
    "label": (117, curses.COLOR_CYAN, 0),
    "title": (228, curses.COLOR_YELLOW, curses.A_BOLD),
}
_BOLD_WITHOUT_COLOUR = {"program", "flag", "check", "section", "key", "accent", "op"}
CURSOR_BG_256 = 237  # dark grey bar behind the highlighted row
MATCH_FG_256, MATCH_BG_256 = 16, 228


class Theme:
    def __init__(self) -> None:
        self.colour = curses.has_colors()
        self.rich = False
        self._pairs: dict[tuple[int, int], int] = {}
        self._cache: dict[tuple[str, bool], int] = {}
        if not self.colour:
            return
        curses.start_color()
        try:
            curses.use_default_colors()
            self.default_fg, self.default_bg = -1, -1
        except curses.error:
            self.default_fg, self.default_bg = curses.COLOR_WHITE, curses.COLOR_BLACK
        self.rich = curses.COLORS >= 256

    def _pair(self, fg: int, bg: int) -> int:
        if (fg, bg) not in self._pairs:
            n = len(self._pairs) + 1
            if n >= curses.COLOR_PAIRS:
                return 0
            curses.init_pair(n, fg, bg)
            self._pairs[(fg, bg)] = n
        return curses.color_pair(self._pairs[(fg, bg)])

    def attr(self, kind: str, cursor: bool = False) -> int:
        key = (kind, cursor)
        if key not in self._cache:
            self._cache[key] = self._compute(kind, cursor)
        return self._cache[key]

    def _compute(self, kind: str, cursor: bool) -> int:
        if not self.colour:
            a = curses.A_BOLD if kind in _BOLD_WITHOUT_COLOUR else 0
            if kind == "match":
                a |= curses.A_UNDERLINE
            return a | (curses.A_REVERSE if cursor else 0)
        if not self.rich and cursor:
            return curses.A_REVERSE | (curses.A_UNDERLINE if kind == "match" else 0)
        bg = CURSOR_BG_256 if cursor else self.default_bg
        if kind == "match":
            if self.rich:
                return self._pair(MATCH_FG_256, MATCH_BG_256) | curses.A_BOLD
            return self._pair(curses.COLOR_BLACK, curses.COLOR_YELLOW) | curses.A_BOLD
        fg256, fg8, extra = PALETTE.get(kind, (None, None, 0))
        extra |= curses.A_BOLD if cursor else 0
        if self.rich:
            return self._pair(fg256 if fg256 is not None else self.default_fg, bg) | extra
        if fg8 is None:
            return self._pair(self.default_fg, bg) | extra | (curses.A_DIM if kind else 0)
        return self._pair(fg8, bg) | extra


def _put(win, y: int, x: int, text: str, attr: int = 0) -> int:
    """Draw clipped text; returns the x after it."""
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return x
    text = text[: max(0, w - x - (1 if y == h - 1 else 0))]
    try:
        win.addstr(y, x, text, attr)
    except curses.error:
        pass
    return x + len(text)


def _put_segments(win, y: int, x: int, segments: list[Segment], theme: Theme,
                  cursor: bool = False, fill: bool = False, extra: int = 0) -> int:
    for text, kind in segments:
        x = _put(win, y, x, text, theme.attr(kind, cursor) | extra)
    if fill:
        w = win.getmaxyx()[1]
        _put(win, y, x, " " * max(0, w - 1 - x), theme.attr("", cursor))
    return x


def _draw_rows(win, y: int, avail: int, rows: list[list[list[Segment]]], scroll: int,
               cursor: int | None, theme: Theme) -> None:
    used = 0
    for i in range(scroll, len(rows)):
        if used >= avail:
            break
        for line in rows[i][: avail - used]:
            _put_segments(win, y + used, 0, line, theme, cursor=i == cursor, fill=i == cursor)
            used += 1


def _draw_keybar(win, s: Session, theme: Theme) -> None:
    h, w = win.getmaxyx()
    x = 1
    for n, (key, desc) in enumerate(key_hints(s)):
        if n:
            x = _put(win, h - 1, x, " · ", theme.attr("dim"))
        x = _put(win, h - 1, x, key, theme.attr("key"))
        x = _put(win, h - 1, x, f" {desc}", theme.attr("dim"))


def draw(win, s: Session, theme: Theme, title: str) -> None:
    win.erase()
    h, w = win.getmaxyx()
    cursor_pos: tuple[int, int] | None = None

    _put_segments(win, 0, 0, title_segments(title), theme)

    # command box, syntax highlighted
    y = 2
    prompt = "❯ "
    focused = s.focus == "cmd" and not s.hist_open and not s.help_open
    _put(win, y, 0, prompt, theme.attr("accent" if focused else "dim"))
    text, col = s.cmd.visible(w - len(prompt) - 1)
    kinds = command_kinds(s.cmd.text, s.options)[s.cmd.scroll:s.cmd.scroll + len(text)]
    _put_segments(win, y, len(prompt), runs(text, kinds), theme)
    if focused:
        cursor_pos = (y, len(prompt) + col)
    _put(win, y + 1, 0, "─" * (w - 1), theme.attr("accent" if focused else "dim"))
    y += 3

    if s.help_open:
        cursor_pos = None
        draw_help(win, s, theme, y)
    elif s.hist_open:
        cursor_pos = draw_history(win, s, theme, y)
    else:
        cursor_pos = draw_options(win, s, theme, y) or cursor_pos
        if s.message:
            _put(win, h - 2, 0, s.message, theme.attr("accent"))
    _draw_keybar(win, s, theme)
    _finish(win, cursor_pos)


def draw_options(win, s: Session, theme: Theme, y: int) -> tuple[int, int] | None:
    h, w = win.getmaxyx()
    cursor_pos = None
    occs = s.occurrences()

    # one input box per checked option that takes a value
    value_opts = s.value_options()
    if value_opts:
        label_w = min(24, max(len(o.name) for o in value_opts)) + 1
        max_rows = max(1, (h - y - 6) // 2)
        for opt in value_opts[:max_rows]:
            box = s.fields.get(opt.key, LineEdit())
            focused = s.focus == f"field:{opt.key}"
            label = f"{opt.name}:".ljust(label_w + 1)
            _put(win, y, 2, label, theme.attr("accent" if focused else "label"))
            x = 2 + len(label) + 1
            text, col = box.visible(w - x - 1)
            if text or focused:
                _put(win, y, x, text, theme.attr("value") | (curses.A_UNDERLINE if focused else 0))
            else:
                _put(win, y, x, f"<{opt.metavar}>", theme.attr("dim"))
            if focused:
                cursor_pos = (y, x + col)
            y += 1
        y += 1

    # option list
    items = s.filtered()
    header, hint = options_header(s)
    x = _put(win, y, 0, header, theme.attr("section"))
    _put(win, y, x + 2, hint, theme.attr("accent" if (s.searching or s.query) else "dim"))
    if s.searching and s.focus == "list":
        cursor_pos = (y, x + 2 + len(hint))
    y += 1

    list_h = max(1, h - y - 2)
    spec_w = spec_width(items, w)
    rows = [option_segments(o, o.key in occs, spec_w, w, s.query) for o in items]
    s.list_scroll = fit_scroll([len(r) for r in rows], s.list_index, s.list_scroll, list_h)
    _draw_rows(win, y, list_h, rows, s.list_scroll, s.list_index if s.focus == "list" else None, theme)
    if not items:
        _put(win, y, 2, "no options match", theme.attr("dim"))
    return cursor_pos


def draw_help(win, s: Session, theme: Theme, y: int) -> None:
    h, w = win.getmaxyx()
    lines = help_lines(w)
    avail = max(1, h - y - 2)
    s.help_scroll = min(s.help_scroll, max(0, len(lines) - avail))
    x = _put(win, y, 0, "Keys", theme.attr("section"))
    if len(lines) > avail:
        shown = min(len(lines), s.help_scroll + avail)
        _put(win, y, x + 2, f"lines {s.help_scroll + 1}-{shown} of {len(lines)} · j/k to scroll", theme.attr("dim"))
    for row, (text, kind, key_end) in enumerate(lines[s.help_scroll:s.help_scroll + avail]):
        if kind == "section":
            _put(win, y + 1 + row, 0, text, theme.attr("section"))
        elif kind == "key":
            x = _put(win, y + 1 + row, 0, text[:key_end], theme.attr("key"))
            _put(win, y + 1 + row, x, text[key_end:])
        else:
            _put(win, y + 1 + row, 0, text)


def draw_history(win, s: Session, theme: Theme, y: int) -> tuple[int, int] | None:
    h, w = win.getmaxyx()
    cursor_pos = None
    items = s.hist_filtered()
    header, hint = history_header(s)
    x = _put(win, y, 0, header, theme.attr("section"))
    _put(win, y, x + 2, hint, theme.attr("accent" if (s.hist_searching or s.hist_query) else "dim"))
    if s.hist_searching:
        cursor_pos = (y, x + 2 + len(hint))
    y += 1

    list_h = max(1, h - y - 3)
    rows = [history_segments(e, w, s.hist_query) for e in items]
    s.hist_scroll = fit_scroll([len(r) for r in rows], s.hist_index, s.hist_scroll, list_h)
    _draw_rows(win, y, list_h, rows, s.hist_scroll, s.hist_index, theme)
    if not items:
        _put(win, y, 2, history_empty_message(s), theme.attr("dim"))
    _put(win, h - 2, 0, s.message or history_status(s), theme.attr("accent" if s.message else "dim"))
    return cursor_pos


def _finish(win, cursor_pos: tuple[int, int] | None) -> None:
    try:
        curses.curs_set(1 if cursor_pos else 0)
    except curses.error:
        pass
    if cursor_pos:
        win.move(*cursor_pos)
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
