"""Shared fixtures for the bassera-agent test suite.

Hermetic-test invariants enforced here (see AGENTS.md for rationale):

1. **No credential env vars.** All provider/credential-shaped env vars
   (ending in _API_KEY, _TOKEN, _SECRET, _PASSWORD, _CREDENTIALS, etc.)
   are unset before every test. Local developer keys cannot leak in.
2. **Isolated BASSERA_HOME.** BASSERA_HOME points to a per-test tempdir so
   code reading ``~/.bassera/*`` via ``get_bassera_home()`` can't see the
   real one. (We do NOT also redirect HOME — that broke subprocesses in
   CI. Code using ``Path.home() / ".bassera"`` instead of the canonical
   ``get_bassera_home()`` is a bug to fix at the callsite.)
3. **Deterministic runtime.** TZ=UTC, LANG=C.UTF-8, PYTHONHASHSEED=0.
4. **No BASSERA_SESSION_* inheritance** — the agent's current gateway
   session must not leak into tests.

These invariants make the local test run match CI closely. Gaps that
remain (CPU count, xdist worker count) are addressed by the canonical
test runner at ``scripts/run_tests.sh``.
"""

import asyncio
import atexit
import importlib.util
import os
import re
import shutil
import signal
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ── Import-time home isolation ─────────────────────────────────────────────
#
# Many project modules freeze BASSERA_HOME into module-level constants at
# import time (bassera_state.DEFAULT_DB_PATH, tools.process_registry
# .CHECKPOINT_PATH, gateway.channel_directory.DIRECTORY_PATH, gateway
# .platforms.base.DOCUMENT_CACHE_DIR, ...). pytest imports this conftest
# BEFORE any test module imports those, so pointing BASSERA_HOME at a
# throwaway directory here makes every frozen constant resolve into that
# directory instead of the developer's real ~/.bassera. Without this, test
# runs leak state.db, processes.json, document caches, gateway state and
# pairing data into the real home (observed in practice).
#
# The per-test hermetic fixture below still redirects BASSERA_HOME to a
# fresh per-test tempdir for everything that resolves the home at RUNTIME;
# this block covers the import-time-frozen half of the codebase.
_TEST_HOME_ROOT = tempfile.mkdtemp(prefix="bassera-import-isolated-")
os.environ["BASSERA_HOME"] = _TEST_HOME_ROOT

# Machine-local gateway locks (gateway/status.py) deliberately live
# OUTSIDE the agent home in production (~/.local/state/bassera/gateway-
# locks): two BASSERA_HOMEs on one machine must not drive the same bot
# token. That also means the suite must isolate them explicitly —
# otherwise xdist workers (which share one machine) collide on the
# identical test identities: the whatsapp connect tests failed
# "session already in use (PID <other worker>)" under -n 4, and the
# runs left lock files in the developer's real state directory
# (observed in practice). Per-process throwaway => per-worker
# isolation; test_status.py's lock tests override it per-test anyway.
os.environ["BASSERA_GATEWAY_LOCK_DIR"] = os.path.join(
    _TEST_HOME_ROOT, "gateway-locks"
)

for _sub in ("sessions", "cron", "memories", "skills", "logs", "plugins", "cache"):
    os.makedirs(os.path.join(_TEST_HOME_ROOT, _sub), exist_ok=True)

atexit.register(shutil.rmtree, _TEST_HOME_ROOT, ignore_errors=True)


# ── Platform-library mocks (discord / telegram / slack) ─────────────────────
#
# The gateway platform tests are written against comprehensive mocks of the
# discord/telegram libraries (they assert on fake classes like
# discord.ForumChannel / telegram ChatType constants). Those mocks must be
# installed ONCE PER WORKER, BEFORE any test module or directory conftest can
# import gateway.platforms.* — this conftest is the only file pytest
# guarantees to import first in every worker.
#
# History: the e2e and gateway conftests each used to install their own
# mocks, gated on IMPORT STATE ("x" in sys.modules) rather than
# installation — so whichever ran first in an xdist worker decided the
# binding, and the e2e conftest's bare MagicMock, installed over the real
# (installed) discord.py, got baked into gateway.platforms.discord at
# import time. Every gateway Discord test in those workers then failed
# (the 50-failure full-suite regression). Rebinding cached modules
# mid-worker (reload/purge) is NOT a fix: it splits class identities
# across two generations (two MessageType enums compare unequal). The
# mocks therefore live in tests/platform_mocks.py and are installed
# here, exactly once, idempotently (sentinel-checked).
def _load_platform_mocks():
    _path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "platform_mocks.py")
    _spec = importlib.util.spec_from_file_location("bassera_platform_mocks", _path)
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["bassera_platform_mocks"] = _mod
    _spec.loader.exec_module(_mod)
    return _mod


_load_platform_mocks().install_platform_mocks()


# ── Credential env-var filter ──────────────────────────────────────────────
#
# Any env var in the current process matching ONE of these patterns is
# unset for every test. Developers' local keys cannot leak into assertions
# about "auto-detect provider when key present".

_CREDENTIAL_SUFFIXES = (
    "_API_KEY",
    "_TOKEN",
    "_SECRET",
    "_PASSWORD",
    "_CREDENTIALS",
    "_ACCESS_KEY",
    "_SECRET_ACCESS_KEY",
    "_PRIVATE_KEY",
    "_OAUTH_TOKEN",
    "_WEBHOOK_SECRET",
    "_ENCRYPT_KEY",
    "_APP_SECRET",
    "_CLIENT_SECRET",
    "_CORP_SECRET",
    "_AES_KEY",
)

# Explicit names (for ones that don't fit the suffix pattern)
_CREDENTIAL_NAMES = frozenset({
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "ANTHROPIC_TOKEN",
    "FAL_KEY",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "NOUS_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
    "XAI_API_KEY",
    "MISTRAL_API_KEY",
    "DEEPSEEK_API_KEY",
    "KIMI_API_KEY",
    "MOONSHOT_API_KEY",
    "GLM_API_KEY",
    "ZAI_API_KEY",
    "MINIMAX_API_KEY",
    "OLLAMA_API_KEY",
    "OPENVIKING_API_KEY",
    "COPILOT_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "BROWSERBASE_API_KEY",
    "FIRECRAWL_API_KEY",
    "PARALLEL_API_KEY",
    "EXA_API_KEY",
    "TAVILY_API_KEY",
    "WANDB_API_KEY",
    "ELEVENLABS_API_KEY",
    "HONCHO_API_KEY",
    "MEM0_API_KEY",
    "SUPERMEMORY_API_KEY",
    "RETAINDB_API_KEY",
    "HINDSIGHT_API_KEY",
    "HINDSIGHT_LLM_API_KEY",
    "TINKER_API_KEY",
    "DAYTONA_API_KEY",
    "TWILIO_AUTH_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "DISCORD_BOT_TOKEN",
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "MATTERMOST_TOKEN",
    "MATRIX_ACCESS_TOKEN",
    "MATRIX_PASSWORD",
    "MATRIX_RECOVERY_KEY",
    "HASS_TOKEN",
    "EMAIL_PASSWORD",
    "BLUEBUBBLES_PASSWORD",
    "FEISHU_APP_SECRET",
    "FEISHU_ENCRYPT_KEY",
    "FEISHU_VERIFICATION_TOKEN",
    "DINGTALK_CLIENT_SECRET",
    "QQ_CLIENT_SECRET",
    "QQ_STT_API_KEY",
    "WECOM_SECRET",
    "WECOM_CALLBACK_CORP_SECRET",
    "WECOM_CALLBACK_TOKEN",
    "WECOM_CALLBACK_ENCODING_AES_KEY",
    "WEIXIN_TOKEN",
    "MODAL_TOKEN_ID",
    "MODAL_TOKEN_SECRET",
    "TERMINAL_SSH_KEY",
    "SUDO_PASSWORD",
    "GATEWAY_PROXY_KEY",
    "API_SERVER_KEY",
    "TOOL_GATEWAY_USER_TOKEN",
    "TELEGRAM_WEBHOOK_SECRET",
    "WEBHOOK_SECRET",
    "AI_GATEWAY_API_KEY",
    "VOICE_TOOLS_OPENAI_KEY",
    "BROWSER_USE_API_KEY",
    "CUSTOM_API_KEY",
    "GATEWAY_PROXY_URL",
    "GEMINI_BASE_URL",
    "OPENAI_BASE_URL",
    "OPENROUTER_BASE_URL",
    "OLLAMA_BASE_URL",
    "GROQ_BASE_URL",
    "XAI_BASE_URL",
    "AI_GATEWAY_BASE_URL",
    "ANTHROPIC_BASE_URL",
})


def _looks_like_credential(name: str) -> bool:
    """True if env var name matches a credential-shaped pattern."""
    if name in _CREDENTIAL_NAMES:
        return True
    return any(name.endswith(suf) for suf in _CREDENTIAL_SUFFIXES)


# BASSERA_* vars that change test behavior by being set. Unset all of these
# unconditionally — individual tests that need them set do so explicitly.
_BASSERA_BEHAVIORAL_VARS = frozenset({
    "BASSERA_YOLO_MODE",
    "BASSERA_INTERACTIVE",
    "BASSERA_QUIET",
    "BASSERA_TOOL_PROGRESS",
    "BASSERA_TOOL_PROGRESS_MODE",
    "BASSERA_MAX_ITERATIONS",
    "BASSERA_SESSION_PLATFORM",
    "BASSERA_SESSION_CHAT_ID",
    "BASSERA_SESSION_CHAT_NAME",
    "BASSERA_SESSION_THREAD_ID",
    "BASSERA_SESSION_SOURCE",
    "BASSERA_SESSION_KEY",
    "BASSERA_GATEWAY_SESSION",
    "BASSERA_PLATFORM",
    "BASSERA_INFERENCE_PROVIDER",
    "BASSERA_MANAGED",
    "BASSERA_DEV",
    "BASSERA_CONTAINER",
    "BASSERA_EPHEMERAL_SYSTEM_PROMPT",
    "BASSERA_TIMEZONE",
    "BASSERA_REDACT_SECRETS",
    "BASSERA_BACKGROUND_NOTIFICATIONS",
    "BASSERA_EXEC_ASK",
    "BASSERA_HOME_MODE",
    # Terminal backend selection — determines tool availability
    # (check_terminal_requirements). Some tests select modal/docker/ssh
    # backends; without clearing, a leak makes terminal+file tools
    # "unavailable" for every later test in the worker.
    "TERMINAL_ENV",
    "TERMINAL_CWD",
    "TERMINAL_TIMEOUT",
    "TERMINAL_LIFETIME_SECONDS",
    "BROWSER_CDP_URL",
    "CAMOFOX_URL",
})


@pytest.fixture(autouse=True)
def _hermetic_environment(tmp_path, monkeypatch):
    """Blank out all credential/behavioral env vars so local and CI match.

    Also redirects HOME and BASSERA_HOME to per-test tempdirs so code that
    reads ``~/.bassera/*`` can't touch the real one, and pins TZ/LANG so
    datetime/locale-sensitive tests are deterministic.
    """
    # 1. Blank every credential-shaped env var that's currently set.
    for name in list(os.environ.keys()):
        if _looks_like_credential(name):
            monkeypatch.delenv(name, raising=False)

    # 2. Blank behavioral BASSERA_* vars that could change test semantics.
    for name in _BASSERA_BEHAVIORAL_VARS:
        monkeypatch.delenv(name, raising=False)

    # 2b. Blank BASSERA_HOME so a developer's Bassera home alias can't
    #     override the isolated BASSERA_HOME we set in step 3.
    monkeypatch.delenv("BASSERA_HOME", raising=False)

    # 3. Redirect BASSERA_HOME to a per-test tempdir. Code that reads
    #    ``~/.bassera/*`` via ``get_bassera_home()`` now gets the tempdir.
    #
    #    NOTE: We do NOT also redirect HOME. Doing so broke CI because
    #    some tests (and their transitive deps) spawn subprocesses that
    #    inherit HOME and expect it to be stable. If a test genuinely
    #    needs HOME isolated, it should set it explicitly in its own
    #    fixture. Any code in the codebase reading ``~/.bassera/*`` via
    #    ``Path.home() / ".bassera"`` instead of ``get_bassera_home()``
    #    is a bug to fix at the callsite.
    fake_bassera_home = tmp_path / "bassera_test"
    fake_bassera_home.mkdir()
    (fake_bassera_home / "sessions").mkdir()
    (fake_bassera_home / "cron").mkdir()
    (fake_bassera_home / "memories").mkdir()
    (fake_bassera_home / "skills").mkdir()
    monkeypatch.setenv("BASSERA_HOME", str(fake_bassera_home))

    # 4. Deterministic locale / timezone / hashseed. CI runs in UTC with
    #    C.UTF-8 locale; local dev often doesn't. Pin everything.
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    monkeypatch.setenv("PYTHONHASHSEED", "0")

    # 4b. Disable AWS IMDS lookups. Without this, any test that ends up
    #     calling has_aws_credentials() / resolve_aws_auth_env_var()
    #     (e.g. provider auto-detect, status command, cron run_job) burns
    #     ~2s waiting for the metadata service at 169.254.169.254 to time
    #     out. Tests don't run on EC2 — IMDS is always unreachable here.
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_METADATA_SERVICE_TIMEOUT", "1")
    monkeypatch.setenv("AWS_METADATA_SERVICE_NUM_ATTEMPTS", "1")

    # 5. Reset plugin singleton so tests don't leak plugins from
    #    ~/.bassera/plugins/ (which, per step 3, is now empty — but the
    #    singleton might still be cached from a previous test).
    try:
        import bassera_cli.plugins as _plugins_mod
        monkeypatch.setattr(_plugins_mod, "_plugin_manager", None)
    except Exception:
        pass


# Backward-compat alias — old tests reference this fixture name. Keep it
# as a no-op wrapper so imports don't break.
@pytest.fixture(autouse=True)
def _isolate_bassera_home(_hermetic_environment):
    """Alias preserved for any test that yields this name explicitly."""
    return None


@pytest.fixture()
def tmp_dir(tmp_path):
    """Provide a temporary directory that is cleaned up automatically."""
    return tmp_path


@pytest.fixture()
def mock_config():
    """Return a minimal bassera config dict suitable for unit tests."""
    return {
        "model": "test/mock-model",
        "toolsets": ["terminal", "file"],
        "max_turns": 10,
        "terminal": {
            "backend": "local",
            "cwd": "/tmp",
            "timeout": 30,
        },
        "compression": {"enabled": False},
        "memory": {"memory_enabled": False, "user_profile_enabled": False},
        "command_allowlist": [],
    }


# ── Global test timeout ─────────────────────────────────────────────────────
# Kill any individual test that takes longer than 30 seconds.
# Prevents hanging tests (subprocess spawns, blocking I/O) from stalling the
# entire test suite.

def _timeout_handler(signum, frame):
    raise TimeoutError("Test exceeded 30 second timeout")

@pytest.fixture(autouse=True)
def _ensure_current_event_loop(request):
    """Provide a default event loop for sync tests that call get_event_loop().

    Python 3.11+ no longer guarantees a current loop for plain synchronous tests.
    A number of gateway tests still use asyncio.get_event_loop().run_until_complete(...).
    Ensure they always have a usable loop without interfering with pytest-asyncio's
    own loop management for @pytest.mark.asyncio tests.
    """
    if request.node.get_closest_marker("asyncio") is not None:
        yield
        return

    try:
        # get_event_loop() on a policy with no set loop emits a
        # DeprecationWarning on 3.12/3.13 (and the same call is what the
        # production code migrated away from). The warning is expected
        # here — this fixture exists precisely to CREATE a loop when
        # there is none — so silence it instead of polluting the
        # suite's warning summary.
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            loop = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        loop = None

    created = loop is None or loop.is_closed()
    if created:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    try:
        yield
    finally:
        if created and loop is not None:
            try:
                loop.close()
            finally:
                asyncio.set_event_loop(None)


@pytest.fixture(autouse=True)
def _enforce_test_timeout():
    """Kill any individual test that takes longer than 30 seconds.
    SIGALRM is Unix-only; skip on Windows."""
    if sys.platform == "win32":
        yield
        return
    old = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(30)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, old)
