"""Read the data Next.js embeds in a page (the "flight" payload).

Pages carry their data in inline scripts `self.__next_f.push([1,"<chunk>"])`. The
chunks are JSON string literals; decoded and joined they form the flight text, in
which component props appear as plain JSON objects.
"""

import json
import re

PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')


class LayoutError(Exception):
    """The page does not look the way the parser expects (site changed)."""


def flight_text(html):
    """Decode and join all flight chunks of a page; LayoutError if there are none."""
    chunks = [json.loads(m.group(1)) for m in PUSH_RE.finditer(html)]
    if not chunks:
        raise LayoutError("page has no Next.js flight data")
    return "".join(chunks)


def find_value(text, key):
    """Return the first JSON object or array stored under `"key":` in the text.

    None if the key does not occur. LayoutError if it holds something else or the
    value is cut off.
    """
    marker = f'"{key}":'
    i = text.find(marker)
    if i < 0:
        return None
    start = i + len(marker)
    while start < len(text) and text[start] in " \t\r\n":
        start += 1
    if start >= len(text) or text[start] not in "{[":
        raise LayoutError(f'"{key}" does not hold an object or array')
    return _extract(text, start, key)


def _extract(text, start, key):
    open_c = text[start]
    close_c = "}" if open_c == "{" else "]"
    depth = 0
    in_str = False
    i = start
    while i < len(text):
        c = text[i]
        if in_str:
            if c == "\\":
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except ValueError as e:
                    raise LayoutError(f'"{key}" is not valid JSON') from e
        i += 1
    raise LayoutError(f'"{key}" is cut off')
