"""Turn Humble library pages into a list of downloadable files."""

import datetime
import re
from dataclasses import dataclass, field

from .flight import LayoutError, find_value

STOREFRONT_SUFFIX = re.compile(r"\s*\((?:steam|download)\)$")
PLACEHOLDER = re.compile(r"<[^<>]*>")  # e.g. a literal "<FILE_NAME>" in real data
MD5 = re.compile(r"[0-9a-fA-F]{32}")


@dataclass(frozen=True)
class FileEntry:
    product_entitlement_id: str  # item page /home/library/<id>, the sign page
    product_title: str  # top folder
    entitlement_id: str  # signed by getSignedUrlsForEntitlements
    entitlement_title: str  # second folder
    download_key: str  # key in the sign response
    platform: str
    web_path: str  # url.web, may carry a publisher prefix ("publisher/x.zip")
    size: int
    md5: str

    @property
    def file_name(self):
        return self.web_path.rsplit("/", 1)[-1]

    @property
    def key(self):
        return f"{self.entitlement_id}/{self.download_key}/{self.web_path}"


@dataclass
class Item:
    product_entitlement_id: str
    title: str
    files: list = field(default_factory=list)
    skipped: list = field(default_factory=list)  # human-readable reasons


def parse_library(text):
    """(productEntitlementId, title) of every item on the library's Bundles tab."""
    items = find_value(text, "bundleItems")
    if items is None:
        raise LayoutError("library page has no bundleItems")
    return [(_req(i, "productEntitlementId"), _req(i, "title")) for i in items]


def purchased_product_ids(rows):
    """productEntitlementIds of DRM-free rows of the Purchased list, in order.

    Reads only `type` and `productEntitlementId`; rows also carry key data, which
    must never be kept.
    """
    out = []
    for row in rows:
        if row.get("type") == "download_drm_free":
            pid = _req(row, "productEntitlementId")
            if pid not in out:
                out.append(pid)
    return out


def parse_item(text, product_entitlement_id, now=None):
    uli = find_value(text, "userLibraryItem")
    if uli is None:
        raise LayoutError("item page has no userLibraryItem")
    title = _req(uli, "title")
    item = Item(product_entitlement_id, title)
    ents = uli.get("entitlements")
    downloads = _list_or_none(
        ents.get("downloads") if isinstance(ents, dict) else ents,
        "userLibraryItem.entitlements.downloads",
    )
    folder_title = title
    if uli.get("type") == "storefront":
        # store purchases are titled "Quiet Lighthouse (steam)"; hbd's folders lack it
        folder_title = STOREFRONT_SUFFIX.sub("", title)
    for ent in downloads or []:
        files = _list_or_none(ent.get("downloads"), "entitlement downloads")
        if files is None:
            continue  # message, cross promo or "No Content" entries
        ent_title = _req(ent, "title")
        unusable = _unusable(ent, now or datetime.datetime.now(datetime.UTC))
        if unusable:
            item.skipped.append(f"{ent_title}: {unusable}")
            continue
        for dl in files:
            for st in dl.get("downloadStruct") or []:
                web = (st.get("url") or {}).get("web")
                size, md5 = st.get("fileSize"), st.get("md5")
                problem = None
                if not isinstance(web, str) or not web or web.startswith("$"):
                    problem = f"{st.get('name')} has no file"
                elif PLACEHOLDER.search(web):
                    problem = f"{st.get('name')} has a placeholder file name {web!r}"
                elif not isinstance(size, int) or isinstance(size, bool):
                    problem = f"{web} has no usable size"
                elif not isinstance(md5, str) or not MD5.fullmatch(md5):
                    problem = f"{web} has no usable md5"
                if problem:
                    item.skipped.append(f"{ent_title}: {problem}")
                    continue
                item.files.append(
                    FileEntry(
                        product_entitlement_id=product_entitlement_id,
                        product_title=folder_title,
                        entitlement_id=_req(ent, "id"),
                        entitlement_title=ent_title,
                        download_key=_req(dl, "key"),
                        platform=_req(dl, "platform"),
                        web_path=web,
                        size=size,
                        md5=md5.lower(),
                    )
                )
    return item


def _unusable(ent, now):
    """Why the site would not offer this entitlement for download, or None.
    Only entitlements the site UI offers are ever signed."""
    if ent.get("type") != "download_drm_free":
        return f"type {ent.get('type')!r} is not a DRM-free download"
    if ent.get("expired") is True:
        return "expired"
    before, after = ent.get("unavailableBefore"), ent.get("unavailableAfter")
    if isinstance(before, str) and now < _when(before):
        return f"not available before {before}"
    if isinstance(after, str) and now > _when(after):
        return f"not available after {after}"
    return None


def _when(value):
    try:
        when = datetime.datetime.fromisoformat(value)
    except ValueError:
        raise LayoutError(f"unreadable date {value!r}") from None
    if when.tzinfo is None:
        raise LayoutError(f"date without time zone {value!r}")
    return when


def _list_or_none(value, name):
    """Lists pass; absent or "$undefined" mean "nothing"; anything else is a
    layout change."""
    if value is None or value == "$undefined":
        return None
    if not isinstance(value, list):
        raise LayoutError(f"{name} is neither a list nor empty")
    return value


def _req(obj, name):
    """A non-empty text field (titles, ids, keys, platforms)."""
    value = obj.get(name) if isinstance(obj, dict) else None
    if value in (None, "", "$undefined"):
        raise LayoutError(f"missing field {name!r}")
    if not isinstance(value, str):
        raise LayoutError(f"field {name!r} is not text")
    return value
