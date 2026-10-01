"""Direct coverage for the LIVE gateway approval handshake.

The blocking messaging-approval flow (a dangerous command in a gateway
session -> agent thread blocks on an _ApprovalEntry -> the user replies
/approve or /deny -> resolve_gateway_approval unblocks it) is the exact
path an attacker's prompt-injected command travels in a running
messenger. Until now it had no direct tests — only the CLI/submit_pending
paths were pinned. These tests exercise the real blocking wait.

Also covers the prompt fallbacks: callback crash -> deny (never fail
open), EOF/invalid input -> deny, and the [a]lways-hiding variant used
when tirith content findings are present.
"""

import threading
import time
from unittest.mock import patch

import pytest

import tools.approval as approval_module
from tools.approval import (
    check_all_command_guards,
    has_blocking_approval,
    prompt_dangerous_approval,
    register_gateway_notify,
    resolve_gateway_approval,
    unregister_gateway_notify,
)

CMD = "rm -rf /home/user/important-data"


class _Waiter:
    """Run check_all_command_guards in a thread and capture its result."""

    def __init__(self, monkeypatch, session_key, command=CMD, timeout="3"):
        self.session_key = session_key
        self.result = None
        self.error = None
        self.notified = []
        monkeypatch.setenv("BASSERA_GATEWAY_SESSION", "1")
        # Threads start with an empty contextvar context, so
        # set_current_session_key in the test process does NOT propagate;
        # the env fallback is what the spawned thread will see.
        monkeypatch.setenv("BASSERA_SESSION_KEY", session_key)
        monkeypatch.delenv("BASSERA_INTERACTIVE", raising=False)
        monkeypatch.delenv("BASSERA_EXEC_ASK", raising=False)
        monkeypatch.delenv("BASSERA_CRON_SESSION", raising=False)
        monkeypatch.delenv("BASSERA_YOLO_MODE", raising=False)
        monkeypatch.setattr(
            "bassera_cli.config.load_config",
            lambda *a, **kw: {"approvals": {"gateway_timeout": int(timeout)}},
        )
        register_gateway_notify(session_key, self.notified.append)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            self.result = check_all_command_guards(CMD, "local")
        except Exception as exc:  # pragma: no cover - diagnostic only
            self.error = exc

    def wait_until_pending(self, deadline=5.0):
        start = time.monotonic()
        while time.monotonic() - start < deadline:
            if has_blocking_approval(self.session_key):
                return True
            time.sleep(0.02)
        return False

    def join(self, timeout=10.0):
        self._thread.join(timeout=timeout)
        return not self._thread.is_alive()


@pytest.fixture(autouse=True)
def _clean_approval_state(monkeypatch):
    # Under the full-suite run another worker can leave the live Tirith
    # detector initialized with real on-disk marker state; its verdicts
    # must not leak into these gateway-resolution tests, so pin it to
    # an unconditional "allow" with no findings.
    monkeypatch.setattr(
        "tools.tirith_security.check_command_security",
        lambda *a, **kw: {"action": "allow", "findings": [], "summary": ""},
    )
    approval_module._gateway_queues.clear()
    approval_module._gateway_notify_cbs.clear()
    approval_module._session_approved.clear()
    approval_module._permanent_approved.clear()
    yield
    approval_module._gateway_queues.clear()
    approval_module._gateway_notify_cbs.clear()
    approval_module._session_approved.clear()
    approval_module._permanent_approved.clear()


class TestBlockingGatewayApproval:
    def test_approve_once_runs_command_this_time_only(self, monkeypatch):
        waiter = _Waiter(monkeypatch, "sess-once")
        assert waiter.wait_until_pending(), "guard must block with a pending approval"
        assert len(waiter.notified) == 1, "user must be notified exactly once"
        assert waiter.notified[0]["command"] == CMD
        assert waiter.notified[0]["pattern_keys"]

        resolved = resolve_gateway_approval("sess-once", "once")
        assert resolved == 1
        assert waiter.join()
        assert waiter.error is None
        assert waiter.result["approved"] is True

        # "once" must NOT persist: the next identical command blocks again
        waiter2 = _Waiter(monkeypatch, "sess-once")
        assert waiter2.wait_until_pending()
        unregister_gateway_notify("sess-once")
        assert waiter2.join()
        assert waiter2.result["approved"] is False

    def test_deny_blocks_with_do_not_retry(self, monkeypatch):
        waiter = _Waiter(monkeypatch, "sess-deny")
        assert waiter.wait_until_pending()
        resolve_gateway_approval("sess-deny", "deny")
        assert waiter.join()
        assert waiter.result["approved"] is False
        assert "Do NOT retry" in waiter.result["message"]
        assert "denied by user" in waiter.result["message"]

    def test_approve_session_persists_for_the_session(self, monkeypatch):
        waiter = _Waiter(monkeypatch, "sess-scope")
        assert waiter.wait_until_pending()
        resolve_gateway_approval("sess-scope", "session")
        assert waiter.join()
        assert waiter.result["approved"] is True
        # Same session + pattern must now auto-approve without blocking.
        monkeypatch.setenv("BASSERA_GATEWAY_SESSION", "1")
        result = check_all_command_guards(CMD, "local")
        assert result["approved"] is True

    def test_approve_always_persists_permanently(self, monkeypatch):
        with patch("tools.approval.save_permanent_allowlist") as mock_save:
            waiter = _Waiter(monkeypatch, "sess-always")
            assert waiter.wait_until_pending()
            resolve_gateway_approval("sess-always", "always")
            assert waiter.join()
        assert waiter.result["approved"] is True
        mock_save.assert_called_once()
        # A brand-NEW session is approved via the permanent allowlist.
        approval_module._session_approved.clear()
        monkeypatch.setenv("BASSERA_GATEWAY_SESSION", "1")
        result = check_all_command_guards(CMD, "local")
        assert result["approved"] is True

    def test_timeout_blocks_command(self, monkeypatch):
        waiter = _Waiter(monkeypatch, "sess-timeout", timeout="1")
        assert waiter.wait_until_pending()
        # No resolution: let the 1s gateway_timeout elapse.
        assert waiter.join(timeout=10)
        assert waiter.result["approved"] is False
        assert "timed out" in waiter.result["message"]

    def test_notify_failure_blocks_instead_of_running(self, monkeypatch):
        """If the platform cannot deliver the approval prompt, the command
        must be BLOCKED — never run unapproved (fail-closed)."""
        token = approval_module.set_current_session_key("sess-notifyfail")
        monkeypatch.setenv("BASSERA_GATEWAY_SESSION", "1")
        monkeypatch.delenv("BASSERA_INTERACTIVE", raising=False)
        monkeypatch.delenv("BASSERA_CRON_SESSION", raising=False)
        monkeypatch.delenv("BASSERA_YOLO_MODE", raising=False)
        monkeypatch.setattr(
            "bassera_cli.config.load_config", lambda *a, **kw: {}
        )

        def _broken_notify(_data):
            raise RuntimeError("platform send failed")

        register_gateway_notify("sess-notifyfail", _broken_notify)
        try:
            result = check_all_command_guards(CMD, "local")
        finally:
            approval_module.reset_current_session_key(token)
        assert result["approved"] is False
        assert "Failed to send approval request" in result["message"]
        assert not has_blocking_approval("sess-notifyfail"), (
            "the queue entry must be removed after a failed notify"
        )

    def test_resolve_all_releases_every_waiter_fifo(self, monkeypatch):
        waiters = [_Waiter(monkeypatch, "sess-all") for _ in range(2)]
        # Both entries must be in the queue before resolving — one entry
        # satisfies has_blocking_approval for both waiters.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if len(approval_module._gateway_queues.get("sess-all", [])) >= 2:
                break
            time.sleep(0.02)
        assert len(approval_module._gateway_queues.get("sess-all", [])) == 2
        resolved = resolve_gateway_approval("sess-all", "once", resolve_all=True)
        assert resolved == 2
        for w in waiters:
            assert w.join()
            assert w.result["approved"] is True

    def test_resolve_empty_queue_returns_zero(self):
        assert resolve_gateway_approval("nobody", "once") == 0

    def test_unregister_unblocks_waiters_as_denied(self, monkeypatch):
        waiter = _Waiter(monkeypatch, "sess-unreg")
        assert waiter.wait_until_pending()
        unregister_gateway_notify("sess-unreg")
        assert waiter.join(timeout=10)
        assert waiter.result["approved"] is False, (
            "an interrupted run must not leave the command approved"
        )


class TestPromptFallbacks:
    def test_callback_crash_denies_never_fails_open(self, monkeypatch):
        """An approval_callback exception must deny — a broken UI can never
        silently allow a dangerous command."""
        monkeypatch.delenv("BASSERA_INTERACTIVE", raising=False)

        def _crash(*a, **kw):
            raise RuntimeError("prompt UI crashed")

        result = prompt_dangerous_approval(CMD, "test", approval_callback=_crash)
        assert result == "deny"

    def test_eof_denies(self, monkeypatch):
        monkeypatch.delenv("BASSERA_INTERACTIVE", raising=False)
        with patch("builtins.input", side_effect=EOFError):
            result = prompt_dangerous_approval(CMD, "test", timeout_seconds=2)
        assert result == "deny"

    def test_invalid_choice_denies(self, monkeypatch, capsys):
        monkeypatch.delenv("BASSERA_INTERACTIVE", raising=False)
        with patch("builtins.input", return_value="zzz"):
            result = prompt_dangerous_approval(CMD, "test", timeout_seconds=2)
        assert result == "deny"

    @pytest.mark.parametrize("raw,expected", [("o", "once"), ("s", "session"), ("a", "always")])
    def test_choice_letters(self, monkeypatch, raw, expected):
        monkeypatch.delenv("BASSERA_INTERACTIVE", raising=False)
        with patch("builtins.input", return_value=raw):
            assert prompt_dangerous_approval(CMD, "test", timeout_seconds=2) == expected

    def test_always_hidden_when_not_allowed_permanent(self, monkeypatch):
        """With tirith content findings, [a]lways is hidden: an 'a' input
        downgrades to session-scope instead of permanent allowlisting."""
        monkeypatch.delenv("BASSERA_INTERACTIVE", raising=False)
        with patch("builtins.input", return_value="a"):
            result = prompt_dangerous_approval(
                CMD, "test", allow_permanent=False, timeout_seconds=2
            )
        assert result == "session"
