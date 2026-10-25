"""URL cleaning: tracking params, affiliate hosts, private-network guards."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "gclsrc",
    "dclid",
    "msclkid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "igsh",
    "mkt_tok",
    "ncid",
    "yclid",
    "vero_id",
    "vero_conv",
    "_ga",
    "_gl",
    "_hsenc",
    "_hsmi",
    "hsa_cam",
    "hsa_grp",
    "hsa_mt",
    "hsa_src",
    "hsa_ad",
    "hsa_acc",
    "hsa_net",
    "hsa_kw",
    "hsa_tgt",
    "hsa_ver",
    "rb_clickid",
    "oly_enc_id",
    "oly_anon_id",
    "ref",
    "ref_src",
    "ref_url",
    "soc_src",
    "soc_trk",
    "ml_subscriber",
    "ml_subscriber_hash",
    "itm_source",
    "itm_medium",
    "itm_campaign",
    "ns_mchannel",
    "ns_source",
    "ns_campaign",
    "ns_linkname",
    "sr_share",
    "taid",
    "share_id",
    "irclickid",
    "irgwc",
    "aff_id",
    "affiliate_id",
    "affiliate",
    "ranmid",
    "raneaid",
    "ransiteid",
    "trk",
    "trk_contact",
    "trk_msg",
    "trk_module",
    "trk_sid",
    "sc_cid",
    "sc_channel",
    "sc_campaign",
    "sc_publisher",
    "sc_content",
    "sc_funnel",
    "sc_country",
    "sc_medium",
    "sc_geo",
}

AMAZON_PARAMS = {
    "tag",
    "linkcode",
    "ascsubtag",
    "camp",
    "creative",
    "creativeasin",
    "linkid",
}

AFFILIATE_SUFFIXES = (
    "shareasale.com",
    "sjv.io",
    "evyy.net",
    "anrdoezrs.net",
    "dpbolvw.net",
    "jdoqocy.com",
    "tkqlhce.com",
    "qksrv.net",
    "avantlink.com",
    "pjtra.com",
    "pntra.com",
    "pntrs.com",
    "pjatr.com",
    "awin1.com",
    "prf.hn",
    "rstyle.me",
    "skimresources.com",
    "redirectingat.com",
    "howl.link",
    "howl.me",
    "shop-links.co",
    "go2cloud.org",
    "impact.com",
    "partnerize.com",
    "linksynergy.com",
    "kqzyfj.com",
    "commission-junction.com",
    "amzn.to",
    "amzn.asia",
    "a.co",
)

TRACKER_SUFFIXES = (
    "doubleclick.net",
    "google-analytics.com",
    "googletagmanager.com",
    "googleadservices.com",
    "googlesyndication.com",
    "scorecardresearch.com",
    "quantserve.com",
    "adnxs.com",
    "adsrvr.org",
    "outbrain.com",
    "taboola.com",
    "moatads.com",
    "demdex.net",
    "omtrdc.net",
    "serving-sys.com",
    "chartbeat.com",
    "chartbeat.net",
    "hotjar.com",
    "mixpanel.com",
    "segment.io",
    "segment.com",
    "newrelic.com",
    "nr-data.net",
    "amazon-adsystem.com",
    "criteo.com",
    "criteo.net",
    "bidswitch.net",
    "casalemedia.com",
    "pubmatic.com",
    "rubiconproject.com",
    "openx.net",
    "mathtag.com",
    "exelator.com",
    "bluekai.com",
    "crwdcntrl.net",
    "rlcdn.com",
    "imrworldwide.com",
    "parsely.com",
    "facebook.net",
    "pixel.wp.com",
    "stats.wp.com",
)


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def host_matches(host: str, suffixes: tuple[str, ...] | frozenset[str]) -> bool:
    host = host.lower().removeprefix("www.").rstrip(".")
    for suffix in suffixes:
        suffix = suffix.lower().removeprefix("www.").rstrip(".")
        if host == suffix or host.endswith("." + suffix):
            return True
    return False


def host_is_amazon(host: str) -> bool:
    host = host.lower().removeprefix("www.").rstrip(".")
    return (
        host in {"amzn.to", "amzn.asia", "a.co"}
        or host.startswith("amazon.")
        or ".amazon." in f".{host}"
    )


def is_affiliate(url: str) -> bool:
    return host_matches(host_of(url), AFFILIATE_SUFFIXES)


def is_tracker_url(url: str) -> bool:
    host = host_of(url)
    if host_matches(host, TRACKER_SUFFIXES):
        return True
    path = ""
    try:
        path = urlsplit(url).path.lower()
    except ValueError:
        return False
    if host.endswith("facebook.com") and (path.startswith("/tr") or "pixel" in path):
        return True
    return False


def unsafe_host(host: str) -> bool:
    """True when the host string itself must never be fetched or linked."""
    host = host.lower().rstrip(".").strip("[]")
    if not host:
        return True
    if host in {"localhost", "metadata.google.internal", "metadata.internal"}:
        return True
    if host.endswith(".local") or host.endswith(".internal") or host.endswith(".localhost"):
        return True
    if re.fullmatch(r"\d+", host):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not ip.is_global


def _tracking_param(name: str, host: str) -> bool:
    key = name.lower()
    if key.startswith("utm_"):
        return True
    if key in TRACKING_PARAMS:
        return True
    if host_is_amazon(host) and key in AMAZON_PARAMS:
        return True
    return False


def clean_url(url: str, rewrites: dict[str, str] | None = None) -> str:
    """Return a tracking-stripped https URL, or "" if the URL should not be kept."""
    raw = (url or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered.startswith(("javascript:", "data:", "file:", "blob:", "about:")):
        return ""
    if raw.startswith("//"):
        raw = "https:" + raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"}:
        return ""
    host = (parts.hostname or "").lower()
    if not host or unsafe_host(host):
        return ""
    rewrite_map = {key.lower(): value.lower() for key, value in (rewrites or {}).items()}
    if host in rewrite_map:
        host = rewrite_map[host]
    elif host.removeprefix("www.") in rewrite_map:
        host = rewrite_map[host.removeprefix("www.")]
    elif host.startswith("www."):
        host = host.removeprefix("www.")
    kept: list[tuple[str, str]] = []
    for key, value in parse_qsl(parts.query, keep_blank_values=False):
        if _tracking_param(key, host):
            continue
        kept.append((key, value))
    kept.sort()
    query = urlencode(kept, doseq=True)
    path = parts.path or ""
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    if path == "/":
        path = ""
    netloc = host
    if parts.port and parts.port not in {80, 443}:
        netloc = f"{host}:{parts.port}"
    return urlunsplit(("https", netloc, path, query, ""))


def canonical_key(url: str, rewrites: dict[str, str] | None = None) -> str:
    return clean_url(url, rewrites)
