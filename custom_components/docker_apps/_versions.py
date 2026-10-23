"""Image tag versions: find a newer tag with the same shape as the current one.

A tag is split into numbers and the text between them. `v2.1.0` has the shape
`vN.N.N` and the numbers (2, 1, 0). A candidate tag is newer when it has the same
shape and bigger numbers, so `v2.1.0` moves to `v2.2.0` but never to `v2.2.0-rc1`
(different shape) or `latest` (no numbers).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_TOKEN = re.compile(r"\d+|\D+")
FLOATING_TAGS = {"latest", "stable", "release", "main", "master", "edge", "nightly"}


def parse_tag(tag: str) -> tuple[str, tuple[int, ...]] | None:
    """Return (shape, numbers) or None when the tag has no version numbers."""
    shape = []
    numbers = []
    for token in _TOKEN.findall(tag):
        if token.isdigit():
            shape.append("N")
            numbers.append(int(token))
        else:
            shape.append(token)
    if not numbers:
        return None
    return "".join(shape), tuple(numbers)


def is_floating(tag: str | None) -> bool:
    """True for tags like `latest` that move without changing their name."""
    if not tag:
        return True
    if tag.lower() in FLOATING_TAGS:
        return True
    parsed = parse_tag(tag)
    # a single number such as `16` (postgres) also moves with minor releases
    return parsed is None or len(parsed[1]) == 1


def newest_tag(
    current: str,
    candidates: Iterable[str],
    include: str | None = None,
    exclude: str | None = None,
) -> str | None:
    """Return the newest candidate with the same shape as current, or None."""
    parsed = parse_tag(current)
    if parsed is None:
        return None
    shape, numbers = parsed
    include_re = re.compile(include) if include else None
    exclude_re = re.compile(exclude) if exclude else None

    best: tuple[tuple[int, ...], str] | None = None
    for tag in candidates:
        if include_re and not include_re.search(tag):
            continue
        if exclude_re and exclude_re.search(tag):
            continue
        candidate = parse_tag(tag)
        if candidate is None or candidate[0] != shape:
            continue
        if candidate[1] > numbers and (best is None or candidate[1] > best[0]):
            best = (candidate[1], tag)

    return best[1] if best else None


def short_digest(digest: str | None) -> str | None:
    if not digest:
        return None
    _, _, value = digest.partition(":")
    return (value or digest)[:12]
