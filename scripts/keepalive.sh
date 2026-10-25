#!/usr/bin/env bash
# GitHub disables scheduled workflows after 60 days without repository activity.
# If the default branch has been quiet for 30 days, push one small commit.
set -euo pipefail

if [[ "${GITHUB_ACTIONS:-}" != "true" ]]; then
  echo "keepalive: not in GitHub Actions, skipping"
  exit 0
fi

threshold=$((30 * 24 * 3600))
now=$(date +%s)
last=$(git log -1 --format=%ct HEAD)
age=$((now - last))
if (( age < threshold )); then
  echo "keepalive: last commit is ${age}s old, skip"
  exit 0
fi

git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git config user.name "github-actions[bot]"
mkdir -p .github
date -u +%Y-%m-%dT%H:%M:%SZ > .github/keepalive
git add .github/keepalive
git commit -m "chore: keep scheduled workflows active"
ref="${GITHUB_REF_NAME:-main}"
git push origin "HEAD:${ref}"
echo "keepalive: pushed to ${ref}"
