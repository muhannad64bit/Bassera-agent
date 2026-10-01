#!/usr/bin/env bash
# Worktree integrity check — detect tracked files whose content silently
# drifted away from HEAD.
#
# WHY THIS EXISTS: twice during the 2026-09 hardening sessions, a tracked
# file (agent/prompt_builder.py) was found reverted to a PRE-REBRAND copy
# in the working tree — content that matched no recent git state. No git
# mechanism explained it (no stash residue, no index flags, no fsmonitor);
# the likely vector is an external editor/IDE (its buffer restore, a
# local-history restore, or an AI-assistant plugin) writing stale content
# into the checkout. One revert broke imports loudly; a revert in a
# less-imported file (docs, tests, config) would have been silent.
#
# Usage:
#   scripts/check_worktree_integrity.sh             # exit 2 on drift
#   scripts/check_worktree_integrity.sh --warn-only # always exit 0
#
# Drift vs HEAD on tracked files is NORMAL while developing — the point of
# this check is to surface UNEXPECTED drift at natural checkpoints (before
# commits, after suspicious events) instead of relying on a broken test to
# notice.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

WARN_ONLY=0
if [ "${1:-}" = "--warn-only" ]; then
  WARN_ONLY=1
fi

# Unstaged worktree modifications on tracked files: porcelain column 2
# holds the worktree state (' M', ' D', ' T'). Staged changes (column 1)
# are about to be committed and are intentional by definition.
DRIFT="$(git status --porcelain | grep -E '^ [MDT]' || true)"

if [ -z "$DRIFT" ]; then
  echo "worktree integrity: OK (no unstaged drift on tracked files)"
  exit 0
fi

COUNT="$(printf '%s\n' "$DRIFT" | wc -l | tr -d ' ')"
echo "worktree integrity: $COUNT tracked file(s) differ from HEAD without being staged:"
printf '%s\n' "$DRIFT" | sed 's/^/   /'
echo "If any of these were NOT edited by you, an external process is writing"
echo "into this checkout. Run 'git diff <file>' and, if stale,"
echo "'git checkout HEAD -- <file>'."
if [ "$WARN_ONLY" -eq 1 ]; then
  exit 0
fi
exit 2
