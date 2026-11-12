"""Heuristic duplicate hints across sources: hints only, never merge or score.

The same story reposted by several feeds usually has different URLs and slightly
different titles. This module normalizes URLs and title fingerprints and appends
a "疑似重复（仅提示，未验证）" note to recommendation reasons. It never changes
identity, never removes items, and never touches credibility or read state.
"""
from __future__ import annotations

import difflib
import unicodedata
import urllib.parse

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "fbclid", "gclid", "yclid", "msclkid", "mc_cid", "mc_eid", "spm", "ref", "source",
}
TITLE_SIMILARITY = 0.9


def normalize_url(url) -> str | None:
    """Canonicalize an article URL for comparison; None when unusable."""
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        parts = urllib.parse.urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    netloc = host if not port or port in (80, 443) else f"{host}:{port}"
    pairs = [(key, value) for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
             if key.lower() not in TRACKING_PARAMS]
    query = urllib.parse.urlencode(sorted(pairs))
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))


def display_url(url) -> str | None:
    """Drop tracking query parameters for display, keeping scheme/host/path intact."""
    if not isinstance(url, str) or not url.strip():
        return url
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return url
    if parts.scheme not in ("http", "https"):
        return url
    pairs = [(key, value) for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
             if key.lower() not in TRACKING_PARAMS]
    query = urllib.parse.urlencode(pairs) if pairs else ""
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def title_fingerprint(title) -> str:
    if not isinstance(title, str):
        return ""
    text = unicodedata.normalize("NFKC", title).casefold()
    return "".join(ch for ch in text
                   if not ch.isspace() and unicodedata.category(ch)[0] not in {"P", "S", "C"})


def titles_similar(left, right) -> bool:
    first, second = title_fingerprint(left), title_fingerprint(right)
    if not first or not second:
        return False
    if first == second:
        return True
    shorter, longer = (first, second) if len(first) <= len(second) else (second, first)
    if len(shorter) >= 10 and shorter in longer:
        return True
    return difflib.SequenceMatcher(None, first, second).ratio() >= TITLE_SIMILARITY


def annotate_duplicates(items: list[dict]) -> list[dict]:
    """Append hint text to reasons of later items that look like earlier ones."""
    seen: list[tuple[int, str | None, str]] = []
    for item in items:
        url = normalize_url(item.get("url"))
        title = item.get("title") or ""
        hints = [other_id for other_id, other_url, other_title in seen
                 if (url is not None and url == other_url) or titles_similar(title, other_title)]
        if hints:
            joined = "、".join(f"I#{item_id}" for item_id in hints)
            item["reason"] = (item.get("reason", "")
                              + f"；疑似重复：与 {joined} 的链接/标题相似（仅提示，未验证）")
        seen.append((item["id"], url, title))
    return items
