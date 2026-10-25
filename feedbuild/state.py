"""Load, merge, and store the rolling window between runs."""

from __future__ import annotations

import json
from pathlib import Path

from feedbuild.config import ROOT

STATE_PATH = ROOT / ".state" / "state.json"


def empty_state() -> dict:
    return {
        "version": 1,
        "rss_items": [],
        "x_posts": [],
        "articles": {},
        "feeds": {},
        "x_since_id": None,
        "x_since_id_supported": None,
        "x_note_tweet": None,
    }


def load_state(path: Path | None = None) -> dict:
    path = path or STATE_PATH
    if not path.exists():
        return empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_state()
    if not isinstance(data, dict) or data.get("version") != 1:
        return empty_state()
    base = empty_state()
    base.update({key: data.get(key, base[key]) for key in base})
    if not isinstance(base["rss_items"], list):
        base["rss_items"] = []
    if not isinstance(base["x_posts"], list):
        base["x_posts"] = []
    if not isinstance(base["articles"], dict):
        base["articles"] = {}
    if not isinstance(base["feeds"], dict):
        base["feeds"] = {}
    return base


def save_state(state: dict, path: Path | None = None) -> None:
    path = path or STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def merge_rss(old: list[dict], new: list[dict], sources: set[str], cutoff_iso: str) -> list[dict]:
    by_id: dict[str, dict] = {}
    for item in old:
        if item.get("source") in sources and str(item.get("time") or "") >= cutoff_iso:
            by_id[item["id"]] = item
    for item in new:
        previous = by_id.get(item["id"])
        if (
            previous
            and item.get("needs_extract")
            and previous.get("html")
            and not previous.get("needs_extract")
        ):
            item["html"] = previous["html"]
            item["needs_extract"] = False
            if len(item.get("summary") or "") < 40 and previous.get("summary"):
                item["summary"] = previous["summary"]
        by_id[item["id"]] = item
    return [item for item in by_id.values() if str(item.get("time") or "") >= cutoff_iso]


def merge_x(old: list[dict], new: list[dict], cutoff_iso: str) -> list[dict]:
    by_id = {
        item["id"]: item
        for item in old
        if str(item.get("time") or "") >= cutoff_iso and item.get("id")
    }
    for item in new:
        by_id[item["id"]] = item
    return [item for item in by_id.values() if str(item.get("time") or "") >= cutoff_iso]


def slim_item(item: dict) -> dict:
    kept = dict(item)
    if kept.get("html") and not kept.get("needs_extract"):
        kept.pop("fallback_html", None)
    return kept


def prune_articles(articles: dict, items: list[dict], cutoff_iso: str, rewrites: dict[str, str]) -> dict:
    from feedbuild.urls import canonical_key

    referenced = {canonical_key(item.get("url") or "", rewrites) for item in items}
    kept = {}
    for url, entry in articles.items():
        if not isinstance(entry, dict):
            continue
        if url in referenced or str(entry.get("at") or "") >= cutoff_iso:
            kept[url] = entry
    return kept
