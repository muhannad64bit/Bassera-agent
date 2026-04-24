from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlanStep:
    id: str
    action: str
    args: dict[str, str]
    risk: str = "low"


class TaskPlanner:
    """Hermes-only planning shim for WAFI architecture."""

    def build_plan(self, user_goal: str) -> list[PlanStep]:
        goal = user_goal.lower()
        if "read" in goal and "file" in goal:
            return [
                PlanStep(
                    id="step-read-file",
                    action="read_file",
                    args={"path": user_goal.split()[-1]},
                    risk="low",
                )
            ]
        return [
            PlanStep(
                id="step-respond",
                action="respond",
                args={"message": "No executable step required."},
                risk="low",
            )
        ]


class PlanCritic:
    """Annotates plan steps before execution."""

    def validate(self, steps: list[PlanStep]) -> list[PlanStep]:
        return steps
