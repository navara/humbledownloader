"""Where a catalog file lives on disk: `<library>/<product>/<entitlement>/<file>`."""

import ntpath
import os
import re
import unicodedata

# Windows refuses these names, with or without an extension
RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    f"{p}{n}" for p in ("COM", "LPT") for n in range(1, 10)
}
BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MAX_PATH = 260  # classic Windows limit; Explorer and many tools still enforce it


class PathError(Exception):
    pass


def clean_name(dirty):
    """Folder name from a title, as hbd made them (existing folders depend on it).

    hbd could leave a trailing space after removing trailing dots, which Windows
    drops silently; strip until stable so the computed name equals the real one.
    """
    allowed = (" ", "_", ".", "-", "[", "]")
    name = "".join(
        c
        for c in dirty.replace("+", "_").replace(":", " -")
        if c.isalpha() or c.isdigit() or c in allowed
    )
    prev = None
    while name != prev:
        prev = name
        name = name.strip().rstrip(".")
    return name


def check_component(name):
    if not name or name in (".", ".."):
        raise PathError(f"empty or relative name {name!r}")
    if BAD_CHARS.search(name):
        raise PathError(f"illegal character in {name!r}")
    if name[-1] in " .":
        raise PathError(f"trailing dot or space in {name!r}")
    # ntpath on every OS: the library must stay usable on Windows (posixpath has no isreserved)
    if name.split(".")[0].strip().upper() in RESERVED or ntpath.isreserved(name):
        raise PathError(f"reserved name {name!r}")


def target_path(library, entry):
    parts = [
        clean_name(entry.product_title),
        clean_name(entry.entitlement_title),
        entry.file_name,
    ]
    for p in parts:
        check_component(p)
    root = os.path.abspath(library)
    path = os.path.abspath(os.path.join(root, *parts))
    if not path.startswith(os.path.join(root, "")):  # works for share roots too
        raise PathError(f"{path!r} is outside the library")
    if len(path) + len(".part") >= MAX_PATH:
        raise PathError(f"path too long ({len(path)} characters): {path!r}")
    return path


def collision_key(path):
    """Same file on this filesystem: NTFS treats names case-insensitively; also
    compare Unicode-normalised."""
    return os.path.normcase(unicodedata.normalize("NFC", path))


def _portable_key(path):
    """Same name on NTFS, on every OS: the library must stay usable on Windows."""
    return ntpath.normcase(unicodedata.normalize("NFC", path))


def assign_targets(library, entries):
    """Map entries to paths.

    Returns (targets, conflicts, errors):
    - targets: {entry.key: path} for every usable entry; entries that resolve to
      the same path with the same md5 share it
    - conflicts: {path: [entries]} where one path would get different contents;
      none of those entries gets a target
    - errors: [(entry, message)] for names that cannot be used
    """
    by_path = {}
    errors = []
    for e in entries:
        try:
            path = target_path(library, e)
        except PathError as err:
            errors.append((e, str(err)))
            continue
        by_path.setdefault(_portable_key(path), (path, []))[1].append(e)
    targets, conflicts = {}, {}
    for path, group in by_path.values():
        if len({e.md5 for e in group}) > 1:
            conflicts[path] = group
            continue
        for e in group:
            targets[e.key] = path
    return targets, conflicts, errors
