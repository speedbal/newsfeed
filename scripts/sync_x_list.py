#!/usr/bin/env python3
"""Add configured accounts to the X list. One-time / when sources.json changes.

The list already exists. This script does not run in GitHub Actions.

    export X_BEARER_TOKEN=...   # user token of the list owner
    python scripts/sync_x_list.py            # add anyone missing
    python scripts/sync_x_list.py --dry-run
    python scripts/sync_x_list.py --prune    # also remove members not in sources.json
    python scripts/sync_x_list.py --create   # make a new private list instead

X_LIST_ID overrides the x_list_id in sources.json. App-only bearer tokens can
read a public list but cannot edit a private one.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.x.com/2"


def request(method: str, url: str, token: str, body: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("User-Agent", "PersonalNewsfeed/1.0")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read().decode()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise SystemExit(f"{method} {urllib.parse.urlparse(url).path} failed: HTTP {exc.code} {detail}") from exc


def members(list_id: str, token: str) -> dict[str, str]:
    found: dict[str, str] = {}
    token_page = ""
    while True:
        query = {"max_results": "100", "user.fields": "username"}
        if token_page:
            query["pagination_token"] = token_page
        url = f"{API}/lists/{list_id}/members?{urllib.parse.urlencode(query)}"
        _status, payload = request("GET", url, token)
        for user in payload.get("data") or []:
            found[str(user["id"])] = user.get("username") or ""
        token_page = (payload.get("meta") or {}).get("next_token") or ""
        if not token_page:
            return found


def create_list(token: str) -> str:
    _status, payload = request(
        "POST",
        f"{API}/lists",
        token,
        {
            "name": "Personal newsfeed",
            "description": "Accounts read by the personal news feed.",
            "private": True,
        },
    )
    list_id = str((payload.get("data") or {}).get("id") or "")
    if not list_id:
        raise SystemExit(f"create list returned no id: {payload}")
    print(f"created private list {list_id}")
    return list_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--prune", action="store_true")
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--list-id", default="")
    args = parser.parse_args()

    token = os.environ.get("X_BEARER_TOKEN", "").strip()
    if not token:
        print("Set X_BEARER_TOKEN to the list owner's user token.", file=sys.stderr)
        return 1
    config = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
    accounts = config["x_accounts"]
    list_id = args.list_id or os.environ.get("X_LIST_ID", "").strip() or str(config.get("x_list_id") or "")
    if args.create:
        if args.dry_run:
            print(f"would create a private list and add {len(accounts)} accounts")
            return 0
        list_id = create_list(token)
    if not list_id.isdigit():
        print("No numeric list id. Pass --list-id, set X_LIST_ID, or use --create.", file=sys.stderr)
        return 1

    current = members(list_id, token)
    wanted = {str(account["id"]): account["handle"] for account in accounts}
    missing = [user_id for user_id in wanted if user_id not in current]
    extra = [user_id for user_id in current if user_id not in wanted]
    print(f"list {list_id}: {len(current)} members, {len(missing)} to add, {len(extra)} not in sources.json")
    for user_id in missing:
        print(f"add @{wanted[user_id]} ({user_id})")
        if args.dry_run:
            continue
        request("POST", f"{API}/lists/{list_id}/members", token, {"user_id": user_id})
        time.sleep(0.5)
    if args.prune:
        for user_id in extra:
            print(f"remove @{current[user_id]} ({user_id})")
            if args.dry_run:
                continue
            request("DELETE", f"{API}/lists/{list_id}/members/{user_id}", token)
            time.sleep(0.5)
    if args.create:
        print(f"Put this id in sources.json or the X_LIST_ID variable: {list_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
