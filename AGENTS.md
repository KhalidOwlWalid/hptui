# Overview

hptui is basically a tool that helps in making --help pages more interactive. Essentially what it does is

```shell

...
 -n, --name=STRING       description for the NAME paragraph
 -s, --section=SECTION   section number for manual page (1, 6, 8)
 -m, --manual=TEXT       name of manual (User Commands, ...)
 -S, --source=TEXT       source of program (FSF, Debian, ...)
 -L, --locale=STRING     select locale (default "C")
...

```
Given that your help pages outputs the following. User can do something along the lines of:

```shell
help2man --help | hptui
```

What this then does is then parse all of the following outputs and then creates an interactive tui which the user can then select from the tui.

User can either choose to type in the box what commands they want, or if they selected the flag, the program automatically populates the field accordingly to the textbox and the user can continue typing the arguments afterwards. for each press, this will also create a new box which the user can input the argument, this updates the textbox as per the argument placed inside the newly generated box. Basically the base layout looks like:

## Base layout
```shell
[<original_command> text_box]

[ ] name
[ ] section
[ ] manual
...
```

## User selcting
```shell
[<original_command> --name=]

name: <user_input>

[x] name
[ ] section
[ ] manual
...
```

User can either update the user input from the text_box itself or they can update the newly generated `name: ` and when enter is pressed, it then automatically populates this into the text box

```shell
[<original_command> --name=KhalidOwlWalid]

name: KhalidOwlWalid

[x] name
[ ] section
[ ] manual
...
```

# Potential issues
There might be arguments that requires path, which may require "", so the program needs to be able to handle this accordingly
