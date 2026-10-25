"""Write the static page."""

from __future__ import annotations

import base64
import hashlib
import html
import re
import shutil
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from feedbuild.config import ROOT
from feedbuild.htmlclean import clean_html
from feedbuild.urls import is_tracker_url
from feedbuild.util import parse_iso, relative

WEB = ROOT / "web"
DIST = ROOT / "dist"
TEMPLATE = WEB / "template.html"

THEME_SCRIPT = (
    'try{var t=localStorage.getItem("newsfeed-theme");'
    'if(t==="light"||t==="dark"){document.documentElement.setAttribute("data-theme",t);'
    'var m=document.querySelector(\'meta[name="theme-color"]\');'
    'if(m)m.setAttribute("content",t==="dark"?"#12110f":"#f6f3ec");}}catch(e){}'
)

LABELS = {"tech": "Tech", "ai": "AI", "crypto": "Crypto"}


class AuditError(RuntimeError):
    pass


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def kicker_for(item: dict) -> str:
    labels = [LABELS[topic] for topic in item.get("topics") or [] if topic in LABELS]
    if item.get("kind") == "x":
        return " · ".join(["X", *labels]) if labels else "X"
    return labels[0] if labels else "News"


def render_item(item: dict, now: datetime, rewrites: dict[str, str]) -> str:
    try:
        when = parse_iso(item["time"])
        label = relative(when, now)
    except (KeyError, ValueError):
        label = ""
    bits = [item.get("source") or ""]
    if item.get("extra"):
        bits.append(item["extra"])
    if item.get("also"):
        bits.append("Also on " + ", ".join(item["also"]))
    summary = item.get("summary") or ""
    summary_html = f'<p class="summary">{_esc(summary)}</p>' if summary else ""
    body = clean_html(item.get("html") or "", item.get("url") or "", rewrites)
    if not body and summary:
        body = f"<p>{_esc(summary)}</p>"
    original = "View on X" if item.get("kind") == "x" else "Read original"
    links = [
        f'<a href="{_esc(item.get("url") or "")}" rel="noopener noreferrer" target="_blank">{original}</a>'
    ]
    if item.get("comments_url"):
        links.append(
            f'<a href="{_esc(item["comments_url"])}" rel="noopener noreferrer" target="_blank">Comments</a>'
        )
    topics = " ".join(topic for topic in item.get("topics") or [] if topic in LABELS)
    return (
        f'<article class="item" data-id="{_esc(item.get("id"))}" data-kind="{_esc(item.get("kind") or "rss")}" '
        f'data-topics="{_esc(topics)}">'
        "<details><summary>"
        '<div class="kicker-row">'
        '<span class="unread-dot" aria-hidden="true"></span>'
        f'<span class="kicker">{_esc(kicker_for(item))}</span>'
        f'<time datetime="{_esc(item.get("time") or "")}">{_esc(label)}</time>'
        '<span class="chev" aria-hidden="true"></span>'
        "</div>"
        f'<h2 class="title">{_esc(item.get("title") or "")}</h2>'
        f'<p class="meta">{_esc(" · ".join(bit for bit in bits if bit))}</p>'
        f"{summary_html}</summary>"
        f'<div class="body"><div class="content">{body}</div>'
        f'<p class="outbound">{"".join(links)}</p></div>'
        "</details></article>"
    )


def _csp() -> str:
    digest = base64.b64encode(hashlib.sha256(THEME_SCRIPT.encode()).digest()).decode()
    return (
        "default-src 'none'; "
        f"script-src 'self' 'sha256-{digest}'; "
        "style-src 'self'; "
        "img-src https: data:; "
        "font-src 'self'; "
        "connect-src 'none'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-src 'none'; "
        "object-src 'none'"
    )


def audit_page(page: str) -> None:
    soup = BeautifulSoup(page, "lxml")
    for tag in soup.find_all(["iframe", "object", "embed", "frame"]):
        raise AuditError(f"page contains <{tag.name}>")
    scripts = soup.find_all("script")
    if len(scripts) > 2:
        raise AuditError("unexpected script tags")
    for script in scripts:
        src = script.get("src")
        if src and src != "app.js":
            raise AuditError(f"unexpected script src {src}")
        if not src and "newsfeed-theme" not in (script.string or ""):
            raise AuditError("unexpected inline script")
    for tag in soup.find_all(href=True):
        href = tag["href"]
        lowered = href.lower()
        if lowered.startswith(("javascript:", "file:")):
            raise AuditError(f"bad link {href}")
        if lowered.startswith("data:") and not (
            tag.name == "link" and "icon" in " ".join(tag.get("rel") or [])
        ):
            raise AuditError(f"bad link {href}")
        if is_tracker_url(href):
            raise AuditError(f"tracker link {href}")
        query = urlsplit(href).query.lower()
        if "utm_" in query or "fbclid=" in query or "gclid=" in query:
            raise AuditError(f"tracking param in {href}")
    for tag in soup.find_all(src=True):
        if tag.name == "script":
            continue
        if is_tracker_url(tag["src"]):
            raise AuditError(f"tracker image {tag['src']}")
    if re.search(r"<\s*(script|iframe)\b[^>]*googletagmanager", page, re.I):
        raise AuditError("analytics snippet")


def write_site(
    items: list[dict],
    *,
    now: datetime,
    window_days: int,
    rewrites: dict[str, str],
    dest: Path | None = None,
) -> Path:
    pieces = []
    for item in items:
        rendered = render_item(item, now, rewrites)
        if "@@" in rendered:
            raise AuditError("item HTML collided with a template token")
        pieces.append(rendered)
    if pieces:
        items_html = "\n".join(pieces)
    else:
        items_html = f'<p class="empty-all">No stories in the last {window_days} days.</p>'
    built_rel = "just now"
    page = TEMPLATE.read_text(encoding="utf-8")
    page = (
        page.replace("@@CSP@@", _csp())
        .replace("@@THEME_SCRIPT@@", THEME_SCRIPT)
        .replace("@@BUILT_ISO@@", now.strftime("%Y-%m-%dT%H:%M:%SZ"))
        .replace("@@BUILT_REL@@", built_rel)
        .replace("@@WINDOW@@", str(window_days))
        .replace("@@COUNT@@", str(len(items)))
        .replace("@@ITEMS@@", items_html)
    )
    audit_page(page)
    out = dest or DIST
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(page, encoding="utf-8")
    shutil.copyfile(WEB / "style.css", out / "style.css")
    shutil.copyfile(WEB / "app.js", out / "app.js")
    return out / "index.html"
