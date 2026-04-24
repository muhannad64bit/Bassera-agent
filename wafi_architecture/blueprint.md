# WAFI-AGENT Architecture Blueprint

## 1. Architecture Overview
WAFI-AGENT is composed of six strict layers:
- Cognition Layer (Hermes-only)
- Policy & Safety Layer
- Execution Orchestration Layer
- Tool Runtime Layer
- Memory & Learning Layer
- Interfaces Layer

Hermes remains the sole cognitive authority. Execution capabilities are modular and policy-gated.

## 2. Module Design
- **Core Brain**: `TaskPlanner`, `PlanCritic`, runtime orchestration in `WafiRuntime`
- **Memory System**: `MemoryManager` with typed records and a single write authority
- **Planner**: deterministic step planner and critic shim
- **Tool Execution Layer**: `ExecutionBroker`, `ToolRegistry`, sandbox profile tagging
- **Skill System**: `SkillManifest`, `SkillRegistry`, `SkillLearner`, `RegressionGate`
- **Security Guard Layer**: `SecurityPolicyGate`, `AccessControl`, `ActionApproval`, `CapabilityFirewall`, `SecretGovernance`, `AuditLog`

## 3. What to Take from Hermes
- Single cognitive orchestration loop
- Stable planning and reflection flow
- Single memory authority model
- Skill learning lifecycle
- Interruptible action semantics

## 4. What to Take from OpenClaw
- Typed control-plane execution concepts
- Idempotent side-effect request patterns
- Queueing and concurrency controls
- Pairing/allowlist-first operational hardening
- Device/node capability declarations as execution-only adapters

## 5. What to Remove
- Any second cognitive planner/reasoner
- Parallel memory truth stores
- Execution routes that bypass policy checks
- Competing approval engines and inconsistent auth semantics

## 6. Final Clean Design
```text
Interfaces -> HermesBrain -> Planner -> SecurityPolicyGate -> ExecutionBroker -> ToolRegistry/Nodes
                                  |                           |
                                  +---- MemoryManager <-------+
                                  +---- SkillLearner -> SkillRegistry
```

## 7. Implementation Strategy
1. Enforce architecture invariants (single brain, single memory authority, policy-before-exec).
2. Deliver one vertical slice: plan -> policy -> safe read-only tool -> response.
3. Harden execution broker (idempotency, queue, retry, timeout, cancel, sandbox profile).
4. Add layered security (auth/pairing, approvals, firewall, secret governance, audit).
5. Add skill manifests, signatures, self-improvement, and regression promotion gate.
