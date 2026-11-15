"""Fetch one file from cdn.humble.com into `<target>.part` and check it.

The CDN needs no cookies; the signed URL carries its own token, so the
URL must never be logged unredacted.
"""

import base64
import functools
import hashlib
import os
import re

import requests

TIMEOUT = (10, 60)
CHUNK = 1 << 20
ETAG = re.compile(r'"([0-9a-f]{32})(?:-([1-9][0-9]*))?"')  # strong; "-N": N parts
# Part size of the CDN's multipart uploads: every multipart ETag checked (2026-11-15)
# was the md5 of the 5 MiB parts' md5s.
PART_SIZE = 5 * 2**20
MD5_HEADER = "x-amz-meta-md5chksum"  # base64 md5 of the whole file, on some objects


class DownloadError(Exception):
    pass


class LinkExpired(DownloadError):
    """The signed URL was refused (403/410): sign again."""


class Digests:
    """md5 of a byte stream, plus the md5 of each PART_SIZE part of it."""

    def __init__(self):
        self.whole = hashlib.md5()
        self.parts = []
        self.part = hashlib.md5()
        self.in_part = 0
        self.size = 0

    def update(self, data):
        self.whole.update(data)
        self.size += len(data)
        view = memoryview(data)
        while view:
            take = view[: PART_SIZE - self.in_part]
            self.part.update(take)
            self.in_part += len(take)
            view = view[len(take) :]
            if self.in_part == PART_SIZE:
                self.parts.append(self.part.digest())
                self.part, self.in_part = hashlib.md5(), 0

    def md5(self):
        return self.whole.hexdigest()

    def multipart(self):
        """(md5 of the part md5s, number of parts), as S3 forms a multipart ETag."""
        parts = self.parts + ([self.part.digest()] if self.in_part else [])
        return hashlib.md5(b"".join(parts)).hexdigest(), len(parts)


def fetch(url, target, size, md5, http=None, progress=None, current=None):
    """Download to target + '.part'; return (part path, size, md5) of what arrived.

    It must match the catalog's size and md5, or, when Humble replaced the file
    but its catalog still lists the old one, the CDN must prove it (see
    _proven). With `current` (the file now at the target), a file the CDN proves
    to be what it serves is not downloaded again: the result is (None, its size,
    its md5). The .part file is removed on any failure, Ctrl+C included.
    """
    http = http or requests
    r = _get(http, url)
    try:
        length = r.headers.get("Content-Length")
        if current and length == str(os.path.getsize(current)):
            r.close()  # hashing a large file takes long: do not hold the connection
            d = _hash_file(current)
            if _proven(r.headers, d):
                return None, d.size, d.md5()
            r = _get(http, url)
            length = r.headers.get("Content-Length")
        if length == "0" and size:
            raise DownloadError(
                f"the CDN serves an empty file instead of {size} bytes "
                "(Humble's copy is broken)"
            )
        return _stream(r, target, size, md5, progress)
    finally:
        r.close()


def _get(http, url):
    try:
        r = http.get(
            url,
            stream=True,
            timeout=TIMEOUT,
            headers={"Accept-Encoding": "identity"},
        )
    except (requests.ConnectionError, requests.Timeout) as e:
        raise DownloadError(e.__class__.__name__) from e
    if r.status_code in (200, 403, 410):
        if r.status_code == 200:
            return r
        r.close()
        raise LinkExpired(f"HTTP {r.status_code}")
    r.close()
    raise DownloadError(f"HTTP {r.status_code}")


def _stream(r, target, size, md5, progress):
    part = target + ".part"
    os.makedirs(os.path.dirname(target), exist_ok=True)
    d = Digests()
    try:
        with open(part, "wb") as f:
            for chunk in r.iter_content(CHUNK):
                f.write(chunk)
                d.update(chunk)
                if progress:
                    progress(d.size, size)
        if (d.size, d.md5()) != (size, md5) and not _proven(r.headers, d):
            if d.size != size:
                raise DownloadError(f"got {d.size} bytes, expected {size}")
            raise DownloadError("md5 does not match the catalog")
    except BaseException as e:
        _remove(part)
        if isinstance(e, requests.RequestException):
            raise DownloadError(e.__class__.__name__) from e
        raise
    return part, d.size, d.md5()


def _hash_file(path):
    """Digests of the file; a retry with a fresh link (LinkExpired) does not read
    an unchanged file again."""
    st = os.stat(path)
    return _hash_unchanged(path, st.st_size, st.st_mtime_ns)


@functools.lru_cache(maxsize=1)
def _hash_unchanged(path, size, mtime_ns):
    d = Digests()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            d.update(block)
    return d


def _proven(headers, d):
    """The CDN announced exactly these bytes and its own checksum matches them:
    the strong ETag (single-part: the md5; multipart: see Digests.multipart) or
    the md5 header some objects carry."""
    if d.size == 0 or headers.get("Content-Length") != str(d.size):
        return False
    m = ETAG.fullmatch(headers.get("ETag", ""))
    if m and m.group(2) is None and m.group(1) == d.md5():
        return True
    if m and m.group(2) is not None and d.multipart() == (m.group(1), int(m.group(2))):
        return True
    return _header_md5(headers) == d.md5()


def _header_md5(headers):
    try:
        raw = base64.b64decode(headers.get(MD5_HEADER, ""), validate=True)
    except ValueError:  # binascii.Error included
        return None
    return raw.hex() if len(raw) == 16 else None


def _remove(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
