# hptui: AI-Slop interactive cli helper

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

Make sure uv's tool directory (`~/.local/bin`) is on your `PATH`; `uv tool update-shell` adds it on any OS.
Then `hptui` works from any directory.

```shell
uv tool upgrade hptui          # pick up changes after a normal (non-editable) install
uv tool uninstall hptui        # remove it
```

## Windows

hptui runs on Windows 10/11; Windows Terminal is recommended for colours and
Unicode. Installing it pulls in `windows-curses` automatically, and if curses is
ever unavailable hptui falls back to the Textual frontend.

```powershell
uv tool install .
git --help | hptui
hptui git
```

Differences from Linux:

- The finished command runs through `cmd.exe` (`%COMSPEC%`), and values are
  quoted with Windows rules: double quotes only, and backslashes in paths are
  left alone (`--output="C:\My Docs\out.txt"`).
- History lives in `%LOCALAPPDATA%\hptui\history.jsonl` and settings in
  `%APPDATA%\hptui\config.json`.
- Help text is parsed for `-x` / `--long` style options; Windows-style `/X`
  switches aren't recognised.

## Standalone executable

`hptui.exe` bundles Python and every dependency into one file, so it runs on a
Windows machine with nothing else installed. Put it anywhere on your `PATH`.

Build it on Windows (PyInstaller can't cross-compile):

```powershell
uv run --group build python scripts/build_exe.py   # -> dist\hptui.exe
```

The same command on Linux produces a `dist/hptui` binary. On GitHub, the
`build` workflow builds the `.exe` on demand (Actions tab -> build -> Run
workflow) and attaches it to a release when you push a version tag
(`git tag v0.1.0 && git push --tags`).

The single-file build unpacks itself on each launch, so it starts a little
slower (about 0.35s) than the `uv tool install` version.

## Frontends

hptui has two interchangeable frontends with the same keys and behaviour:

| Frontend            | Notes                                                              |
|---------------------|--------------------------------------------------------------------|
| `curses` (default)  | Faster: starts about 4x quicker, uses only the Python standard library |
| `textual`           | Mouse support (click to toggle, scroll wheel), paste, nicer styling |

```shell
hptui --frontend=textual       # switch; remembered for future runs
hptui --frontend=curses        # switch back
```

The choice is saved in `~/.config/hptui/config.json` (override with
`$HPTUI_CONFIG` or `$XDG_CONFIG_HOME`). Passing `--frontend` alongside a
program (`hptui --frontend=textual ls`) uses and saves it in one go.

## Keys

Press `?` in the option list or history to see all of these inside hptui.

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
| option list  | `l` / `Enter` on a `›` row  | open that subcommand's page                     |
| option list  | `h` on the `●` row          | remove the subcommand (and what follows it)     |
| option list  | `l` / `Space` on an argument | type its value (`h` clears it)                 |
| option list  | `Esc`                       | clear filter, else back to the parent command   |
| option list  | `Backspace`                 | back to the parent command                      |
| option list  | `?`                         | show every keybinding (also from history)       |
| option list  | `q`                         | quit without output                             |
| search       | type, `Backspace`, `Ctrl-W` | edit the query                                  |
| search       | `↑`/`↓`, `Ctrl-D`/`Ctrl-U`  | move through matches                            |
| search       | `Enter`                     | pick the match, clear the search, edit value    |
| search       | `Esc`                       | stop typing but keep results to browse with j/k |
| option list  | `H` (or `Ctrl-R` anywhere)  | open this program's command history             |
| option list  | `E`                         | open the examples from the help page            |
| option list  | `D`                         | details: read this page's whole help text       |
| history      | `j`/`k`, `g`/`G`, `/`       | move, jump, search (same as the option list)    |
| history      | `Enter` / `l`               | load the command into the box to tweak it       |
| history      | `r` / `Ctrl-S`              | run the command as-is                           |
| history      | `d`                         | delete the entry                                |
| history      | `H` / `E`                   | switch between history and examples             |
| history      | `Esc` / `q` / `h`           | back to the options                             |
| value box    | `Enter`                     | write the value into the command                |
| value box    | `Esc`                       | discard the edit                                |
| command box  | `Enter`                     | done                                            |
| command box  | `Esc`                       | back to the option list                         |

The command box is the source of truth: you can type options straight into
it and the checkboxes and value boxes follow. Values with spaces or special
characters are quoted automatically (`--output="my file"`, `~/"My Docs"`).

## Subcommands

Programs like `git`, `docker`, `uv`, `pip`, `apt` and `systemctl` list
subcommands in their help. They show up at the top of the list (`› commit`).
Open one with `l` / `Enter` to get a page with that subcommand's own options,
loaded from `git commit -h` (or `docker container --help`, and so on). `Esc` or
`Backspace` goes back to the parent page, and the subcommand in use is marked
with `●`. Pages can nest (`docker container ls`).

Each page only edits its own part of the command, so in
`git -C repo commit -m "msg"` the git page owns `-C repo` and the commit page
owns `-m "msg"`. Options ticked on the git page go before `commit`.

`hptui git commit` opens straight on the commit page, with git's page behind
it. Loading a history entry or example opens the page of the subcommand it uses.

## Details

`D` opens the current page's whole help text (the program's description,
usage, notes and anything else that isn't an option), scrollable with
`j`/`k`, `Ctrl-D`/`Ctrl-U` and `g`/`G`. On a subcommand page it shows that
subcommand's help. `Esc` or `D` closes it, and `?` switches to the key reference.

## Custom parsers

Some programs format their help in ways hptui can't follow. For those, paste
the help into a text file and add a few lines that point at its sections. The
installed file then becomes that program's help as far as hptui is concerned:
`hptui <program>` reads it instead of running `--help`.

```text
usage: mytool [-h] [-o DIR] [file ...]
...the rest of the help, pasted exactly...

--- hptui ---
positionals: after "positional arguments:" before "options:"
options: after "options:"
subcommands: after "Things you can do"
examples: after "Try these" before "Notes"
ignore: -h, --bogus
```

- Everything above `--- hptui ---` is the help text. You can edit it too,
  e.g. to fix a line hptui misreads.
- Each marker is `<kind>: after "<heading>"`, with an optional `before "<heading>"`.
  The kinds are `options`, `positionals`, `subcommands` and `examples`. A heading
  matches the first line that starts with that text, ignoring case. Without
  `before`, the section ends at the next line indented no deeper than the
  heading.
- Inside a marked section, every entry is read as that kind: subcommands
  don't need a "Commands:" heading, and every example line counts as an
  example. Text that isn't marked is parsed as usual.
- `ignore:` hides entries (flags, subcommand or argument names). `program:`
  says which program the file is for, e.g. `program: git commit` for a
  subcommand's page. Otherwise it's taken from the usage line, then the file
  name. Lines starting with `#` are comments.

```shell
hptui parser --init mytool             # mytool.txt: the current help + marker hints
hptui parser --check mytool.txt        # what the file gives, without installing it
hptui parser --file mytool.txt         # install it
hptui parser --init git commit         # git-commit.txt, for the `git commit` page
hptui parser --list                    # installed parsers
hptui parser --remove mytool           # back to hptui's own parsing
```

Installed files live in `~/.config/hptui/parsers/` (`%APPDATA%\hptui\parsers`
on Windows, or `$HPTUI_PARSERS`), one per program or subcommand page, e.g.
`mytool.txt` and `git commit.txt`. Markers that point at headings the help
doesn't contain are reported with their line number when you check or install.

## Positional arguments

Arguments like `program` in `hptui [options] [program ...]` show up as rows
under the subcommands. hptui finds them in argparse- and clap-style
"positional arguments:" / "Arguments:" sections, or wherever a parser file's
`positionals:` marker points. `l` or `Space` opens a value box. A single argument
is quoted like any value, while a repeatable one (`program ...`) takes several
words exactly as you type them. `h` clears it. Options you tick go before the
arguments.

## Examples

If the help page has an "Examples:" section (like `tar --help`), press `E` to
pick one; it loads into the command box with its options ticked, ready to tweak.
`hptui --examples tar` opens straight into them. Descriptions come from inline
`# comments` or the line above each command.

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

Code layout: `parser.py` reads help text, `custom.py` applies custom parser files, `cmdline.py` edits the command line,
`history.py` and `config.py` handle storage, `session.py` holds all UI state
and key handling, and `curses_ui.py` / `textual_ui.py` only draw it.
