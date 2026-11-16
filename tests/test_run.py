import datetime
import hashlib
import json
import os

import pytest

from humbledownloader import state as st
from humbledownloader.run import LibraryLocked, Lock, run
from humbledownloader.site import Site
from tests.fakes import (
    CHUNK,
    CHUNK_JS,
    FakeHttp,
    FakeResponse,
    logged_in_page,
    md5,
    rsc,
)
from tests.flightdata import download, entitlement, item_props, struct

A = b"adopt me"
B = b"download me"
MAC = b"mac build"


def item_page():
    ents = [
        entitlement(
            "e1",
            "Book",
            [
                download("book_ebook", "ebook", [struct("book.pdf", len(A), md5(A))]),
                download(
                    "book_extra", "ebook", [struct("pub/extra.pdf", len(B), md5(B))]
                ),
            ],
        ),
        entitlement(
            "e2",
            "Game",
            [download("game_mac", "mac", [struct("g.dmg", len(MAC), md5(MAC))])],
        ),
        entitlement("e3", "No Content", "$undefined"),
    ]
    return logged_in_page(item_props("p1", "Bundle: One", ents))


def make_http(cdn=None):
    http = FakeHttp()
    http.add(
        "GET",
        "/home/library",
        FakeResponse(
            200,
            logged_in_page(
                {
                    "bundleItems": [
                        {"productEntitlementId": "p1", "title": "Bundle: One"}
                    ]
                }
            ),
        ),
    )
    http.add("GET", CHUNK, FakeResponse(200, CHUNK_JS))
    http.add(
        "POST",
        "/home/library",
        FakeResponse(
            200,
            rsc(
                {
                    "results": [],
                    "pagination": {"page": 1, "totalPages": 1},
                    "success": True,
                }
            ),
        ),
    )
    http.add("GET", "/home/library/p1", FakeResponse(200, item_page()))
    files = {
        "book_extra": {"pub/extra.pdf": "https://cdn.humble.com/extra.pdf?t=s"},
        "book_ebook": {"book.pdf": "https://cdn.humble.com/book.pdf?t=s"},
    }
    http.add(
        "POST",
        "/home/library/p1",
        FakeResponse(
            200,
            rsc({"ok": True, "data": {"signedUrls": {"m": files}, "expiresAt": "x"}}),
        ),
    )
    cdn = cdn or FakeHttp()
    cdn.add("GET", "https://cdn.humble.com/extra.pdf?t=s", FakeResponse(200, content=B))
    cdn.add("GET", "https://cdn.humble.com/book.pdf?t=s", FakeResponse(200, content=A))
    return http, cdn


def go(lib, http, cdn, dry_run=False):
    return run(
        str(lib),
        Site(http, sleep=lambda s: None),
        {"mac", "linux"},
        dry_run=dry_run,
        http=cdn,
    )


def folder(lib):
    return lib / "Bundle - One" / "Book"


def test_existing_file_is_adopted_not_downloaded_and_rerun_does_nothing(tmp_path):
    folder(tmp_path).mkdir(parents=True)
    (folder(tmp_path) / "book.pdf").write_bytes(A)
    http, cdn = make_http()
    s = go(tmp_path, http, cdn)
    assert s.counts["adopted"] == 1 and s.counts["downloaded"] == 1
    assert s.counts["excluded"] == 1 and s.counts["items without files"] == 0
    assert (folder(tmp_path) / "extra.pdf").read_bytes() == B
    assert cdn.count("GET", "https://cdn.humble.com/book.pdf") == 0
    data = json.loads((tmp_path / st.STATE_NAME).read_text(encoding="utf-8"))
    assert data["files"]["e1/book_ebook/book.pdf"]["source"] == "adopted"
    assert data["files"]["e1/book_extra/pub/extra.pdf"]["verified"] is True
    assert not (tmp_path / ".humble-lock").exists()

    http2, cdn2 = make_http()
    s2 = go(tmp_path, http2, cdn2)
    assert s2.counts["skipped"] == 2 and not s2.counts["downloaded"]
    assert http2.count("POST", "https://www.humblebundle.com/home/library/p1") == 0
    assert cdn2.calls == []


def test_wrong_size_file_is_kept_aside_and_replaced(tmp_path):
    folder(tmp_path).mkdir(parents=True)
    (folder(tmp_path) / "book.pdf").write_bytes(b"old version, other size")
    old = datetime.datetime(2019, 3, 12, 12, 0).timestamp()
    os.utime(folder(tmp_path) / "book.pdf", (old, old))
    http, cdn = make_http()
    s = go(tmp_path, http, cdn)
    assert s.counts["updated"] == 1
    assert (folder(tmp_path) / "book.pdf").read_bytes() == A
    aside = [n for n in os.listdir(folder(tmp_path)) if "_old-" in n]
    assert aside == ["book_old-20190312.pdf"]
    assert (folder(tmp_path) / aside[0]).read_bytes() == b"old version, other size"


def test_dry_run_changes_nothing_and_signs_nothing(tmp_path):
    http, cdn = make_http()
    s = go(tmp_path, http, cdn, dry_run=True)
    assert s.counts["would download"] == 2
    assert s.pending_bytes == len(A) + len(B)
    assert http.count("POST", "https://www.humblebundle.com/home/library/p1") == 0
    assert cdn.calls == []
    assert os.listdir(tmp_path) == []


def test_failed_download_is_counted_and_leaves_no_file(tmp_path):
    cdn = FakeHttp()
    http, cdn = make_http(cdn)
    cdn.add(
        "GET",
        "https://cdn.humble.com/extra.pdf?t=s",
        FakeResponse(200, content=b"short"),
    )
    s = go(tmp_path, http, cdn)
    assert s.counts["failed"] == 1 and s.counts["downloaded"] == 1
    assert not (folder(tmp_path) / "extra.pdf").exists()
    assert not (folder(tmp_path) / "extra.pdf.part").exists()


def test_expired_link_is_signed_again_once(tmp_path):
    http, cdn = make_http()
    cdn.add(
        "GET",
        "https://cdn.humble.com/book.pdf?t=s",
        [FakeResponse(403), FakeResponse(200, content=A)],
    )
    s = go(tmp_path, http, cdn)
    assert s.counts["downloaded"] == 2 and not s.counts["failed"]
    assert http.count("POST", "https://www.humblebundle.com/home/library/p1") == 2


def test_lock_prevents_a_second_run(tmp_path):
    with Lock(str(tmp_path)), pytest.raises(LibraryLocked):
        go(tmp_path, *make_http())


def test_tracked_file_is_moved_when_the_folder_name_changes(tmp_path):
    old = tmp_path / "Old Title" / "Book"
    old.mkdir(parents=True)
    (old / "book.pdf").write_bytes(A)
    data = {
        "version": 1,
        "files": {
            "e1/book_ebook/book.pdf": {
                "path": os.path.join("Old Title", "Book", "book.pdf"),
                "size": len(A),
                "md5": md5(A),
                "verified": True,
                "source": "download",
            }
        },
    }
    st.save(str(tmp_path / st.STATE_NAME), data)
    http, cdn = make_http()
    s = go(tmp_path, http, cdn)
    assert s.counts["moved"] == 1
    assert (folder(tmp_path) / "book.pdf").read_bytes() == A
    assert not (old / "book.pdf").exists()
    assert cdn.count("GET", "https://cdn.humble.com/book.pdf") == 0
    rec = json.loads((tmp_path / st.STATE_NAME).read_text(encoding="utf-8"))["files"]
    assert rec["e1/book_ebook/book.pdf"]["path"] == os.path.join(
        "Bundle - One", "Book", "book.pdf"
    )


def test_file_listed_twice_with_different_sizes_keeps_the_matching_copy(tmp_path):
    http, cdn = make_http()
    ents = [
        entitlement(
            "e1",
            "Book",
            [
                download(
                    "book_ebook",
                    "ebook",
                    [
                        struct("book.pdf", len(A), md5(A)),
                        struct("book.pdf", 999, "f" * 32),
                    ],
                ),
            ],
        )
    ]
    http.add(
        "GET",
        "/home/library/p1",
        FakeResponse(200, logged_in_page(item_props("p1", "Bundle: One", ents))),
    )
    folder(tmp_path).mkdir(parents=True)
    (folder(tmp_path) / "book.pdf").write_bytes(A)
    s = go(tmp_path, http, cdn)
    assert s.counts["conflicts"] == 1 and s.counts["adopted"] == 1
    assert not s.counts["failed"]
    assert cdn.calls == []


def test_unsignable_item_fails_its_files_once_and_the_run_goes_on(tmp_path):
    http, cdn = make_http()
    http.add("POST", "/home/library/p1", FakeResponse(200, rsc({"ok": False})))
    s = go(tmp_path, http, cdn)
    assert s.counts["failed"] == 2
    assert http.count("POST", "https://www.humblebundle.com/home/library/p1") == 1
    assert cdn.calls == []


def test_failed_final_rename_restores_the_old_version(tmp_path, monkeypatch):
    from humbledownloader import run as run_mod

    folder(tmp_path).mkdir(parents=True)
    (folder(tmp_path) / "book.pdf").write_bytes(b"old version, other size")
    real_rename_new = run_mod.rename_new

    # patched one level up from os.rename: on POSIX rename_new links instead
    def rename_new(src, dst):
        if src.endswith(".part"):
            raise PermissionError("held by antivirus")
        real_rename_new(src, dst)

    monkeypatch.setattr(run_mod, "rename_new", rename_new)
    monkeypatch.setattr(run_mod.time, "sleep", lambda s: None)
    s = go(tmp_path, *make_http())
    assert s.counts["failed"] == 2
    assert (folder(tmp_path) / "book.pdf").read_bytes() == b"old version, other size"
    assert not [
        n for n in os.listdir(folder(tmp_path)) if n.endswith(".part") or "_old-" in n
    ]


def test_shared_file_recorded_twice_moves_once(tmp_path):
    old = tmp_path / "Old Title" / "Book"
    old.mkdir(parents=True)
    (old / "book.pdf").write_bytes(A)
    rec = {
        "path": os.path.join("Old Title", "Book", "book.pdf"),
        "size": len(A),
        "md5": md5(A),
        "verified": True,
        "source": "download",
    }
    st.save(
        str(tmp_path / st.STATE_NAME),
        {
            "version": 1,
            "files": {
                "e1/book_ebook/book.pdf": dict(rec),
                "e9/book_ebook/book.pdf": dict(rec),
            },
        },
    )
    http, cdn = make_http()
    ents = [
        entitlement(
            "e1",
            "Book",
            [download("book_ebook", "ebook", [struct("book.pdf", len(A), md5(A))])],
        ),
        entitlement(
            "e9",
            "Book",
            [download("book_ebook", "ebook", [struct("book.pdf", len(A), md5(A))])],
        ),
    ]
    http.add(
        "GET",
        "/home/library/p1",
        FakeResponse(200, logged_in_page(item_props("p1", "Bundle: One", ents))),
    )
    s = go(tmp_path, http, cdn)
    assert s.counts["moved"] == 1 and not s.counts["failed"]
    files = json.loads((tmp_path / st.STATE_NAME).read_text(encoding="utf-8"))["files"]
    new = os.path.join("Bundle - One", "Book", "book.pdf")
    assert (
        files["e1/book_ebook/book.pdf"]["path"]
        == files["e9/book_ebook/book.pdf"]["path"]
        == new
    )
    assert (tmp_path / ".humble-moves.log").exists()


def test_dry_run_takes_no_lock(tmp_path):
    with Lock(str(tmp_path)):
        s = go(tmp_path, *make_http(), dry_run=True)
    assert s.counts["would download"] == 2


def test_only_limits_the_run_to_matching_items(tmp_path):
    http, cdn = make_http()
    s = run(
        str(tmp_path),
        Site(http, sleep=lambda s: None),
        {"mac"},
        dry_run=True,
        http=cdn,
        only="one",
    )
    assert s.counts["would download"] == 2
    with pytest.raises(Exception, match="no item title contains"):
        run(
            str(tmp_path),
            Site(make_http()[0], sleep=lambda s: None),
            {"mac"},
            dry_run=True,
            only="nothing",
        )


NEWER = b"download me, newer edition"


def newer_extra(cdn):
    cdn.add(
        "GET",
        "https://cdn.humble.com/extra.pdf?t=s",
        FakeResponse(
            200,
            content=NEWER,
            headers={"Content-Length": str(len(NEWER)), "ETag": f'"{md5(NEWER)}"'},
        ),
    )


def test_file_newer_than_the_catalog_is_kept_and_not_downloaded_again(tmp_path):
    http, cdn = make_http()
    newer_extra(cdn)
    s = go(tmp_path, http, cdn)
    assert s.counts["downloaded"] == 2 and s.counts["newer than catalog"] == 1
    assert not s.counts["failed"]
    assert s.downloaded_bytes == len(A) + len(NEWER)
    assert (folder(tmp_path) / "extra.pdf").read_bytes() == NEWER
    rec = json.loads((tmp_path / st.STATE_NAME).read_text(encoding="utf-8"))["files"]
    extra = rec["e1/book_extra/pub/extra.pdf"]
    assert (extra["size"], extra["md5"]) == (len(B), md5(B))
    assert (extra["disk_size"], extra["disk_md5"]) == (len(NEWER), md5(NEWER))
    assert "disk_size" not in rec["e1/book_ebook/book.pdf"]

    http2, cdn2 = make_http()
    s2 = go(tmp_path, http2, cdn2)
    assert s2.counts["skipped"] == 2 and not s2.counts["updated"]
    assert cdn2.calls == []


def test_update_identical_to_the_file_on_disk_keeps_it_without_a_copy(tmp_path):
    folder(tmp_path).mkdir(parents=True)
    (folder(tmp_path) / "extra.pdf").write_bytes(NEWER)  # already the new edition
    old_mtime = 1_000_000_000
    os.utime(folder(tmp_path) / "extra.pdf", (old_mtime, old_mtime))
    http, cdn = make_http()
    newer_extra(cdn)
    s = go(tmp_path, http, cdn)
    assert s.counts["already current"] == 1 and not s.counts["updated"]
    assert s.counts["downloaded"] == 1  # book.pdf
    assert sorted(os.listdir(folder(tmp_path))) == ["book.pdf", "extra.pdf"]
    assert (folder(tmp_path) / "extra.pdf").read_bytes() == NEWER
    assert os.stat(folder(tmp_path) / "extra.pdf").st_mtime == old_mtime  # untouched

    http2, cdn2 = make_http()
    s2 = go(tmp_path, http2, cdn2)
    assert s2.counts["skipped"] == 2 and cdn2.calls == []


def test_update_the_cdn_proves_on_disk_is_recorded_without_downloading(tmp_path):
    folder(tmp_path).mkdir(parents=True)
    (folder(tmp_path) / "extra.pdf").write_bytes(NEWER)
    one_part = hashlib.md5(hashlib.md5(NEWER).digest()).hexdigest()
    resp = FakeResponse(
        200,
        content=NEWER,
        headers={"Content-Length": str(len(NEWER)), "ETag": f'"{one_part}-1"'},
    )
    resp.iter_content = None  # the body must not be read
    http, cdn = make_http()
    cdn.add("GET", "https://cdn.humble.com/extra.pdf?t=s", resp)
    s = go(tmp_path, http, cdn)
    assert s.counts["already current"] == 1 and s.counts["newer than catalog"] == 1
    assert not s.counts["failed"] and not s.counts["updated"]
    assert s.downloaded_bytes == len(A)  # book.pdf only
    assert sorted(os.listdir(folder(tmp_path))) == ["book.pdf", "extra.pdf"]
    rec = json.loads((tmp_path / st.STATE_NAME).read_text(encoding="utf-8"))["files"]
    extra = rec["e1/book_extra/pub/extra.pdf"]
    assert (extra["source"], extra["verified"]) == ("adopted", True)
    assert (extra["disk_size"], extra["disk_md5"]) == (len(NEWER), md5(NEWER))

    http2, cdn2 = make_http()
    s2 = go(tmp_path, http2, cdn2)
    assert s2.counts["skipped"] == 2 and cdn2.calls == []


def test_update_of_same_size_but_other_content_keeps_the_old_one_aside(tmp_path):
    folder(tmp_path).mkdir(parents=True)
    old = b"X" * len(NEWER)
    (folder(tmp_path) / "extra.pdf").write_bytes(old)
    http, cdn = make_http()
    newer_extra(cdn)
    s = go(tmp_path, http, cdn)
    assert s.counts["updated"] == 1 and not s.counts["already current"]
    assert (folder(tmp_path) / "extra.pdf").read_bytes() == NEWER
    aside = [n for n in os.listdir(folder(tmp_path)) if "_old-" in n]
    assert [(folder(tmp_path) / n).read_bytes() for n in aside] == [old]


def test_identical_update_with_a_locked_part_still_counts_as_current(
    tmp_path, monkeypatch
):
    from humbledownloader import run as run_mod

    folder(tmp_path).mkdir(parents=True)
    (folder(tmp_path) / "extra.pdf").write_bytes(NEWER)
    http, cdn = make_http()
    newer_extra(cdn)
    real_remove = os.remove

    # only extra.pdf's part: on POSIX rename_new also removes book.pdf's part
    def locked(path):
        if str(path).endswith("extra.pdf.part"):
            raise PermissionError(13, "locked", str(path))
        real_remove(path)

    monkeypatch.setattr(run_mod.os, "remove", locked)
    monkeypatch.setattr(run_mod.time, "sleep", lambda s: None)
    s = go(tmp_path, http, cdn)
    assert s.counts["already current"] == 1 and not s.counts["failed"]
    rec = json.loads((tmp_path / st.STATE_NAME).read_text(encoding="utf-8"))["files"]
    assert rec["e1/book_extra/pub/extra.pdf"]["disk_md5"] == md5(NEWER)


def test_failed_rename_restores_the_old_version_even_if_the_part_stays_locked(
    tmp_path, monkeypatch
):
    from humbledownloader import run as run_mod

    target = tmp_path / "a.pdf"
    target.write_bytes(b"old")
    part = tmp_path / "a.pdf.part"
    part.write_bytes(b"new")
    real_rename = os.rename

    def rename(src, dst):
        if str(src).endswith(".part"):
            raise PermissionError(13, "locked", str(src))
        real_rename(src, dst)

    def remove(path):
        raise PermissionError(13, "locked", str(path))

    monkeypatch.setattr(run_mod.os, "rename", rename)
    monkeypatch.setattr(run_mod.os, "remove", remove)
    monkeypatch.setattr(run_mod.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        run_mod._install(str(part), str(target), True, md5(b"new"))
    assert target.read_bytes() == b"old"


def test_unexpected_shape_is_counted_and_the_run_goes_on(tmp_path):
    from humbledownloader.run import Summary, collect

    class FakeSite:
        def library(self):
            return "", [("p1", "One"), ("p2", "Two")]

        def purchased_ids(self, html):
            return []

        def item(self, pid):
            if pid == "p1":
                raise TypeError("odd value")
            return "html", type(
                "Item", (), {"title": "Two", "skipped": [], "files": [1]}
            )()

    s = Summary()
    items = collect(FakeSite(), s)
    assert [i.title for _, i in items] == ["Two"]
    assert s.counts["failed"] == 1 and "unexpected page data" in s.failures[0]


@pytest.fixture
def posix_rename(monkeypatch):
    """Run rename_new's non-Windows branch (hard links work on NTFS too), with
    os.rename replacing an existing file as it does on POSIX."""
    from humbledownloader import run as run_mod

    monkeypatch.setattr(run_mod.os, "name", "posix")
    monkeypatch.setattr(run_mod.os, "rename", os.replace)
    return run_mod.rename_new


def test_rename_new_moves_a_file(tmp_path, posix_rename):
    (tmp_path / "a").write_bytes(b"A")
    posix_rename(str(tmp_path / "a"), str(tmp_path / "b"))
    assert not (tmp_path / "a").exists() and (tmp_path / "b").read_bytes() == b"A"


def test_rename_new_never_replaces(tmp_path, posix_rename):
    (tmp_path / "a").write_bytes(b"A")
    (tmp_path / "b").write_bytes(b"B")
    with pytest.raises(FileExistsError):
        posix_rename(str(tmp_path / "a"), str(tmp_path / "b"))
    assert (tmp_path / "a").read_bytes() == b"A" and (
        tmp_path / "b"
    ).read_bytes() == b"B"


def test_rename_new_without_hard_links_still_never_replaces(
    tmp_path, posix_rename, monkeypatch
):
    from humbledownloader import run as run_mod

    def no_links(src, dst):
        raise PermissionError(1, "operation not permitted")

    monkeypatch.setattr(run_mod.os, "link", no_links)
    (tmp_path / "a").write_bytes(b"A")
    (tmp_path / "b").write_bytes(b"B")
    with pytest.raises(FileExistsError):
        posix_rename(str(tmp_path / "a"), str(tmp_path / "b"))
    assert (tmp_path / "b").read_bytes() == b"B"
    posix_rename(str(tmp_path / "a"), str(tmp_path / "c"))
    assert (tmp_path / "c").read_bytes() == b"A" and not (tmp_path / "a").exists()


def test_rename_new_undoes_the_link_when_the_old_name_cannot_be_removed(
    tmp_path, posix_rename, monkeypatch
):
    from humbledownloader import run as run_mod

    real_remove = os.remove
    src, dst = str(tmp_path / "a"), str(tmp_path / "b")

    def refuse_src(path):
        if path == src:
            raise PermissionError(13, "locked", path)
        real_remove(path)

    monkeypatch.setattr(run_mod.os, "remove", refuse_src)
    (tmp_path / "a").write_bytes(b"A")
    with pytest.raises(PermissionError):
        posix_rename(src, dst)
    assert (tmp_path / "a").read_bytes() == b"A" and not (tmp_path / "b").exists()
