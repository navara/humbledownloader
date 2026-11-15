"""Keep secrets out of everything the tool prints, tracebacks included.

Signed download URLs carry their token in the query string (`?t=...`), the session
lives in cookies, and library data can carry product keys. requests and urllib3
put URLs into exception and warning text, so formatting our own messages
carefully is not enough. install() redacts every log record when it is created
(so every handler sees only redacted text), uncaught exceptions in the main
thread and in other threads, and warnings.
"""

import logging
import re
import sys
import threading
import traceback

# any query string after a URL or a bare path ("': /humblebundle/a.pdf?t=...")
URL_QUERY = re.compile(r"((?:https?://|(?<![\w.~-])/)[^\s?#\"'<>]*)\?[^\s\"'<>)]*")
# any other query holding a "=", wherever it starts (a quote in the path, no scheme)
QUERY = re.compile(r"\?(?=[^\s\"'<>)]*=)[^\s\"'<>)]*")
COOKIE_PAIR = re.compile(
    r"((?:ory_session_|csrf_token_)\w*['\"]?\s*[:=]\s*['\"]?)[^;\s'\"]+"
)
COOKIE_HEADER = re.compile(r"((?:set-)?cookie['\"]?\s*[:=]\s*)[^\r\n]*", re.IGNORECASE)
KEY_FIELD = re.compile(
    r"(['\"]?(?:keyFormatted|redemptionData)['\"]?\s*[:=]\s*)[^\r\n]*", re.IGNORECASE
)
# product keys like ABCDE-FGHIJ-KLMNO; at least one letter, not part of a longer
# dash-joined token (so UUIDs and version strings stay readable)
PRODUCT_KEY = re.compile(
    r"(?<![\w-])(?=[A-Z0-9-]*[A-Z])[A-Z0-9]{3,6}(?:-[A-Z0-9]{3,6}){2,}(?![\w-])"
)


def redact(text):
    text = URL_QUERY.sub(r"\1?<redacted>", text)
    text = QUERY.sub("?<redacted>", text)
    text = COOKIE_HEADER.sub(r"\1<redacted>", text)
    text = COOKIE_PAIR.sub(r"\1<redacted>", text)
    text = KEY_FIELD.sub(r"\1<redacted>", text)
    return PRODUCT_KEY.sub("<redacted>", text)


def _redact_record(record):
    record.msg = redact(record.getMessage())
    record.args = None
    if record.exc_info:
        record.exc_text = redact(
            "".join(traceback.format_exception(*record.exc_info))
        ).rstrip()
        record.exc_info = None
    elif record.exc_text:
        record.exc_text = redact(record.exc_text)
    if record.stack_info:
        record.stack_info = redact(record.stack_info)
    return record


_installed = False


def install(level=logging.INFO, stream=None):
    """Log to stderr; redact every record, uncaught exception and warning."""
    global _installed
    if not _installed:
        make_record = logging.getLogRecordFactory()
        logging.setLogRecordFactory(
            lambda *a, **k: _redact_record(make_record(*a, **k))
        )
        _installed = True
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.captureWarnings(True)

    def excepthook(exc_type, exc, tb):
        sys.stderr.write(redact("".join(traceback.format_exception(exc_type, exc, tb))))

    def thread_hook(args):
        excepthook(args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = excepthook
    threading.excepthook = thread_hook
