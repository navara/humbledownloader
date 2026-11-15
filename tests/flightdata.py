"""Builders for synthetic flight pages (no real account data)."""

import json


def page(*props, chunk_size=200):
    """HTML page whose flight payload holds the given props objects."""
    text = "".join("0:" + json.dumps(p, separators=(",", ":")) + "\n" for p in props)
    chunks = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
    pushes = "".join(
        f"<script>self.__next_f.push([1,{json.dumps(c)}])</script>" for c in chunks
    )
    return f"<html><body>{pushes}</body></html>"


def struct(file_name, size=1000, md5="0" * 32, name="PDF"):
    return {
        "id": "s-" + file_name,
        "name": name,
        "fileSize": size,
        "md5": md5,
        "sha1": None,
        "url": {"web": file_name, "bittorrent": file_name + ".torrent"},
    }


def download(key, platform, structs):
    return {
        "id": "d-" + key,
        "key": key,
        "platform": platform,
        "deliveryMethods": ["download"],
        "downloadStruct": structs,
    }


def entitlement(ent_id, title, downloads, type_="download_drm_free", **extra):
    e = {"id": ent_id, "title": title, "type": type_, "downloads": downloads}
    e.update(extra)
    return e


def item_props(product_id, title, entitlements, type_="bundle"):
    return {
        "userLibraryItem": {
            "id": "uli-" + product_id,
            "title": title,
            "type": type_,
            "entitlements": {"keys": [], "downloads": entitlements},
        },
        "productEntitlementId": product_id,
    }
