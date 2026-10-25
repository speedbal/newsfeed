"""Parse one RSS or Atom feed into item dicts."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import feedparser

from feedbuild.config import Rules
from feedbuild.htmlclean import blurb, clean_html, linkish, to_text
from feedbuild.urls import canonical_key, clean_url, host_matches, host_of, is_affiliate, is_tracker_url

TITLE_DROP = re.compile(
    r"(?i)(\[\s*sponsored\s*\]|\(\s*sponsored\s*\)|^\s*sponsored\b|"
    r"\bpartner content\b|\bpress release\b|\badvertorial\b|\bpaid program\b|\bpaid post\b)"
)
POINTS = re.compile(r"Points:\s*(\d+)", re.I)
COMMENTS_RE = re.compile(r"#?\s*Comments:\s*(\d+)", re.I)
COMMENT_HOSTS = ("news.ycombinator.com", "lobste.rs")


def full_text_mode(value: object) -> str:
    if value is True or value == "true":
        return "full"
    if value == "partial":
        return "partial"
    return "none"


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return (slug or "feed")[:48]


def _entry_time(entry: feedparser.FeedParserDict) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        struct = entry.get(key)
        if not struct:
            continue
        try:
            return datetime(*struct[:6], tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
    return None


def _entry_link(entry: feedparser.FeedParserDict) -> str:
    link = entry.get("link") or ""
    if link:
        return str(link)
    for item in entry.get("links") or []:
        if item.get("rel") in {None, "alternate"} and item.get("href"):
            return str(item["href"])
    return ""


def _comments_link(entry: feedparser.FeedParserDict, rewrites: dict[str, str]) -> str:
    raw = str(entry.get("comments") or "")
    if not raw:
        for item in entry.get("links") or []:
            if item.get("rel") == "replies" and item.get("href"):
                raw = str(item["href"])
                break
    cleaned = clean_url(raw, rewrites)
    if cleaned and host_matches(host_of(cleaned), COMMENT_HOSTS):
        return cleaned
    return ""


def _tags(entry: feedparser.FeedParserDict) -> list[str]:
    found: list[str] = []
    for tag in entry.get("tags") or []:
        if isinstance(tag, dict):
            term = tag.get("term") or tag.get("label") or ""
        else:
            term = str(tag)
        if term:
            found.append(str(term))
    return found


def is_sponsored(title: str, tags: list[str], url: str, rules: Rules) -> bool:
    for tag in tags:
        normalized = re.sub(r"\s+", " ", tag).strip().casefold()
        if not normalized:
            continue
        if normalized in rules.drop_tags or normalized.startswith("sponsored"):
            return True
    if title and TITLE_DROP.search(title):
        return True
    try:
        segments = {part.casefold() for part in urlsplit(url).path.split("/") if part}
        host = host_of(url).removeprefix("www.")
    except ValueError:
        return False
    if segments & set(rules.drop_path_segments):
        return True
    if host_matches(host, tuple(rules.deals_hosts)) and segments & set(rules.deals_segments):
        return True
    return False


def _clean_title(title: str, source: str) -> str:
    text = to_text(title)
    text = re.sub(r"\s+", " ", text).strip()
    for separator in (" | ", " — ", " – ", " - "):
        suffix = separator + source
        if text.endswith(suffix) and len(text) > len(suffix) + 8:
            text = text[: -len(suffix)].strip()
    return text


def _content_html(entry: feedparser.FeedParserDict) -> str:
    chunks = []
    for block in entry.get("content") or []:
        value = block.get("value") or ""
        if value:
            chunks.append(str(value))
    if chunks:
        return max(chunks, key=len)
    return str(entry.get("summary") or "")


def parse_feed_window(
    feed: dict,
    body: bytes,
    now: datetime,
    rules: Rules,
    *,
    cutoff: datetime,
    per_feed_cap: int,
) -> list[dict]:
    parsed = feedparser.parse(body)
    entries = list(parsed.entries or [])
    if not entries:
        if getattr(parsed, "bozo", False):
            raise ValueError(f"unreadable feed: {parsed.bozo_exception}")
        return []
    items = _parse_entries(feed, entries, now, rules, cutoff=cutoff)
    items.sort(key=lambda item: item["time"], reverse=True)
    return items[:per_feed_cap]


def _parse_entries(feed, entries, now, rules: Rules, cutoff: datetime | None) -> list[dict]:
    items: list[dict] = []
    mode = full_text_mode(feed.get("full_text"))
    source = feed["name"]
    rewrites = rules.host_rewrites
    for entry in entries:
        title = _clean_title(str(entry.get("title") or ""), source)
        link = _entry_link(entry)
        url = clean_url(link, rewrites)
        if not title or not url or is_affiliate(url) or is_tracker_url(url):
            continue
        if is_sponsored(title, _tags(entry), url, rules):
            continue
        published = _entry_time(entry)
        if published is None:
            continue
        if published > now + timedelta(days=2):
            continue
        if published > now + timedelta(hours=6):
            published = now
        elif published > now:
            published = now
        if cutoff is not None and published < cutoff:
            continue

        summary_html = str(entry.get("summary") or "")
        content_html = _content_html(entry)
        summary_text = to_text(summary_html)
        content_text = to_text(content_html)
        if summary_text.casefold() == title.casefold() or linkish(summary_text):
            summary_text = ""

        extra_bits = []
        points = POINTS.search(to_text(summary_html) or content_text)
        comments_n = COMMENTS_RE.search(to_text(summary_html) or content_text)
        if points:
            extra_bits.append(f"{points.group(1)} pts")
        if comments_n:
            extra_bits.append(f"{comments_n.group(1)} comments")

        key = canonical_key(url, rewrites)
        host = host_of(key)
        blocked = host_matches(host, tuple(rules.never_extract_hosts))
        html_out = ""
        fallback = ""
        needs_extract = False
        if mode == "full" and len(content_text) >= 40:
            html_out = clean_html(content_html, key, rewrites)
            if not summary_text:
                summary_text = blurb(content_text)
        elif feed.get("extract") and not blocked:
            needs_extract = True
            if len(content_text) >= 80:
                fallback = clean_html(content_html, key, rewrites)
            if not summary_text and len(content_text) >= 40:
                summary_text = blurb(content_text)
        elif len(content_text) >= 40:
            html_out = clean_html(content_html, key, rewrites)
            if not summary_text:
                summary_text = blurb(content_text)

        digest = hashlib.sha1(key.encode()).hexdigest()[:12]
        published_iso = published.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
        items.append(
            {
                "id": f"rss:{_slug(source)}:{digest}",
                "kind": "rss",
                "title": title,
                "source": source,
                "topics": [feed["category"]],
                "url": key,
                "time": published_iso,
                "summary": blurb(summary_text) if summary_text else "",
                "html": html_out,
                "fallback_html": fallback,
                "images": [],
                "comments_url": _comments_link(entry, rewrites),
                "extra": " · ".join(extra_bits),
                "also": [],
                "handle": "",
                "needs_extract": needs_extract,
            }
        )
    return items
