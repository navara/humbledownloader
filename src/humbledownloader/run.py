"""Collect the catalog, decide per file, then adopt, sign and download."""

import contextlib
import datetime
import errno
import hashlib
import logging
import os
import time
from collections import Counter
from dataclasses import dataclass, field

from . import state as st
from .download import DownloadError, LinkExpired, fetch
from .flight import LayoutError
from .paths import MAX_PATH, assign_targets, collision_key
from .site import SiteError

logger = logging.getLogger(__name__)

LOCK_NAME = ".humble-lock"
MOVES_LOG = ".humble-moves.log"  # old<TAB>new per moved file, for undoing by hand
MAX_FAILED_PRODUCTS = 3  # items in a row where every download failed
RENAME_RETRIES = (0.1, 0.5, 2.0, None)


@dataclass
class Task:
    entry: object
    path: str
    action: st.Action


@dataclass
class Summary:
    counts: Counter = field(default_factory=Counter)
    failures: list = field(default_factory=list)
    pending_bytes: int = 0
    downloaded_bytes: int = 0

    def fail(self, message):
        self.counts["failed"] += 1
        self.failures.append(message)
        logger.error(message)


class LibraryLocked(Exception):
    pass


class Lock:
    """One run per library at a time."""

    def __init__(self, library):
        self.path = os.path.join(library, LOCK_NAME)

    def __enter__(self):
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                with open(self.path, encoding="utf-8") as f:
                    owner = f.read().strip()
            except OSError:
                owner = "?"
            raise LibraryLocked(
                f"{self.path} exists: another run (process {owner}) is active, or "
                "one was killed (then delete the file)"
            ) from None
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return self

    def __exit__(self, *exc):
        os.remove(self.path)


def collect(site, summary, only=None):
    """[(item html, Item)] for the Bundles tab plus DRM-free Purchased items;
    with `only`, just the items whose title contains that text."""
    library_html, bundles = site.library()
    ids = [pid for pid, _ in bundles]
    for pid in site.purchased_ids(library_html):
        if pid not in ids:
            ids.append(pid)
    logger.info("Library: %d bundles, %d items in total", len(bundles), len(ids))
    items = []
    for n, pid in enumerate(ids, 1):
        try:
            html, item = site.item(pid)
        except (LayoutError, SiteError) as e:
            summary.fail(f"Item {pid}: {e}")
            continue
        except (AttributeError, TypeError) as e:  # page data of an unexpected shape
            summary.fail(
                f"Item {pid}: unexpected page data ({e.__class__.__name__}: {e})"
            )
            continue
        if only and only.lower() not in item.title.lower():
            continue
        for reason in item.skipped:
            logger.warning("%s: %s", item.title, reason)
        if not item.files:
            summary.counts["items without files"] += 1
        items.append((html, item))
        if n % 25 == 0:
            logger.info("Read %d/%d items", n, len(ids))
    return items


def plan(library, items, records, exclude, summary):
    """Tasks for every usable file; excluded platforms, name errors and
    conflicts are counted and logged."""
    entries = []
    for _, item in items:
        for e in item.files:
            if e.platform in exclude:
                summary.counts["excluded"] += 1
            else:
                entries.append(e)
    targets, conflicts, errors = assign_targets(library, entries)
    for e, message in errors:
        summary.fail(f"{e.product_title} / {e.entitlement_title}: {message}")
    # entries in a conflict may share one key, so choose them by identity
    chosen = {}  # id(entry) -> path
    blocked = set()
    for path, group in conflicts.items():
        # the site lists one file name in several versions; the sign response can
        # only name one, so keep the copy on disk if it matches one of them
        blocked.update(id(e) for e in group)
        size = _size(path)
        matching = [e for e in group if e.size == size]
        if len(matching) == 1:
            chosen[id(matching[0])] = path
        summary.counts["conflicts"] += 1
        logger.warning(
            "%s is listed %d times with different contents%s",
            path,
            len(group),
            "; keeping the copy on disk" if len(matching) == 1 else "; not downloaded",
        )
    tasks = []
    for e in entries:
        path = chosen.get(id(e)) if id(e) in blocked else targets.get(e.key)
        if path is None:
            continue
        record = records.get(e.key)
        recorded_size = None
        if record and record.get("path"):
            old = os.path.join(library, record["path"])
            if collision_key(old) != collision_key(path):
                recorded_size = _size(old)
        tasks.append(Task(e, path, st.decide(e, record, _size(path), recorded_size)))
    return tasks


def _size(path):
    return os.path.getsize(path) if os.path.isfile(path) else None


def execute(library, site, items, tasks, data, state_path, summary, dry_run, http=None):
    records = data["files"]
    html_of = {item.product_entitlement_id: html for html, item in items}
    done_paths = {}  # collision key of a path handled in this run -> its record
    moved = {}  # collision key of an old path -> new path
    pending = []
    for t in tasks:
        if t.action is st.Action.SKIP:
            summary.counts["skipped"] += 1
            rel = os.path.relpath(t.path, library)
            if not dry_run and records[t.entry.key].get("path") != rel:
                records[t.entry.key] = dict(records[t.entry.key], path=rel)
        elif t.action is st.Action.ADOPT:
            summary.counts["adopted"] += 1
            if not dry_run:
                records[t.entry.key] = st.record_for(
                    library, t.path, t.entry, "adopted", False
                )
        elif t.action is st.Action.MOVE:
            _move(library, t, records, moved, done_paths, summary, dry_run)
            if not dry_run:
                st.save(state_path, data)
        else:
            pending.append(t)
    if not dry_run:
        st.save(state_path, data)
    summary.pending_bytes = sum(t.entry.size for t in pending)
    if dry_run:
        for t in pending:
            summary.counts["would " + t.action.value] += 1
            logger.info("Would %s: %s", t.action.value, t.path)
        return

    by_product = {}
    for t in pending:
        by_product.setdefault(t.entry.product_entitlement_id, []).append(t)
    failed_products = 0
    for pid, group in by_product.items():
        ok, failed = _download_product(
            library, site, pid, html_of[pid], group, records, done_paths,
            state_path, data, summary, http,
        )  # fmt: skip
        failed_products = failed_products + 1 if failed and not ok else 0
        if failed_products >= MAX_FAILED_PRODUCTS:
            summary.fail(f"Stopped after {failed_products} items in a row failed")
            return


def _move(library, t, records, moved, done_paths, summary, dry_run):
    """Move a tracked file to the folder its (renamed) title now gives."""
    old = os.path.join(library, records[t.entry.key]["path"])
    if dry_run:
        summary.counts["would move"] += 1
        logger.info("Would move %s -> %s", old, t.path)
        return
    old_key = collision_key(old)
    if old_key in moved:  # the same file, recorded under another key, moved already
        if collision_key(moved[old_key]) != collision_key(t.path):
            summary.fail(f"{old} is shared and would move to two places; left in place")
            return
    else:
        try:
            os.makedirs(os.path.dirname(t.path), exist_ok=True)
            rename_new(old, t.path)
        except OSError as e:
            summary.fail(f"Moving {old} -> {t.path}: {e}")
            return
        moved[old_key] = t.path
        with open(os.path.join(library, MOVES_LOG), "a", encoding="utf-8") as log:
            log.write(f"{old}\t{t.path}\n")
        summary.counts["moved"] += 1
        logger.info("Moved %s -> %s", old, t.path)
    records[t.entry.key] = dict(
        records[t.entry.key], path=os.path.relpath(t.path, library)
    )
    done_paths[collision_key(t.path)] = records[t.entry.key]


def _download_product(
    library,
    site,
    pid,
    html,
    group,
    records,
    done_paths,
    state_path,
    data,
    summary,
    http,
):
    """Sign once per item, then download its files. Returns (succeeded, failed)."""
    ent_ids = sorted({t.entry.entitlement_id for t in group})
    urls = None
    ok = failed = 0
    for n, t in enumerate(group):
        key = collision_key(t.path)
        if key in done_paths:  # same file listed under another entitlement
            records[t.entry.key] = dict(done_paths[key])
            st.save(state_path, data)
            continue
        for attempt in (1, 2):
            if urls is None:
                try:
                    urls = site.sign(pid, html, ent_ids)
                except (LayoutError, SiteError) as e:
                    # asking again for every file would not help: fail the rest
                    for rest in group[n:]:
                        summary.fail(f"{rest.path}: cannot sign ({e})")
                    return ok, failed + len(group) - n
            url = urls.get((t.entry.download_key, t.entry.web_path)) or urls.get(
                (t.entry.download_key, t.entry.file_name)
            )
            try:
                if url is None:
                    raise DownloadError("the signed links do not name this file")
                update = t.action is st.Action.UPDATE
                current = t.path if update and os.path.isfile(t.path) else None
                logger.info("Downloading: %s", t.path)
                part, size, md5 = fetch(
                    url, t.path, t.entry.size, t.entry.md5, http=http, current=current
                )
                # part None: the CDN proved the file on disk is what it serves
                installed = part is not None and _install(part, t.path, update, md5)
            except LinkExpired:
                urls = None
                if attempt == 1:
                    continue
                summary.fail(f"{t.path}: signed link refused twice")
                failed += 1
            except (DownloadError, OSError) as e:
                summary.fail(f"{t.path}: {e}")
                failed += 1
            else:
                ok += 1
                if (size, md5) != (t.entry.size, t.entry.md5):
                    summary.counts["newer than catalog"] += 1
                    logger.warning(
                        "%s: Humble's catalog lists %d bytes, the CDN serves a newer "
                        "file of %d bytes (complete, matches the CDN's checksum)",
                        t.path, t.entry.size, size,
                    )  # fmt: skip
                if not installed:
                    summary.counts["already current"] += 1
                    logger.info("Already current, kept as it was: %s", t.path)
                elif update:
                    summary.counts["updated"] += 1
                else:
                    summary.counts["downloaded"] += 1
                if part is not None:
                    summary.downloaded_bytes += size
                records[t.entry.key] = st.record_for(
                    library, t.path, t.entry,
                    "download" if part is not None else "adopted", True, size, md5,
                )  # fmt: skip
                done_paths[key] = records[t.entry.key]
                st.save(state_path, data)
            break
    return ok, failed


def _install(part, path, update, md5):
    """Give a verified .part its final name, keeping an old version aside first.
    Returns False, dropping the .part, when the file at `path` already has the
    same content. On failure (Ctrl+C included) the .part is removed and the old
    version restored."""
    aside = None
    try:
        if update and os.path.exists(path):
            if _same_content(path, part, md5):
                _discard(part)
                return False
            aside = rename_aside(path)
            logger.info("Kept the previous version as %s", aside)
            if len(aside) >= MAX_PATH:
                logger.warning("The kept version's path has %d characters", len(aside))
        for delay in RENAME_RETRIES:
            try:
                rename_new(part, path)
                return True
            except PermissionError:  # antivirus or indexer still holds the file
                if delay is None:
                    raise
                time.sleep(delay)
    except BaseException:
        if aside and not os.path.exists(path):
            rename_new(aside, path)
        _discard(part)
        raise


def _discard(part):
    """Remove the tool's own .part, waiting for a scanner that still holds it;
    if it stays locked, leave it with a warning rather than fail the file."""
    for delay in RENAME_RETRIES:
        try:
            os.remove(part)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if delay is None:
                logger.warning("%s stays locked; delete it by hand", part)
                return
            time.sleep(delay)


def _same_content(path, part, md5):
    if os.path.getsize(path) != os.path.getsize(part):
        return False
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest() == md5


def rename_aside(path):
    """Move an existing file out of the way without ever overwriting another.

    The new name carries the old file's own modification date: it names that version.
    """
    stem, ext = os.path.splitext(path)
    stamp = datetime.date.fromtimestamp(os.path.getmtime(path)).strftime("%Y%m%d")
    candidate = f"{stem}_old-{stamp}{ext}"
    n = 1
    while os.path.exists(candidate):
        candidate = f"{stem}_old-{stamp}-{n}{ext}"
        n += 1
    rename_new(path, candidate)
    return candidate


def rename_new(src, dst):
    """Rename that never replaces an existing dst (FileExistsError instead).

    Windows refuses by itself; elsewhere os.rename would replace dst silently, so
    link + unlink is used, which fails when dst exists. Where the file system has no
    hard links, dst is checked first (a small race, better than always replacing)."""
    if os.name == "nt":
        os.rename(src, dst)
        return
    try:
        os.link(src, dst)
    except FileExistsError:
        raise
    except OSError:  # no hard links here
        if os.path.lexists(dst):
            raise FileExistsError(errno.EEXIST, "file exists", dst) from None
        os.rename(src, dst)
        return
    try:
        os.remove(src)
    except BaseException:  # keep one name: undo the link (dst is the same file)
        os.remove(dst)
        raise


def run(library, site, exclude, dry_run=False, http=None, only=None):
    summary = Summary()
    state_path = os.path.join(library, st.STATE_NAME)
    with contextlib.nullcontext() if dry_run else Lock(library):
        data = st.load(state_path)
        items = collect(site, summary, only)
        if not items:
            raise LayoutError(
                f"no item title contains {only!r}"
                if only
                else "the library returned no items"
            )
        tasks = plan(library, items, data["files"], exclude, summary)
        execute(
            library, site, items, tasks, data, state_path, summary, dry_run, http=http
        )
    return summary
