import base64
import hashlib
import itertools
import os

import pytest
import requests

from humbledownloader import download
from humbledownloader.download import Digests, DownloadError, LinkExpired, fetch
from tests.fakes import FakeHttp, FakeResponse, md5

URL = "https://cdn.humble.com/x/a.pdf?t=secret"
DATA = b"0123456789abcdef"


def http_with(response):
    h = FakeHttp()
    h.add("GET", URL, response)
    return h


def test_fetch_writes_part_and_checks(tmp_path):
    target = str(tmp_path / "B" / "a.pdf")
    resp = FakeResponse(200, content=DATA)
    h = http_with(resp)
    part, size, digest = fetch(URL, target, len(DATA), md5(DATA), http=h)
    assert part == target + ".part"
    assert (size, digest) == (len(DATA), md5(DATA))
    with open(part, "rb") as f:
        assert f.read() == DATA
    assert not os.path.exists(target)
    assert resp.closed
    assert h.calls[0][2]["headers"]["Accept-Encoding"] == "identity"


@pytest.mark.parametrize(
    "content, size, digest, message",
    [
        (DATA[:-1], len(DATA), md5(DATA), "expected"),
        (DATA, len(DATA), "0" * 32, "md5"),
    ],
)
def test_fetch_rejects_bad_content(tmp_path, content, size, digest, message):
    target = str(tmp_path / "a.pdf")
    with pytest.raises(DownloadError, match=message):
        fetch(
            URL,
            target,
            size,
            digest,
            http=http_with(FakeResponse(200, content=content)),
        )
    assert not os.path.exists(target + ".part")


@pytest.mark.parametrize("status", [403, 410])
def test_refused_link(tmp_path, status):
    resp = FakeResponse(status)
    with pytest.raises(LinkExpired):
        fetch(URL, str(tmp_path / "a.pdf"), 1, "x", http=http_with(resp))
    assert resp.closed


def test_other_status_is_download_error(tmp_path):
    resp = FakeResponse(500)
    with pytest.raises(DownloadError):
        fetch(URL, str(tmp_path / "a.pdf"), 1, "x", http=http_with(resp))
    assert resp.closed


@pytest.mark.parametrize(
    "exc", [KeyboardInterrupt(), requests.ConnectionError("reset")]
)
def test_interrupted_download_removes_part(tmp_path, exc):
    target = str(tmp_path / "a.pdf")
    resp = FakeResponse(200, content=DATA, iter_exc=exc)
    with pytest.raises((KeyboardInterrupt, DownloadError)):
        fetch(URL, target, len(DATA) * 2, md5(DATA), http=http_with(resp))
    assert not os.path.exists(target + ".part")
    assert resp.closed


NEW = b"a newer edition of the file"


@pytest.fixture(autouse=True)
def small_parts(monkeypatch):
    """4-byte parts, so the fakes' 3-byte chunks cross part boundaries."""
    monkeypatch.setattr(download, "PART_SIZE", 4)
    download._hash_unchanged.cache_clear()


def multipart(data, part_size):
    parts = [data[i : i + part_size] for i in range(0, len(data), part_size)]
    joined = b"".join(hashlib.md5(p).digest() for p in parts)
    return hashlib.md5(joined).hexdigest(), len(parts)


def b64(hex_md5):
    return base64.b64encode(bytes.fromhex(hex_md5)).decode()


def cdn_headers(**over):
    h = {"Content-Length": str(len(NEW)), "ETag": f'"{md5(NEW)}"'}
    h.update(over)
    return {k: v for k, v in h.items() if v is not None}


def test_newer_file_than_the_catalog_is_accepted_when_the_cdn_proves_it(tmp_path):
    target = str(tmp_path / "a.epub")
    resp = FakeResponse(200, content=NEW, headers=cdn_headers())
    part, size, digest = fetch(URL, target, len(DATA), md5(DATA), http=http_with(resp))
    assert (size, digest) == (len(NEW), md5(NEW))
    with open(part, "rb") as f:
        assert f.read() == NEW


@pytest.mark.parametrize(
    "headers",
    [
        {},
        cdn_headers(ETag=None),
        cdn_headers(**{"Content-Length": None}),
        cdn_headers(**{"Content-Length": str(len(NEW) + 1)}),
        cdn_headers(ETag=f'W/"{md5(NEW)}"'),
        cdn_headers(ETag=f'"{multipart(NEW, 4)[0]}-{multipart(NEW, 4)[1] + 1}"'),
        cdn_headers(ETag=f'"{md5(NEW)}-1"'),
        cdn_headers(**{"x-amz-meta-md5chksum": "not base64!"}, ETag=None),
        cdn_headers(**{"x-amz-meta-md5chksum": b64(md5(DATA))}, ETag=None),
        cdn_headers(ETag=f'"{md5(DATA)}"'),
        cdn_headers(ETag=md5(NEW)),
        cdn_headers(ETag=f'"{md5(NEW).upper()}"'),
        cdn_headers(ETag=f' "{md5(NEW)}" '),
    ],
    ids=[
        "no headers",
        "no etag",
        "no length",
        "short body",
        "weak etag",
        "multipart etag, other part count",
        "whole md5 as multipart etag",
        "md5 header not base64",
        "md5 header of other content",
        "other md5",
        "unquoted",
        "uppercase",
        "padded",
    ],
)
def test_newer_file_without_proof_is_rejected(tmp_path, headers):
    target = str(tmp_path / "a.epub")
    resp = FakeResponse(200, content=NEW, headers=headers)
    with pytest.raises(DownloadError, match="expected"):
        fetch(URL, target, len(DATA), md5(DATA), http=http_with(resp))
    assert not os.path.exists(target + ".part")


def test_same_size_other_md5_is_accepted_only_with_proof(tmp_path):
    other = b"X" * len(DATA)
    target = str(tmp_path / "a.pdf")
    plain = FakeResponse(200, content=other)
    with pytest.raises(DownloadError, match="md5"):
        fetch(URL, target, len(DATA), md5(DATA), http=http_with(plain))
    proven = FakeResponse(
        200,
        content=other,
        headers={"Content-Length": str(len(other)), "ETag": f'"{md5(other)}"'},
    )
    _, size, digest = fetch(URL, target, len(DATA), md5(DATA), http=http_with(proven))
    assert (size, digest) == (len(other), md5(other))


def test_empty_body_is_never_proof(tmp_path):
    target = str(tmp_path / "a.pdf")
    resp = FakeResponse(200, content=b"", headers={"ETag": f'"{md5(b"")}"'})
    with pytest.raises(DownloadError, match="expected"):
        fetch(URL, target, len(DATA), md5(DATA), http=http_with(resp))


def test_empty_cdn_object_is_named_as_such(tmp_path):
    target = str(tmp_path / "a.zip")
    resp = FakeResponse(
        200, content=b"", headers={"Content-Length": "0", "ETag": f'"{md5(b"")}"'}
    )
    with pytest.raises(DownloadError, match="empty file instead of 16 bytes"):
        fetch(URL, target, len(DATA), md5(DATA), http=http_with(resp))
    assert not os.path.exists(target + ".part")


@pytest.mark.parametrize("cuts", [[], [1], [3, 4, 5], [4, 8], [2, 7, 11, 12, 20]])
def test_digests_match_a_direct_computation_however_the_stream_is_cut(cuts):
    d = Digests()
    edges = [0, *cuts, len(NEW)]
    for a, b in itertools.pairwise(edges):
        d.update(NEW[a:b])
    assert d.size == len(NEW) and d.md5() == md5(NEW)
    assert d.multipart() == multipart(NEW, 4)


def test_multipart_etag_proves_a_newer_file(tmp_path):
    etag, parts = multipart(NEW, 4)
    resp = FakeResponse(200, content=NEW, headers=cdn_headers(ETag=f'"{etag}-{parts}"'))
    _, size, digest = fetch(
        URL, str(tmp_path / "a.epub"), len(DATA), md5(DATA), http=http_with(resp)
    )
    assert (size, digest) == (len(NEW), md5(NEW))


def test_multipart_etag_of_an_exact_number_of_parts(tmp_path):
    data = NEW[:24]  # 6 full parts, no remainder
    etag, parts = multipart(data, 4)
    resp = FakeResponse(
        200,
        content=data,
        headers={"Content-Length": str(len(data)), "ETag": f'"{etag}-{parts}"'},
    )
    _, size, _ = fetch(
        URL, str(tmp_path / "a.epub"), len(DATA), md5(DATA), http=http_with(resp)
    )
    assert (size, parts) == (24, 6)


def test_md5_header_proves_a_newer_file_behind_an_unknown_etag(tmp_path):
    headers = cdn_headers(ETag='"0123456789abcdef0123456789abcdef-99"')
    headers["x-amz-meta-md5chksum"] = b64(md5(NEW))
    resp = FakeResponse(200, content=NEW, headers=headers)
    _, size, digest = fetch(
        URL, str(tmp_path / "a.epub"), len(DATA), md5(DATA), http=http_with(resp)
    )
    assert (size, digest) == (len(NEW), md5(NEW))


def current_file(tmp_path, data):
    path = tmp_path / "a.epub"
    path.write_bytes(data)
    return str(path)


def test_file_on_disk_proven_by_the_cdn_is_not_downloaded(tmp_path):
    current = current_file(tmp_path, NEW)
    etag, parts = multipart(NEW, 4)
    resp = FakeResponse(200, content=NEW, headers=cdn_headers(ETag=f'"{etag}-{parts}"'))
    resp.iter_content = None  # reading the body would fail the test
    h = http_with(resp)
    result = fetch(URL, current, len(DATA), md5(DATA), http=h, current=current)
    assert result == (None, len(NEW), md5(NEW))
    assert h.count("GET") == 1 and resp.closed
    assert not os.path.exists(current + ".part")


def test_file_on_disk_of_the_same_length_but_other_content_is_downloaded(
    tmp_path, monkeypatch
):
    current = current_file(tmp_path, b"Y" * len(NEW))
    first = FakeResponse(200, headers=cdn_headers())
    second = FakeResponse(200, content=NEW, headers=cdn_headers())
    h = FakeHttp()
    h.add("GET", URL, [first, second])
    real_hash = download._hash_file
    closed_when_hashing = []

    def spy(path):
        closed_when_hashing.append(first.closed)
        return real_hash(path)

    monkeypatch.setattr(download, "_hash_file", spy)
    part, _, digest = fetch(URL, current, len(DATA), md5(DATA), http=h, current=current)
    assert part == current + ".part" and digest == md5(NEW)
    assert h.count("GET") == 2
    assert closed_when_hashing == [True] and second.closed


def test_retry_after_an_expired_link_does_not_hash_the_file_again(
    tmp_path, monkeypatch
):
    current = current_file(tmp_path, b"Y" * len(NEW))
    reads = []
    real_open = open

    def counting_open(path, mode="r", *a, **k):
        if path == current and "b" in mode:
            reads.append(path)
        return real_open(path, mode, *a, **k)

    monkeypatch.setattr("builtins.open", counting_open)
    h = FakeHttp()
    h.add(
        "GET",
        URL,
        [
            FakeResponse(200, headers=cdn_headers()),
            FakeResponse(403),
            FakeResponse(200, headers=cdn_headers()),
            FakeResponse(200, content=NEW, headers=cdn_headers()),
        ],
    )
    with pytest.raises(LinkExpired):
        fetch(URL, current, len(DATA), md5(DATA), http=h, current=current)
    part, _, _ = fetch(URL, current, len(DATA), md5(DATA), http=h, current=current)
    assert part == current + ".part" and reads == [current]


def test_empty_file_on_disk_is_not_proven_by_an_empty_cdn_object(tmp_path):
    current = current_file(tmp_path, b"")
    empty = {"Content-Length": "0", "ETag": f'"{md5(b"")}"'}
    h = http_with(FakeResponse(200, content=b"", headers=empty))
    with pytest.raises(DownloadError, match="empty file instead of 16 bytes"):
        fetch(URL, current, len(DATA), md5(DATA), http=h, current=current)


def test_file_that_really_is_empty_is_accepted(tmp_path):
    empty = {"Content-Length": "0", "ETag": f'"{md5(b"")}"'}
    h = http_with(FakeResponse(200, content=b"", headers=empty))
    part, size, digest = fetch(URL, str(tmp_path / "a.txt"), 0, md5(b""), http=h)
    assert (size, digest) == (0, md5(b"")) and os.path.getsize(part) == 0


def test_real_part_size_is_5_mib(monkeypatch):
    monkeypatch.undo()  # drop the small_parts fixture: the production value
    assert download.PART_SIZE == 5 * 2**20
    data = b"\x01" * (5 * 2**20 + 1)
    d = Digests()
    d.update(data)
    parts = hashlib.md5(data[: 5 * 2**20]).digest() + hashlib.md5(b"\x01").digest()
    assert d.multipart() == (hashlib.md5(parts).hexdigest(), 2)


def test_file_on_disk_of_another_length_is_not_hashed(tmp_path, monkeypatch):
    current = current_file(tmp_path, b"short")
    monkeypatch.setattr(download, "_hash_file", None)  # calling it would fail
    h = http_with(FakeResponse(200, content=NEW, headers=cdn_headers()))
    part, _, _ = fetch(URL, current, len(DATA), md5(DATA), http=h, current=current)
    assert part == current + ".part" and h.count("GET") == 1


def test_refused_second_request_after_the_disk_check(tmp_path):
    current = current_file(tmp_path, b"Y" * len(NEW))
    refused = FakeResponse(403)
    h = FakeHttp()
    h.add("GET", URL, [FakeResponse(200, headers=cdn_headers()), refused])
    with pytest.raises(LinkExpired):
        fetch(URL, current, len(DATA), md5(DATA), http=h, current=current)
    assert not os.path.exists(current + ".part") and refused.closed
