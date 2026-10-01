"""Tests for the optional uvloop event-loop policy (tools/event_loop_perf.py).

The policy must be conservative: auto mode never fails when uvloop is
absent, explicit mode warns but does not crash, and 'default' never
touches anything. The gateway calls this before asyncio.run(); a bug
here would break gateway startup.
"""

import logging
import sys
import types
from unittest.mock import patch

from tools.event_loop_perf import _resolve_mode, maybe_install_uvloop


def _fake_uvloop_module():
    mod = types.ModuleType("uvloop")
    mod.install = lambda: installed.append(True) or True
    return mod


installed = []


class TestModeResolution:
    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("BASSERA_EVENT_LOOP", "default")
        assert _resolve_mode({"performance": {"event_loop": "uvloop"}}) == "default"

    def test_config_used_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("BASSERA_EVENT_LOOP", raising=False)
        assert _resolve_mode({"performance": {"event_loop": "uvloop"}}) == "uvloop"

    def test_default_is_auto(self, monkeypatch):
        monkeypatch.delenv("BASSERA_EVENT_LOOP", raising=False)
        assert _resolve_mode(None) == "auto"
        assert _resolve_mode({}) == "auto"

    def test_invalid_values_fall_back_to_auto(self, monkeypatch):
        monkeypatch.setenv("BASSERA_EVENT_LOOP", "nonsense")
        assert _resolve_mode({"performance": {"event_loop": "turbo"}}) == "auto"


class TestInstallBehavior:
    def test_auto_installs_when_available(self, monkeypatch):
        monkeypatch.delenv("BASSERA_EVENT_LOOP", raising=False)
        installed.clear()
        fake = _fake_uvloop_module()
        monkeypatch.setitem(sys.modules, "uvloop", fake)
        assert maybe_install_uvloop() is True
        assert installed == [True]

    def test_auto_silent_when_missing(self, monkeypatch):
        monkeypatch.delenv("BASSERA_EVENT_LOOP", raising=False)
        monkeypatch.setitem(sys.modules, "uvloop", None)  # import raises
        assert maybe_install_uvloop() is False

    def test_explicit_uvloop_missing_warns(self, monkeypatch, caplog):
        monkeypatch.setenv("BASSERA_EVENT_LOOP", "uvloop")
        monkeypatch.setitem(sys.modules, "uvloop", None)
        with caplog.at_level(logging.WARNING, logger="tools.event_loop_perf"):
            assert maybe_install_uvloop() is False
        assert any("uvloop is not installed" in r.message for r in caplog.records)

    def test_default_mode_never_imports(self, monkeypatch):
        monkeypatch.setenv("BASSERA_EVENT_LOOP", "default")
        # A successful import would still be refused in default mode.
        installed.clear()
        monkeypatch.setitem(sys.modules, "uvloop", _fake_uvloop_module())
        assert maybe_install_uvloop() is False
        assert installed == []

    def test_install_failure_falls_back(self, monkeypatch):
        monkeypatch.delenv("BASSERA_EVENT_LOOP", raising=False)
        broken = types.ModuleType("uvloop")

        def _boom():
            raise RuntimeError("nope")

        broken.install = _boom
        monkeypatch.setitem(sys.modules, "uvloop", broken)
        with caplog_warning() as _:
            assert maybe_install_uvloop() is False

    def test_windows_is_noop(self, monkeypatch):
        monkeypatch.delenv("BASSERA_EVENT_LOOP", raising=False)
        installed.clear()
        monkeypatch.setitem(sys.modules, "uvloop", _fake_uvloop_module())
        with patch.object(sys, "platform", "win32"):
            assert maybe_install_uvloop() is False
        assert installed == []


class _caplog_ctx:
    def __enter__(self):
        logging.getLogger("tools.event_loop_perf").setLevel(logging.WARNING)
        return self

    def __exit__(self, *exc):
        return False


def caplog_warning():
    return _caplog_ctx()


def test_live_uvloop_when_installed():
    """On environments with real uvloop (dev venv, CI [perf]): install works."""
    try:
        import uvloop  # noqa: F401
    except ImportError:
        import pytest

        pytest.skip("uvloop not installed")
    assert maybe_install_uvloop({"performance": {"event_loop": "auto"}}) is True


def test_gateway_main_wires_the_policy():
    """The gateway entry point must install the policy before asyncio.run."""
    source = open("gateway/run.py", encoding="utf-8").read()
    assert "maybe_install_uvloop()" in source, (
        "gateway/run.py main() stopped installing the event-loop policy"
    )
    assert source.index("maybe_install_uvloop()") < source.index(
        "asyncio.run(start_gateway"
    ), "the policy must be installed BEFORE the loop is created"
