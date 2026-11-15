import io
import logging
import sys
import threading
import warnings

import pytest
import requests

from humbledownloader import redact
from humbledownloader.redact import redact as r

SECRET_URL = "https://cdn.humble.com/humblebundle/book.pdf?t=SECRETTOKEN&x=1"


@pytest.fixture(autouse=True)
def restore_logging_and_hooks():
    root = logging.getLogger()
    saved = root.handlers[:], root.level, sys.excepthook, threading.excepthook
    yield
    root.handlers[:], level, sys.excepthook, threading.excepthook = saved[0], *saved[1:]
    root.setLevel(level)
    logging.captureWarnings(False)


@pytest.mark.parametrize(
    "text, gone, kept",
    [
        (f"Failed {SECRET_URL}.", "SECRETTOKEN", "book.pdf?<redacted>"),
        (
            "Max retries exceeded with url: /humblebundle/a.pdf?t=SECRETTOKEN (Caused",
            "SECRETTOKEN",
            "/humblebundle/a.pdf?<redacted>",
        ),
        # urllib3 retry warning: a bare path after a quote and colon
        (
            "Retrying (Retry(total=2)) after connection broken by 'ReadTimeoutError()': /humblebundle/a.pdf?t=SECRETTOKEN",
            "SECRETTOKEN",
            "a.pdf?<redacted>",
        ),
        (
            "Location: /home/library?return_to=SECRETTOKEN",
            "SECRETTOKEN",
            "/home/library?<redacted>",
        ),
        ('{"keyFormatted":"AAAAA-BBBBB-CCCCC"}', "BBBBB", '"keyFormatted":<redacted>'),
        # a quote in the path, and a link without a scheme
        (
            "Failed to parse headers (url=https://cdn.humble.com/humblebundle/hero's_journey.pdf?t=SECRETTOKEN): x",
            "SECRETTOKEN",
            "_journey.pdf?<redacted>",
        ),
        (
            "Invalid URL 'cdn.humble.com/a.pdf?t=SECRETTOKEN&ttl=9': No scheme supplied",
            "SECRETTOKEN",
            "a.pdf?<redacted>",
        ),
        (
            "https://cdn.humble.com/it's.pdf?download&t=SECRETTOKEN",
            "SECRETTOKEN",
            "it's.pdf?<redacted>",
        ),
        ("cdn.humble.com/a.pdf?&t=SECRETTOKEN", "SECRETTOKEN", "a.pdf?<redacted>"),
        ("x/it's.pdf?t%5B%5D=SECRETTOKEN", "SECRETTOKEN", "pdf?<redacted>"),
        ("{'keyFormatted': 'abcd1234efgh'}", "abcd1234efgh", "'keyFormatted':"),
        ("{'redemptionData': {'code': 'X'}, 'more': 'Y'}", "'X'", "'redemptionData':"),
        ("key ABCDE-FGHIJ-KLMNO here", "FGHIJ", "key <redacted> here"),
        (
            "Cookie: csrf_token_a=abc; ory_session_x=SECRETCOOKIE",
            "SECRETCOOKIE",
            "Cookie: <redacted>",
        ),
        (
            "<RequestsCookieJar[<Cookie ory_session_x=SECRETCOOKIE for .humblebundle.com/>]>",
            "SECRETCOOKIE",
            "ory_session_x=<redacted>",
        ),
        ("{'ory_session_x': 'SECRETCOOKIE'}", "SECRETCOOKIE", "ory_session_x"),
        # must stay readable
        (
            "entitlement 00000000-0000-4000-8000-000000000000 failed",
            None,
            "00000000-0000-4000-8000-000000000000",
        ),
        (
            "Humble Book Bundle - Weather Any Storm / Book / guide.pdf",
            None,
            "Weather Any Storm / Book / guide.pdf",
        ),
    ],
)
def test_redact(text, gone, kept):
    out = r(text)
    if gone:
        assert gone not in out
    assert kept in out


def test_every_handler_gets_redacted_records(caplog):
    redact.install(stream=io.StringIO())
    logging.getLogger().addHandler(caplog.handler)  # a handler install() does not own
    try:
        raise requests.ConnectionError(
            "HTTPSConnectionPool(host='cdn.humble.com', port=443): Max retries exceeded "
            "with url: /humblebundle/book.pdf?t=SECRETTOKEN"
        )
    except requests.ConnectionError:
        logging.getLogger("t").exception("Download failed: %s", SECRET_URL)
    assert "Download failed" in caplog.text
    assert "SECRETTOKEN" not in caplog.text


def test_install_output_is_redacted():
    stream = io.StringIO()
    redact.install(stream=stream)
    try:
        raise requests.HTTPError(f"403 Client Error: Forbidden for url: {SECRET_URL}")
    except requests.HTTPError:
        logging.getLogger("t").exception("failed")
    assert "failed" in stream.getvalue() and "SECRETTOKEN" not in stream.getvalue()


def test_uncaught_exception_hooks_redact(capsys):
    redact.install(stream=io.StringIO())
    try:
        raise RuntimeError(f"boom {SECRET_URL}")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())
    t = threading.Thread(
        target=lambda: (_ for _ in ()).throw(RuntimeError(f"bang {SECRET_URL}"))
    )
    t.start()
    t.join()
    err = capsys.readouterr().err
    assert "boom" in err and "bang" in err and "SECRETTOKEN" not in err


def test_warnings_are_redacted():
    stream = io.StringIO()
    redact.install(stream=stream)
    warnings.warn(f"odd response from {SECRET_URL}", stacklevel=1)
    assert (
        "odd response" in stream.getvalue() and "SECRETTOKEN" not in stream.getvalue()
    )
