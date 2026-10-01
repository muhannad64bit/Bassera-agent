"""Optional event-loop performance policy (uvloop).

The messaging gateway is a long-lived asyncio server; the platform default
event loop is not the fastest available. When ``uvloop`` is installed it
typically doubles event-loop throughput (task churn, socket callbacks) —
a straight latency win for busy multi-platform gateways.

Behavior is controlled by, in order:

1. ``BASSERA_EVENT_LOOP`` env var (``auto`` / ``uvloop`` / ``default``)
2. ``performance.event_loop`` in config.yaml (same values)
3. Default: ``auto`` — use uvloop when it is installed, silently fall
   back to the platform loop when it is not.

``uvloop`` requested-but-missing logs a warning (a deployment that pinned
the policy expects it); ``auto`` never complains. uvloop is POSIX-only
and is never attempted on Windows.

Import-safe: stdlib only until uvloop is actually loaded.
"""

import logging
import os
import sys

logger = logging.getLogger(__name__)

_VALID_MODES = ("auto", "uvloop", "default")


def _resolve_mode(config=None) -> str:
    env_mode = (os.environ.get("BASSERA_EVENT_LOOP", "") or "").strip().lower()
    if env_mode in _VALID_MODES:
        return env_mode
    if isinstance(config, dict):
        perf = config.get("performance")
        if isinstance(perf, dict):
            cfg_mode = str(perf.get("event_loop", "") or "").strip().lower()
            if cfg_mode in _VALID_MODES:
                return cfg_mode
    return "auto"


def maybe_install_uvloop(config=None) -> bool:
    """Install the uvloop event loop policy when appropriate.

    Returns True when uvloop was installed, False otherwise. Must be
    called BEFORE ``asyncio.run(...)`` / loop creation — the policy only
    affects loops created after installation.
    """
    if sys.platform == "win32":
        return False

    mode = _resolve_mode(config)
    if mode == "default":
        return False

    try:
        import uvloop
    except ImportError:
        if mode == "uvloop":
            logger.warning(
                "performance.event_loop is 'uvloop' but uvloop is not "
                "installed — using the platform default loop. "
                "Install with: pip install 'bassera-agent[perf]'"
            )
        return False

    try:
        uvloop.install()
        logger.info("Event loop: uvloop (performance.event_loop=%s)", mode)
        return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("uvloop install failed (%s); using the default loop", exc)
        return False
