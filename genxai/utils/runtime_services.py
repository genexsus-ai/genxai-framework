"""Shared runtime services for GenXAI.

Provides a single, stable import surface for the security (RBAC, policy,
audit) and observability (logging context, metrics, tracing) services used
throughout the runtime, backed by the real implementations in
``genxai.security`` and ``genxai.observability``.
"""

from __future__ import annotations

from genxai.observability.logging import clear_log_context, set_log_context
from genxai.observability.metrics import (
    record_agent_execution,
    record_llm_request,
    record_tool_execution,
    record_workflow_execution,
    record_workflow_node_execution,
)
from genxai.observability.tracing import add_event, record_exception, span
from genxai.security.audit import AuditEvent, get_audit_log
from genxai.security.policy_engine import get_policy_engine
from genxai.security.rbac import Permission, get_current_user

__all__ = [
    "AuditEvent",
    "Permission",
    "add_event",
    "clear_log_context",
    "get_audit_log",
    "get_current_user",
    "get_policy_engine",
    "record_agent_execution",
    "record_exception",
    "record_llm_request",
    "record_tool_execution",
    "record_workflow_execution",
    "record_workflow_node_execution",
    "set_log_context",
    "span",
]
