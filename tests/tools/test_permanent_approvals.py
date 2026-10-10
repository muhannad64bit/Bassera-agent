"""Tests for permanent-approval inspection and revocation.

Permanent approvals ("[a]lways" choices and command_allowlist entries)
are standing permissions to execute commands matching a pattern. Until
now they were invisible and irrevocable without hand-editing config.yaml
— an over-authorization risk with no remediation path.

tools/approval.py now provides list_permanent_approvals() (read-only
snapshot) and revoke_permanent_approval() (narrowing-only: removes a
grant, persists it, and records a 'revoked' audit event).
"""

import json

import pytest

import tools.approval as approval_module
from tools.approval import (
    list_permanent_approvals,
    revoke_permanent_approval,
)


@pytest.fixture(autouse=True)
def _clean_permanent_state(monkeypatch, tmp_path):
    approval_module._permanent_approved.clear()
    approval_module._permanent_approved.update(
        {"recursive delete of user files", "disk wipe via dd"}
    )
    saved = {}

    def fake_save(patterns):
        saved["patterns"] = sorted(patterns)

    monkeypatch.setattr(approval_module, "save_permanent_allowlist", fake_save)
    monkeypatch.setenv("BASSERA_HOME", str(tmp_path))
    yield saved
    approval_module._permanent_approved.clear()


def _audit_lines(tmp_path):
    log = tmp_path / "logs" / "audit.log"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line]


class TestListPermanentApprovals:
    def test_returns_sorted_snapshot(self):
        listed = list_permanent_approvals()
        assert listed == ["disk wipe via dd", "recursive delete of user files"]

    def test_snapshot_is_a_copy(self):
        listed = list_permanent_approvals()
        listed.clear()
        assert len(list_permanent_approvals()) == 2


class TestRevokePermanentApproval:
    def test_revoke_removes_and_persists(self, tmp_path):
        assert revoke_permanent_approval("disk wipe via dd") is True
        assert list_permanent_approvals() == ["recursive delete of user files"]

    def test_revoke_is_audited(self, tmp_path):
        revoke_permanent_approval("disk wipe via dd")
        entries = _audit_lines(tmp_path)
        revoked = [e for e in entries if e.get("decision") == "revoked"]
        assert len(revoked) == 1
        assert revoked[0]["pattern_key"] == "disk wipe via dd"
        # No command text is involved in a revocation event.
        assert revoked[0]["command"] is None

    def test_revoke_unknown_pattern_is_a_noop(self, tmp_path):
        assert revoke_permanent_approval("never existed") is False
        assert len(list_permanent_approvals()) == 2
        assert _audit_lines(tmp_path) == []

    def test_revoke_never_expands_permissions(self, tmp_path):
        """Revoking a pattern that is not approved must not ADD it."""
        before = list_permanent_approvals()
        revoke_permanent_approval("fabricated grant")
        assert list_permanent_approvals() == before

    def test_all_approvals_can_be_revoked_one_by_one(self, tmp_path):
        for pattern in list_permanent_approvals():
            assert revoke_permanent_approval(pattern) is True
        assert list_permanent_approvals() == []
        assert len([e for e in _audit_lines(tmp_path) if e["decision"] == "revoked"]) == 2
