"""Textual frontend: mouse support and nicer styling, but slower to start than curses.

Behaviour lives in session.Session; this module only draws it and turns Textual
key and mouse events into the same keys the curses frontend sends.
"""

from __future__ import annotations

import curses  # only for the KEY_* constants; curses is never initialised here

from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import Static

from .parser import Option
from .session import (
    ESC, LineEdit, Session, fit_scroll, help_lines, history_empty_message, history_header, history_lines,
    history_status, key_hints, new_session, option_lines, options_header, spec_width,
)

_KEYS = {
    "up": curses.KEY_UP, "down": curses.KEY_DOWN, "left": curses.KEY_LEFT, "right": curses.KEY_RIGHT,
    "home": curses.KEY_HOME, "end": curses.KEY_END, "pageup": curses.KEY_PPAGE, "pagedown": curses.KEY_NPAGE,
    "delete": curses.KEY_DC, "shift+tab": curses.KEY_BTAB,
    "enter": "\r", "escape": ESC, "tab": "\t", "backspace": "\x7f", "ctrl+h": "\x7f", "space": " ",
}


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


def _edit_text(box: LineEdit, width: int, show_cursor: bool, placeholder: str = "") -> Text:
    visible, col = box.visible(max(1, width - 1))
    if not visible and not show_cursor:
        return Text(placeholder, style="dim")
    text = Text(visible)
    if show_cursor:
        if col >= len(visible):
            text.append(" ")
        text.stylize("reverse", col, col + 1)
    return text


class SessionView(Widget):
    @property
    def s(self) -> Session:
        return self.app.session  # type: ignore[attr-defined]

    def style_of(self, name: str) -> Style:
        return self.get_component_rich_style(name, partial=True)


class CommandBox(SessionView):
    COMPONENT_CLASSES = {"commandbox--prompt"}
    DEFAULT_CSS = """
    CommandBox { height: 3; border: round $foreground 25%; border-title-color: $text-muted; padding: 0 1; }
    CommandBox.-focused { border: round $accent; border-title-color: $accent; }
    CommandBox > .commandbox--prompt { color: $accent; text-style: bold; }
    """
    PROMPT = "❯ "

    def on_mount(self) -> None:
        self.border_title = "command"

    def render(self) -> Text:
        focused = self.s.focus == "cmd" and not self.s.hist_open and not self.s.help_open
        text = Text(self.PROMPT, style=self.style_of("commandbox--prompt"))
        text.append_text(_edit_text(self.s.cmd, self.size.width - len(self.PROMPT), focused))
        return text

    def on_click(self, event: events.Click) -> None:
        s = self.s
        if s.hist_open:
            return
        s.commit_field()
        s.searching = False
        s.focus = "cmd"
        offset = event.get_content_offset(self)
        if offset is not None:
            s.cmd.cursor = max(0, min(len(s.cmd.text), s.cmd.scroll + offset.x - len(self.PROMPT)))
        self.app.sync()  # type: ignore[attr-defined]


class FieldsView(SessionView):
    COMPONENT_CLASSES = {"fields--label", "fields--focused"}
    DEFAULT_CSS = """
    FieldsView { height: auto; border: round $foreground 25%; border-title-color: $text-muted; padding: 0 1; }
    FieldsView.-focused { border: round $accent; border-title-color: $accent; }
    FieldsView > .fields--label { color: $warning; }
    FieldsView > .fields--focused { color: $accent; text-style: bold; }
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
            out.append(f"{opt.name}:".ljust(label_w), self.style_of("fields--focused" if focused else "fields--label"))
            out.append_text(_edit_text(box, self.size.width - label_w, focused, f"<{opt.metavar}>"))
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
    """A scrolled list whose items can span several lines (wrapped text)."""

    COMPONENT_CLASSES = {"list--cursor", "list--checked", "list--dim"}
    DEFAULT_CSS = """
    RowList { height: 1fr; border: round $foreground 25%; border-title-color: $foreground;
               border-title-style: bold; border-subtitle-color: $text-muted; }
    RowList.-focused { border: round $accent; }
    RowList.-searching { border-subtitle-color: $accent; }
    RowList > .list--cursor { background: $accent 35%; text-style: bold; }
    RowList > .list--checked { color: $success; text-style: bold; }
    RowList > .list--dim { color: $text-muted; }
    """
    row_map: list[int]

    def rows(self, width: int) -> list[list[str]]: ...
    def index(self) -> int: ...
    def set_index(self, i: int) -> None: ...
    def get_scroll(self) -> int: ...
    def set_scroll(self, v: int) -> None: ...
    def is_checked(self, i: int) -> bool:
        return False
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
            style = Style()
            if self.is_checked(i):
                style += self.style_of("list--checked")
            if i == cursor:
                style += self.style_of("list--cursor")
            for line in rows[i]:
                if len(self.row_map) >= h:
                    break
                if self.row_map:
                    out.append("\n")
                out.append(line.ljust(w) if i == cursor else line, style)
                self.row_map.append(i)
        if not rows:
            out.append("  " + self.empty_message(), self.style_of("list--dim"))
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
    def rows(self, width: int) -> list[list[str]]:
        items = self.s.filtered()
        occs = self.s.occurrences()
        spec_w = spec_width(items, width)
        return [option_lines(o, o.key in occs, spec_w, width) for o in items]

    def index(self) -> int:
        return self.s.list_index

    def set_index(self, i: int) -> None:
        self.s.list_index = i

    def get_scroll(self) -> int:
        return self.s.list_scroll

    def set_scroll(self, v: int) -> None:
        self.s.list_scroll = v

    def is_checked(self, i: int) -> bool:
        return self.s.filtered()[i].key in self.s.occurrences()

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
    def rows(self, width: int) -> list[list[str]]:
        return [history_lines(e, width) for e in self.s.hist_filtered()]

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
    COMPONENT_CLASSES = {"help--section", "help--key"}
    DEFAULT_CSS = """
    HelpView { height: 1fr; border: round $accent; border-title-style: bold;
               border-subtitle-color: $text-muted; }
    HelpView > .help--section { color: $accent; text-style: bold; }
    HelpView > .help--key { color: $warning; text-style: bold; }
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
                out.append(text, self.style_of("help--section"))
            elif kind == "key":
                out.append(text[:key_end], self.style_of("help--key"))
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


class HptuiApp(App):
    ENABLE_COMMAND_PALETTE = False  # it would steal ^P, which hptui uses
    BINDINGS = [
        Binding("ctrl+c", "cancel", show=False, priority=True),
        Binding("ctrl+q", "cancel", show=False, priority=True),
    ]
    CSS = """
    Screen { layout: vertical; }
    #title { height: 1; background: $primary; color: $text; text-style: bold; padding: 0 1; }
    #status { height: 1; color: $text-muted; padding: 0 1; }
    #status.-message { color: $accent; }
    #keys { height: 1; background: $panel; color: $text-muted; padding: 0 1; }
    """

    def __init__(self, session: Session, title: str) -> None:
        super().__init__()
        self.session = session
        self.title_text = title

    def compose(self) -> ComposeResult:
        yield Static(f"hptui · {self.title_text}", id="title")
        yield CommandBox()
        yield FieldsView()
        yield OptionsView()
        yield HistoryView()
        yield HelpView()
        yield Static("", id="status")
        yield Static("", id="keys")

    def on_mount(self) -> None:
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
        if not typing:
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
        self.query_one("#keys", Static).update(key_hints(s))

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
