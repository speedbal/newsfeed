"""Offline checks for cleaning, filtering, dedupe, and page output."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

from feedbuild.config import Rules, load_config
from feedbuild.dedupe import cluster_rss
from feedbuild.htmlclean import clean_html
from feedbuild.httpfetch import Fetch, _looks_like_challenge, _parse_curl_headers
from feedbuild.render import audit_page, write_site
from feedbuild.rss import is_sponsored, parse_feed_window
from feedbuild.state import merge_rss
from feedbuild.urls import clean_url, host_is_amazon, is_affiliate, is_tracker_url, unsafe_host
from feedbuild.util import iso
from feedbuild.xsource import drop_reason, tweets_to_items


def rules() -> Rules:
    return Rules(
        drop_tags=frozenset(
            {"sponsored", "deals", "partner content", "press release", "verge shopping", "shopping"}
        ),
        drop_path_segments=frozenset({"sponsored", "partner-content", "advertorial"}),
        deals_hosts=frozenset({"theverge.com"}),
        deals_segments=frozenset({"deals", "shopping"}),
        never_extract_hosts=frozenset({"openai.com", "theblock.co", "blockworks.com", "x.com"}),
        host_rewrites={"vitalik.ca": "vitalik.eth.limo", "www.vitalik.ca": "vitalik.eth.limo"},
        aggregators=frozenset({"hacker news", "lobsters", "techmeme"}),
    )


def test_urls() -> None:
    rewrites = rules().host_rewrites
    assert clean_url("http://www.Example.com/a/?utm_source=rss&id=3&fbclid=zzz") == "https://example.com/a?id=3"
    assert clean_url("https://example.com/a?ref=platformer.news&id=1") == "https://example.com/a?id=1"
    amazon = clean_url("https://www.amazon.com/dp/B000?tag=foo-20&linkCode=as2")
    assert "tag=" not in amazon and "linkcode=" not in amazon
    assert "amazon.com/dp/B000" in amazon
    assert clean_url("https://vitalik.ca/general/2026/x.html?utm_campaign=n", rewrites) == (
        "https://vitalik.eth.limo/general/2026/x.html"
    )
    assert clean_url("javascript:alert(1)") == ""
    assert clean_url("http://127.0.0.1/latest") == ""
    assert clean_url("http://169.254.169.254/latest/meta-data") == ""
    assert unsafe_host("10.1.2.3")
    assert unsafe_host("localhost")
    assert not unsafe_host("example.com")
    assert is_affiliate("https://amzn.to/abc")
    assert is_tracker_url("https://www.google-analytics.com/collect")
    assert host_is_amazon("smile.amazon.com")
    assert not host_is_amazon("example.com")


def test_html() -> None:
    raw = """
    <p onclick="alert(1)">Hello <a href="https://example.com/story?utm_source=x&amp;id=3">story</a></p>
    <script>alert(1)</script>
    <iframe src="https://evil.test/ad"></iframe>
    <img src="https://cdn.example.com/pixel.gif" width="1" height="1">
    <img src="https://doubleclick.net/ad.png">
    <img src="/photo.jpg" alt="A photo">
    <a href="https://amzn.to/abc">buy this</a>
    <a href="https://vitalik.ca/foo">essay</a>
    <p>Advertisement</p>
    """
    cleaned = clean_html(raw, "https://example.com/post", rules().host_rewrites)
    lowered = cleaned.lower()
    assert "<script" not in lowered
    assert "<iframe" not in lowered
    assert "onclick" not in lowered
    assert "doubleclick" not in lowered
    assert "pixel.gif" not in lowered
    assert "utm_" not in lowered
    assert "https://example.com/story?id=3" in cleaned
    assert "https://example.com/photo.jpg" in cleaned
    assert "amzn.to" not in cleaned
    assert "buy this" in cleaned
    assert "https://vitalik.eth.limo/foo" in cleaned
    assert "advertisement" not in lowered
    assert clean_html(cleaned, "https://example.com/post", rules().host_rewrites)


def test_sponsored_filter() -> None:
    active = rules()
    assert is_sponsored("A real headline", ["Deals"], "https://example.com/a", active)
    assert is_sponsored("Partner Content: chips", [], "https://example.com/a", active)
    assert not is_sponsored("How the Fed deals with rates", ["Policy"], "https://example.com/fed", active)
    assert is_sponsored(
        "Nice laptop",
        [],
        "https://www.theverge.com/deals/2026/10/1/laptop",
        active,
    )


def test_parse_feed() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    recent = format_datetime(now - timedelta(hours=2))
    old = format_datetime(now - timedelta(days=10))
    body = f"""<?xml version="1.0"?>
    <rss version="2.0"><channel><title>Example</title>
      <item>
        <title>Chip supply eases as factories add capacity</title>
        <link>https://example.com/chips?utm_source=rss&amp;id=7</link>
        <pubDate>{recent}</pubDate>
        <description>Factories added capacity and the shortage is easing.</description>
      </item>
      <item>
        <title>Buy this laptop today</title>
        <link>https://www.example.com/deal</link>
        <category>Deals</category>
        <pubDate>{recent}</pubDate>
        <description>A shopping deal.</description>
      </item>
      <item>
        <title>How the Fed deals with rates this quarter</title>
        <link>https://example.com/fed</link>
        <pubDate>{recent}</pubDate>
        <description>Policy makers are watching prices this quarter.</description>
      </item>
      <item>
        <title>Old story about something else entirely here</title>
        <link>https://example.com/old</link>
        <pubDate>{old}</pubDate>
        <description>This one is old.</description>
      </item>
      <item>
        <title>Vitalik essay on coordination</title>
        <link>https://vitalik.ca/general/2026/coordination.html</link>
        <pubDate>{recent}</pubDate>
        <description>A long essay about coordination mechanisms.</description>
      </item>
    </channel></rss>""".encode()
    feed = {
        "name": "Example",
        "url": "https://example.com/rss",
        "category": "tech",
        "full_text": False,
        "extract": False,
    }
    items = parse_feed_window(
        feed,
        body,
        now,
        rules(),
        cutoff=now - timedelta(days=3),
        per_feed_cap=20,
    )
    titles = {item["title"] for item in items}
    assert "Buy this laptop today" not in titles
    assert "Old story about something else entirely here" not in titles
    assert "How the Fed deals with rates this quarter" in titles
    chips = next(item for item in items if "Chip supply" in item["title"])
    assert chips["url"] == "https://example.com/chips?id=7"
    vitalik = next(item for item in items if item["title"].startswith("Vitalik"))
    assert vitalik["url"] == "https://vitalik.eth.limo/general/2026/coordination.html"


def test_dedupe_and_merge() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    stamp = iso(now - timedelta(hours=1))
    ars = {
        "id": "rss:ars:1",
        "kind": "rss",
        "title": "Chip supply eases as factories add capacity this month",
        "source": "Ars Technica",
        "topics": ["tech"],
        "url": "https://example.com/chips?id=7",
        "time": stamp,
        "summary": "Factories added capacity.",
        "html": "<p>Full article about factories and capacity across the quarter, with enough of the piece to prefer this copy over a shorter writeup.</p>",
        "comments_url": "",
        "extra": "",
        "needs_extract": False,
    }
    hn = dict(ars)
    hn.update(
        {
            "id": "rss:hn:1",
            "source": "Hacker News",
            "html": "",
            "summary": "",
            "extra": "120 pts · 40 comments",
            "comments_url": "https://news.ycombinator.com/item?id=1",
            "url": "https://www.example.com/chips?id=7&utm_source=hn",
        }
    )
    other = dict(ars)
    other.update(
        {
            "id": "rss:other:1",
            "source": "The Register",
            "title": "Chip supply eases as factories add capacity this month",
            "url": "https://example.com/other-chips",
            "html": "<p>Same story, different url, still the same headline about factories.</p>",
            "time": iso(now - timedelta(hours=3)),
        }
    )
    clustered = cluster_rss([ars, hn, other], rules().aggregators, rules().host_rewrites)
    assert len(clustered) == 1
    story = clustered[0]
    assert story["source"] in {"Ars Technica", "The Register"}
    assert story["source"] != "Hacker News"
    assert "Hacker News" in story["also"]
    assert story["comments_url"].startswith("https://news.ycombinator.com/")
    assert story["id"].startswith("s:")

    cutoff = iso(now - timedelta(days=3))
    fresh = dict(ars)
    fresh["html"] = ""
    fresh["needs_extract"] = True
    fresh["summary"] = ""
    merged = merge_rss([ars], [fresh], {"Ars Technica"}, cutoff)
    assert len(merged) == 1
    assert merged[0]["needs_extract"] is False
    assert "Full article" in merged[0]["html"]
    kept = merge_rss([ars], [], {"Ars Technica"}, cutoff)
    assert len(kept) == 1
    dropped = merge_rss([ars], [], set(), cutoff)
    assert dropped == []


def test_x_posts() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    recent = (now - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    old = (now - timedelta(days=12)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    payload = {
        "data": [
            {
                "id": "100",
                "created_at": recent,
                "author_id": "1",
                "text": "New note https://t.co/abc",
                "entities": {
                    "urls": [
                        {"url": "https://t.co/abc", "expanded_url": "https://example.com/a?utm_source=x&id=9"}
                    ]
                },
            },
            {
                "id": "101",
                "created_at": recent,
                "author_id": "1",
                "text": "repost",
                "referenced_tweets": [{"type": "retweeted", "id": "9"}],
            },
            {
                "id": "102",
                "created_at": recent,
                "author_id": "1",
                "text": "a reply",
                "referenced_tweets": [{"type": "replied_to", "id": "8"}],
            },
            {"id": "103", "created_at": old, "author_id": "1", "text": "old post that should age out"},
            {
                "id": "104",
                "created_at": recent,
                "author_id": "1",
                "text": "Look at this",
                "referenced_tweets": [{"type": "quoted", "id": "50"}],
                "attachments": {"media_keys": ["m1"]},
            },
        ],
        "includes": {
            "users": [{"id": "1", "username": "karpathy", "name": "Andrej"}],
            "tweets": [{"id": "50", "author_id": "2", "text": "Original thought worth quoting"}],
            "media": [
                {
                    "media_key": "m1",
                    "type": "photo",
                    "url": "https://pbs.twimg.com/media/x.jpg",
                    "alt_text": "diagram",
                }
            ],
        },
    }
    assert drop_reason(payload["data"][1]) == "retweet"
    assert drop_reason(payload["data"][2]) == "reply"
    assert drop_reason(payload["data"][4]) == ""
    items, raw_ids, _oldest = tweets_to_items(
        payload,
        {"1": {"id": "1", "handle": "karpathy", "name": "Andrej Karpathy", "topics": ["ai"]}},
        rules().host_rewrites,
        now=now,
        cutoff=now - timedelta(days=3),
    )
    kept = {item["id"] for item in items}
    assert raw_ids == ["100", "101", "102", "103", "104"]
    assert kept == {"x:100", "x:104"}
    note = next(item for item in items if item["id"] == "x:100")
    assert "utm_" not in note["html"]
    assert "https://example.com/a?id=9" in note["html"]
    assert note["topics"] == ["ai"]
    assert note["title"] == "Andrej Karpathy"
    photo = next(item for item in items if item["id"] == "x:104")
    assert "pbs.twimg.com/media/x.jpg" in photo["html"]
    assert "Original thought" in photo["html"]


def test_page_and_sources() -> None:
    cfg = load_config()
    assert cfg.x_accounts and cfg.rss_feeds
    assert cfg.x_list_id.isdigit()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    item = {
        "id": "s:abc",
        "kind": "rss",
        "title": "A readable headline about factories",
        "source": "Ars Technica",
        "topics": ["tech"],
        "url": "https://example.com/chips?id=7",
        "time": iso(now - timedelta(hours=1)),
        "summary": "A short blurb that is long enough to stand in for the lede of the story.",
        "html": "<p>The full text.</p><iframe src='https://evil.test'></iframe>",
        "comments_url": "",
        "extra": "",
        "also": [],
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = write_site(
            [item],
            now=now,
            window_days=3,
            rewrites={},
            dest=Path(tmp),
        )
        page = path.read_text(encoding="utf-8")
    audit_page(page)
    assert "<iframe" not in page.lower()
    assert "The full text." in page
    assert 'data-topics="tech"' in page
    assert "app.js" in page


def test_curl_challenge() -> None:
    challenge = Fetch(
        status=403,
        body=b"<html>Just a moment...</html>",
        text="<html>Just a moment...</html>",
        headers={"content-type": "text/html"},
    )
    assert _looks_like_challenge(challenge)
    assert not _looks_like_challenge(Fetch(status=200, text="ok", body=b"ok"))
    status, headers = _parse_curl_headers(
        b"HTTP/2 200 \r\ncontent-type: application/xml\r\netag: W/\"abc\"\r\n\r\n"
    )
    assert status == 200
    assert headers["etag"] == 'W/"abc"'
    assert headers["content-type"] == "application/xml"


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {exc}")
        else:
            print(f"ok   {test.__name__}")
    if failed:
        print(f"{failed} failed")
        return 1
    print(f"{len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
