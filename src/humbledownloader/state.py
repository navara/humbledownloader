"""What the tool knows it has: `<library>/.humble-state.json`.

{"version": 1, "files": {"<entry.key>": {"path": "<relative>", "size": int,
 "md5": str, "verified": bool, "source": "download" | "adopted",
 "disk_size": int, "disk_md5": str}}}

size and md5 are the catalog's; disk_size/disk_md5 are present only when Humble
served another file than its catalog lists.
"""

import enum
import json
import os
import time

STATE_NAME = ".humble-state.json"
VERSION = 1


class StateError(Exception):
    pass


def load(path):
    if not os.path.exists(path):
        return {"version": VERSION, "files": {}}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        hint = (
            f"{path}.tmp holds the last write that did not complete; check it"
            if os.path.exists(path + ".tmp")
            else "move it away to start over (existing files are adopted again)"
        )
        raise StateError(
            f"{path} cannot be read ({e.__class__.__name__}); it was left untouched. "
            + hint
        ) from e
    if (
        not isinstance(data, dict)
        or data.get("version") != VERSION
        or not isinstance(data.get("files"), dict)
    ):
        raise StateError(
            f"{path} is not a version {VERSION} state file; left untouched"
        )
    return data


def save(path, data):
    """Write atomically: temp file, fsync, replace. Windows may briefly lock the
    target (antivirus, indexer, backup), so retry; if it stays locked, fail loudly
    rather than write in place (a crash mid-write would truncate the state)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    for delay in (0.1, 0.5, 2.0, 5.0):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(delay)
    raise StateError(
        f"{path} stays locked by another program; the new state is in {tmp}"
    )


class Action(enum.Enum):
    SKIP = "skip"  # have it
    ADOPT = "adopt"  # untracked file with the catalog size: record, do not download
    DOWNLOAD = "download"  # nothing at the target
    UPDATE = "update"  # something else at the target: rename it aside, download
    MOVE = "move"  # tracked file whose folder name changed: move it to the target


def decide(entry, record, disk_size, recorded_size=None):
    """What to do with one catalog file.

    record: its state entry or None; disk_size: size of the file at the target
    path, or None when there is none; recorded_size: size of the file at the
    record's path when that path differs from the target, else None.
    """
    if disk_size is None:
        if (
            record
            and recorded_size is not None
            and recorded_size == _have(record, entry)
        ):
            return Action.MOVE
        return Action.DOWNLOAD
    if record is None:
        return Action.ADOPT if disk_size == entry.size else Action.UPDATE
    if disk_size == _have(record, entry):
        return Action.SKIP
    return Action.UPDATE


def _have(record, entry):
    """Size the recorded file has if it is this catalog file, else None."""
    if record.get("md5") == entry.md5:
        return record.get("disk_size", entry.size)
    if record.get("disk_md5") == entry.md5 and record.get("disk_size") == entry.size:
        return entry.size  # the catalog has caught up with the newer file
    return None


def record_for(library, path, entry, source, verified, size=None, md5=None):
    """size/md5: what was really downloaded, when known."""
    record = {
        "path": os.path.relpath(path, library),
        "size": entry.size,
        "md5": entry.md5,
        "verified": verified,
        "source": source,
    }
    if size is not None and (size, md5) != (entry.size, entry.md5):
        record.update(disk_size=size, disk_md5=md5)
    return record
