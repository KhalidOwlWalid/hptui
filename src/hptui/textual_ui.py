"""Textual frontend: mouse support and nicer styling, but slower to start than curses.

Behaviour lives in session.Session; this module only draws it and turns Textual
key and mouse events into the same keys the curses frontend sends.
"""

from __future__ import annotations


from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import Static

from . import keys
from .parser import Option
from .session import (
    ESC, LineEdit, Segment, Session, command_kinds, fit_scroll, help_lines, history_empty_message,
    history_header, history_segments, history_status, key_hints, new_session, option_segments,
    options_header, runs, spec_width, title_segments,
)

_KEYS = {
    "up": keys.KEY_UP, "down": keys.KEY_DOWN, "left": keys.KEY_LEFT, "right": keys.KEY_RIGHT,
    "home": keys.KEY_HOME, "end": keys.KEY_END, "pageup": keys.KEY_PPAGE, "pagedown": keys.KEY_NPAGE,
    "delete": keys.KEY_DC, "shift+tab": keys.KEY_BTAB,
    "enter": "\r", "escape": ESC, "tab": "\t", "backspace": "\x7f", "ctrl+h": "\x7f", "space": " ",
}

# Dracula palette, matching the curses frontend and Textual's built-in "dracula" theme.
_STYLES = {
    "program": "bold #ff79c6", "flag": "#8be9fd", "punct": "#6272a4", "value": "#f1fa8c",
    "string": "#50fa7b", "op": "bold #ff5555", "metavar": "#bd93f9", "check": "bold #50fa7b",
    "uncheck": "#6272a4", "time": "#6272a4", "dim": "#6272a4", "match": "bold #282a36 on #f1fa8c",
    "section": "bold #ff79c6", "key": "bold #f1fa8c", "accent": "bold #8be9fd", "label": "#8be9fd",
    "title": "bold #f1fa8c",
}
STYLE = {k: Style.parse(v) for k, v in _STYLES.items()}
CURSOR = Style(bgcolor="#44475a", bold=True)  # Dracula "current line"


def style(kind: str, cursor: bool = False) -> Style:
    base = STYLE.get(kind, Style())
    return CURSOR + base if cursor else base


def segments_text(segments: list[Segment], cursor: bool = False) -> Text:
    text = Text()
    for chunk, kind in segments:
        text.append(chunk, style(kind, cursor))
    return text


def translate(event: events.Key):
    """Textual key event -> the key vocabulary Session.handle understands."""
    k = event.key
    if k in _KEYS:
        return _KEYS[k]
    if k.startswith("ctrl+") and len(k) == 6 and k[5].isalpha():
        return chr(ord(k[5]) - ord("a") + 1)
    if event.character and event.character.isprintable():
        return event.character
    return None


def _edit_text(box: LineEdit, width: int, show_cursor: bool, placeholder: str = "",
               kinds: list[str] | None = None, kind: str = "") -> Text:
    """A LineEdit's visible slice, coloured, with a block cursor when focused."""
    visible, col = box.visible(max(1, width - 1))
    if not visible and not show_cursor:
        return Text(placeholder, style=style("dim"))
    if kinds is None:
        kinds = [kind] * len(box.text)
    text = segments_text(runs(visible, kinds[box.scroll:box.scroll + len(visible)]))
    if show_cursor:
        if col >= len(visible):
            text.append(" ")
        text.stylize("reverse", col, col + 1)
    return text


class SessionView(Widget):
    @property
    def s(self) -> Session:
        return self.app.session  # type: ignore[attr-defined]


class CommandBox(SessionView):
    DEFAULT_CSS = """
    CommandBox { height: 3; border: round $foreground 25%; border-title-color: $text-muted; padding: 0 1; }
    CommandBox.-focused { border: round $accent; border-title-color: $accent; }
    """
    PROMPT = "❯ "

    def on_mount(self) -> None:
        self.border_title = "command"

    def render(self) -> Text:
        s = self.s
        focused = s.focus == "cmd" and not s.hist_open and not s.help_open
        text = Text(self.PROMPT, style=style("accent" if focused else "dim"))
        kinds = command_kinds(s.cmd.text, s.options)
        text.append_text(_edit_text(s.cmd, self.size.width - len(self.PROMPT), focused, kinds=kinds))
        return text

    def on_click(self, event: events.Click) -> None:
        s = self.s
        if s.hist_open or s.help_open:
            return
        s.commit_field()
        s.searching = False
        s.focus = "cmd"
        offset = event.get_content_offset(self)
        if offset is not None:
            s.cmd.cursor = max(0, min(len(s.cmd.text), s.cmd.scroll + offset.x - len(self.PROMPT)))
        self.app.sync()  # type: ignore[attr-defined]


class FieldsView(SessionView):
    DEFAULT_CSS = """
    FieldsView { height: auto; border: round $foreground 25%; border-title-color: $text-muted; padding: 0 1; }
    FieldsView.-focused { border: round $accent; border-title-color: $accent; }
    """

    def on_mount(self) -> None:
        self.border_title = "values"

    def layout(self) -> tuple[list[Option], int]:
        opts = self.s.value_options()
        label_w = min(24, max((len(o.name) for o in opts), default=0)) + 2
        return opts, label_w

    def render(self) -> Text:
        opts, label_w = self.layout()
        out = Text()
        for i, opt in enumerate(opts):
            focused = self.s.focus == f"field:{opt.key}"
            box = self.s.fields.get(opt.key, LineEdit())
            out.append(f"{opt.name}:".ljust(label_w), style("accent" if focused else "label"))
            out.append_text(_edit_text(box, self.size.width - label_w, focused, f"<{opt.metavar}>", kind="value"))
            if i < len(opts) - 1:
                out.append("\n")
        return out

    def on_click(self, event: events.Click) -> None:
        offset = event.get_content_offset(self)
        opts, label_w = self.layout()
        if offset is None or not 0 <= offset.y < len(opts):
            return
        s = self.s
        s.commit_field()
        s.searching = False
        key = opts[offset.y].key
        s.focus = f"field:{key}"
        box = s.fields[key]
        box.cursor = max(0, min(len(box.text), box.scroll + offset.x - label_w))
        self.app.sync()  # type: ignore[attr-defined]


class RowList(SessionView):
    """A scrolled list whose items can span several lines (wrapped, coloured text)."""

    DEFAULT_CSS = """
    RowList { height: 1fr; border: round $foreground 25%; border-title-color: $secondary;
              border-title-style: bold; border-subtitle-color: $text-muted; }
    RowList.-focused { border: round $accent; border-title-color: $accent; }
    RowList.-searching { border-subtitle-color: $warning; }
    """
    row_map: list[int]

    def rows(self, width: int) -> list[list[list[Segment]]]: ...
    def index(self) -> int: ...
    def set_index(self, i: int) -> None: ...
    def get_scroll(self) -> int: ...
    def set_scroll(self, v: int) -> None: ...
    def show_cursor(self) -> bool:
        return True
    def empty_message(self) -> str: ...
    def activate(self, i: int) -> None: ...

    def render(self) -> Text:
        w, h = self.size.width, self.size.height
        rows = self.rows(w + 1)  # layout helpers leave a 1-column margin; borders already pad
        self.set_scroll(fit_scroll([len(r) for r in rows], self.index(), self.get_scroll(), h))
        self.row_map = []
        out = Text()
        cursor = self.index() if self.show_cursor() else None
        for i in range(self.get_scroll(), len(rows)):
            for line in rows[i]:
                if len(self.row_map) >= h:
                    break
                if self.row_map:
                    out.append("\n")
                is_cursor = i == cursor
                out.append_text(segments_text(line, is_cursor))
                if is_cursor:
                    out.append(" " * max(0, w - sum(len(t) for t, _ in line)), CURSOR)
                self.row_map.append(i)
        if not rows:
            out.append("  " + self.empty_message(), style("dim"))
        return out

    def on_click(self, event: events.Click) -> None:
        offset = event.get_content_offset(self)
        if offset is None or not 0 <= offset.y < len(getattr(self, "row_map", [])):
            return
        self.activate(self.row_map[offset.y])
        self.app.sync()  # type: ignore[attr-defined]

    def _wheel(self, step: int) -> None:
        n = len(self.rows(self.size.width + 1))
        self.set_index(max(0, min(n - 1, self.index() + step)))
        self.app.sync()  # type: ignore[attr-defined]

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self._wheel(3)

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self._wheel(-3)


class OptionsView(RowList):
    def rows(self, width: int) -> list[list[list[Segment]]]:
        items = self.s.filtered()
        occs = self.s.occurrences()
        spec_w = spec_width(items, width)
        return [option_segments(o, o.key in occs, spec_w, width, self.s.query) for o in items]

    def index(self) -> int:
        return self.s.list_index

    def set_index(self, i: int) -> None:
        self.s.list_index = i

    def get_scroll(self) -> int:
        return self.s.list_scroll

    def set_scroll(self, v: int) -> None:
        self.s.list_scroll = v

    def show_cursor(self) -> bool:
        return self.s.focus == "list"

    def empty_message(self) -> str:
        return "no options match"

    def activate(self, i: int) -> None:
        """Click: toggle the option (and jump to its value box when it needs one)."""
        s = self.s
        s.commit_field()
        opt = s.filtered()[i]
        if s.searching or s.query:
            s.pick(opt, jump_to_field=True)
        else:
            s.focus = "list"
            s.list_index = i
            s.toggle(opt, jump_to_field=True)


class HistoryView(RowList):
    def rows(self, width: int) -> list[list[list[Segment]]]:
        return [history_segments(e, width, self.s.hist_query) for e in self.s.hist_filtered()]

    def index(self) -> int:
        return self.s.hist_index

    def set_index(self, i: int) -> None:
        self.s.hist_index = i

    def get_scroll(self) -> int:
        return self.s.hist_scroll

    def set_scroll(self, v: int) -> None:
        self.s.hist_scroll = v

    def empty_message(self) -> str:
        return history_empty_message(self.s)

    def activate(self, i: int) -> None:
        """Click selects; clicking the selected entry loads it."""
        s = self.s
        if i == s.hist_index:
            s.load_entry(s.hist_filtered()[i])
        else:
            s.hist_index = i


class HelpView(SessionView):
    DEFAULT_CSS = """
    HelpView { height: 1fr; border: round $accent; border-title-color: $accent; border-title-style: bold;
               border-subtitle-color: $text-muted; }
    """

    def on_mount(self) -> None:
        self.border_title = "Keys"

    def render(self) -> Text:
        lines = help_lines(self.size.width + 1)
        h = max(1, self.size.height)
        s = self.s
        s.help_scroll = min(s.help_scroll, max(0, len(lines) - h))
        shown = min(len(lines), s.help_scroll + h)
        subtitle = f"{s.help_scroll + 1}-{shown} of {len(lines)} · j/k scroll" if len(lines) > h else ""
        if subtitle != self.border_subtitle:
            # Changing the border mid-render isn't repainted; apply it right after.
            self.call_later(setattr, self, "border_subtitle", subtitle)
        out = Text()
        for i, (text, kind, key_end) in enumerate(lines[s.help_scroll:shown]):
            if i:
                out.append("\n")
            if kind == "section":
                out.append(text, style("section"))
            elif kind == "key":
                out.append(text[:key_end], style("key"))
                out.append(text[key_end:])
            else:
                out.append(text)
        return out

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self.s.help_scroll += 3
        self.refresh()

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self.s.help_scroll = max(0, self.s.help_scroll - 3)
        self.refresh()


def keybar_text(s: Session) -> Text:
    out = Text()
    for n, (key, desc) in enumerate(key_hints(s)):
        if n:
            out.append(" · ", style("dim"))
        out.append(key, style("key"))
        out.append(f" {desc}", style("dim"))
    return out


class HptuiApp(App):
    ENABLE_COMMAND_PALETTE = False  # it would steal ^P, which hptui uses
    BINDINGS = [
        Binding("ctrl+c", "cancel", show=False, priority=True),
        Binding("ctrl+q", "cancel", show=False, priority=True),
    ]
    CSS = """
    Screen { layout: vertical; }
    #title { height: 1; background: $panel; padding: 0 0; }
    #status { height: 1; color: $text-muted; padding: 0 1; }
    #status.-message { color: $accent; }
    #keys { height: 1; background: $panel; padding: 0 1; }
    """

    def __init__(self, session: Session, title: str) -> None:
        super().__init__()
        self.session = session
        self.title_text = title

    def compose(self) -> ComposeResult:
        yield Static(segments_text(title_segments(self.title_text)), id="title")
        yield CommandBox()
        yield FieldsView()
        yield OptionsView()
        yield HistoryView()
        yield HelpView()
        yield Static("", id="status")
        yield Static("", id="keys")

    def on_mount(self) -> None:
        self.theme = "dracula"
        self.sync()

    def on_key(self, event: events.Key) -> None:
        key = translate(event)
        if key is None:
            return
        event.stop()
        event.prevent_default()
        self.session.handle(key)
        self.sync()

    def on_paste(self, event: events.Paste) -> None:
        s = self.session
        typing = s.hist_searching if s.hist_open else (s.focus != "list" or s.searching)
        if not typing or s.help_open:
            return
        for ch in event.text.replace("\r", "").replace("\n", " "):
            if ch.isprintable():
                s.handle(ch)
        self.sync()

    def action_cancel(self) -> None:
        self.session.cancel()
        self.exit()

    def sync(self) -> None:
        """Push session state into the widgets."""
        s = self.session
        if s.done:
            self.exit()
            return
        cmd = self.query_one(CommandBox)
        fields = self.query_one(FieldsView)
        options = self.query_one(OptionsView)
        hist = self.query_one(HistoryView)
        help_view = self.query_one(HelpView)
        help_view.display = s.help_open

        cmd.set_class(s.focus == "cmd" and not s.hist_open and not s.help_open, "-focused")

        n_fields = len(s.value_options())
        fields.display = n_fields > 0 and not s.hist_open and not s.help_open
        fields.set_class(s.focus.startswith("field:"), "-focused")

        options.display = not s.hist_open and not s.help_open
        header, hint = options_header(s)
        options.border_title = header
        options.border_subtitle = hint + ("▏" if s.searching else "")
        options.set_class(s.focus == "list", "-focused")
        options.set_class(s.searching, "-searching")

        hist.display = s.hist_open and not s.help_open
        header, hint = history_header(s)
        hist.border_title = header
        hist.border_subtitle = hint + ("▏" if s.hist_searching else "")
        hist.set_class(True, "-focused")
        hist.set_class(s.hist_searching, "-searching")

        status = self.query_one("#status", Static)
        status.update(s.message or (history_status(s) if s.hist_open and not s.help_open else ""))
        status.set_class(bool(s.message), "-message")
        self.query_one("#keys", Static).update(keybar_text(s))

        for w in (cmd, fields, options, hist, help_view):
            w.refresh()


def run(
    options: list[Option],
    command: str,
    title: str | None = None,
    *,
    program: str | None = None,
    open_history: bool = False,
    standalone: bool = False,
) -> Session:
    """Show the Textual TUI. The returned session's .result is the final command (None if cancelled)."""
    s = new_session(options, command, program=program, open_history=open_history, standalone=standalone)
    HptuiApp(s, title or command or "history").run()
    return s
