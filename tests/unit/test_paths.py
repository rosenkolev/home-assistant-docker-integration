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


def test__a_private_file_is_never_readable_by_others(tmp_path, monkeypatch):
    modes = []
    chmod = os.chmod
    monkeypatch.setattr(
        _paths.os, "chmod", lambda path, mode: modes.append(mode) or chmod(path, mode)
    )
    opened = []
    real_open = os.open
    monkeypatch.setattr(
        _paths.os,
        "open",
        lambda path, flags, mode=0o777: opened.append(mode)
        or real_open(path, flags, mode),
    )
    _paths.write_text(str(tmp_path / ".env"), "A=1\n", private=True)
    assert opened[0] == 0o600  # created closed, not opened up later
    assert modes == [0o600]


def test__the_owner_gets_files_and_new_folders(tmp_path, monkeypatch):
    owned = []
    monkeypatch.setattr(
        _paths.os,
        "chown",
        lambda path, uid, gid: owned.append((path, uid, gid)),
        raising=False,
    )
    modes = {}
    chmod = os.chmod
    monkeypatch.setattr(
        _paths.os,
        "chmod",
        lambda path, mode: modes.__setitem__(path, mode) or chmod(path, mode),
    )
    app = tmp_path / "apps" / "demo"
    _paths.write_text(
        str(app / "compose.override.yaml"),
        "services: {}\n",
        owner=(1000, 1000),
        private=_paths.is_private("compose.override.yaml"),
    )
    _paths.write_text(str(app / "compose.yaml"), "services: {}\n", owner=(1000, 1000))

    assert (str(app.parent), 1000, 1000) in owned  # the new folders
    assert modes[str(app)] == 0o770
    private_tmp = str(app / "compose.override.yaml") + ".docker-apps.tmp"
    plain_tmp = str(app / "compose.yaml") + ".docker-apps.tmp"
    assert modes[private_tmp] == 0o660  # the owner's group reads it, others not
    assert modes[plain_tmp] == 0o664


def test__without_chown_private_files_stay_owner_only(tmp_path, monkeypatch):
    def refuse(*args):
        raise PermissionError("not root")

    monkeypatch.setattr(_paths.os, "chown", refuse, raising=False)
    modes = []
    chmod = os.chmod
    monkeypatch.setattr(
        _paths.os, "chmod", lambda path, mode: modes.append(mode) or chmod(path, mode)
    )
    _paths.write_text(str(tmp_path / ".env"), "A=1\n", private=True, owner=(1000, 1000))
    assert modes == [0o600]
