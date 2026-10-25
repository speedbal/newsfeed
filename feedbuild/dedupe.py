"""Collapse the same story when several feeds point at it."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from difflib import SequenceMatcher

from feedbuild.urls import canonical_key
from feedbuild.util import parse_iso

TOPIC_ORDER = ("tech", "ai", "crypto")


def norm_title(title: str) -> str:
    text = title.casefold()
    text = re.sub(r"\s+[-|–—:]\s+.{0,40}$", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _titles_match(left: str, right: str) -> bool:
    if len(left) < 40 or len(right) < 40:
        return False
    longest = max(len(left), len(right))
    if abs(len(left) - len(right)) > longest * 0.35:
        return False
    return SequenceMatcher(None, left, right).ratio() >= 0.92


def _is_aggregator(source: str, aggregators: frozenset[str]) -> bool:
    return source.casefold() in aggregators


def _collapse(members: list[dict], aggregators: frozenset[str], rewrites: dict[str, str]) -> dict:
    def score(item: dict) -> tuple:
        aggregated = 0 if _is_aggregator(item["source"], aggregators) else 1
        return (aggregated, len(item.get("html") or ""), len(item.get("summary") or ""))

    primary = max(members, key=score)
    earliest = min(members, key=lambda item: (item["time"], canonical_key(item["url"], rewrites)))
    story = hashlib.sha1(canonical_key(earliest["url"], rewrites).encode()).hexdigest()[:16]
    topics = [
        topic
        for topic in TOPIC_ORDER
        if any(topic in (member.get("topics") or []) for member in members)
    ]
    also: list[str] = []
    seen: set[str] = set()
    for member in sorted(members, key=lambda item: item["time"]):
        if member["source"] == primary["source"] or member["source"] in seen:
            continue
        seen.add(member["source"])
        also.append(member["source"])
    title_pool = [member for member in members if not _is_aggregator(member["source"], aggregators)] or members
    title = max(title_pool, key=lambda member: len(member["title"]))["title"]
    summary = max((member.get("summary") or "" for member in members), key=len)
    html = max(members, key=lambda member: len(member.get("html") or "")) .get("html") or ""
    extra = next((member.get("extra") or "" for member in members if member.get("extra")), "")
    comments = next((member.get("comments_url") or "" for member in members if member.get("comments_url")), "")
    return {
        "id": f"s:{story}",
        "kind": "rss",
        "title": title,
        "source": primary["source"],
        "topics": topics or list(primary.get("topics") or ["tech"]),
        "url": primary["url"],
        "time": earliest["time"],
        "summary": summary,
        "html": html,
        "images": [],
        "comments_url": comments,
        "extra": extra,
        "also": also,
        "handle": "",
    }


def cluster_rss(items: list[dict], aggregators: frozenset[str], rewrites: dict[str, str]) -> list[dict]:
    ordered = sorted(items, key=lambda item: item["time"])
    count = len(ordered)
    parent = list(range(count))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    by_url: dict[str, int] = {}
    keys = [canonical_key(item["url"], rewrites) for item in ordered]
    for index, key in enumerate(keys):
        if not key:
            continue
        if key in by_url:
            union(index, by_url[key])
        else:
            by_url[key] = index

    norms = [norm_title(item["title"]) for item in ordered]
    times = []
    for item in ordered:
        try:
            times.append(parse_iso(item["time"]))
        except ValueError:
            times.append(parse_iso("1970-01-01T00:00:00Z"))

    for left in range(count):
        if len(norms[left]) < 40:
            continue
        for right in range(left):
            if find(left) == find(right) or len(norms[right]) < 40:
                continue
            if abs((times[left] - times[right]).total_seconds()) > 36 * 3600:
                continue
            if _titles_match(norms[left], norms[right]):
                union(left, right)

    groups: dict[int, list[dict]] = defaultdict(list)
    for index, item in enumerate(ordered):
        groups[find(index)].append(item)
    return [_collapse(members, aggregators, rewrites) for members in groups.values()]
