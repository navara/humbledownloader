"""All access to www.humblebundle.com: pages, the Purchased list and signing.

Observed on the site (2026-11-15): pages embed their data as Next.js flight
data; logged-out pages answer 200 with `"logged_in":false`; server actions are
POSTs to the page path with `Next-Action: <id>`, ids found by export name in the
page's /_next/static/chunks; a stale id answers 409 with
`x-nextjs-action-not-found: 1`.
"""

import http.cookiejar
import json
import logging
import math
import re
import time
import warnings

import requests

from . import __version__, catalog
from .flight import LayoutError, flight_text

logger = logging.getLogger(__name__)

BASE = "https://www.humblebundle.com"
# Cloudflare blocks anonymous-looking POSTs (default python-requests agent, no Origin):
# say honestly which tool this is, and send the Origin a browser would send.
USER_AGENT = f"humbledl/{__version__} (+https://github.com/navara/humbledownloader)"
TIMEOUT = (10, 60)
PACE = 1.0  # seconds between requests to www
RETRY_STATUSES = {429, 500, 502, 503, 504}
TRIES = 3
MAX_RETRY_AFTER = 300  # seconds; longer waits stop the run instead
SIGN_ACTION = "getSignedUrlsForEntitlements"
LIST_ACTION = "filterUserLibrary"
LIST_LIMIT = 100
LIST_EMPTY_STATE = (
    '[{"results":[],"pagination":{"page":1,"totalPages":1,"totalDocs":0},'
    '"success":true},"$K1"]'
)
CHUNK_RE = re.compile(r"/_next/static/chunks/[A-Za-z0-9_.~-]+\.js")


class SessionLost(Exception):
    """Not logged in any more: export the cookies again."""


class CookieFileError(Exception):
    """The cookie file cannot be used: export it again."""


class Challenge(Exception):
    """Cloudflare asks for a browser check; this tool does not try to pass it."""


class Blocked(Exception):
    """Cloudflare's firewall refuses the request ("Sorry, you have been blocked")."""


class SiteError(Exception):
    """A request kept failing."""


class RateLimited(Exception):
    """Humble keeps answering 429: stop and try again later."""


def _cloudflare_block(r):
    """A 403 page from Cloudflare's firewall, not from Humble: no cf-mitigated header,
    so it is not a browser check, and the session may well be valid."""
    return (
        r.status_code == 403
        and r.headers.get("server", "").lower() == "cloudflare"
        and b"you have been blocked" in r.content[:65536]
    )


def load_session(cookie_file):
    """requests.Session carrying only the Humble session cookie from a Netscape
    cookie export (the browser's other cookies are not replayed)."""
    jar = http.cookiejar.MozillaCookieJar(cookie_file)
    try:
        with warnings.catch_warnings():  # cookiejar warns "bug!" on malformed lines
            warnings.simplefilter("ignore")
            jar.load(ignore_discard=True)  # the session cookie has expiry 0
    except OSError, ValueError:  # LoadError is an OSError; ValueError: not UTF-8
        # the LoadError text quotes the offending line, cookie value included
        raise CookieFileError(
            f"{cookie_file} cannot be read as a Netscape cookie export"
        ) from None
    found = [c for c in jar if c.name.startswith("ory_session_")]
    if len(found) != 1:
        raise CookieFileError(
            f"{cookie_file} holds {len(found)} ory_session_ cookies, expected 1"
        )
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    session.cookies.set_cookie(found[0])
    return session


class Site:
    def __init__(self, session, sleep=time.sleep, clock=time.monotonic):
        self.session = session
        self.sleep = sleep
        self.clock = clock
        self._last = None
        self._actions = {}  # export name -> id (build-wide)

    # --- HTTP -------------------------------------------------------------

    def _request(self, method, path, **kwargs):
        for attempt in range(1, TRIES + 1):
            if self._last is not None:
                wait = PACE - (self.clock() - self._last)
                if wait > 0:
                    self.sleep(wait)
            self._last = self.clock()
            try:
                r = self.session.request(
                    method,
                    BASE + path,
                    allow_redirects=False,
                    timeout=TIMEOUT,
                    **kwargs,
                )
            except requests.RequestException as e:
                if attempt == TRIES:
                    raise SiteError(
                        f"{method} {path}: {e.__class__.__name__}"
                    ) from None
                self.sleep(5 * attempt)
                continue
            if r.headers.get("cf-mitigated") == "challenge":
                raise Challenge(f"{method} {path}: Cloudflare challenge")
            if _cloudflare_block(r):
                raise Blocked(f"{method} {path}: HTTP {r.status_code}")
            if r.headers.get("x-action-redirect"):
                raise SessionLost(f"{method} {path}: redirected by the server action")
            if r.status_code in RETRY_STATUSES:
                wait = _retry_after(r, 5 * attempt)
                if attempt == TRIES or wait > MAX_RETRY_AFTER:
                    if r.status_code == 429:
                        raise RateLimited(f"{method} {path}: HTTP 429")
                    raise SiteError(f"{method} {path}: HTTP {r.status_code}")
                self.sleep(wait)
                continue
            if 300 <= r.status_code < 400 or r.status_code in (401, 403):
                raise SessionLost(f"{method} {path}: HTTP {r.status_code}")
            return r
        raise AssertionError("unreachable")

    def page(self, path):
        """(html, flight text) of a logged-in page."""
        r = self._request("GET", path)
        if r.status_code != 200:
            raise SiteError(f"GET {path}: HTTP {r.status_code}")
        text = flight_text(r.text)
        if '"logged_in":false' in text:
            raise SessionLost(f"GET {path}: not logged in")
        if '"logged_in":true' not in text:
            raise LayoutError(f"GET {path}: no login state in page data")
        return r.text, text

    # --- server actions ---------------------------------------------------

    def _discover(self, path, html, name):
        ids = set()
        pattern = re.compile(
            r'createServerReference\)\("([0-9a-f]{40,42})",[^)]*?"'
            + re.escape(name)
            + r'"\)'
        )
        for chunk in sorted(set(CHUNK_RE.findall(html))):
            r = self._request("GET", chunk)
            if r.status_code != 200:
                # a missing chunk could hide a second id: never decide without it
                raise SiteError(f"GET {chunk}: HTTP {r.status_code}")
            ids |= set(pattern.findall(r.text))
        if len(ids) != 1:
            raise LayoutError(f"server action {name}: found {len(ids)} ids on {path}")
        self._actions[name] = ids.pop()
        return self._actions[name]

    def _action(self, path, name, html, headers_extra=None, **kwargs):
        """POST a server action; rediscover its id once if Humble redeployed."""
        aid = self._actions.get(name) or self._discover(path, html, name)
        for again in (True, False):
            headers = {"Accept": "text/x-component", "Next-Action": aid, "Origin": BASE}
            headers.update(headers_extra or {})
            r = self._request("POST", path, headers=headers, **kwargs)
            if r.status_code == 409 and r.headers.get("x-nextjs-action-not-found"):
                if not again:
                    break
                html, _ = self.page(path)
                aid = self._discover(path, html, name)
                continue
            if r.status_code != 200:
                raise SiteError(f"{name}: HTTP {r.status_code}")
            # text/x-component carries no charset: requests would guess Latin-1
            return _row(r.content.decode("utf-8", "replace"), "1", name)
        raise LayoutError(f"server action {name} not found even after rediscovery")

    # --- library ----------------------------------------------------------

    def library(self):
        """(html, [(productEntitlementId, title)]) of the Bundles tab."""
        html, text = self.page("/home/library")
        return html, catalog.parse_library(text)

    def purchased_ids(self, library_html):
        """productEntitlementIds of DRM-free items on the Purchased tab."""
        ids, page, pages = [], 1, 1
        while page <= pages:
            fields = {
                "_1_requestId": "1",
                "_1_query": "",
                "_1_sort": "mostRecent",
                "_1_cursor": str(page * LIST_LIMIT),
                "_1_page": str(page),
                "_1_limit": str(LIST_LIMIT),
                "_1_hideRevealed": "false",
                "_1_hideExpired": "false",
                "_1_dateExpires": "",
                "0": LIST_EMPTY_STATE,
            }
            data = self._action(
                "/home/library",
                LIST_ACTION,
                library_html,
                files={k: (None, v) for k, v in fields.items()},
            )
            if not (
                isinstance(data, dict)
                and data.get("success") is True
                and isinstance(data.get("results"), list)
                and isinstance(data.get("pagination"), dict)
            ):
                raise LayoutError(f"{LIST_ACTION}: unexpected response for page {page}")
            pages = data["pagination"].get("totalPages")
            if not isinstance(pages, int) or data["pagination"].get("page") != page:
                raise LayoutError(
                    f"{LIST_ACTION}: page {page} answered with other paging"
                )
            for pid in catalog.purchased_product_ids(data["results"]):
                if pid not in ids:
                    ids.append(pid)
            del data  # rows carry key data; keep nothing but the ids
            page += 1
        return ids

    def item(self, product_entitlement_id):
        """(html, catalog.Item) of one item page."""
        path = "/home/library/" + product_entitlement_id
        html, text = self.page(path)
        return html, catalog.parse_item(text, product_entitlement_id)

    def sign(self, product_entitlement_id, html, entitlement_ids):
        """{(download_key, file name): signed url} for the given entitlements.

        The response names files by url.web, which may carry a publisher prefix;
        both the full value and its last part are mapped.
        """
        path = "/home/library/" + product_entitlement_id
        data = self._action(
            path,
            SIGN_ACTION,
            html,
            data=json.dumps([list(entitlement_ids)]),
            headers_extra={"Content-Type": "text/plain;charset=UTF-8"},
        )
        body = data.get("data") if isinstance(data, dict) else None
        urls = body.get("signedUrls") if isinstance(body, dict) else None
        if not (
            isinstance(data, dict) and data.get("ok") is True and isinstance(urls, dict)
        ):
            raise LayoutError(f"{SIGN_ACTION}: unexpected response shape")
        out = {}
        ambiguous = set()
        for machine in urls.values():
            if not isinstance(machine, dict):
                raise LayoutError(f"{SIGN_ACTION}: unexpected response shape")
            for download_key, files in machine.items():
                if not isinstance(files, dict):
                    raise LayoutError(f"{SIGN_ACTION}: unexpected response shape")
                for name, url in files.items():
                    if name.endswith(".torrent") or not isinstance(url, str):
                        continue
                    for k in ((download_key, name), (download_key, _base(name))):
                        if out.setdefault(k, url) != url:
                            ambiguous.add(k)
        for k in ambiguous:
            # two links for one name: a file could be stored under the other's name
            logger.warning("Signed links name %s twice; not downloaded", "/".join(k))
            out[k] = None
        return out


def _base(name):
    return name.rsplit("/", 1)[-1]


def _row(text, row_id, name):
    prefix = row_id + ":"
    for line in text.split(chr(10)):  # not splitlines(): titles may hold U+0085
        if line.startswith(prefix):
            try:
                return json.loads(line[len(prefix) :])
            except ValueError as e:
                raise LayoutError(f"{name}: row {row_id} is not JSON") from e
    raise LayoutError(f"{name}: response has no row {row_id}")


def _retry_after(response, default):
    try:
        wait = float(response.headers.get("Retry-After", default))
    except ValueError:
        return default
    return wait if math.isfinite(wait) and wait >= 0 else default
