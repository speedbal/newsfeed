"""Load and check sources.json."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCES = ROOT / "sources.json"

FEED_CATEGORIES = {"tech", "ai", "crypto"}
ACCOUNT_CATEGORIES = {"tech_ai", "crypto"}
TOPICS = {"tech", "ai", "crypto"}
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
ID_RE = re.compile(r"^\d{1,20}$")

DEFAULT_LIMITS = {
    "max_extract_per_run": 160,
    "x_max_requests": 2,
    "x_max_requests_cold": 5,
    "x_page_size": 100,
    "max_items": 500,
    "per_feed_cap": 60,
}


class ConfigError(ValueError):
    pass


@dataclass
class Rules:
    drop_tags: frozenset[str]
    drop_path_segments: frozenset[str]
    deals_hosts: frozenset[str]
    deals_segments: frozenset[str]
    never_extract_hosts: frozenset[str]
    host_rewrites: dict[str, str]
    aggregators: frozenset[str]


@dataclass
class Config:
    window_days: int
    x_list_id: str
    limits: dict[str, int]
    x_accounts: list[dict]
    rss_feeds: list[dict]
    rules: Rules
    path: Path = field(default=DEFAULT_SOURCES)


def _norm_tag(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _limits(raw: object) -> dict[str, int]:
    merged = dict(DEFAULT_LIMITS)
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("limits must be an object")
    bounds = {
        "max_extract_per_run": (1, 500),
        "x_max_requests": (1, 10),
        "x_max_requests_cold": (1, 10),
        "x_page_size": (10, 100),
        "max_items": (20, 1000),
        "per_feed_cap": (5, 200),
    }
    for key, (low, high) in bounds.items():
        if key in raw:
            try:
                value = int(raw[key])
            except (TypeError, ValueError) as exc:
                raise ConfigError(f"limits.{key} must be an integer") from exc
            if not low <= value <= high:
                raise ConfigError(f"limits.{key} must be between {low} and {high}")
            merged[key] = value
    if merged["x_max_requests_cold"] < merged["x_max_requests"]:
        merged["x_max_requests_cold"] = merged["x_max_requests"]
    return merged


def load_config(path: Path | None = None) -> Config:
    path = path or DEFAULT_SOURCES
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"missing {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("sources.json must be an object")

    try:
        window = int(data.get("window_days", 3))
    except (TypeError, ValueError) as exc:
        raise ConfigError("window_days must be an integer") from exc
    if not 1 <= window <= 14:
        raise ConfigError("window_days must be between 1 and 14")

    list_id = str(data.get("x_list_id") or "").strip()
    if list_id and not ID_RE.fullmatch(list_id):
        raise ConfigError("x_list_id must be a numeric string")

    accounts_raw = data.get("x_accounts")
    feeds_raw = data.get("rss_feeds")
    if not isinstance(accounts_raw, list) or not accounts_raw:
        raise ConfigError("x_accounts must be a non-empty list")
    if not isinstance(feeds_raw, list) or not feeds_raw:
        raise ConfigError("rss_feeds must be a non-empty list")

    accounts: list[dict] = []
    seen_handles: set[str] = set()
    seen_ids: set[str] = set()
    for index, raw in enumerate(accounts_raw):
        if not isinstance(raw, dict):
            raise ConfigError(f"x_accounts[{index}] must be an object")
        handle = str(raw.get("handle") or "").strip()
        user_id = str(raw.get("id") or "").strip()
        name = str(raw.get("name") or "").strip()
        category = str(raw.get("category") or "").strip()
        topics = raw.get("topics") or []
        where = f"x_accounts[{index}] ({handle or index})"
        if not HANDLE_RE.fullmatch(handle):
            raise ConfigError(f"{where}: handle must be 1-15 letters, numbers, or underscores")
        if not ID_RE.fullmatch(user_id):
            raise ConfigError(f"{where}: id must be a numeric string")
        if not name:
            raise ConfigError(f"{where}: name is required")
        if category not in ACCOUNT_CATEGORIES:
            raise ConfigError(f"{where}: category must be tech_ai or crypto")
        if not isinstance(topics, list) or any(topic not in TOPICS for topic in topics):
            raise ConfigError(f"{where}: topics must be a list of tech, ai, crypto")
        key = handle.casefold()
        if key in seen_handles:
            raise ConfigError(f"duplicate X handle {handle}")
        if user_id in seen_ids:
            raise ConfigError(f"duplicate X id {user_id}")
        seen_handles.add(key)
        seen_ids.add(user_id)
        accounts.append(
            {
                "handle": handle,
                "id": user_id,
                "name": name,
                "category": category,
                "topics": list(dict.fromkeys(topics)),
            }
        )

    feeds: list[dict] = []
    seen_names: set[str] = set()
    seen_urls: set[str] = set()
    for index, raw in enumerate(feeds_raw):
        if not isinstance(raw, dict):
            raise ConfigError(f"rss_feeds[{index}] must be an object")
        name = str(raw.get("name") or "").strip()
        url = str(raw.get("url") or "").strip()
        category = str(raw.get("category") or "").strip()
        full_text = raw.get("full_text")
        extract = raw.get("extract")
        where = f"rss_feeds[{index}] ({name or index})"
        if not name:
            raise ConfigError(f"{where}: name is required")
        if not url.startswith(("https://", "http://")):
            raise ConfigError(f"{where}: url must be http(s)")
        if category not in FEED_CATEGORIES:
            raise ConfigError(f"{where}: category must be tech, ai, or crypto")
        if full_text not in {True, False, "partial", "true", "false"}:
            raise ConfigError(f"{where}: full_text must be true, false, or \"partial\"")
        if not isinstance(extract, bool):
            raise ConfigError(f"{where}: extract must be true or false")
        if name.casefold() in seen_names:
            raise ConfigError(f"duplicate feed name {name}")
        if url in seen_urls:
            raise ConfigError(f"duplicate feed url {url}")
        seen_names.add(name.casefold())
        seen_urls.add(url)
        if full_text == "true":
            full_text = True
        elif full_text == "false":
            full_text = False
        feeds.append(
            {
                "name": name,
                "url": url,
                "category": category,
                "full_text": full_text,
                "extract": extract,
                "notes": str(raw.get("notes") or ""),
            }
        )

    drop_tags = data.get("drop_tags") or []
    if not isinstance(drop_tags, list):
        raise ConfigError("drop_tags must be a list")
    rewrites = data.get("host_rewrites") or {}
    if not isinstance(rewrites, dict):
        raise ConfigError("host_rewrites must be an object")

    def _string_set(key: str) -> frozenset[str]:
        value = data.get(key) or []
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ConfigError(f"{key} must be a list of strings")
        return frozenset(item.strip().casefold() for item in value if item.strip())

    rules = Rules(
        drop_tags=frozenset(_norm_tag(tag) for tag in drop_tags if isinstance(tag, str)),
        drop_path_segments=_string_set("drop_path_segments"),
        deals_hosts=_string_set("deals_hosts"),
        deals_segments=_string_set("deals_path_segments"),
        never_extract_hosts=_string_set("never_extract_hosts"),
        host_rewrites={str(key).lower(): str(value).lower() for key, value in rewrites.items()},
        aggregators=_string_set("aggregators"),
    )
    return Config(
        window_days=window,
        x_list_id=list_id,
        limits=_limits(data.get("limits")),
        x_accounts=accounts,
        rss_feeds=feeds,
        rules=rules,
        path=path,
    )
