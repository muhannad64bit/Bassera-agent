"""Shared helpers for direct xAI HTTP integrations."""

from __future__ import annotations


def wafi_xai_user_agent() -> str:
    """Return a stable Wafi-specific User-Agent for xAI HTTP calls."""
    try:
        from wafi_cli import __version__
    except Exception:
        __version__ = "unknown"
    return f"Bassera-Agent/{__version__}"
