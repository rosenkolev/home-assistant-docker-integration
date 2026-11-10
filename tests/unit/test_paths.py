import os

from custom_components.docker_apps import _paths


def test__write_text_is_on_disk_before_the_rename(tmp_path, monkeypatch):
    events = []
    fsync, replace = os.fsync, os.replace
    monkeypatch.setattr(
        _paths.os, "fsync", lambda fd: events.append("fsync") or fsync(fd)
    )
    monkeypatch.setattr(
        _paths.os, "replace", lambda a, b: events.append("replace") or replace(a, b)
    )
    target = tmp_path / "app" / ".env"
    target.parent.mkdir()
    target.write_text("OLD=1\n", encoding="utf-8")

    _paths.write_text(str(target), "NEW=1\n", private=True)

    assert target.read_text(encoding="utf-8") == "NEW=1\n"
    assert events[:2] == ["fsync", "replace"]
    # the folder too, where folders can be opened (not on Windows)
    assert events.count("fsync") == (1 if os.name == "nt" else 2)
    assert not list(target.parent.glob("*.tmp"))
