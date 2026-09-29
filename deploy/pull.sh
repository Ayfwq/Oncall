#!/usr/bin/env bash
# Pull the published main branch on the server, then apply an incremental update.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ ! -d .git ]; then
  echo "This directory is not a Git checkout. Clone the repository first (see deploy/README.md)." >&2
  exit 1
fi
if [ ! -f .env ]; then
  echo "Missing server .env. Create it from .env.server.example before deploying." >&2
  exit 1
fi
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "Tracked files have local changes; refusing to overwrite server edits." >&2
  exit 1
fi

git fetch --prune origin main
if ! git merge-base --is-ancestor HEAD origin/main; then
  echo "The server checkout has diverged from origin/main; resolve it before deploying." >&2
  exit 1
fi

old_commit="$(git rev-parse HEAD)"
git merge --ff-only origin/main
new_commit="$(git rev-parse HEAD)"

if [ "$old_commit" = "$new_commit" ]; then
  if [ -f .deploy/checksums/deps.backend ]; then
    echo "Already at ${new_commit}; nothing to deploy."
    exit 0
  fi
  echo "Already at ${new_commit}; completing initial deployment."
else
  echo "Updating ${old_commit} -> ${new_commit}"
fi

bash deploy/update.sh
