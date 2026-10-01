#!/usr/bin/env bash
# Canonical test runner for bassera-agent. Run this instead of calling
# `pytest` directly to guarantee your local run matches CI behavior.
#
# What this script enforces:
#   * -n 4 xdist workers (CI has 4 cores; -n auto diverges locally)
#   * TZ=UTC, LANG=C.UTF-8, PYTHONHASHSEED=0 (deterministic)
#   * Credential env vars blanked (conftest.py also does this, but this
#     is belt-and-suspenders for anyone running `pytest` outside of
#     our conftest path — e.g. calling pytest on a single file)
#   * Proper venv activation
#
# Usage:
#   scripts/run_tests.sh                     # full suite
#   scripts/run_tests.sh tests/agent/        # one directory
#   scripts/run_tests.sh tests/agent/test_foo.py::TestClass::test_method
#   scripts/run_tests.sh --tb=long -v        # pass-through pytest args

set -euo pipefail

# ── Locate repo root ────────────────────────────────────────────────────────
# Works whether this is the main checkout or a worktree.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Activate venv ───────────────────────────────────────────────────────────
# Prefer a .venv in the current tree, fall back to the main checkout's venv
# (useful for worktrees where we don't always duplicate the venv).
# Windows venvs use Scripts/ instead of bin/ — accept both layouts.
VENV=""
for candidate in "$REPO_ROOT/.venv" "$REPO_ROOT/venv" "$HOME/.bassera/bassera-agent/venv" "$HOME/.wafi/wafi-agent/venv"; do
  if [ -f "$candidate/bin/activate" ] || [ -f "$candidate/Scripts/python.exe" ] || [ -f "$candidate/Scripts/python" ]; then
    VENV="$candidate"
    break
  fi
done

if [ -z "$VENV" ]; then
  echo "error: no virtualenv found in $REPO_ROOT/.venv or $REPO_ROOT/venv" >&2
  exit 1
fi

# Interpreter path inside the venv differs by platform.
if [ -f "$VENV/Scripts/python.exe" ]; then
  PYTHON="$VENV/Scripts/python.exe"
elif [ -f "$VENV/Scripts/python" ]; then
  PYTHON="$VENV/Scripts/python"
else
  PYTHON="$VENV/bin/python"
fi

# ── Ensure pytest-split is installed (required for shard-equivalent runs) ──
if ! "$PYTHON" -c "import pytest_split" 2>/dev/null; then
  if "$PYTHON" -m pip --version >/dev/null 2>&1; then
    echo "→ installing pytest-split into $VENV"
    "$PYTHON" -m pip install --quiet "pytest-split>=0.9,<1"
  elif command -v uv >/dev/null 2>&1; then
    # uv-created venvs don't ship pip (and the documented install path uses
    # uv) — install through uv instead.
    echo "→ installing pytest-split into $VENV via uv"
    VIRTUAL_ENV="$VENV" uv pip install --quiet "pytest-split>=0.9,<1"
  else
    echo "error: pytest-split missing and neither pip nor uv available" >&2
    exit 1
  fi
fi

# ── File-descriptor headroom ────────────────────────────────────────────────
# macOS defaults to a 256-fd soft limit; the gateway suites open many
# sockets/sessions under 4 xdist workers and exhaust it, causing cascading
# "[Errno 24] Too many open files" failures that never appear in CI. The
# api-server / SSE tests have been observed to exceed 1024 under 4 workers,
# so target 4096 (best effort — the hard limit may cap it).
if [ "$(ulimit -n)" -lt 4096 ] 2>/dev/null; then
  ulimit -n 4096 2>/dev/null || {
    [ "$(ulimit -n)" -lt 1024 ] 2>/dev/null && ulimit -n 1024 2>/dev/null || true
  }
fi

# ── Hermetic environment ────────────────────────────────────────────────────
# Mirror what CI does in .github/workflows/tests.yml + what conftest.py does.
# Unset every credential-shaped var currently in the environment.
while IFS='=' read -r name _; do
  case "$name" in
    *_API_KEY|*_TOKEN|*_SECRET|*_PASSWORD|*_CREDENTIALS|*_ACCESS_KEY| \
    *_SECRET_ACCESS_KEY|*_PRIVATE_KEY|*_OAUTH_TOKEN|*_WEBHOOK_SECRET| \
    *_ENCRYPT_KEY|*_APP_SECRET|*_CLIENT_SECRET|*_CORP_SECRET|*_AES_KEY| \
    AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN|FAL_KEY| \
    GH_TOKEN|GITHUB_TOKEN)
      unset "$name"
      ;;
  esac
done < <(env)

# Unset BASSERA_* behavioral vars too — AND their legacy HERMES_*/WAFI_*
# spellings: bassera_constants.apply_legacy_env_aliases() maps any legacy
# var still present into the BASSERA_* namespace at import, so a leftover
# from the developer's shell would otherwise leak into the suite.
_BASSERA_VARS="BASSERA_YOLO_MODE BASSERA_INTERACTIVE BASSERA_QUIET BASSERA_TOOL_PROGRESS \
      BASSERA_TOOL_PROGRESS_MODE BASSERA_MAX_ITERATIONS BASSERA_SESSION_PLATFORM \
      BASSERA_SESSION_CHAT_ID BASSERA_SESSION_CHAT_NAME BASSERA_SESSION_THREAD_ID \
      BASSERA_SESSION_SOURCE BASSERA_SESSION_KEY BASSERA_GATEWAY_SESSION \
      BASSERA_PLATFORM BASSERA_INFERENCE_PROVIDER BASSERA_MANAGED BASSERA_DEV \
      BASSERA_CONTAINER BASSERA_EPHEMERAL_SYSTEM_PROMPT BASSERA_TIMEZONE \
      BASSERA_REDACT_SECRETS BASSERA_BACKGROUND_NOTIFICATIONS BASSERA_EXEC_ASK \
      BASSERA_HOME_MODE BASSERA_CRON_SESSION"
_LEGACY_VARS=""
for _v in $_BASSERA_VARS; do
  _LEGACY_VARS="$_LEGACY_VARS HERMES_${_v#BASSERA_} WAFI_${_v#BASSERA_}"
done
unset $_BASSERA_VARS $_LEGACY_VARS 2>/dev/null || true

# Pin deterministic runtime.
export TZ=UTC
export LANG=C.UTF-8
export LC_ALL=C.UTF-8
export PYTHONHASHSEED=0

# ── Worker count ────────────────────────────────────────────────────────────
# CI uses `-n auto` on ubuntu-latest which gives 4 workers. A 20-core
# workstation with `-n auto` gets 20 workers and exposes test-ordering
# flakes that CI will never see. Pin to 4 so local matches CI.
WORKERS="${BASSERA_TEST_WORKERS:-4}"

# ── Run pytest ──────────────────────────────────────────────────────────────
cd "$REPO_ROOT"

# If the first argument starts with `-` treat all args as pytest flags;
# otherwise treat them as test paths.
ARGS=("$@")

echo "▶ running pytest with $WORKERS workers, hermetic env, in $REPO_ROOT"
echo "  (TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0; all credential env vars unset)"

# `"${ARGS[@]}"` on an empty array is an "unbound variable" error under
# `set -u` on bash < 4.4 (macOS ships bash 3.2), so use the portable
# `${arr[@]+...}` expansion.
# shellcheck disable=SC2068
exec "$PYTHON" -m pytest \
  -o "addopts=" \
  -n "$WORKERS" \
  --ignore=tests/integration \
  -m "not integration" \
  ${ARGS[@]+"${ARGS[@]}"}
# NOTE: tests/e2e IS part of the canonical run (hermetic: scripted LLM client,
# mocked platform adapters). tests/integration remains excluded — those tests
# need live credentials and services (Modal, Daytona, provider APIs).
