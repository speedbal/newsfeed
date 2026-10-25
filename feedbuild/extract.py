"""Download an article page and keep the readable part."""

from __future__ import annotations

import re

import httpx
import trafilatura
from readability import Document

from feedbuild.config import Rules
from feedbuild.htmlclean import clean_html, to_text
from feedbuild.httpfetch import BROWSER_UA, fetch_url
from feedbuild.urls import canonical_key

PAYWALL = re.compile(
    r"(subscribe to (read|continue)|sign in to (read|continue)|"
    r"this (article|post) is for subscribers|already a subscriber\?)",
    re.I,
)
HARD_STATUS = {401, 403, 404, 410, 451}

ARTICLE_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8",
}


def cache_usable(entry: dict | None) -> bool:
    if not entry:
        return False
    status = entry.get("status")
    if status == "ok" and entry.get("html"):
        return True
    if status == "empty":
        return True
    try:
        code = int(entry.get("code") or 0)
    except (TypeError, ValueError):
        code = 0
    return status == "error" and code in HARD_STATUS


def _html_type(content_type: str) -> bool:
    if not content_type:
        return True
    lowered = content_type.lower()
    return "html" in lowered or "xml" in lowered or lowered.startswith("text/")


def _paywalled(text: str) -> bool:
    return bool(PAYWALL.search(text)) and len(text) < 900


def _readable(content: str, url: str) -> str:
    extracted = ""
    try:
        extracted = trafilatura.extract(
            content,
            url=url,
            include_images=True,
            include_links=True,
            include_formatting=True,
            include_tables=True,
            output_format="html",
            favor_precision=True,
            include_comments=False,
        ) or ""
    except Exception:
        extracted = ""
    extracted_len = len(to_text(extracted))
    if extracted and extracted_len >= 180:
        return extracted
    try:
        alternative = Document(content).summary(html_partial=True) or ""
    except Exception:
        alternative = ""
    if len(to_text(alternative)) > extracted_len:
        return alternative
    return extracted


def extract_article(client: httpx.Client, url: str, rules: Rules) -> dict:
    target = canonical_key(url, rules.host_rewrites) or url
    fetched = fetch_url(client, target, headers=ARTICLE_HEADERS, timeout=18, max_bytes=2_000_000)
    if fetched.error or fetched.status != 200:
        return {
            "status": "error",
            "code": fetched.status,
            "html": "",
            "text": "",
            "error": fetched.error or f"HTTP {fetched.status}",
        }
    if not _html_type(fetched.headers.get("content-type", "")):
        return {"status": "empty", "code": fetched.status, "html": "", "text": "", "error": "not html"}
    raw_html = _readable(fetched.text, fetched.final_url or target)
    cleaned = clean_html(raw_html, fetched.final_url or target, rules.host_rewrites)
    text = to_text(cleaned)
    if len(cleaned) > 80_000:
        cleaned = clean_html(cleaned[:80_000], fetched.final_url or target, rules.host_rewrites)
        text = to_text(cleaned)
    if len(text) < 120 or _paywalled(text):
        return {"status": "empty", "code": fetched.status, "html": "", "text": "", "error": "short or paywalled"}
    return {"status": "ok", "code": fetched.status, "html": cleaned, "text": text[:50_000], "error": ""}
