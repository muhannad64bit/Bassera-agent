"""Security coverage for the smart-approval LLM authorization boundary.

approvals.mode=smart lets an auxiliary LLM auto-approve or hard-deny a
command that pattern detection flagged. Those verdict branches had zero
coverage (only the APPROVE unit path was tested), which matters because
they are authorization decisions:
- verdict approve -> command runs AND the pattern is granted for the
  session (subsequent calls must not consult the LLM again)
- verdict deny -> command is blocked outright
- verdict escalate / LLM failure -> falls through to the human prompt
  (never silently allows)

Also covers _format_tirith_description: the human-readable justification
the user bases an approval decision on must render all finding shapes
correctly.
"""

from types import SimpleNamespace
from unittest.mock import patch as mock_patch

import pytest

import tools.approval as approval_module
from tools.approval import (
    _format_tirith_description,
    _smart_approve,
    check_all_command_guards,
)

CMD = "rm -rf /tmp/stuff"


@pytest.fixture(autouse=True)
def _clean_approval_state(monkeypatch):
    # Pin the live tirith detector: verdicts from other workers must not
    # leak into these tests (same class of poisoning as the gateway-
    # resolution file).
    monkeypatch.setattr(
        "tools.tirith_security.check_command_security",
        lambda *a, **kw: {"action": "allow", "findings": [], "summary": ""},
    )
    # Pristine session contextvars (worker-order poisoning guard).
    import gateway.session_context as _sc

    for _v in (
        _sc._SESSION_PLATFORM, _sc._SESSION_CHAT_ID, _sc._SESSION_CHAT_NAME,
        _sc._SESSION_THREAD_ID, _sc._SESSION_USER_ID, _sc._SESSION_USER_NAME,
        _sc._SESSION_KEY,
    ):
        _v.set(_sc._UNSET)
    approval_module._approval_session_key.set("")
    approval_module._session_approved.clear()
    approval_module._permanent_approved.clear()
    approval_module._session_yolo.clear()
    monkeypatch.setenv("BASSERA_SESSION_KEY", "smart-test-session")
    monkeypatch.delenv("BASSERA_YOLO_MODE", raising=False)
    monkeypatch.delenv("BASSERA_CRON_SESSION", raising=False)
    monkeypatch.delenv("BASSERA_EXEC_ASK", raising=False)
    monkeypatch.delenv("BASSERA_GATEWAY_SESSION", raising=False)
    yield
    approval_module._session_approved.clear()
    approval_module._permanent_approved.clear()
    approval_module._session_yolo.clear()


def _llm_response(text):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
    )


class TestSmartApproveVerdictParsing:
    def test_deny_verdict(self):
        with mock_patch(
            "agent.auxiliary_client.call_llm", return_value=_llm_response("DENY")
        ):
            assert _smart_approve(CMD, "recursive delete") == "deny"

    def test_uncertain_verdict_escalates(self):
        with mock_patch(
            "agent.auxiliary_client.call_llm", return_value=_llm_response("MAYBE")
        ):
            assert _smart_approve(CMD, "recursive delete") == "escalate"

    def test_llm_failure_escalates_never_allows(self):
        def _boom(*args, **kwargs):
            raise RuntimeError("no aux LLM configured")

        with mock_patch("agent.auxiliary_client.call_llm", side_effect=_boom):
            assert _smart_approve(CMD, "recursive delete") == "escalate"


class TestSmartApprovalEndToEnd:
    """The full guard in smart mode: LLM verdicts are authorization decisions."""

    @pytest.fixture(autouse=True)
    def _smart_mode(self, monkeypatch):
        monkeypatch.setattr(
            "bassera_cli.config.load_config",
            lambda *a, **kw: {"approvals": {"mode": "smart"}},
        )
        monkeypatch.setenv("BASSERA_INTERACTIVE", "1")

    def test_approve_verdict_runs_and_grants_session(self):
        with mock_patch(
            "agent.auxiliary_client.call_llm", return_value=_llm_response("APPROVE")
        ) as llm:
            result = check_all_command_guards(CMD, "local")

        assert result["approved"] is True
        assert result["smart_approved"] is True
        llm.assert_called_once()

        # The approval must be session-granted: a second check for the
        # same session+pattern must NOT consult the LLM again.
        with mock_patch(
            "agent.auxiliary_client.call_llm", return_value=_llm_response("APPROVE")
        ) as llm2:
            again = check_all_command_guards(CMD, "local")
        assert again["approved"] is True
        llm2.assert_not_called()

    def test_deny_verdict_blocks_outright(self):
        with mock_patch(
            "agent.auxiliary_client.call_llm", return_value=_llm_response("DENY")
        ):
            result = check_all_command_guards(CMD, "local")

        assert result["approved"] is False
        assert result["smart_denied"] is True
        assert "BLOCKED by smart approval" in result["message"]

        # A denial grants nothing: the next check consults the LLM again.
        with mock_patch(
            "agent.auxiliary_client.call_llm", return_value=_llm_response("DENY")
        ) as llm2:
            again = check_all_command_guards(CMD, "local")
        assert again["approved"] is False
        assert llm2.called

    def test_escalate_verdict_falls_through_to_human_prompt(self):
        # Uncertain LLM verdict must hand off to the interactive prompt,
        # never auto-allow. The human denies.
        with mock_patch(
            "agent.auxiliary_client.call_llm", return_value=_llm_response("ESCALATE")
        ) as llm:
            result = check_all_command_guards(
                CMD, "local", approval_callback=lambda *a: "deny"
            )

        assert llm.called
        assert result["approved"] is False
        # The block came from the human prompt, not the smart layer.
        assert "smart_denied" not in result
        assert "BLOCKED" in result["message"]


class TestFormatTirithDescription:
    """The approval message the user reads must render every finding shape."""

    def test_no_findings_uses_summary(self):
        out = _format_tirith_description(
            {"action": "block", "findings": [], "summary": "bad stuff"}
        )
        assert out == "Security scan: bad stuff"

    def test_no_findings_no_summary_uses_default(self):
        out = _format_tirith_description({"action": "block", "findings": []})
        assert out == "Security scan: security issue detected"

    def test_full_finding_renders_severity_title_desc(self):
        out = _format_tirith_description(
            {
                "action": "warn",
                "findings": [
                    {
                        "severity": "high",
                        "title": "curl pipe",
                        "description": "remote code execution",
                    }
                ],
            }
        )
        assert out == "Security scan — [high] curl pipe: remote code execution"

    def test_title_only_finding(self):
        out = _format_tirith_description(
            {"action": "warn", "findings": [{"severity": "low", "title": "odd flag"}]}
        )
        assert out == "Security scan — [low] odd flag"

    def test_unusable_findings_fall_back_to_summary(self):
        out = _format_tirith_description(
            {
                "action": "block",
                "findings": [{"severity": "", "title": "", "description": ""}],
                "summary": "unspecified issue",
            }
        )
        assert out == "Security scan: unspecified issue"
