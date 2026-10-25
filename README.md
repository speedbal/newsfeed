# Feed

A personal page for tech, AI, and crypto. It is static HTML, rebuilt every 15 minutes by GitHub Actions, and published on GitHub Pages. There are no ads, no trackers, no third-party scripts, and no LLM calls.

After the first deploy the page is [https://speedbal.github.io/newsfeed/](https://speedbal.github.io/newsfeed/).

Stories are newest first, for about the last three days. Tabs: All, Tech, AI, Crypto, and X. A card shows the source, age, and a three-line summary. Open it in place for the article text and images, or the full post on X. Read and unread state stays in this browser (`localStorage`). The same story from two feeds is shown once.

## One-time GitHub settings

Scheduled runs start only after this workflow is on the default branch (`main`).

1. **Settings → Pages → Build and deployment → Source: GitHub Actions.** Save.
2. **Settings → Actions → General → Workflow permissions → Read and write permissions.** Save. The workflow asks for `contents: write` so it can update the `feed-state` branch and, rarely, a keepalive commit. The repository setting is the ceiling on that token.
3. **Settings → Secrets and variables → Actions → New repository secret.** Name: `X_BEARER_TOKEN`. Value: your X API token. If the secret is missing, the site still builds from RSS.
4. Optional. **Variables → `X_LIST_ID`** overrides the list id in `sources.json` (`2114366436562928021`). The id is not secret. Leave the variable unset to use the file.
5. **Actions → Build feed → Run workflow** once, so you do not have to wait for the quarter-hour cron.

`main` does not need branch protection that blocks the Actions bot. The keepalive commit is pushed to `main` only when that branch has been quiet for 30 days. GitHub turns scheduled workflows off after 60 days without repository activity, and workflow runs themselves do not count.

## X: requests, the list, and the token

Posts come from one list, `GET /2/lists/:id/tweets`, not from 59 separate timelines.

- A normal run is **one request**, with `since_id` set to the newest id stored last time. A quiet 15 minutes often returns no posts and still costs that one read.
- If X rejects or ignores `since_id` (the list endpoint has not always accepted it), the run still does **one request** for the newest page and drops posts already saved. It does not walk older pages on later runs.
- A cold start, or a burst of more than 100 new posts when `since_id` works, may use more pages: at most **2** once caught up, and at most **5** on the first backfill (500 posts). It stops at the 3-day window.
- Retweets and replies are discarded after the response. They are not extra requests. Adding a person to the list does not add a request.
- RSS and article downloads are ordinary HTTP, not X API usage.

X's current bill is whatever the developer console charges for that read. This repo cannot see the price. `since_id` plus one list is what keeps the count flat.

The list is private and already has all 59 accounts. An **app-only** bearer token can read a **public** list. For this private list, `X_BEARER_TOKEN` has to be a token for the account that owns the list, sent the same way (`Authorization: Bearer …`). If X returns 403 or 404, the build logs it, keeps any posts still inside the saved window, and publishes RSS anyway.

App-only bearer tokens do not expire. OAuth 2.0 user tokens usually expire in about two hours, which is a poor fit for an unattended job. The durable choices are: make the list public and use the app-only bearer (the id stays the same), or put a non-expiring user token in the secret. The build does not refresh tokens.

When you add a handle, put it in `sources.json` and add them to the list or their posts will not show up:

```bash
export X_BEARER_TOKEN=...   # list owner's user token
python scripts/sync_x_list.py --dry-run
python scripts/sync_x_list.py
python scripts/sync_x_list.py --prune   # remove members you deleted from sources.json
```

`python scripts/sync_x_list.py --create` makes a new private list and prints an id. You can also create the list on x.com and copy the id from `x.com/i/lists/ID`.

## Add or remove sources

Edit [`sources.json`](sources.json). The next build on `main` picks it up (push to `main`, or wait for cron).

An RSS entry:

```json
{
  "name": "Example",
  "url": "https://example.com/feed.xml",
  "category": "tech",
  "full_text": false,
  "extract": true,
  "notes": "optional, for you"
}
```

`category` is `tech`, `ai`, or `crypto`. `full_text` is `true` when the feed already contains the article, `"partial"` when it has a lede, or `false`. `extract` downloads the article page when the feed is not the full text. Set `extract` to `false` for sites that block scripts (OpenAI, The Block, Blockworks are already set that way); the card keeps the feed summary.

An X account:

```json
{
  "handle": "someone",
  "id": "123",
  "name": "Someone",
  "category": "tech_ai",
  "topics": ["ai"]
}
```

`topics` chooses the Tech, AI, and Crypto tabs (`tech`, `ai`, `crypto`). The X tab shows every post. `category` is `tech_ai` or `crypto`. Delete an object to drop that source. User ids belong in quotes so they are not rounded.

Entries tagged Sponsored, Partner Content, Press Release, Deals, Verge Shopping, or Shopping are skipped. `utm_*` and other click ids are stripped. Known affiliate links are removed. `vitalik.ca` links are rewritten to `vitalik.eth.limo`.

## What the workflow stores

Seen RSS items, X posts, the list cursor, and extracted articles live on the `feed-state` branch. Each run force-pushes **one** commit there, so `main` does not get a commit every 15 minutes. Do not make `feed-state` the default branch. Article text is cached by URL and dropped outside the rolling window.

One broken feed is logged and skipped. The Actions job summary lists failures.

## Local build

RSS only, unless `X_BEARER_TOKEN` is set in the environment.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m feedbuild.selftest
python -m feedbuild
python -m http.server -d dist 8000
```

Open http://127.0.0.1:8000 . `X_LIST_ID` overrides the list id the same way the Actions variable does.

`curl` is optional. A few sites answer the Python client with a bot check and a normal browser (or curl) with the feed. When that happens the build retries that one URL with curl, still without following redirects itself.
