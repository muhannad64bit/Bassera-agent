"""Shared platform-library mocks for the test suite.

Single source of truth for the ``telegram`` / ``discord`` / ``slack_bolt``
mocks. The gateway platform tests are WRITTEN against the comprehensive
discord/telegram mocks (they assert on the fake classes defined here),
so those mocks must be installed in every worker that runs them — even
when the real libraries are installed.

WHY THIS MODULE EXISTS (the 50-failure regression): the e2e and gateway
conftests each used to install their OWN mocks with an
``"x" in sys.modules``-based gate. That gate checks IMPORT STATE, not
INSTALLATION, so whichever conftest ran first in an xdist worker decided
the binding — and the e2e conftest's bare MagicMock, installed over the
real (installed) discord.py, got baked into ``gateway.platforms.discord``
at import time. Per-file patching could not undo the bindings, and every
gateway Discord test in those workers failed. Worse, any attempt to fix
it by reloading cached production modules mid-worker splits class
identities (two generations of ``MessageType`` compare unequal even with
identical values).

The contract now: the ROOT conftest (``tests/conftest.py``) installs
these mocks exactly once per worker, before any test module or directory
conftest can import ``gateway.platforms.*``. Directory conftests only
re-assert installation (idempotent via the ``BASSERA_PLATFORM_MOCK``
sentinel). Nothing rebinds anything mid-worker.

slack_bolt is different: gateway slack tests run against the REAL
library, so the slack mock is only installed when slack is NOT
installed (environments without the platform extras).
"""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

MOCK_SENTINEL = "BASSERA_PLATFORM_MOCK"
MOCK_VERSION = "1"


def _is_shared_mock(entry) -> bool:
    return entry is not None and getattr(entry, MOCK_SENTINEL, None) == MOCK_VERSION


class FakeAllowedMentions:
    """Stand-in for ``discord.AllowedMentions`` that stores its kwargs.

    ``gateway.platforms.discord._build_allowed_mentions`` constructs it
    with the four boolean flags and the tests assert on them; a bare
    MagicMock attribute would return MagicMocks from the constructor
    and fail those assertions no matter what the env vars say.
    """

    def __init__(self, *, everyone=True, roles=True, users=True, replied_user=True):
        self.everyone = everyone
        self.roles = roles
        self.users = users
        self.replied_user = replied_user

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"AllowedMentions(everyone={self.everyone}, roles={self.roles}, "
            f"users={self.users}, replied_user={self.replied_user})"
        )


def ensure_telegram_mock() -> None:
    """Install the comprehensive telegram mock (idempotent, overwrite).

    Covers every attribute any test file needs: the ChatType/ParseMode
    constants, real exception classes for ``except`` clauses, and the
    ext/request names the adapters import at module level.
    """
    if _is_shared_mock(sys.modules.get("telegram")):
        return

    mod = MagicMock()
    setattr(mod, MOCK_SENTINEL, MOCK_VERSION)
    mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
    mod.constants.ParseMode.MARKDOWN = "Markdown"
    mod.constants.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    mod.constants.ParseMode.HTML = "HTML"
    mod.constants.ChatType.PRIVATE = "private"
    mod.constants.ChatType.GROUP = "group"
    mod.constants.ChatType.SUPERGROUP = "supergroup"
    mod.constants.ChatType.CHANNEL = "channel"

    # Real exception classes so ``except (NetworkError, ...)`` clauses
    # in production code don't blow up with TypeError.
    mod.error.NetworkError = type("NetworkError", (OSError,), {})
    mod.error.TimedOut = type("TimedOut", (OSError,), {})
    mod.error.BadRequest = type("BadRequest", (Exception,), {})
    mod.error.Forbidden = type("Forbidden", (Exception,), {})
    mod.error.InvalidToken = type("InvalidToken", (Exception,), {})
    mod.error.RetryAfter = type("RetryAfter", (Exception,), {"retry_after": 1})
    mod.error.Conflict = type("Conflict", (Exception,), {})

    # Update.ALL_TYPES used in start_polling()
    mod.Update = MagicMock()
    mod.Update.ALL_TYPES = []

    mod.Bot = MagicMock
    mod.ext.Application = MagicMock()
    mod.ext.Application.builder = MagicMock
    mod.ext.MessageHandler = MagicMock
    mod.ext.CommandHandler = MagicMock
    mod.ext.filters = MagicMock()
    mod.request.HTTPXRequest = MagicMock

    for name in (
        "telegram",
        "telegram.ext",
        "telegram.ext.filters",
        "telegram.constants",
        "telegram.request",
        "telegram.error",
    ):
        sys.modules[name] = mod
    sys.modules["telegram.error"] = mod.error


def ensure_discord_mock() -> None:
    """Install the comprehensive discord mock (idempotent, overwrite).

    Includes the fake channel types production ``isinstance`` checks
    rely on, the slash-command scaffolding, and the kwargs-storing
    FakeAllowedMentions.
    """
    if _is_shared_mock(sys.modules.get("discord")):
        return

    discord_mod = MagicMock()
    setattr(discord_mod, MOCK_SENTINEL, MOCK_VERSION)
    discord_mod.Intents.default.return_value = MagicMock()
    discord_mod.Client = MagicMock
    discord_mod.File = MagicMock
    discord_mod.Embed = MagicMock
    discord_mod.Message = MagicMock
    discord_mod.AllowedMentions = FakeAllowedMentions
    discord_mod.DMChannel = type("DMChannel", (), {})
    discord_mod.Thread = type("Thread", (), {})
    discord_mod.ForumChannel = type("ForumChannel", (), {})
    discord_mod.Interaction = object
    discord_mod.opus = SimpleNamespace(is_loaded=lambda: True)
    discord_mod.ui = SimpleNamespace(
        View=object,
        button=lambda *a, **k: (lambda fn: fn),
        Button=object,
    )
    discord_mod.ButtonStyle = SimpleNamespace(
        success=1, primary=2, secondary=2, danger=3,
        green=1, grey=2, blurple=2, red=3,
    )
    discord_mod.Color = SimpleNamespace(
        orange=lambda: 1, green=lambda: 2, blue=lambda: 3,
        red=lambda: 4, purple=lambda: 5,
    )

    # app_commands — needed by _register_slash_commands auto-registration
    class _FakeGroup:
        def __init__(self, *, name, description, parent=None):
            self.name = name
            self.description = description
            self.parent = parent
            self._children: dict = {}
            if parent is not None:
                parent.add_command(self)

        def add_command(self, cmd):
            self._children[cmd.name] = cmd

    class _FakeCommand:
        def __init__(self, *, name, description, callback, parent=None):
            self.name = name
            self.description = description
            self.callback = callback
            self.parent = parent

    discord_mod.app_commands = SimpleNamespace(
        describe=lambda **kwargs: (lambda fn: fn),
        choices=lambda **kwargs: (lambda fn: fn),
        Choice=lambda **kwargs: SimpleNamespace(**kwargs),
        Group=_FakeGroup,
        Command=_FakeCommand,
    )

    ext_mod = MagicMock()
    commands_mod = MagicMock()
    commands_mod.Bot = MagicMock
    ext_mod.commands = commands_mod

    sys.modules["discord"] = discord_mod
    sys.modules["discord.ext"] = ext_mod
    sys.modules["discord.ext.commands"] = commands_mod
    sys.modules["discord.opus"] = discord_mod.opus


def ensure_slack_mock() -> None:
    """Install the slack mocks, but ONLY when slack is not installed.

    Gateway slack tests run against the REAL library; shadowing an
    installed slack_bolt would poison them the way the bare discord
    mock once poisoned the discord tests.
    """
    if _is_shared_mock(sys.modules.get("slack_bolt")):
        return

    import importlib.util

    try:
        if importlib.util.find_spec("slack_bolt") is not None:
            return  # real library installed — never mock over it
    except (ImportError, ValueError):
        pass

    slack_bolt = MagicMock()
    setattr(slack_bolt, MOCK_SENTINEL, MOCK_VERSION)
    slack_bolt.async_app.AsyncApp = MagicMock
    slack_bolt.adapter.socket_mode.async_handler.AsyncSocketModeHandler = MagicMock

    slack_sdk = MagicMock()
    slack_sdk.web.async_client.AsyncWebClient = MagicMock

    for name, mod in [
        ("slack_bolt", slack_bolt),
        ("slack_bolt.async_app", slack_bolt.async_app),
        ("slack_bolt.adapter", slack_bolt.adapter),
        ("slack_bolt.adapter.socket_mode", slack_bolt.adapter.socket_mode),
        ("slack_bolt.adapter.socket_mode.async_handler",
         slack_bolt.adapter.socket_mode.async_handler),
        ("slack_sdk", slack_sdk),
        ("slack_sdk.web", slack_sdk.web),
        ("slack_sdk.web.async_client", slack_sdk.web.async_client),
    ]:
        sys.modules[name] = mod


def install_platform_mocks() -> None:
    """Install all suite mocks. Safe to call from any conftest."""
    ensure_telegram_mock()
    ensure_discord_mock()
    ensure_slack_mock()
