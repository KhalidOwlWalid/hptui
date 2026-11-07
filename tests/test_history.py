from hptui import history


def test_history_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("HPTUI_HISTORY", str(tmp_path / "h.jsonl"))
    history.add("help2man --name=a prog")
    history.add("/usr/bin/ls -la")
    history.add("help2man --name=b prog")
    history.add("help2man --name=a prog")  # repeat moves it to the top

    assert [e.command for e in history.load("help2man")] == [
        "help2man --name=a prog",
        "help2man --name=b prog",
    ]
    assert [e.command for e in history.load("ls")] == ["/usr/bin/ls -la"]
    assert len(history.load()) == 3

    history.delete("help2man --name=b prog")
    assert [e.command for e in history.load("help2man")] == ["help2man --name=a prog"]


def test_corrupt_lines_are_skipped(tmp_path, monkeypatch):
    p = tmp_path / "h.jsonl"
    p.write_text('not json\n{"program": "x", "command": "x -y", "time": 1}\n')
    monkeypatch.setenv("HPTUI_HISTORY", str(p))
    assert [e.command for e in history.load()] == ["x -y"]


def test_config_roundtrip(tmp_path, monkeypatch):
    from hptui import config

    monkeypatch.setenv("HPTUI_CONFIG", str(tmp_path / "c.json"))
    assert config.load()["frontend"] == "curses"  # default
    config.save(frontend="textual")
    assert config.load()["frontend"] == "textual"
    config.save(frontend="curses")
    assert config.load()["frontend"] == "curses"
