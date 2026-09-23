#!/usr/bin/env bash
# Install a fast pre-commit hook for Bassera Agent.
#
# What the hook does on each commit:
#   1. Byte-compiles every staged .py file (catches syntax errors in
#      under a second without importing anything).
#   2. Runs the fast security test subset (approval guards incl. the
#      fuzz suite, write-deny) — ~5 seconds, the tests most likely to
#      catch a dangerous regression in a quick edit.
#
# The hook NEVER replaces the full suite: run scripts/run_tests.sh
# before pushing. Uninstall with:
#   rm .git/hooks/pre-commit
#
# Usage: scripts/install-hooks.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK="$REPO_ROOT/.git/hooks/pre-commit"

# Locate the venv the same way scripts/run_tests.sh does.
VENV=""
for candidate in "$REPO_ROOT/.venv" "$REPO_ROOT/venv" "$HOME/.bassera/bassera-agent/venv"; do
  if [ -f "$candidate/bin/activate" ]; then
    VENV="$candidate"
    break
  fi
done

if [ -z "$VENV" ]; then
  echo "error: no virtualenv found in $REPO_ROOT/.venv or $REPO_ROOT/venv" >&2
  exit 1
fi

mkdir -p "$REPO_ROOT/.git/hooks"

cat > "$HOOK" <<EOF
#!/usr/bin/env bash
# Installed by scripts/install-hooks.sh — fast pre-commit checks.
set -euo pipefail

REPO_ROOT="$REPO_ROOT"
PYTHON="$VENV/bin/python"

fail=0

# 1. Byte-compile staged .py files.
STAGED_PY=\$(git diff --cached --name-only --diff-filter=ACMR -- '*.py')
if [ -n "\$STAGED_PY" ]; then
  TMPDIR_PY=\$(mktemp -d)
  trap 'rm -rf "\$TMPDIR_PY"' EXIT
  # Copy staged content, not working-tree content, so the check matches
  # what is actually being committed.
  while IFS= read -r f; do
    mkdir -p "\$TMPDIR_PY/\$(dirname "\$f")"
    git show ":\$f" > "\$TMPDIR_PY/\$f"
  done <<PYLIST
\$STAGED_PY
PYLIST
  if ! (cd "\$TMPDIR_PY" && find . -name '*.py' -print0 | xargs -0 "\$PYTHON" -m py_compile); then
    echo "pre-commit: staged Python files fail to compile" >&2
    fail=1
  fi
fi

# 2. Fast security subset (full suite: scripts/run_tests.sh).
if [ "\$fail" -eq 0 ]; then
  (cd "\$REPO_ROOT" && TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0 \\
    "\$PYTHON" -m pytest -o "addopts=" -q -p no:cacheprovider \\
    tests/tools/test_approval.py tests/tools/test_write_deny.py \\
    tests/tools/test_approval_fuzz.py) || fail=1
fi

if [ "\$fail" -ne 0 ]; then
  echo "pre-commit: checks failed; commit aborted (use --no-verify to override)" >&2
  exit 1
fi
echo "pre-commit: fast checks passed (run scripts/run_tests.sh before pushing)"
EOF
chmod +x "$HOOK"

echo "installed $HOOK"
echo "  venv: $VENV"
echo "  scope: staged .py compile check + security test subset"
echo "remove with: rm .git/hooks/pre-commit"
