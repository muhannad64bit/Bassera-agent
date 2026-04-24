from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AccessControl:
    allowed_users: set[str] = field(default_factory=set)
    paired_users: set[str] = field(default_factory=set)

    def can_access(self, user_id: str) -> bool:
        return user_id in self.allowed_users or user_id in self.paired_users


@dataclass
class ActionApproval:
    dangerous_actions: set[str] = field(default_factory=lambda: {"write_file", "execute_shell"})

    def requires_approval(self, action: str) -> bool:
        return action in self.dangerous_actions


@dataclass
class CapabilityFirewall:
    allowed_tools: set[str] = field(default_factory=set)

    def permit(self, tool_name: str) -> bool:
        return tool_name in self.allowed_tools


@dataclass
class SecretGovernance:
    allowed_env_vars: set[str] = field(default_factory=set)

    def filter_env(self, env: dict[str, str]) -> dict[str, str]:
        return {k: v for k, v in env.items() if k in self.allowed_env_vars}


@dataclass
class AuditEvent:
    category: str
    message: str


@dataclass
class AuditLog:
    events: list[AuditEvent] = field(default_factory=list)

    def record(self, category: str, message: str) -> None:
        self.events.append(AuditEvent(category=category, message=message))


@dataclass
class SecurityPolicyGate:
    access_control: AccessControl
    approval: ActionApproval
    firewall: CapabilityFirewall
    audit_log: AuditLog

    def authorize(self, *, user_id: str, tool_name: str) -> tuple[bool, str]:
        if not self.access_control.can_access(user_id):
            self.audit_log.record("auth", f"Denied user={user_id}")
            return False, "User not authorized."
        if not self.firewall.permit(tool_name):
            self.audit_log.record("firewall", f"Denied tool={tool_name}")
            return False, "Tool is blocked by capability firewall."
        if self.approval.requires_approval(tool_name):
            self.audit_log.record("approval", f"Approval required tool={tool_name}")
            return False, "Action requires explicit approval."
        self.audit_log.record("policy", f"Approved user={user_id} tool={tool_name}")
        return True, "Approved"
