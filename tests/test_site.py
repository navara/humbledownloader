import json

import pytest
import requests

from humbledownloader.flight import LayoutError
from humbledownloader.site import (
    Blocked,
    Challenge,
    CookieFileError,
    SessionLost,
    Site,
    SiteError,
    load_session,
)
from tests.fakes import (
    CHUNK,
    CHUNK_JS,
    LIST_ID,
    SIGN_ID,
    FakeHttp,
    FakeResponse,
    logged_in_page,
    rsc,
)
from tests.flightdata import page

ITEM = "/home/library/p1"


def site_with(http):
    return Site(http, sleep=lambda s: None)


def cookie_file(tmp_path, names):
    lines = ["# Netscape HTTP Cookie File"]
    for n in names:
        lines.append(f".humblebundle.com\tTRUE\t/\tTRUE\t0\t{n}\tvalue-{n}")
    p = tmp_path / "cookies.txt"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


def test_load_session_keeps_only_the_session_cookie(tmp_path):
    s = load_session(
        cookie_file(tmp_path, ["ory_session_x", "csrf_token_y", "__cf_bm"])
    )
    assert [c.name for c in s.cookies] == ["ory_session_x"]
    assert s.headers["User-Agent"].startswith("humbledl/")


def test_load_session_without_session_cookie(tmp_path):
    with pytest.raises(CookieFileError, match="0 ory_session_ cookies"):
        load_session(cookie_file(tmp_path, ["csrf_token_y"]))


def test_missing_cookie_file_is_a_cookie_file_error(tmp_path):
    with pytest.raises(CookieFileError, match="cannot be read"):
        load_session(str(tmp_path / "missing.txt"))


def test_cookie_file_that_is_not_utf8_is_a_cookie_file_error(tmp_path):
    p = tmp_path / "c.txt"
    p.write_text("# Netscape HTTP Cookie File\n", encoding="utf-16")
    with pytest.raises(CookieFileError, match="cannot be read"):
        load_session(str(p))


@pytest.mark.parametrize(
    "value, wait", [("7", 7.0), ("-1", 5), ("nan", 5), ("inf", 5), ("soon", 5)]
)
def test_retry_after_is_a_usable_wait(value, wait):
    from humbledownloader.site import _retry_after

    assert _retry_after(FakeResponse(429, headers={"Retry-After": value}), 5) == wait


def test_page_logged_out_is_session_lost():
    http = FakeHttp()
    http.add(
        "GET", "/home/library", FakeResponse(200, page({"user": {"logged_in": False}}))
    )
    with pytest.raises(SessionLost):
        site_with(http).page("/home/library")


def test_page_without_login_state_is_layout_error():
    http = FakeHttp()
    http.add("GET", "/home/library", FakeResponse(200, page({"x": 1})))
    with pytest.raises(LayoutError):
        site_with(http).page("/home/library")


@pytest.mark.parametrize("status", [302, 307, 401, 403])
def test_redirect_or_auth_failure_is_session_lost(status):
    http = FakeHttp()
    http.add("GET", "/home/library", FakeResponse(status, ""))
    with pytest.raises(SessionLost):
        site_with(http).page("/home/library")


BLOCK_PAGE = "<title>Attention Required! | Cloudflare</title><h1>Sorry, you have been blocked</h1>"


def test_cloudflare_block_is_not_session_lost():
    http = FakeHttp()
    http.add(
        "GET", "/home/library", FakeResponse(403, BLOCK_PAGE, {"server": "cloudflare"})
    )
    with pytest.raises(Blocked):
        site_with(http).page("/home/library")


def test_plain_403_through_cloudflare_is_still_session_lost():
    http = FakeHttp()
    http.add("GET", "/home/library", FakeResponse(403, "", {"server": "cloudflare"}))
    with pytest.raises(SessionLost):
        site_with(http).page("/home/library")


def test_challenge_stops():
    http = FakeHttp()
    http.add(
        "GET", "/home/library", FakeResponse(403, "", {"cf-mitigated": "challenge"})
    )
    with pytest.raises(Challenge):
        site_with(http).page("/home/library")


def test_retries_5xx_then_succeeds():
    http = FakeHttp()
    ok = FakeResponse(200, logged_in_page())
    http.add("GET", "/home/library", [FakeResponse(503, ""), ok])
    site_with(http).page("/home/library")
    assert http.count("GET") == 2


def test_connection_errors_give_up_after_three_tries():
    http = FakeHttp()
    http.add("GET", "/home/library", requests.ConnectionError("down"))
    with pytest.raises(SiteError):
        site_with(http).page("/home/library")
    assert http.count("GET") == 3


def test_requests_are_paced_and_never_follow_redirects():
    http = FakeHttp()
    http.add("GET", "/home/library", FakeResponse(200, logged_in_page()))
    waits = []
    t = iter([0.0, 0.2, 0.2])
    s = Site(http, sleep=waits.append, clock=lambda: next(t))
    s.page("/home/library")
    s.page("/home/library")
    assert waits and abs(waits[0] - 0.8) < 1e-9
    assert all(kw["allow_redirects"] is False for _, _, kw in http.calls)


def sign_response(files):
    return FakeResponse(
        200,
        rsc({"ok": True, "data": {"signedUrls": {"m": files}, "expiresAt": "x"}}),
    )


def test_sign_discovers_the_action_by_exact_name_and_maps_files():
    http = FakeHttp()
    http.add("GET", CHUNK, FakeResponse(200, CHUNK_JS))
    files = {"k_ebook": {"pub/a.pdf": "https://cdn/x?t=1", "pub/a.pdf.torrent": "t"}}
    http.add("POST", ITEM, sign_response(files))
    urls = site_with(http).sign("p1", logged_in_page(), ["e1", "e2"])
    assert (
        urls[("k_ebook", "pub/a.pdf")]
        == urls[("k_ebook", "a.pdf")]
        == "https://cdn/x?t=1"
    )
    assert not any(k[1].endswith(".torrent") for k in urls)
    method, _, kwargs = http.calls[-1]
    assert (
        method == "POST"
        and kwargs["headers"]["Origin"] == "https://www.humblebundle.com"
    )
    _, _, kw = http.calls[-1]
    assert kw["headers"]["Next-Action"] == SIGN_ID
    assert json.loads(kw["data"]) == [["e1", "e2"]]


def test_sign_rediscovers_once_after_a_deploy():
    http = FakeHttp()
    http.add("GET", CHUNK, FakeResponse(200, CHUNK_JS))
    http.add("GET", ITEM, FakeResponse(200, logged_in_page()))
    stale = FakeResponse(
        409, "Server Action unavailable.", {"x-nextjs-action-not-found": "1"}
    )
    http.add("POST", ITEM, [stale, sign_response({"k": {"a.pdf": "u"}})])
    assert site_with(http).sign("p1", logged_in_page(), ["e1"])[("k", "a.pdf")] == "u"


def test_sign_unexpected_shape_is_layout_error():
    http = FakeHttp()
    http.add("GET", CHUNK, FakeResponse(200, CHUNK_JS))
    http.add("POST", ITEM, FakeResponse(200, rsc({"ok": False})))
    with pytest.raises(LayoutError):
        site_with(http).sign("p1", logged_in_page(), ["e1"])


def test_ambiguous_action_name_is_layout_error():
    http = FakeHttp()
    http.add(
        "GET", CHUNK, FakeResponse(200, CHUNK_JS + CHUNK_JS.replace(SIGN_ID, "d" * 42))
    )
    with pytest.raises(LayoutError, match="found 2 ids"):
        site_with(http).sign("p1", logged_in_page(), ["e1"])


def test_purchased_ids_pages_through_and_keeps_only_ids():
    http = FakeHttp()
    http.add("GET", CHUNK, FakeResponse(200, CHUNK_JS))

    def listing(kwargs):
        page_no = int(kwargs["files"]["_1_page"][1])
        assert kwargs["headers"]["Next-Action"] == LIST_ID
        rows = [
            {"type": "download_drm_free", "productEntitlementId": f"p{page_no}"},
            {
                "type": "steam",
                "productEntitlementId": "s",
                "redemptionData": {"keyFormatted": "K"},
            },
        ]
        return FakeResponse(
            200,
            rsc(
                {
                    "results": rows,
                    "pagination": {"page": page_no, "totalPages": 2},
                    "success": True,
                }
            ),
        )

    http.add("POST", "/home/library", listing)
    assert site_with(http).purchased_ids(logged_in_page()) == ["p1", "p2"]


def site_and_chunks():
    http = FakeHttp()
    http.add("GET", CHUNK, FakeResponse(200, CHUNK_JS))
    return http


def test_server_action_redirect_is_session_lost():
    http = site_and_chunks()
    http.add(
        "POST",
        ITEM,
        FakeResponse(200, "0:{}\n1:I[]", {"x-action-redirect": "/login?x=1"}),
    )
    with pytest.raises(SessionLost):
        site_with(http).sign("p1", logged_in_page(), ["e1"])


@pytest.mark.parametrize(
    "row1", [["$", "x"], {"ok": True, "data": {"signedUrls": {"m": ["x"]}}}]
)
def test_sign_odd_rows_are_layout_errors(row1):
    http = site_and_chunks()
    http.add("POST", ITEM, FakeResponse(200, rsc(row1)))
    with pytest.raises(LayoutError):
        site_with(http).sign("p1", logged_in_page(), ["e1"])


def test_missing_chunk_stops_discovery():
    http = site_and_chunks()
    page_html = logged_in_page(chunks=(CHUNK, "/_next/static/chunks/b.js"))
    http.add("GET", "/_next/static/chunks/b.js", FakeResponse(404, ""))
    with pytest.raises(SiteError):
        site_with(http).sign("p1", page_html, ["e1"])


@pytest.mark.parametrize(
    "row1",
    [
        {"results": [], "pagination": {"page": 1, "totalPages": 1}, "success": False},
        {"results": [], "pagination": {"page": 7, "totalPages": 9}, "success": True},
    ],
)
def test_purchased_failed_or_mismatched_page_is_layout_error(row1):
    http = site_and_chunks()
    http.add("POST", "/home/library", FakeResponse(200, rsc(row1)))
    with pytest.raises(LayoutError):
        site_with(http).purchased_ids(logged_in_page())


def test_body_read_errors_are_retried():
    http = FakeHttp()
    http.add(
        "GET",
        "/home/library",
        [
            requests.exceptions.ChunkedEncodingError("cut"),
            FakeResponse(200, logged_in_page()),
        ],
    )
    site_with(http).page("/home/library")


def test_persistent_429_stops_and_long_retry_after_stops_at_once():
    from humbledownloader.site import RateLimited

    http = FakeHttp()
    http.add("GET", "/home/library", FakeResponse(429, "", {"Retry-After": "1"}))
    with pytest.raises(RateLimited):
        site_with(http).page("/home/library")
    assert http.count("GET") == 3
    http2 = FakeHttp()
    http2.add("GET", "/home/library", FakeResponse(429, "", {"Retry-After": "3600"}))
    with pytest.raises(RateLimited):
        site_with(http2).page("/home/library")
    assert http2.count("GET") == 1


def test_action_response_is_utf8_even_with_byte_0x85():
    http = site_and_chunks()
    title = "Å collection"  # UTF-8 C3 85: read as Latin-1 it holds U+0085
    body = rsc(
        {"ok": True, "data": {"signedUrls": {"m": {"k": {f"{title}.pdf": "u"}}}}}
    )
    http.add("POST", ITEM, FakeResponse(200, content=body.encode("utf-8")))
    assert (
        site_with(http).sign("p1", logged_in_page(), ["e1"])[("k", f"{title}.pdf")]
        == "u"
    )


def test_malformed_cookie_file_does_not_echo_the_value(tmp_path):
    p = tmp_path / "c.txt"
    p.write_text(
        "# Netscape HTTP Cookie File\n.humblebundle.com TRUE / TRUE 0 ory_session_x SECRETVALUE\n",
        encoding="utf-8",
    )
    with pytest.raises(CookieFileError) as ex:
        load_session(str(p))
    assert "SECRETVALUE" not in str(ex.value) and ex.value.__cause__ is None


def test_sign_name_with_two_links_maps_to_none():
    http = FakeHttp()
    http.add("GET", CHUNK, FakeResponse(200, CHUNK_JS))
    machines = {
        "m1": {"k": {"a/x.pdf": "https://cdn/1?t=1", "same.pdf": "https://cdn/3"}},
        "m2": {"k": {"b/x.pdf": "https://cdn/2?t=2", "same.pdf": "https://cdn/3"}},
    }
    http.add(
        "POST",
        ITEM,
        FakeResponse(200, rsc({"ok": True, "data": {"signedUrls": machines}})),
    )
    urls = site_with(http).sign("p1", logged_in_page(), ["e1"])
    assert urls[("k", "a/x.pdf")] == "https://cdn/1?t=1"
    assert urls[("k", "b/x.pdf")] == "https://cdn/2?t=2"
    assert urls[("k", "x.pdf")] is None  # which one? neither
    assert urls[("k", "same.pdf")] == "https://cdn/3"  # same link twice is fine
