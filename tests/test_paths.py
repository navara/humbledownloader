import os

import pytest

from humbledownloader.catalog import FileEntry
from humbledownloader.paths import (
    PathError,
    assign_targets,
    check_component,
    clean_name,
    target_path,
)

LIB = os.path.abspath("lib")


def entry(
    product="Bundle", ent="Book", web="book.pdf", md5="a" * 32, key="k", eid="e1"
):
    return FileEntry("p1", product, eid, ent, key, "ebook", web, 10, md5)


@pytest.mark.parametrize(
    "title, folder",
    [
        # folder names as hbd created them
        (
            "Humble Book Bundle: Lantern Keeper's Night Library",
            "Humble Book Bundle - Lantern Keepers Night Library",
        ),
        (
            "Humble Tabletop Bundle: Sky Isles – Ships, Storms, Charts and more",
            "Humble Tabletop Bundle - Sky Isles  Ships Storms Charts and more",
        ),
        ("Humble Desk & Pocket Bundle 7", "Humble Desk  Pocket Bundle 7"),
        ("Far Away: Ω Edition Android", "Far Away - Ω Edition Android"),
        ("C++ Primer", "C__ Primer"),
        ("Ends with dots...", "Ends with dots"),
        ("Dot then space . ", "Dot then space"),
    ],
)
def test_clean_name(title, folder):
    assert clean_name(title) == folder


@pytest.mark.parametrize(
    "name", ["", "..", "CON", "nul.txt", "a.", "a ", "a:b", "a\x01"]
)
def test_check_component_rejects(name):
    with pytest.raises(PathError):
        check_component(name)


def test_target_path_uses_file_name_without_publisher_prefix():
    p = target_path(LIB, entry(product="B: X", web="publisher/game.zip"))
    assert p == os.path.join(LIB, "B - X", "Book", "game.zip")


def test_target_path_too_long():
    with pytest.raises(PathError, match="too long"):
        target_path(LIB, entry(ent="x" * 250))


def test_empty_title_is_an_error():
    with pytest.raises(PathError):
        target_path(LIB, entry(product="!!!"))


def test_assign_targets_shares_same_md5_and_reports_conflicts():
    a = entry(eid="e1")
    b = entry(eid="e2")  # same path, same md5: shared
    c = entry(web="other.pdf", md5="b" * 32, eid="e3")
    d = entry(web="OTHER.pdf", md5="c" * 32, eid="e4")  # same path on NTFS, other md5
    bad = entry(ent="CON", eid="e5")
    targets, conflicts, errors = assign_targets(LIB, [a, b, c, d, bad])
    assert (
        targets[a.key]
        == targets[b.key]
        == os.path.join(LIB, "Bundle", "Book", "book.pdf")
    )
    assert c.key not in targets and d.key not in targets
    assert len(conflicts) == 1 and len(next(iter(conflicts.values()))) == 2
    assert [e.key for e, _ in errors] == [bad.key]


@pytest.mark.parametrize("name", ["COM¹", "CONIN$", "CON .txt", "lpt1.pdf"])
def test_check_component_rejects_other_reserved_forms(name):
    with pytest.raises(PathError):
        check_component(name)


def test_check_component_works_where_os_path_is_posixpath(monkeypatch):
    import posixpath

    from humbledownloader import paths

    monkeypatch.setattr(paths.os, "path", posixpath)
    check_component("book.pdf")
    with pytest.raises(PathError):
        check_component("CONIN$")


def test_share_root_library_works():
    p = target_path("\\server\\share", entry())
    assert p.endswith(os.path.join("Bundle", "Book", "book.pdf"))
