"""Build dist/ from sources.json. One bad feed never fails the run."""

from __future__ import annotations

import json
import logging
import os
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import httpx

from feedbuild.config import ConfigError, load_config
from feedbuild.dedupe import cluster_rss
from feedbuild.extract import cache_usable, extract_article
from feedbuild.htmlclean import blurb, to_text
from feedbuild.httpfetch import BROWSER_UA, fetch_url
from feedbuild.render import AuditError, write_site
from feedbuild.rss import parse_feed_window
from feedbuild.state import (
    load_state,
    merge_rss,
    merge_x,
    prune_articles,
    save_state,
    slim_item,
)
from feedbuild.urls import canonical_key
from feedbuild.util import iso, safe_err
from feedbuild.xsource import broadcast_avatars, fetch_x

log = logging.getLogger("feed")

FEED_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
    "Accept-Language": "en",
}


def _fetch_feed(client: httpx.Client, feed: dict, meta: dict, now: datetime, rules, cutoff, cap: int) -> dict:
    headers = dict(FEED_HEADERS)
    etag = str(meta.get("etag") or "")
    modified = str(meta.get("modified") or "")
    if etag:
        headers["If-None-Match"] = etag
    if modified:
        headers["If-Modified-Since"] = modified
    fetched = fetch_url(client, feed["url"], headers=headers, timeout=25, max_bytes=8_000_000)
    base = {
        "name": feed["name"],
        "url": feed["url"],
        "items": [],
        "etag": etag,
        "modified": modified,
    }
    if fetched.status == 304:
        return {**base, "status": "not_modified", "error": ""}
    if fetched.error or fetched.status != 200:
        return {**base, "status": "error", "error": fetched.error or f"HTTP {fetched.status}"}
    try:
        items = parse_feed_window(
            feed,
            fetched.body,
            now,
            rules,
            cutoff=cutoff,
            per_feed_cap=cap,
        )
    except Exception as exc:
        return {**base, "status": "error", "error": safe_err(exc)}
    return {
        **base,
        "status": "ok",
        "error": "",
        "items": items,
        "etag": fetched.headers.get("etag", ""),
        "modified": fetched.headers.get("last-modified", ""),
    }


def _apply_cached(item: dict, articles: dict, rewrites: dict[str, str]) -> None:
    if not item.get("needs_extract"):
        return
    entry = articles.get(canonical_key(item.get("url") or "", rewrites))
    if not cache_usable(entry):
        return
    if entry.get("status") == "ok" and entry.get("html"):
        item["html"] = entry["html"]
        item["needs_extract"] = False
        if len(item.get("summary") or "") < 40 and entry.get("text"):
            item["summary"] = blurb(entry["text"])
        return
    item["needs_extract"] = False
    if not item.get("html"):
        item["html"] = item.get("fallback_html") or ""
    if len(item.get("summary") or "") < 40:
        item["summary"] = blurb(to_text(item.get("html") or "")) or item.get("summary") or ""


def _fill_fallback(item: dict) -> None:
    if item.get("html"):
        if len(item.get("summary") or "") < 40:
            item["summary"] = blurb(to_text(item["html"])) or item.get("summary") or ""
        return
    fallback = item.get("fallback_html") or ""
    if fallback:
        item["html"] = fallback
    if len(item.get("summary") or "") < 40 and item.get("html"):
        item["summary"] = blurb(to_text(item["html"])) or item.get("summary") or ""


def _enrich(client: httpx.Client, items: list[dict], state: dict, cfg, now: datetime) -> dict:
    articles = state.setdefault("articles", {})
    rewrites = cfg.rules.host_rewrites
    for item in items:
        _apply_cached(item, articles, rewrites)
    ordered = sorted(
        [item for item in items if item.get("needs_extract")],
        key=lambda item: item.get("time") or "",
        reverse=True,
    )
    empties = [item for item in ordered if len(item.get("summary") or "") < 40]
    rest = [item for item in ordered if len(item.get("summary") or "") >= 40]
    todo: list[str] = []
    seen: set[str] = set()
    for item in empties + rest:
        key = canonical_key(item.get("url") or "", rewrites)
        if not key or key in seen or cache_usable(articles.get(key)):
            continue
        seen.add(key)
        todo.append(key)
        if len(todo) >= cfg.limits["max_extract_per_run"]:
            break

    stats = {"attempted": len(todo), "ok": 0, "empty": 0, "error": 0, "cached": 0}
    stats["cached"] = sum(1 for item in items if not item.get("needs_extract"))

    def work(url: str) -> tuple[str, dict]:
        try:
            return url, extract_article(client, url, cfg.rules)
        except Exception as exc:
            return url, {"status": "error", "code": 0, "html": "", "text": "", "error": safe_err(exc)}

    if todo:
        workers = min(6, len(todo))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for url, entry in pool.map(work, todo):
                entry["at"] = iso(now)
                articles[url] = entry
                stats[entry.get("status") or "error"] = stats.get(entry.get("status") or "error", 0) + 1
                if entry.get("status") != "ok":
                    log.info("extract %s %s %s", entry.get("status"), entry.get("code"), url)

    for item in items:
        _apply_cached(item, articles, rewrites)
        if item.get("needs_extract") or not item.get("html"):
            _fill_fallback(item)
    return stats


def _summary(path: str, lines: list[str]) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("trafilatura").setLevel(logging.ERROR)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("readability").setLevel(logging.ERROR)
    try:
        cfg = load_config()
    except ConfigError as exc:
        log.error("%s", exc)
        return 1

    now = datetime.now(timezone.utc).replace(microsecond=0)
    cutoff = now - timedelta(days=cfg.window_days)
    cutoff_iso = iso(cutoff)
    state = load_state()
    socket.setdefaulttimeout(8)

    feed_results: list[dict] = []
    with httpx.Client(
        follow_redirects=False,
        limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
        timeout=httpx.Timeout(25.0, connect=8.0),
    ) as client:
        metas = state.setdefault("feeds", {})

        def job(feed: dict) -> dict:
            try:
                return _fetch_feed(
                    client,
                    feed,
                    metas.get(feed["url"]) or {},
                    now,
                    cfg.rules,
                    cutoff,
                    cfg.limits["per_feed_cap"],
                )
            except Exception as exc:
                return {
                    "name": feed["name"],
                    "url": feed["url"],
                    "status": "error",
                    "error": safe_err(exc),
                    "items": [],
                    "etag": "",
                    "modified": "",
                }

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(job, feed) for feed in cfg.rss_feeds]
            for future in as_completed(futures):
                result = future.result()
                feed_results.append(result)
                log.info("feed %-28s %s %s", result["name"][:28], result["status"], result.get("error") or "")

        fresh: list[dict] = []
        for result in feed_results:
            metas[result["url"]] = {
                "etag": result.get("etag") or "",
                "modified": result.get("modified") or "",
                "status": result["status"],
                "error": result.get("error") or "",
                "at": iso(now),
            }
            if result["status"] == "ok":
                fresh.extend(result["items"])

        names = {feed["name"] for feed in cfg.rss_feeds}
        rss_items = merge_rss(state.get("rss_items") or [], fresh, names, cutoff_iso)
        extract_stats = _enrich(client, rss_items, state, cfg, now)
        x_items, x_report = fetch_x(client, cfg, state, now)
        x_items = merge_x(state.get("x_posts") or [], x_items, cutoff_iso)
        broadcast_avatars(x_items)

    state["rss_items"] = [slim_item(item) for item in rss_items]
    state["x_posts"] = x_items
    state["articles"] = prune_articles(state.get("articles") or {}, rss_items, cutoff_iso, cfg.rules.host_rewrites)
    state["saved_at"] = iso(now)
    save_state(state)

    clustered = cluster_rss(rss_items, cfg.rules.aggregators, cfg.rules.host_rewrites)
    for post in x_items:
        post.setdefault("also", [])
    display = clustered + x_items
    display.sort(key=lambda item: item.get("time") or "", reverse=True)
    display = display[: cfg.limits["max_items"]]

    try:
        write_site(display, now=now, window_days=cfg.window_days, rewrites=cfg.rules.host_rewrites)
    except AuditError as exc:
        log.error("page audit failed: %s", exc)
        return 1

    failed = [result for result in feed_results if result["status"] == "error"]
    log.info(
        "items rss=%s x=%s displayed=%s extract=%s",
        len(rss_items),
        len(x_items),
        len(display),
        extract_stats,
    )
    log.info(
        "x status=%s requests=%s new=%s %s",
        x_report.get("status"),
        x_report.get("requests"),
        x_report.get("new"),
        x_report.get("error") or "",
    )
    if failed:
        log.info("FAILED_SOURCES")
        for result in sorted(failed, key=lambda item: item["name"]):
            log.info("FAIL %s: %s", result["name"], result.get("error") or "error")
    else:
        log.info("FAILED_SOURCES none")

    report = {
        "built": iso(now),
        "feeds": [
            {"name": result["name"], "status": result["status"], "error": result.get("error") or ""}
            for result in sorted(feed_results, key=lambda item: item["name"])
        ],
        "extract": extract_stats,
        "x": x_report,
        "displayed": len(display),
    }
    report_path = state_report_path()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    _summary(
        os.environ.get("GITHUB_STEP_SUMMARY", ""),
        [
            "### Feed build",
            "",
            f"Stories on the page: {len(display)}",
            "",
            "Feeds that failed:" if failed else "All feeds responded.",
            *[f"- {result['name']}: {result.get('error') or 'error'}" for result in failed],
            "",
            (
                f"X: {x_report.get('status')} · requests {x_report.get('requests')} · "
                f"new posts {x_report.get('new')} · {x_report.get('error') or 'ok'}"
            ),
        ],
    )
    return 0


def state_report_path():
    from feedbuild.state import STATE_PATH

    return STATE_PATH.parent / "last-report.json"
