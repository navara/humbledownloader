"""Fake HTTP for site/download/run tests (no network)."""

import hashlib
import json

from requests.structures import CaseInsensitiveDict

from tests.flightdata import page

BASE = "https://www.humblebundle.com"
SIGN_ID = "a" * 42
LIST_ID = "b" * 42
CHUNK = "/_next/static/chunks/app.js"
CHUNK_JS = (
    f'let s=(0,r.createServerReference)("{SIGN_ID}",r.callServer,void 0,'
    'r.findSourceMapURL,"getSignedUrlsForEntitlements");'
    f'let l=(0,r.createServerReference)("{LIST_ID}",r.callServer,void 0,'
    'r.findSourceMapURL,"filterUserLibrary");'
    f'let x=(0,r.createServerReference)("{"c" * 42}",r.callServer,void 0,'
    'r.findSourceMapURL,"redeemEntitlementAction");'
)


class FakeResponse:
    def __init__(self, status=200, text="", headers=None, content=None, iter_exc=None):
        self.status_code = status
        self.text = text
        self.headers = CaseInsensitiveDict(headers or {})
        self.content = content if content is not None else text.encode()
        self.iter_exc = iter_exc
        self.closed = False

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self.content), 3):
            yield self.content[i : i + 3]
        if self.iter_exc:
            raise self.iter_exc

    def close(self):
        self.closed = True


class FakeHttp:
    """Routes (METHOD, url or path) to a response, a list of responses (served in
    order), an exception, or a callable(kwargs) -> response."""

    def __init__(self):
        self.routes = {}
        self.calls = []

    def add(self, method, where, response):
        self.routes[(method, where)] = response

    def _serve(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        key = (method, url.removeprefix(BASE))
        r = self.routes.get(key)
        if r is None:
            return FakeResponse(404, "not found")
        if isinstance(r, list):
            r = r.pop(0) if len(r) > 1 else r[0]
        if isinstance(r, BaseException):
            raise r
        if callable(r):
            return r(kwargs)
        return r

    def request(self, method, url, **kwargs):
        return self._serve(method, url, kwargs)

    def get(self, url, **kwargs):
        return self._serve("GET", url, kwargs)

    def count(self, method, prefix=""):
        return sum(1 for m, u, _ in self.calls if m == method and u.startswith(prefix))


def logged_in_page(*props, chunks=(CHUNK,)):
    html = page({"user": {"logged_in": True}}, *props)
    scripts = "".join(f'<script src="{c}"></script>' for c in chunks)
    return html.replace("<body>", "<body>" + scripts)


def rsc(row1):
    return '0:{"a":"$@1"}\n1:' + json.dumps(row1) + "\n"


def md5(data):
    return hashlib.md5(data).hexdigest()
