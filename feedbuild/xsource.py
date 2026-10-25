"""Read new posts from one X list. One request on a normal run."""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta

import httpx

from feedbuild.config import Config
from feedbuild.htmlclean import blurb
from feedbuild.httpfetch import BROWSER_UA
from feedbuild.urls import clean_url, host_of, is_affiliate, is_tracker_url
from feedbuild.util import iso, parse_iso, safe_err

API = "https://api.x.com/2/lists/{list_id}/tweets"
URL_RE = re.compile(r"https://[^\s<>()]+")
DEAL_RE = re.compile(r"(?i)^deal:")


def _types(tweet: dict) -> set[str]:
    found = set()
    for ref in tweet.get("referenced_tweets") or []:
        if isinstance(ref, dict) and ref.get("type"):
            found.add(ref["type"])
    return found


def drop_reason(tweet: dict) -> str:
    kinds = _types(tweet)
    if "retweeted" in kinds:
        return "retweet"
    if "replied_to" in kinds:
        return "reply"
    if tweet.get("in_reply_to_user_id") and "quoted" not in kinds:
        return "reply"
    return ""


def expand_text(text: str, entities: dict, rewrites: dict[str, str]) -> str:
    for item in (entities or {}).get("urls") or []:
        short = item.get("url") or ""
        expanded = item.get("expanded_url") or ""
        if not short:
            continue
        cleaned = clean_url(expanded, rewrites) if expanded else ""
        host = host_of(cleaned)
        if (
            not cleaned
            or is_affiliate(cleaned)
            or is_tracker_url(cleaned)
            or host == "pic.twitter.com"
            or host.endswith("twimg.com")
        ):
            replacement = ""
        else:
            replacement = cleaned
        text = text.replace(short, replacement)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" *\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def linkify(line: str, rewrites: dict[str, str]) -> str:
    import html as html_lib

    pieces: list[str] = []
    last = 0
    for match in URL_RE.finditer(line):
        pieces.append(html_lib.escape(line[last : match.start()]))
        raw = match.group(0).rstrip(".,;:)")
        trailing = match.group(0)[len(raw) :]
        cleaned = clean_url(raw, rewrites)
        if cleaned and not is_affiliate(cleaned) and not is_tracker_url(cleaned):
            href = html_lib.escape(cleaned, quote=True)
            label = html_lib.escape(cleaned)
            pieces.append(
                f'<a href="{href}" rel="noopener noreferrer" target="_blank">{label}</a>'
            )
        pieces.append(html_lib.escape(trailing))
        last = match.end()
    pieces.append(html_lib.escape(line[last:]))
    return "".join(pieces)


def _paragraphs(text: str, rewrites: dict[str, str]) -> str:
    blocks = []
    for paragraph in re.split(r"\n{2,}", text.strip()):
        lines = [linkify(line, rewrites) for line in paragraph.split("\n")]
        if any(line.strip() for line in lines):
            blocks.append("<p>" + "<br>".join(lines) + "</p>")
    return "".join(blocks)


def _images(tweet: dict, media_index: dict[str, dict], rewrites: dict[str, str]) -> list[dict]:
    keys = ((tweet.get("attachments") or {}).get("media_keys")) or []
    images = []
    for key in keys:
        media = media_index.get(key) or {}
        if media.get("type") == "photo":
            src = media.get("url") or media.get("preview_image_url") or ""
        else:
            src = media.get("preview_image_url") or ""
        cleaned = clean_url(src, rewrites) if src else ""
        if not cleaned or is_tracker_url(cleaned):
            continue
        image = {"src": cleaned, "alt": (media.get("alt_text") or "")[:300]}
        width = media.get("width")
        height = media.get("height")
        if isinstance(width, int) and isinstance(height, int) and width > 1 and height > 1:
            image["width"] = width
            image["height"] = height
        images.append(image)
    return images


def _html(text: str, images: list[dict], quote: dict | None, rewrites: dict[str, str]) -> str:
    import html as html_lib

    parts = [_paragraphs(text, rewrites)]
    if quote and quote.get("text"):
        label = quote.get("name") or ""
        handle = quote.get("handle") or ""
        if handle:
            label = f"{label} (@{handle})" if label else f"@{handle}"
        quote_html = "<blockquote>"
        if label:
            quote_html += "<p><strong>" + html_lib.escape(label) + "</strong></p>"
        quote_html += _paragraphs(quote["text"], rewrites) + "</blockquote>"
        parts.append(quote_html)
    for image in images:
        src = html_lib.escape(image["src"], quote=True)
        alt = html_lib.escape(image.get("alt") or "", quote=True)
        dims = ""
        if image.get("width") and image.get("height"):
            dims = f' width="{int(image["width"])}" height="{int(image["height"])}"'
        parts.append(
            f'<figure><img src="{src}" alt="{alt}" loading="lazy" decoding="async"{dims}></figure>'
        )
    return "".join(parts)


def _avatar(url: str, rewrites: dict[str, str]) -> str:
    cleaned = clean_url(url or "", rewrites)
    if not cleaned or is_tracker_url(cleaned):
        return ""
    return re.sub(r"_normal(\.[A-Za-z0-9]+)$", r"_400x400\1", cleaned)


def broadcast_avatars(items: list[dict]) -> None:
    """Copy a profile image onto older posts from the same handle."""
    found: dict[str, str] = {}
    for item in items:
        handle = str(item.get("handle") or "").lower()
        avatar = str(item.get("avatar") or "")
        if handle and avatar:
            found[handle] = avatar
    for item in items:
        handle = str(item.get("handle") or "").lower()
        if handle and not item.get("avatar") and found.get(handle):
            item["avatar"] = found[handle]


def tweets_to_items(
    payload: dict,
    accounts_by_id: dict[str, dict],
    rewrites: dict[str, str],
    *,
    now: datetime,
    cutoff: datetime,
) -> tuple[list[dict], list[str], datetime | None]:
    """Return kept items, every tweet id in the payload, and the oldest created time."""
    includes = payload.get("includes") or {}
    users = {user.get("id"): user for user in includes.get("users") or [] if user.get("id")}
    media_index = {
        media.get("media_key"): media
        for media in includes.get("media") or []
        if media.get("media_key")
    }
    quoted = {tweet.get("id"): tweet for tweet in includes.get("tweets") or [] if tweet.get("id")}
    items: list[dict] = []
    raw_ids: list[str] = []
    oldest: datetime | None = None
    for tweet in payload.get("data") or []:
        tweet_id = str(tweet.get("id") or "")
        if not tweet_id.isdigit():
            continue
        raw_ids.append(tweet_id)
        created_raw = tweet.get("created_at")
        if not created_raw:
            continue
        try:
            created = parse_iso(created_raw)
        except ValueError:
            continue
        oldest = created if oldest is None or created < oldest else oldest
        if drop_reason(tweet):
            continue
        if created < cutoff or created > now + timedelta(hours=6):
            continue
        author = users.get(tweet.get("author_id")) or {}
        account = accounts_by_id.get(str(tweet.get("author_id") or "")) or {}
        handle = author.get("username") or account.get("handle") or ""
        name = account.get("name") or author.get("name") or handle or "Post"
        note = tweet.get("note_tweet") if isinstance(tweet.get("note_tweet"), dict) else {}
        if note.get("text"):
            text, entities = note.get("text") or "", note.get("entities") or tweet.get("entities") or {}
        else:
            text, entities = tweet.get("text") or "", tweet.get("entities") or {}
        plain = expand_text(text, entities, rewrites)
        if handle.casefold() == "verge" and (DEAL_RE.match(plain) or "/deals" in plain or "/shopping" in plain):
            continue
        images = _images(tweet, media_index, rewrites)
        quote = None
        for ref in tweet.get("referenced_tweets") or []:
            if not isinstance(ref, dict) or ref.get("type") != "quoted":
                continue
            original = quoted.get(ref.get("id")) or {}
            q_note = original.get("note_tweet") if isinstance(original.get("note_tweet"), dict) else {}
            q_text = q_note.get("text") or original.get("text") or ""
            q_entities = q_note.get("entities") or original.get("entities") or {}
            q_user = users.get(original.get("author_id")) or {}
            quote = {
                "name": q_user.get("name") or "",
                "handle": q_user.get("username") or "",
                "text": expand_text(q_text, q_entities, rewrites),
            }
            break
        if not plain and not images and not (quote and quote.get("text")):
            continue
        summary = blurb(plain, 600) if plain else ("Photo" if images else "Quote")
        items.append(
            {
                "id": f"x:{tweet_id}",
                "kind": "x",
                "title": name,
                "source": f"@{handle}" if handle else name,
                "topics": list(account.get("topics") or []),
                "url": f"https://x.com/{handle}/status/{tweet_id}" if handle else f"https://x.com/i/status/{tweet_id}",
                "time": iso(created if created <= now else now),
                "summary": summary,
                "html": _html(plain, images, quote, rewrites),
                "images": images,
                "comments_url": "",
                "extra": "",
                "also": [],
                "handle": handle,
                "avatar": _avatar(author.get("profile_image_url") or "", rewrites),
                "needs_extract": False,
            }
        )
    return items, raw_ids, oldest


def _request(client: httpx.Client, list_id: str, token: str, params: dict[str, str]) -> tuple[dict | None, str, int]:
    try:
        response = client.get(
            API.format(list_id=list_id),
            params=params,
            headers={"Authorization": f"Bearer {token}", "User-Agent": BROWSER_UA},
            timeout=25,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        return None, safe_err(exc), 0
    body = response.text[:400]
    if response.status_code != 200:
        return None, body, response.status_code
    try:
        return response.json(), "", 200
    except ValueError:
        return None, "invalid JSON", response.status_code


def _params(page_size: int, *, since_id: str = "", pagination: str = "", note_tweet: bool = True) -> dict[str, str]:
    fields = "created_at,author_id,attachments,entities,referenced_tweets,conversation_id,in_reply_to_user_id"
    if note_tweet:
        fields += ",note_tweet"
    params = {
        "max_results": str(page_size),
        "tweet.fields": fields,
        "expansions": "author_id,attachments.media_keys,referenced_tweets.id",
        "media.fields": "url,preview_image_url,type,alt_text,width,height",
        "user.fields": "username,name,profile_image_url",
    }
    if since_id:
        params["since_id"] = since_id
    if pagination:
        params["pagination_token"] = pagination
    return params


def fetch_x(client: httpx.Client, cfg: Config, state: dict, now: datetime) -> tuple[list[dict], dict]:
    report = {
        "status": "skipped",
        "requests": 0,
        "new": 0,
        "error": "",
        "since_id": state.get("x_since_id") or "",
    }
    token = os.environ.get("X_BEARER_TOKEN", "").strip()
    if not token:
        report["error"] = "X_BEARER_TOKEN not set"
        return [], report
    list_id = (os.environ.get("X_LIST_ID") or "").strip() or cfg.x_list_id
    if not re.fullmatch(r"\d{1,20}", list_id or ""):
        report["status"] = "error"
        report["error"] = "X list id is missing or not numeric"
        return [], report

    previous = str(state.get("x_since_id") or "")
    supported = state.get("x_since_id_supported")
    if previous and supported is False:
        max_pages = 1
    elif previous:
        max_pages = cfg.limits["x_max_requests"]
    else:
        max_pages = cfg.limits["x_max_requests_cold"]
    cutoff = now - timedelta(days=cfg.window_days)
    accounts = {account["id"]: account for account in cfg.x_accounts}
    collected: list[dict] = []
    raw_ids: list[str] = []
    pagination = ""
    note_tweet = True
    first_ok = False
    saw_old = False

    for page in range(max_pages):
        since = previous if page == 0 and previous and supported is not False else ""
        params = _params(
            cfg.limits["x_page_size"],
            since_id=since,
            pagination=pagination,
            note_tweet=note_tweet,
        )
        payload, error, status = _request(client, list_id, token, params)
        report["requests"] += 1
        if status == 400 and since and "since_id" in error.lower():
            state["x_since_id_supported"] = False
            supported = False
            params = _params(cfg.limits["x_page_size"], note_tweet=note_tweet)
            payload, error, status = _request(client, list_id, token, params)
            report["requests"] += 1
            since = ""
        if status == 400 and note_tweet and "note_tweet" in error.lower():
            note_tweet = False
            params = _params(
                cfg.limits["x_page_size"],
                since_id=since,
                pagination=pagination,
                note_tweet=False,
            )
            payload, error, status = _request(client, list_id, token, params)
            report["requests"] += 1
        if status != 200 or payload is None:
            report["status"] = "error"
            hint = ""
            if status in {403, 404}:
                hint = " Private lists need the list owner's user token; an app-only bearer can only read a public list."
            elif status == 401:
                hint = " Check that X_BEARER_TOKEN is valid."
            report["error"] = f"HTTP {status}: {error[:180]}{hint}"
            break

        first_ok = True if page == 0 else first_ok
        items, ids, oldest = tweets_to_items(
            payload,
            accounts,
            cfg.rules.host_rewrites,
            now=now,
            cutoff=cutoff,
        )
        raw_ids.extend(ids)
        if since and any(int(item_id) <= int(previous) for item_id in ids):
            saw_old = True
            state["x_since_id_supported"] = False
            items = [item for item in items if int(item["id"].split(":", 1)[1]) > int(previous)]
        elif since:
            state["x_since_id_supported"] = True
        if previous and state.get("x_since_id_supported") is False:
            items = [item for item in items if int(item["id"].split(":", 1)[1]) > int(previous)]
        collected.extend(items)
        pagination = str((payload.get("meta") or {}).get("next_token") or "")
        if not ids or not pagination or saw_old or state.get("x_since_id_supported") is False:
            break
        if oldest is not None and oldest < cutoff:
            break
        if since and len(ids) < cfg.limits["x_page_size"]:
            break

    if first_ok and raw_ids:
        state["x_since_id"] = max(raw_ids, key=lambda value: int(value))
        report["since_id"] = state["x_since_id"]
    if report["status"] != "error":
        report["status"] = "ok"
    report["new"] = len(collected)
    return collected, report
