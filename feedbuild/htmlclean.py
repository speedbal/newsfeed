"""Turn feed and article HTML into a small safe fragment."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Comment, Tag

from feedbuild.urls import clean_url, is_affiliate, is_tracker_url

ALLOW = {
    "p",
    "br",
    "h2",
    "h3",
    "h4",
    "h5",
    "ul",
    "ol",
    "li",
    "blockquote",
    "pre",
    "code",
    "a",
    "img",
    "figure",
    "figcaption",
    "strong",
    "em",
    "b",
    "i",
    "hr",
    "sup",
    "sub",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "span",
    "div",
}

DROP_WITH_CONTENT = {
    "script",
    "style",
    "iframe",
    "noscript",
    "svg",
    "math",
    "object",
    "embed",
    "form",
    "video",
    "audio",
    "canvas",
    "link",
    "meta",
    "button",
    "input",
    "textarea",
    "select",
    "template",
    "frame",
    "frameset",
    "applet",
    "nav",
    "footer",
    "aside",
}

BAD_TOKENS = {
    "ad",
    "ads",
    "advert",
    "advertisement",
    "sponsored",
    "sponsor",
    "outbrain",
    "taboola",
    "socialshare",
    "sharebar",
}

SPONSOR_PARAGRAPH = re.compile(
    r"(?i)^(advertisement|sponsored|sponsored content|sponsor content|ad)$"
    r"|^(this (newsletter|post|issue) is sponsored|sponsored by|a message from our sponsor)\b"
)

PIXEL_HINT = ("1x1", "pixel.gif", "spacer.gif", "/beacon", "trackingpixel", "scorecardresearch")


def to_text(value: str) -> str:
    if not value:
        return ""
    if "<" not in value and "&" not in value:
        return re.sub(r"\s+", " ", value).strip()
    soup = BeautifulSoup(value, "lxml")
    text = soup.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def blurb(text: str, limit: int = 420) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    cut = cleaned[: limit + 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(".,;:") + "…"


def linkish(text: str) -> bool:
    if "http" not in text.lower():
        return False
    remainder = re.sub(r"https?://\S+", "", text)
    remainder = re.sub(r"\s+", "", remainder)
    return len(remainder) < 40


def _tokens(value: str) -> set[str]:
    return {part for part in re.split(r"[^a-z0-9]+", value.lower()) if part}


def _bad_element(tag: Tag) -> bool:
    blob = " ".join(
        [
            " ".join(tag.get("class") or []),
            str(tag.get("id") or ""),
        ]
    )
    tokens = _tokens(blob)
    if tokens & BAD_TOKENS:
        return True
    if tag.name == "p":
        text = tag.get_text(" ", strip=True)
        if text and SPONSOR_PARAGRAPH.search(text) and len(text) < 280:
            return True
    return False


def _parse_dim(value: object) -> int | None:
    if value is None:
        return None
    match = re.match(r"\s*(\d+)", str(value))
    if not match:
        return None
    return int(match.group(1))


def _is_pixel(tag: Tag) -> bool:
    width = _parse_dim(tag.get("width"))
    height = _parse_dim(tag.get("height"))
    if width is not None and height is not None and width <= 1 and height <= 1:
        return True
    src = (tag.get("src") or "").lower()
    return any(hint in src for hint in PIXEL_HINT)


def _mailto(href: str) -> str:
    if not href.lower().startswith("mailto:"):
        return ""
    address = href[7:].split("?", 1)[0].strip()
    if re.fullmatch(r"[^@\s/]+@[^@\s/]+", address):
        return "mailto:" + address
    return ""


def _fix_link(tag: Tag, base_url: str, rewrites: dict[str, str]) -> None:
    href = (tag.get("href") or "").strip()
    mail = _mailto(href)
    if mail:
        tag.attrs = {"href": mail}
        return
    absolute = urljoin(base_url, href) if href else ""
    if not absolute or is_affiliate(absolute) or is_tracker_url(absolute):
        tag.unwrap()
        return
    cleaned = clean_url(absolute, rewrites)
    if not cleaned:
        tag.unwrap()
        return
    tag.attrs = {
        "href": cleaned,
        "rel": "noopener noreferrer",
        "target": "_blank",
    }


def _fix_img(tag: Tag, base_url: str, rewrites: dict[str, str]) -> None:
    if _is_pixel(tag):
        tag.decompose()
        return
    src = urljoin(base_url, (tag.get("src") or "").strip()) if tag.get("src") else ""
    cleaned = clean_url(src, rewrites) if src else ""
    if not cleaned or is_tracker_url(cleaned) or is_affiliate(cleaned):
        tag.decompose()
        return
    alt = (tag.get("alt") or "")[:300]
    width = _parse_dim(tag.get("width"))
    height = _parse_dim(tag.get("height"))
    attrs = {
        "src": cleaned,
        "alt": alt,
        "loading": "lazy",
        "decoding": "async",
    }
    if width and height and width > 1 and height > 1 and width <= 8000 and height <= 8000:
        attrs["width"] = str(width)
        attrs["height"] = str(height)
    tag.attrs = attrs


def clean_html(html: str, base_url: str = "", rewrites: dict[str, str] | None = None) -> str:
    if not html:
        return ""
    if len(html) > 500_000:
        html = html[:500_000]
    rewrites = rewrites or {}
    soup = BeautifulSoup(html, "lxml")
    for comment in list(soup.find_all(string=lambda text: isinstance(text, Comment))):
        comment.extract()

    for tag in list(soup.find_all(True)):
        if tag.parent is None:
            continue
        name = (tag.name or "").lower()
        if name in {"html", "body"}:
            continue
        if name == "h1":
            tag.name = "h2"
            name = "h2"
        if name in DROP_WITH_CONTENT or _bad_element(tag):
            tag.decompose()

    for tag in list(soup.find_all(True)):
        if tag.parent is None:
            continue
        name = (tag.name or "").lower()
        if name in {"html", "body"} or name in ALLOW:
            continue
        tag.unwrap()

    for tag in list(soup.find_all(True)):
        if tag.parent is None:
            continue
        name = (tag.name or "").lower()
        if name == "a":
            _fix_link(tag, base_url, rewrites)
        elif name == "img":
            _fix_img(tag, base_url, rewrites)
        elif name not in {"html", "body"}:
            tag.attrs = {}

    for table in list(soup.find_all("table")):
        if table.parent is None:
            continue
        wrapper = soup.new_tag("div")
        wrapper["class"] = ["table-scroll"]
        table.wrap(wrapper)

    for tag in reversed(list(soup.find_all(True))):
        if tag.parent is None:
            continue
        name = (tag.name or "").lower()
        if name in {"html", "body", "br", "hr", "img"}:
            continue
        if tag.find("img") or tag.get_text(strip=True):
            continue
        tag.decompose()

    body = soup.body or soup
    fragment = "".join(str(child) for child in list(body.contents)).strip()
    if re.search(r"<\s*(script|iframe|object|embed)\b", fragment, re.I):
        return ""
    return fragment
