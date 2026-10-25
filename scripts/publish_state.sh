#!/usr/bin/env bash
# Replace the feed-state branch with one commit that holds .state/.
# History on main is not touched. A failed push must not print the remote URL.
set -euo pipefail

if [[ "${GITHUB_ACTIONS:-}" != "true" ]]; then
  echo "publish_state: not in GitHub Actions, skipping"
  exit 0
fi

if [[ ! -f .state/state.json ]]; then
  echo "publish_state: no .state/state.json"
  exit 1
fi

if [[ -n "${X_BEARER_TOKEN:-}" ]] && grep -qF -- "$X_BEARER_TOKEN" .state/state.json; then
  echo "publish_state: refusing to commit state that contains X_BEARER_TOKEN"
  exit 1
fi

git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git config user.name "github-actions[bot]"

git branch -D feed-state-publish >/dev/null 2>&1 || true

WT="$(mktemp -d)"
cleanup() {
  git worktree remove --force "$WT" >/dev/null 2>&1 || rm -rf "$WT"
}
trap cleanup EXIT

git worktree add --detach "$WT" HEAD
cd "$WT"
git checkout --orphan feed-state-publish
git rm -rf . >/dev/null
mkdir -p .state
cp -a "${GITHUB_WORKSPACE}/.state/." .state/
find .state -name '*.pyc' -delete
git add -f .state
git commit -m "Update feed state"
git push --force origin HEAD:feed-state
echo "publish_state: pushed feed-state"
