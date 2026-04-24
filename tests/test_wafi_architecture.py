from __future__ import annotations

from pathlib import Path

from wafi_architecture.execution import ExecutionBroker, ExecutionRequest
from wafi_architecture.invariants import default_invariants
from wafi_architecture.memory import MemoryManager
from wafi_architecture.planner import PlanCritic, TaskPlanner
from wafi_architecture.runtime import WafiRuntime
from wafi_architecture.security import (
    AccessControl,
    ActionApproval,
    AuditLog,
    CapabilityFirewall,
    SecretGovernance,
    SecurityPolicyGate,
)
from wafi_architecture.skills import RegressionGate, SkillLearner, SkillManifest, SkillRegistry
from wafi_architecture.tools import ToolRegistry, ToolSpec, default_tool_registry


def build_runtime(allowed_user: str, allowed_tools: set[str]) -> WafiRuntime:
    planner = TaskPlanner()
    critic = PlanCritic()
    memory = MemoryManager()
    gate = SecurityPolicyGate(
        access_control=AccessControl(allowed_users={allowed_user}),
        approval=ActionApproval(),
        firewall=CapabilityFirewall(allowed_tools=allowed_tools),
        audit_log=AuditLog(),
    )
    broker = ExecutionBroker(default_tool_registry())
    return WafiRuntime(planner, critic, memory, gate, broker)


def test_invariants_lint_detects_forbidden_topology() -> None:
    registry = default_invariants()
    violations = registry.lint_layer_config(
        has_secondary_brain=True,
        has_secondary_memory_authority=False,
        execution_bypasses_policy_gate=True,
    )
    assert any("single_brain" in item for item in violations)
    assert any("policy_before_execution" in item for item in violations)


def test_minimal_vertical_slice_reads_file(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("hello-wafi", encoding="utf-8")
    runtime = build_runtime("user-1", {"read_file"})
    response = runtime.handle("user-1", f"read file {target}")
    assert response.executed is True
    assert response.message == "hello-wafi"


def test_execution_broker_idempotency_retry_queue_cancel_timeout() -> None:
    calls = {"count": 0}

    def flaky(_: dict[str, str]) -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("fail once")
        return "ok"

    registry = ToolRegistry()
    registry.register(ToolSpec(name="flaky", side_effecting=False, risk_level="low"), flaky)
    broker = ExecutionBroker(registry)

    req = ExecutionRequest(idempotency_key="k1", tool_name="flaky", args={}, max_retries=1, timeout_s=1.0, sandbox_profile="constrained")
    result = broker.execute(req)
    assert result.success is True

    # idempotency should return cached result without rerunning
    cached = broker.execute(req)
    assert cached.output == result.output
    assert result.attempts == 2

    cancelled = ExecutionRequest(idempotency_key="k2", tool_name="flaky", args={})
    broker.cancel("k2")
    broker.enqueue(cancelled)
    cancel_result = broker.run_next()
    assert cancel_result.output == "Cancelled"


def test_security_controls_and_secret_governance() -> None:
    audit = AuditLog()
    gate = SecurityPolicyGate(
        access_control=AccessControl(allowed_users={"u1"}),
        approval=ActionApproval(dangerous_actions={"execute_shell"}),
        firewall=CapabilityFirewall(allowed_tools={"read_file"}),
        audit_log=audit,
    )

    ok, _ = gate.authorize(user_id="u1", tool_name="read_file")
    denied, message = gate.authorize(user_id="u2", tool_name="read_file")
    blocked, blocked_msg = gate.authorize(user_id="u1", tool_name="execute_shell")

    sg = SecretGovernance(allowed_env_vars={"SAFE_TOKEN"})
    filtered = sg.filter_env({"SAFE_TOKEN": "a", "OPENAI_API_KEY": "b"})

    assert ok is True
    assert denied is False and "not authorized" in message.lower()
    assert blocked is False and "blocked" in blocked_msg.lower()
    assert filtered == {"SAFE_TOKEN": "a"}
    assert len(audit.events) >= 3


def test_skill_signing_learning_and_regression_gate() -> None:
    skill = SkillManifest(
        name="summarize",
        version="1.0.0",
        permissions=["read_file"],
        required_secrets=[],
        body="summarize content",
    )
    registry = SkillRegistry()
    signature = registry.add(skill)
    assert registry.verify(skill, signature)

    learner = SkillLearner()
    refined = learner.refine(skill, trajectory_success_rate=0.5)
    assert refined.body != skill.body

    gate = RegressionGate(min_success_rate=0.8)
    assert gate.can_promote(before=0.81, after=0.9) is True
    assert gate.can_promote(before=0.9, after=0.85) is False
