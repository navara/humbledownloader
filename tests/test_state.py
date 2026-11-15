import json

import pytest

from humbledownloader import state
from humbledownloader.catalog import FileEntry
from humbledownloader.state import Action, decide

E = FileEntry("p", "B", "e", "T", "k", "ebook", "a.pdf", 100, "m" * 32)


@pytest.mark.parametrize(
    "record, disk_size, expected",
    [
        (None, None, Action.DOWNLOAD),
        (None, 100, Action.ADOPT),  # existing library file: never re-download
        (None, 99, Action.UPDATE),
        ({"md5": "m" * 32}, 100, Action.SKIP),
        ({"md5": "m" * 32}, None, Action.DOWNLOAD),
        ({"md5": "m" * 32}, 99, Action.UPDATE),  # changed on disk
        ({"md5": "x" * 32}, 100, Action.UPDATE),  # new version in the catalog
        ({"md5": "x" * 32}, None, Action.DOWNLOAD),
        # Humble served a newer file than its catalog lists
        ({"md5": "m" * 32, "disk_size": 150}, 150, Action.SKIP),
        ({"md5": "m" * 32, "disk_size": 150}, 100, Action.UPDATE),
        ({"md5": "x" * 32, "disk_size": 150}, 150, Action.UPDATE),  # catalog moved on
        # the catalog caught up with the newer file already on disk
        ({"md5": "x" * 32, "disk_size": 100, "disk_md5": "m" * 32}, 100, Action.SKIP),
        ({"md5": "x" * 32, "disk_size": 150, "disk_md5": "m" * 32}, 150, Action.UPDATE),
    ],
)
def test_decide(record, disk_size, expected):
    assert decide(E, record, disk_size) is expected


@pytest.mark.parametrize(
    "record, recorded_size, expected",
    [
        ({"md5": "m" * 32, "path": "Old/T/a.pdf"}, 100, Action.MOVE),  # folder renamed
        ({"md5": "m" * 32, "path": "Old/T/a.pdf"}, None, Action.DOWNLOAD),  # gone
        ({"md5": "m" * 32, "path": "Old/T/a.pdf"}, 5, Action.DOWNLOAD),  # changed
        ({"md5": "x" * 32, "path": "Old/T/a.pdf"}, 100, Action.DOWNLOAD),  # new version
    ],
)
def test_decide_moves_a_tracked_file_whose_folder_changed(
    record, recorded_size, expected
):
    assert decide(E, record, None, recorded_size) is expected


def test_missing_state_is_empty(tmp_path):
    assert state.load(str(tmp_path / "s.json")) == {"version": 1, "files": {}}


def test_save_and_load_round_trip(tmp_path):
    p = str(tmp_path / "s.json")
    data = {"version": 1, "files": {"k": {"path": "Ω/a.pdf", "size": 1}}}
    state.save(p, data)
    assert state.load(p) == data
    assert not (tmp_path / "s.json.tmp").exists()


@pytest.mark.parametrize("content", ['{"half": ', "[]", '{"version": 2, "files": {}}'])
def test_damaged_state_stops_and_is_left_untouched(tmp_path, content):
    p = tmp_path / "s.json"
    p.write_text(content, encoding="utf-8")
    with pytest.raises(state.StateError):
        state.load(str(p))
    assert p.read_text(encoding="utf-8") == content


def test_save_retries_a_locked_file_then_fails_without_touching_it(
    tmp_path, monkeypatch
):
    p = tmp_path / "s.json"
    old = json.dumps({"version": 1, "files": {}})
    p.write_text(old, encoding="utf-8")
    calls = []

    def locked(src, dst):
        calls.append(1)
        raise PermissionError("locked")

    monkeypatch.setattr(state.os, "replace", locked)
    monkeypatch.setattr(state.time, "sleep", lambda s: None)
    with pytest.raises(state.StateError, match="locked"):
        state.save(str(p), {"version": 1, "files": {"k": {}}})
    assert len(calls) == 4
    assert p.read_text(encoding="utf-8") == old
    assert json.loads((tmp_path / "s.json.tmp").read_text(encoding="utf-8"))[
        "files"
    ] == {"k": {}}


def test_save_succeeds_after_a_brief_lock(tmp_path, monkeypatch):
    p = str(tmp_path / "s.json")
    real = state.os.replace
    attempts = iter([PermissionError("locked"), None])

    def flaky(src, dst):
        err = next(attempts)
        if err:
            raise err
        real(src, dst)

    monkeypatch.setattr(state.os, "replace", flaky)
    monkeypatch.setattr(state.time, "sleep", lambda s: None)
    state.save(p, {"version": 1, "files": {"k": {}}})
    assert state.load(p)["files"] == {"k": {}}


def test_record_for_stores_relative_path(tmp_path):
    lib = str(tmp_path)
    r = state.record_for(lib, str(tmp_path / "B" / "T" / "a.pdf"), E, "adopted", False)
    assert r == {
        "path": "B\\T\\a.pdf" if "\\" in str(tmp_path) else "B/T/a.pdf",
        "size": 100,
        "md5": "m" * 32,
        "verified": False,
        "source": "adopted",
    }


def test_decide_moves_a_newer_than_catalog_file_by_its_real_size():
    rec = {"md5": "m" * 32, "disk_size": 150}
    assert decide(E, rec, None, recorded_size=150) is Action.MOVE
    assert decide(E, rec, None, recorded_size=100) is Action.DOWNLOAD
    caught_up = {"md5": "x" * 32, "disk_size": 100, "disk_md5": "m" * 32}
    assert decide(E, caught_up, None, recorded_size=100) is Action.MOVE
    assert decide(E, {"md5": "x" * 32}, None, recorded_size=None) is Action.DOWNLOAD


def test_record_for_keeps_the_real_size_only_when_it_differs(tmp_path):
    lib = str(tmp_path)
    path = str(tmp_path / "a.pdf")
    same = state.record_for(lib, path, E, "download", True, 100, "m" * 32)
    assert "disk_size" not in same and "disk_md5" not in same
    other = state.record_for(lib, path, E, "download", True, 150, "n" * 32)
    assert (other["size"], other["md5"]) == (100, "m" * 32)
    assert (other["disk_size"], other["disk_md5"]) == (150, "n" * 32)
