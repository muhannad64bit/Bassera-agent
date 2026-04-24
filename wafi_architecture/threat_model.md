# WAFI Threat Model (Baseline)

## Trust boundaries
- External users/channels are untrusted by default.
- Hermes cognition is trusted to propose actions, not to self-authorize them.
- Tool runtimes are constrained by capability firewall and sandbox policy.

## Key risks
- Prompt injection causing unsafe tool execution.
- Secret exfiltration via over-permissive passthrough.
- Device/node abuse if control plane is exposed.

## Controls
- Pairing/allowlist authorization.
- Capability firewall and action approval.
- Audit logging and anomaly-triggered containment.
