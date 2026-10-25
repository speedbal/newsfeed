"""HTTP fetches that refuse private networks and tracking redirects."""

from __future__ import annotations

import ipaddress
import shutil
import socket
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from feedbuild.urls import unsafe_host
from feedbuild.util import safe_err

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

REDIRECTS = {301, 302, 303, 307, 308}

_dns_lock = threading.Lock()
_dns_cache: dict[str, bool] = {}


@dataclass
class Fetch:
    status: int = 0
    body: bytes = b""
    text: str = ""
    final_url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    error: str = ""


def _ip_is_public(raw: str) -> bool:
    try:
        ip = ipaddress.ip_address(raw.split("%", 1)[0])
    except ValueError:
        return False
    return bool(ip.is_global)


def host_is_public(host: str) -> bool:
    host = host.lower().rstrip(".").strip("[]")
    if not host or unsafe_host(host):
        return False
    with _dns_lock:
        cached = _dns_cache.get(host)
    if cached is not None:
        return cached
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    ok = bool(infos) and all(_ip_is_public(info[4][0]) for info in infos)
    if ok or infos:
        with _dns_lock:
            _dns_cache[host] = ok
    return ok


def _looks_like_challenge(fetched: Fetch) -> bool:
    """Cloudflare and similar gates return 403 HTML to httpx and 200 to curl."""
    if fetched.status != 403 or fetched.error:
        return False
    sample = (fetched.text or "")[:6000].lower()
    if "just a moment" in sample or "cf-browser-verification" in sample or "attention required" in sample:
        return True
    ctype = (fetched.headers.get("content-type") or "").lower()
    return "html" in ctype and len(fetched.body) < 80_000


def _parse_curl_headers(raw: bytes) -> tuple[int, dict[str, str]]:
    status = 0
    header_map: dict[str, str] = {}
    text = raw.decode("iso-8859-1", errors="replace")
    for block in text.split("\r\n\r\n"):
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines or not lines[0].startswith("HTTP/"):
            continue
        parts = lines[0].split()
        if len(parts) >= 2 and parts[1].isdigit():
            status = int(parts[1])
        header_map = {}
        for line in lines[1:]:
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            header_map[key.strip().lower()] = value.strip()
    return status, header_map


def _curl_once(url: str, headers: dict[str, str], timeout: float, max_bytes: int) -> Fetch | None:
    if shutil.which("curl") is None:
        return None
    header_args: list[str] = []
    for key, value in headers.items():
        if any(char in f"{key}{value}" for char in "\r\n\x00"):
            continue
        header_args.extend(["-H", f"{key}: {value}"])
    try:
        with tempfile.TemporaryDirectory() as tmp:
            body_path = Path(tmp) / "body"
            hdr_path = Path(tmp) / "hdr"
            cmd = [
                "curl",
                "-sS",
                "--max-redirs",
                "0",
                "--proto",
                "=https,http",
                "--proto-redir",
                "=https,http",
                "-D",
                str(hdr_path),
                "-o",
                str(body_path),
                "--max-time",
                str(max(1, int(timeout))),
                *header_args,
                url,
            ]
            subprocess.run(cmd, check=False, timeout=timeout + 5, capture_output=True)
            raw_headers = hdr_path.read_bytes() if hdr_path.exists() else b""
            body = body_path.read_bytes() if body_path.exists() else b""
    except (OSError, subprocess.TimeoutExpired):
        return None
    status, header_map = _parse_curl_headers(raw_headers)
    if status == 0:
        return None
    body = body[:max_bytes]
    encoding = "utf-8"
    ctype = header_map.get("content-type") or ""
    if "charset=" in ctype.lower():
        encoding = ctype.lower().split("charset=", 1)[1].split(";", 1)[0].strip() or encoding
    return Fetch(
        status=status,
        body=body,
        text=body.decode(encoding, errors="replace"),
        final_url=url,
        headers=header_map,
    )


def fetch_url(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    timeout: float,
    max_bytes: int = 2_000_000,
) -> Fetch:
    current = url
    seen: set[str] = set()
    for _ in range(6):
        if current in seen:
            return Fetch(error="redirect loop", final_url=current)
        seen.add(current)
        try:
            parts = urlsplit(current)
        except ValueError:
            return Fetch(error="bad url", final_url=current)
        host = (parts.hostname or "").lower()
        if parts.scheme not in {"http", "https"} or not host_is_public(host):
            return Fetch(error=f"blocked host {host or current}", final_url=current)
        try:
            with client.stream(
                "GET",
                current,
                headers=headers,
                timeout=timeout,
                follow_redirects=False,
            ) as response:
                status = response.status_code
                header_map = {key.lower(): value for key, value in response.headers.items()}
                if status in REDIRECTS:
                    location = header_map.get("location")
                    if not location:
                        return Fetch(status=status, error="redirect without location", final_url=current)
                    current = urljoin(current, location)
                    continue
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) >= max_bytes:
                        break
                encoding = response.encoding or "utf-8"
                body = bytes(data)
                text = body.decode(encoding, errors="replace")
                fetched = Fetch(
                    status=status,
                    body=body,
                    text=text,
                    final_url=str(response.url),
                    headers=header_map,
                )
                if not _looks_like_challenge(fetched):
                    return fetched
                alternate = _curl_once(current, headers, timeout, max_bytes)
                if alternate is None or alternate.status == 0:
                    return fetched
                if alternate.status in REDIRECTS:
                    location = alternate.headers.get("location")
                    if not location:
                        return alternate
                    current = urljoin(current, location)
                    continue
                return alternate
        except httpx.HTTPError as exc:
            return Fetch(error=safe_err(exc), final_url=current)
    return Fetch(error="too many redirects", final_url=current)
