"""Build a standalone hptui executable with PyInstaller.

    uv run --group build python scripts/build_exe.py

Produces dist/hptui.exe on Windows (dist/hptui on Linux). PyInstaller can't
cross-compile, so build the .exe on Windows; CI does this in .github/workflows/build.yml.
"""

from __future__ import annotations

import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"


def main() -> None:
    BUILD.mkdir(exist_ok=True)
    entry = BUILD / "hptui_entry.py"
    entry.write_text("from hptui import main\n\nmain()\n", encoding="utf-8")

    PyInstaller.__main__.run([
        str(entry),
        "--name", "hptui",
        "--onefile",
        "--console",
        "--clean",
        "--noconfirm",
        "--paths", str(ROOT / "src"),
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(BUILD / "work"),
        "--specpath", str(BUILD),
        # Textual and Rich load parts of themselves dynamically (drivers, CSS,
        # unicode width tables), which PyInstaller's import scan can't see.
        "--collect-submodules", "textual",
        "--collect-data", "textual",
        "--collect-submodules", "rich",
        # Frontends are imported lazily inside main(), so name them explicitly.
        "--hidden-import", "hptui.curses_ui",
        "--hidden-import", "hptui.textual_ui",
        *(["--hidden-import", "_curses"] if sys.platform == "win32" else []),
    ])
    exe = ROOT / "dist" / ("hptui.exe" if sys.platform == "win32" else "hptui")
    print(f"\nbuilt {exe} ({exe.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
