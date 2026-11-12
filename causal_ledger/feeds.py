"""Bounded, unverified RSS/Atom reading queue. No claim or credibility inference."""
from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import re
import sqlite3
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from . import extract
from .db import atomic, now_iso
from .dedup import annotate_duplicates

MAX_FEED_BYTES = 2 * 1024 * 1024
MAX_ITEMS = 100
MAX_FEEDS = 20
MAX_CANDIDATES = 5000
ATOM = "{http://www.w3.org/2005/Atom}"
XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"
_UNSAFE_URL = re.compile(r"[\x00-\x20\x7f-\x9f\\]")
_ANSI = re.compile(
    r"(?:\x1b\]|\x9d).*?(?:\x07|\x1b\\|\x9c|$)"
    r"|(?:\x1b[P^_]|[\x90\x9e\x9f]).*?(?:\x1b\\|\x9c|$)"
    r"|(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]|\x1b[ -/]*[@-Z\\-_]",
    re.S,
)


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = []

    def handle_starttag(self, tag, attrs):
        tag = tag.rsplit(":", 1)[-1]
        if tag in {"script", "style", "noscript", "template"}:
            self.hidden.append(tag)
        if not self.hidden:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        tag = tag.rsplit(":", 1)[-1]
        if self.hidden and tag == self.hidden[-1]:
            self.hidden.pop()
        if not self.hidden:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _clean(text: str, limit: int) -> str:
    # Preserve numeric terminal controls until whole escape sequences are removed;
    # html.unescape alone drops ESC and would leave the remainder of a CSI/OSC.
    def numeric_control(match):
        token = match.group(1)
        number = int(token[1:], 16) if token.lower().startswith("x") else int(token)
        return chr(number) if number < 32 or 127 <= number <= 159 else match.group(0)

    text = re.sub(r"&#(x[0-9a-fA-F]{1,8}|[0-9]{1,8});", numeric_control, text, flags=re.I)
    text = _ANSI.sub("", html.unescape(text))
    parser = _PlainText()
    parser.feed(text)
    parser.close()
    text = _ANSI.sub("", "".join(parser.parts))
    text = "".join(" " if char.isspace() else char for char in text
                   if char.isspace() or unicodedata.category(char) not in {"Cc", "Cf", "Cs"})
    return " ".join(text.split())[:limit]


def _safe_url(url: str) -> str:
    """Syntax/literal checks only. DNS is deferred to the fetch transport."""
    if not isinstance(url, str):
        raise ValueError("URL must be HTTP(S) text")
    url = url.strip()
    if _UNSAFE_URL.search(url) or any(c.isspace() or unicodedata.category(c) in {"Cf", "Cs"} for c in url):
        raise ValueError("Invalid HTTP(S) URL")
    parts, host, _ = extract._split_url(url)
    host = host.lower().rstrip(".")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        extract._public_ip(host)
        if literal.version == 6 and literal.ipv4_mapped is not None:
            extract._public_ip(str(literal.ipv4_mapped))
        authority = f"[{host}]" if literal.version == 6 else host
    else:
        labels = host.split(".")
        # Reject ambiguous legacy numeric IP spellings rather than DNS-resolving
        # them; single-label and conventional local names are not public sources.
        if (len(labels) < 2 or len(host) > 253
                or host == "localhost" or host.endswith((".localhost", ".local", ".internal"))
                or all(re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", label) for label in labels)
                or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                       for label in labels)):
            raise ValueError("URL requires a public host or public IP literal")
        authority = host
    # Default ports and fragments do not identify different articles.
    return urllib.parse.urlunsplit((parts.scheme.lower(), authority, parts.path, parts.query, ""))


def _link(value: str | None, base: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    value = value.strip()
    if _UNSAFE_URL.search(value):
        return None
    try:
        return _safe_url(urllib.parse.urljoin(base or "", value))
    except ValueError:
        return None


def _base(element: ET.Element, parent: str | None) -> str | None:
    value = element.get(XML_BASE)
    return _link(value, parent) if value else parent


def _content(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return (element.text or "") + "".join(ET.tostring(child, encoding="unicode") for child in element)


def _date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def parse_feed(raw: bytes, base_url: str) -> list[dict]:
    """Read at most 100 titled RSS2/Atom items from at most 2 MiB of XML."""
    if not isinstance(raw, bytes):
        raise ValueError("Feed input must be bytes")
    if len(raw) > MAX_FEED_BYTES:
        raise ValueError("Feed exceeds 2 MiB byte limit")
    # Removing NULs exposes ASCII declarations in UTF-16/32, either endian,
    # before Expat can expand even an internal entity. Also reject external DTDs.
    if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)\b", raw.replace(b"\x00", b""), re.I):
        raise ValueError("Feed DTD and entity declarations are forbidden")
    base_url = _safe_url(base_url)
    try:
        root = ET.fromstring(raw)
    except (ET.ParseError, ValueError, LookupError):
        raise ValueError("Invalid feed XML") from None
    root_base = _base(root, base_url)
    if root.tag == "rss":
        channel = root.find("channel")
        if channel is None:
            raise ValueError("RSS feed requires a channel")
        parent_base = _base(channel, root_base)
        entries = channel.iterfind("item")
        prefix = ""
    elif root.tag == ATOM + "feed":
        parent_base = root_base
        entries = root.iterfind(ATOM + "entry")
        prefix = ATOM
    else:
        raise ValueError("Unsupported feed: expected RSS2 or Atom")
    result = []
    for entry in entries:
        title = _clean(_content(entry.find(prefix + "title")), 500)
        if not title:
            continue
        entry_base = _base(entry, parent_base)
        summary_node = entry.find(prefix + ("summary" if prefix else "description"))
        if prefix and summary_node is None:
            summary_node = entry.find(prefix + "content")
        summary = _clean(_content(summary_node), 4000)
        url = None
        for node in entry.iterfind(prefix + "link"):
            if prefix and node.get("rel", "alternate") not in {"", "alternate"}:
                continue
            value = node.get("href") if prefix else node.text
            url = _link(value, _base(node, entry_base))
            if url:
                break
        published_node = entry.find(prefix + ("published" if prefix else "pubDate"))
        if prefix and (published_node is None or not _content(published_node).strip()):
            published_node = entry.find(prefix + "updated")
        published_raw = _clean(_content(published_node), 500) or None
        published_at = _date(published_raw)
        identifier = _clean(_content(entry.find(prefix + ("id" if prefix else "guid"))), MAX_FEED_BYTES)
        if identifier:
            item_key = "id:" + hashlib.sha256(identifier.encode("utf-8")).hexdigest()
        elif url:
            item_key = "url:" + url
        else:
            content = json.dumps([title, summary, published_raw], ensure_ascii=False, separators=(",", ":"))
            item_key = "hash:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
        result.append({"item_key": item_key, "title": title, "url": url, "summary": summary,
                       "published_at": published_at, "published_raw": published_raw})
        if len(result) == MAX_ITEMS:
            break
    return result


def _subscription(conn: sqlite3.Connection, feed_id: int):
    row = conn.execute("SELECT * FROM subscriptions WHERE id=?", (feed_id,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown subscription ID: {feed_id}")
    return row


def add_subscription(conn: sqlite3.Connection, name: str, url: str) -> int:
    url = _safe_url(url)
    if not isinstance(name, str) or not (name := _clean(name, 500)):
        raise ValueError("Subscription name must not be empty")
    with atomic(conn):
        if conn.execute("SELECT id FROM subscriptions WHERE url=?", (url,)).fetchone() is not None:
            raise ValueError("Subscription URL already exists")
        cursor = conn.execute("INSERT INTO subscriptions(name,url,created_at) VALUES(?,?,?)",
                              (name, url, now_iso()))
    return cursor.lastrowid


def set_subscription_enabled(conn: sqlite3.Connection, id: int, enabled: bool) -> None:
    if not isinstance(enabled, (bool, int)) or enabled not in (0, 1):
        raise ValueError("enabled must be 0 or 1")
    with atomic(conn):
        _subscription(conn, id)
        conn.execute("UPDATE subscriptions SET enabled=? WHERE id=?", (int(enabled), id))


def store_feed(conn: sqlite3.Connection, feed_id: int, raw: bytes, base_url: str | None = None) -> dict:
    subscription = _subscription(conn, feed_id)
    items = parse_feed(raw, subscription["url"] if base_url is None else base_url)
    fetched_at = now_iso()
    added = 0
    with atomic(conn):
        for entry in items:
            cursor = conn.execute(
                "INSERT INTO feed_items(feed_id,item_key,title,url,summary,published_at,published_raw,fetched_at) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(feed_id,item_key) DO NOTHING",
                (feed_id, entry["item_key"], entry["title"], entry["url"], entry["summary"],
                 entry["published_at"], entry["published_raw"], fetched_at),
            )
            added += cursor.rowcount
        conn.execute("UPDATE subscriptions SET checked_at=?,last_error=NULL WHERE id=?", (fetched_at, feed_id))
    return {"feed_id": feed_id, "added": added, "duplicates": len(items) - added}


def collect_feeds(conn: sqlite3.Connection, feed_ids=None) -> list[dict]:
    if feed_ids is None:
        subscriptions = conn.execute("SELECT * FROM subscriptions WHERE enabled=1 ORDER BY id LIMIT ?",
                                     (MAX_FEEDS + 1,)).fetchall()
    else:
        subscriptions = []
        seen = set()
        for feed_id in feed_ids:
            if feed_id in seen:
                continue
            seen.add(feed_id)
            row = _subscription(conn, feed_id)
            if row["enabled"]:
                subscriptions.append(row)
        subscriptions.sort(key=lambda row: row["id"])
    if len(subscriptions) > MAX_FEEDS:
        raise ValueError("At most 20 enabled feeds per run; select explicit subscription IDs")
    results = []
    for subscription in subscriptions:
        feed_id = subscription["id"]
        try:
            raw, final_url = extract.fetch_feed(subscription["url"])
            result = store_feed(conn, feed_id, raw, final_url)
        except Exception as error:
            # Do not swallow interrupts. Never persist raw XML/HTML or terminal
            # sequences from a failed source's exception message.
            message = _clean(str(error)[:8000], 500) or "Feed collection failed"
            with atomic(conn):
                conn.execute("UPDATE subscriptions SET checked_at=?,last_error=? WHERE id=?",
                             (now_iso(), message, feed_id))
            result = {"feed_id": feed_id, "added": 0, "duplicates": 0, "error": message}
        results.append(result)
    return results


def mark_read(conn: sqlite3.Connection, item_id: int) -> None:
    with atomic(conn):
        cursor = conn.execute("UPDATE feed_items SET read_at=COALESCE(read_at,?) WHERE id=?",
                              (now_iso(), item_id))
        if cursor.rowcount == 0:
            raise ValueError(f"Unknown feed item ID: {item_id}")


def recommend(conn: sqlite3.Connection, days: int = 7, limit: int = 10, topics=None,
              per_source: int = 2, now: datetime | None = None) -> list[dict]:
    """Return unverified unread items, considering only the newest 5000 eligible
    candidates before topic matching, URL deduplication and source caps. Empty
    output therefore does not prove no older matching candidates exist.
    """
    for name, value, maximum in (("days", days, 365), ("limit", limit, 100), ("per_source", per_source, 100)):
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f"{name} must be an integer from 1 to {maximum}")
    if now is None:
        now = datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    now = now.astimezone(timezone.utc)
    try:
        cutoff = now - timedelta(days=days)
    except OverflowError:
        raise ValueError("now is outside the supported date range") from None
    if topics is None:
        topics = []
    elif isinstance(topics, str):
        topics = [topics]
    cleaned_topics = []
    for topic in topics:
        if not isinstance(topic, str):
            raise ValueError("topics must contain text")
        topic = _clean(topic, 500)
        if topic and topic not in cleaned_topics:
            cleaned_topics.append(topic)
    # SQL performs time eligibility and ordering before LIMIT, not fetch-time
    # ordering that could push recent dated publications behind undated entries.
    rows = conn.execute(
        "SELECT i.*,s.name AS feed_name FROM feed_items i "
        "JOIN subscriptions s ON s.id=i.feed_id "
        "WHERE s.enabled=1 AND i.read_at IS NULL "
        "AND julianday(COALESCE(i.published_at,i.fetched_at)) BETWEEN julianday(?) AND julianday(?) "
        "ORDER BY (i.published_at IS NULL), julianday(COALESCE(i.published_at,i.fetched_at)) DESC, i.id ASC "
        "LIMIT ?",
        (cutoff.isoformat(), now.isoformat(), MAX_CANDIDATES),
    ).fetchall()
    results = []
    counts = {}
    seen_urls = set()
    for row in rows:
        searchable = (row["title"] + " " + row["summary"]).casefold()
        matched = [topic for topic in cleaned_topics if topic.casefold() in searchable]
        if cleaned_topics and not matched:
            continue
        source = row["feed_id"]
        if counts.get(source, 0) >= per_source or (row["url"] and row["url"] in seen_urls):
            continue
        reason = (f"近 {days} 天发布" if row["published_at"] is not None
                  else f"发布时间未知；按近 {days} 天采集时间筛选")
        reason += "；主题匹配：" + (", ".join(matched) if matched else "未设置主题筛选")
        reason += (f"；时间降序、同时间按 ID；每来源最多 {per_source} 条"
                   f"；仅检查最新 {MAX_CANDIDATES} 条合格候选；相同 URL 去重不代表独立证据；未核验阅读队列")
        entry = dict(row)
        entry["reason"] = reason
        results.append(entry)
        counts[source] = counts.get(source, 0) + 1
        if row["url"]:
            seen_urls.add(row["url"])
        if len(results) == limit:
            break
    return annotate_duplicates(results)
