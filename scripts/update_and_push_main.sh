#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

REMOTE="${1:-origin}"
BRANCH="${2:-main}"
MESSAGE="${3:-chore: sync local updates}"

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$CURRENT_BRANCH" != "$BRANCH" ]]; then
  git checkout "$BRANCH"
fi

git fetch "$REMOTE" "$BRANCH"
git add -A

if git diff --cached --quiet; then
  echo "No changes to commit."
else
  git commit -m "$MESSAGE"
fi

git pull --rebase --autostash "$REMOTE" "$BRANCH"

git push "$REMOTE" "$BRANCH"

echo "Push complete: $REMOTE/$BRANCH"
