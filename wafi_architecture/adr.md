# WAFI Architecture Decision Records

## ADR-001: Single Cognitive Authority
- **Decision**: Hermes cognitive loop is the sole brain for reasoning/planning/reflection.
- **Consequence**: No parallel planner/reasoner from execution subsystems.

## ADR-002: Single Memory Authority
- **Decision**: One memory manager owns writes, dedupe, and conflict resolution.
- **Consequence**: No secondary memory stores with independent truth semantics.

## ADR-003: Policy Before Execution
- **Decision**: All side-effecting actions pass through policy and approval checks.
- **Consequence**: Nodes/tools cannot execute by direct bypass routes.
