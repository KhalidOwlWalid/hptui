"""Special key codes shared by every frontend.

They come from curses when it is available, so the curses frontend can pass its
keys straight through (the values differ between ncurses and PDCurses on
Windows). Without curses, any distinct values work because only the Textual
frontend is in use and it translates its own events into these.
"""

try:
    import curses

    AVAILABLE = True
    KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT = curses.KEY_UP, curses.KEY_DOWN, curses.KEY_LEFT, curses.KEY_RIGHT
    KEY_HOME, KEY_END, KEY_PPAGE, KEY_NPAGE = curses.KEY_HOME, curses.KEY_END, curses.KEY_PPAGE, curses.KEY_NPAGE
    KEY_DC, KEY_BACKSPACE, KEY_ENTER, KEY_BTAB = curses.KEY_DC, curses.KEY_BACKSPACE, curses.KEY_ENTER, curses.KEY_BTAB
    KEY_RESIZE = curses.KEY_RESIZE
except ImportError:  # e.g. Windows without the windows-curses package
    AVAILABLE = False
    KEY_DOWN, KEY_UP, KEY_LEFT, KEY_RIGHT, KEY_HOME, KEY_BACKSPACE = 258, 259, 260, 261, 262, 263
    KEY_DC, KEY_NPAGE, KEY_PPAGE, KEY_ENTER, KEY_BTAB, KEY_END, KEY_RESIZE = 330, 338, 339, 343, 353, 360, 410
