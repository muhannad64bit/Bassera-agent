from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from .execution import ExecutionBroker, ExecutionRequest
from .memory import MemoryManager
from .planner import PlanCritic, TaskPlanner
from .security import SecurityPolicyGate


@dataclass
class RuntimeResponse:
    message: str
    executed: bool


class WafiRuntime:
    """Minimal vertical slice: goal -> Hermes planner -> policy gate -> read-only tool."""

    def __init__(self, planner: TaskPlanner, critic: PlanCritic, memory: MemoryManager, policy_gate: SecurityPolicyGate, broker: ExecutionBroker) -> None:
        self.planner = planner
        self.critic = critic
        self.memory = memory
        self.policy_gate = policy_gate
        self.broker = broker

    def handle(self, user_id: str, goal: str) -> RuntimeResponse:
        steps = self.critic.validate(self.planner.build_plan(goal))
        if not steps:
            return RuntimeResponse(message="No plan produced.", executed=False)

        step = steps[0]
        approved, reason = self.policy_gate.authorize(user_id=user_id, tool_name=step.action)
        if not approved:
            return RuntimeResponse(message=reason, executed=False)

        request = ExecutionRequest(
            idempotency_key=str(uuid4()),
            tool_name=step.action,
            args=step.args,
            max_retries=1,
            timeout_s=2.0,
            sandbox_profile="personal-trusted",
        )
        result = self.broker.execute(request)
        if result.success:
            self.memory.write("task", "last_result", result.output)
            return RuntimeResponse(message=result.output, executed=True)
        return RuntimeResponse(message=result.output, executed=False)
