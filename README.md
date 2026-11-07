# hptui

Turn any program's `--help` output into an interactive command builder.

```shell
help2man --help | hptui        # build a command, then run it
hptui help2man                 # same, runs `help2man --help` for you
hptui -p help2man              # print the command instead of running it
eval "$(hptui help2man)"       # run it in your current shell (e.g. for cd/export)
```

When hptui's output goes to your terminal it runs the command with your
`$SHELL`. When the output is captured (`$(...)`, a pipe, a file) it prints the
command instead. `-x` forces running and `-p` forces printing.

## Install

From the repo root:

```shell
uv tool install .              # installs `hptui` into ~/.local/bin
uv tool install --editable .   # same, but code changes apply without reinstalling
```

Make sure `~/.local/bin` is on your `PATH` (`uv tool update-shell` adds it).
Then `hptui` works from any directory.

```shell
uv tool upgrade hptui          # pick up changes after a normal (non-editable) install
uv tool uninstall hptui        # remove it
```

## Keys

The option list uses vim-style keys. Arrow keys work everywhere too.

| Where        | Key                         | Action                                          |
|--------------|-----------------------------|-------------------------------------------------|
| anywhere     | `Tab` / `Shift-Tab`         | move between command box, value boxes, options  |
| anywhere     | `Ctrl-P`                    | jump between the command box and the options    |
| anywhere     | `Ctrl-S`                    | done: run the command (or print, see above)     |
| anywhere     | `Ctrl-C`                    | quit without output                             |
| option list  | `j` / `k`                   | down / up (`k` at the top moves to the boxes)   |
| option list  | `g` / `G`, `Ctrl-D`/`Ctrl-U`| top / bottom, half-page down / up               |
| option list  | `Space`                     | toggle option                                   |
| option list  | `l` / `Enter`               | toggle option and jump to its value box         |
| option list  | `h`                         | uncheck option                                  |
| option list  | `/`                         | quick search                                    |
| option list  | `i` / `a`                   | edit the command box                            |
| option list  | `Esc`                       | clear filter                                    |
| option list  | `q`                         | quit without output                             |
| search       | type, `Backspace`, `Ctrl-W` | edit the query                                  |
| search       | `↑`/`↓`, `Ctrl-D`/`Ctrl-U`  | move through matches                            |
| search       | `Enter`                     | pick the match, clear the search, edit value    |
| search       | `Esc`                       | stop typing but keep results to browse with j/k |
| option list  | `H` (or `Ctrl-R` anywhere)  | open this program's command history             |
| history      | `j`/`k`, `g`/`G`, `/`       | move, jump, search (same as the option list)    |
| history      | `Enter` / `l`               | load the command into the box to tweak it       |
| history      | `r` / `Ctrl-S`              | run the command as-is                           |
| history      | `d`                         | delete the entry                                |
| history      | `Esc` / `q` / `h`           | back to the options                             |
| value box    | `Enter`                     | write the value into the command                |
| value box    | `Esc`                       | discard the edit                                |
| command box  | `Enter`                     | done                                            |
| command box  | `Esc`                       | back to the option list                         |

The command box is the source of truth: you can type options straight into
it and the checkboxes and value boxes follow. Values with spaces or special
characters are quoted automatically (`--output="my file"`, `~/"My Docs"`).

## History

Every command you confirm is saved to `~/.local/state/hptui/history.jsonl`
(override with `$HPTUI_HISTORY`, or `$XDG_STATE_HOME`). It is one global file,
but inside `hptui help2man` you only see `help2man` commands. The newest 1000
distinct commands are kept.

```shell
hptui --history                # browse every program's history
hptui --history help2man       # open straight into help2man's history
alias hh='hptui --history'     # pick a past command and run it
```

In `hptui --history`, `Enter` on an entry runs that program's `--help` and
opens the full option editor with the command loaded.

## Development

```shell
uv run pytest
```
