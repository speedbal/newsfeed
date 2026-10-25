"""Write the static page."""

from __future__ import annotations

import base64
import hashlib
import html
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from feedbuild.config import ROOT
from feedbuild.htmlclean import clean_html, to_text
from feedbuild.urls import clean_url, is_tracker_url
from feedbuild.util import parse_iso, relative

log = logging.getLogger("feed")

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


def _when(item: dict, now: datetime) -> str:
    try:
        return relative(parse_iso(item["time"]), now)
    except (KeyError, ValueError):
        return ""


def _safe_handle(handle: str) -> str:
    handle = (handle or "").strip().lstrip("@")
    if re.fullmatch(r"[A-Za-z0-9_]{1,15}", handle):
        return handle
    return ""


def _initials(name: str) -> str:
    parts = [part for part in re.split(r"\s+", (name or "").strip()) if part]
    if not parts:
        return "X"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def kicker_for(item: dict) -> str:
    labels = [LABELS[topic] for topic in item.get("topics") or [] if topic in LABELS]
    if item.get("kind") == "x":
        return " · ".join(["X", *labels]) if labels else "X"
    return labels[0] if labels else "News"


def render_tweet(item: dict, now: datetime, rewrites: dict[str, str]) -> str:
    handle = _safe_handle(str(item.get("handle") or ""))
    if not handle:
        source = str(item.get("source") or "")
        if source.startswith("@"):
            handle = _safe_handle(source[1:])
    name = str(item.get("title") or handle or "Post")
    profile = clean_url(f"https://x.com/{handle}", rewrites) if handle else ""
    avatar = clean_url(str(item.get("avatar") or ""), rewrites)
    if avatar and is_tracker_url(avatar):
        avatar = ""
    if avatar and profile:
        avatar_html = (
            f'<a class="avatar" href="{_esc(profile)}" rel="noopener noreferrer" target="_blank">'
            f'<img src="{_esc(avatar)}" alt="" width="80" height="80"></a>'
        )
    elif profile:
        avatar_html = (
            f'<a class="avatar" href="{_esc(profile)}" rel="noopener noreferrer" target="_blank">'
            f"{_esc(_initials(name))}</a>"
        )
    else:
        avatar_html = f'<span class="avatar">{_esc(_initials(name))}</span>'
    if profile:
        name_html = (
            f'<a class="tweet-name" href="{_esc(profile)}" rel="noopener noreferrer" target="_blank">'
            f"{_esc(name)}</a>"
        )
        handle_html = (
            f'<a class="tweet-handle" href="{_esc(profile)}" rel="noopener noreferrer" target="_blank">'
            f"@{_esc(handle)}</a>"
        )
    else:
        name_html = f'<span class="tweet-name">{_esc(name)}</span>'
        handle_html = ""
    body = clean_html(item.get("html") or "", item.get("url") or "", rewrites)
    chars = len(to_text(body))
    more = '<button type="button" class="more" hidden>Show more</button>' if chars > 400 else ""
    post_url = clean_url(str(item.get("url") or ""), rewrites)
    link = (
        f'<p class="tweet-links"><a href="{_esc(post_url)}" rel="noopener noreferrer" target="_blank">Open on X</a></p>'
        if post_url
        else ""
    )
    topics = " ".join(topic for topic in item.get("topics") or [] if topic in LABELS)
    return (
        f'<article class="item item-x" data-id="{_esc(item.get("id"))}" data-kind="x" '
        f'data-topics="{_esc(topics)}" data-chars="{chars}">'
        f'<div class="tweet">{avatar_html}<div class="tweet-main">'
        '<div class="tweet-head">'
        '<span class="unread-dot" aria-hidden="true"></span>'
        f"{name_html}{handle_html}"
        f'<time datetime="{_esc(item.get("time") or "")}">{_esc(_when(item, now))}</time>'
        "</div>"
        f'<div class="tweet-text"><div class="tweet-body">{body}</div></div>'
        f"{more}{link}"
        "</div></div></article>"
    )


def render_item(item: dict, now: datetime, rewrites: dict[str, str]) -> str:
    if item.get("kind") == "x":
        return render_tweet(item, now, rewrites)
    label = _when(item, now)
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
    links = []
    story_url = clean_url(str(item.get("url") or ""), rewrites)
    if story_url:
        links.append(
            f'<a href="{_esc(story_url)}" rel="noopener noreferrer" target="_blank">{original}</a>'
        )
    comments_url = clean_url(str(item.get("comments_url") or ""), rewrites)
    if comments_url:
        links.append(
            f'<a href="{_esc(comments_url)}" rel="noopener noreferrer" target="_blank">Comments</a>'
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


def _bad_href(href: str, *, data_icon: bool) -> str:
    lowered = href.lower()
    if lowered.startswith(("javascript:", "file:")):
        return f"bad link {href}"
    if lowered.startswith("data:") and not data_icon:
        return f"bad link {href}"
    if is_tracker_url(href):
        return f"tracker link {href}"
    query = urlsplit(href).query.lower()
    if "utm_" in query or "fbclid=" in query or "gclid=" in query:
        return f"tracking param in {href}"
    return ""


def audit_fragment(fragment: str) -> str:
    """Return why this story HTML must not be embedded, or "" if it is safe."""
    soup = BeautifulSoup(f"<div id='audit'>{fragment}</div>", "lxml")
    root = soup.find(id="audit") or soup
    for tag in root.find_all(["iframe", "object", "embed", "frame", "script"]):
        return f"contains <{tag.name}>"
    for tag in root.find_all(href=True):
        reason = _bad_href(tag["href"], data_icon=False)
        if reason:
            return reason
    for tag in root.find_all(src=True):
        if is_tracker_url(tag["src"]):
            return f"tracker image {tag['src']}"
    return ""


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
        data_icon = tag.name == "link" and "icon" in " ".join(tag.get("rel") or [])
        reason = _bad_href(tag["href"], data_icon=data_icon)
        if reason:
            raise AuditError(reason)
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
        item_id = str(item.get("id") or "")
        try:
            rendered = render_item(item, now, rewrites)
        except Exception as exc:
            log.info("SKIP %s: %s", item_id or "?", exc)
            continue
        # Template tokens are @@NAME@@. A story that mentions @@ must not be able to
        # rewrite the page, and must not fail the build.
        rendered = rendered.replace("@@", "&#64;&#64;")
        reason = audit_fragment(rendered)
        if reason:
            log.info("SKIP %s: %s", item_id or "?", reason)
            continue
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
