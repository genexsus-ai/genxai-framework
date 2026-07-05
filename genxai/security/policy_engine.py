"""Resource-level policy engine for GenXAI."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from genxai.security.audit import get_approval_service
from genxai.security.rbac import Permission, PermissionDenied, User


class ResourceType(str, Enum):
    """Resource types covered by policy engine."""

    AGENT = "agent"
    TOOL = "tool"
    WORKFLOW = "workflow"
    MEMORY = "memory"


@dataclass
class AccessRule:
    """ACL rule for a resource."""

    permissions: set[Permission]
    allowed_users: set[str] | None = None
    requires_approval: bool = False
    approval_request_id: str | None = None


class PolicyEngine:
    """Simple ACL-based policy engine."""

    def __init__(self) -> None:
        self._rules: dict[str, AccessRule] = {}

    def add_rule(self, resource_id: str, rule: AccessRule) -> None:
        self._rules[resource_id] = rule

    def check(self, user: User, resource_id: str, permission: Permission) -> None:
        rule = self._rules.get(resource_id)
        if rule is None:
            if not user.has_permission(permission):
                raise PermissionDenied(
                    f"User {user.user_id} missing permission: {permission.value}"
                )
            return

        if rule.requires_approval:
            if not rule.approval_request_id:
                approval = get_approval_service().submit(
                    f"{permission.value}",
                    resource_id,
                    user.user_id,
                )
                rule.approval_request_id = approval.request_id
            approval = get_approval_service().get(rule.approval_request_id)
            if not approval or approval.status != "approved":
                raise PermissionDenied(
                    f"Approval required for resource {resource_id}"
                )

        if rule.allowed_users and user.user_id not in rule.allowed_users:
            raise PermissionDenied(
                f"User {user.user_id} not allowed for resource {resource_id}"
            )

        if permission not in rule.permissions:
            raise PermissionDenied(
                f"Permission {permission.value} denied for resource {resource_id}"
            )


_policy_engine: PolicyEngine | None = None


def get_policy_engine() -> PolicyEngine:
    global _policy_engine
    if _policy_engine is None:
        _policy_engine = PolicyEngine()
    return _policy_engine

