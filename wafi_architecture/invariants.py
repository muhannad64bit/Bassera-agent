from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ArchitectureInvariant:
    """Non-negotiable architecture rule."""

    id: str
    description: str


class InvariantRegistry:
    """Central source of truth for architecture boundaries."""

    def __init__(self, invariants: Iterable[ArchitectureInvariant]) -> None:
        self._invariants = {item.id: item for item in invariants}

    def get(self, invariant_id: str) -> ArchitectureInvariant | None:
        return self._invariants.get(invariant_id)

    def all(self) -> list[ArchitectureInvariant]:
        return list(self._invariants.values())

    def lint_layer_config(self, *, has_secondary_brain: bool, has_secondary_memory_authority: bool, execution_bypasses_policy_gate: bool) -> list[str]:
        """Return violations discovered in a runtime wiring config."""

        violations: list[str] = []
        if has_secondary_brain:
            violations.append("single_brain: detected second reasoning authority")
        if has_secondary_memory_authority:
            violations.append("single_memory_authority: detected duplicate memory authority")
        if execution_bypasses_policy_gate:
            violations.append("policy_before_execution: detected execution path bypassing policy")
        return violations


def default_invariants() -> InvariantRegistry:
    return InvariantRegistry(
        [
            ArchitectureInvariant(
                id="single_brain",
                description="Hermes cognitive loop is the only reasoning authority.",
            ),
            ArchitectureInvariant(
                id="single_memory_authority",
                description="Only one memory manager can own memory writes and truth reconciliation.",
            ),
            ArchitectureInvariant(
                id="policy_before_execution",
                description="Every tool/action must pass through security policy gate before execution.",
            ),
        ]
    )
