# Governance Policy Engine

GenXAI includes a resource‑level ACL policy engine layered on top of RBAC. This
capability is part of the MIT-licensed runtime.

## How it works
- RBAC provides coarse permissions (`tool:execute`, `agent:read`, etc.)
- Policy engine enforces **resource‑specific** ACLs (e.g. tool `tool:csv_processor`)

## Example

```python
from genxai.security.policy_engine import get_policy_engine, AccessRule
from genxai.security.rbac import Permission, User, Role, set_current_user

policy = get_policy_engine()
policy.add_rule(
    "tool:calculator",
    AccessRule(permissions={Permission.TOOL_EXECUTE}, allowed_users={"alice"})
)

policy.add_rule(
    "agent:finance_agent",
    AccessRule(permissions={Permission.AGENT_EXECUTE}, allowed_users={"alice"})
)

policy.add_rule(
    "memory:shared_plan",
    AccessRule(permissions={Permission.MEMORY_READ, Permission.MEMORY_WRITE}, allowed_users={"alice"})
)

set_current_user(User(user_id="alice", role=Role.DEVELOPER))
# Tool, agent, and memory access will be allowed only for alice.
```

> **Note**: The `Permission` enum currently covers `AGENT_*`, `WORKFLOW_*`,
> `TOOL_*`, and `MEMORY_*` permissions only. Dedicated trigger-level and
> connector-level permissions are not yet implemented and are planned as
> future work.

## Policy Enforcement Points

GenXAI enforces policies at multiple layers:

1. **Tool Execution**: Before executing any tool, the policy engine checks if the current user has `TOOL_EXECUTE` permission for that specific tool resource.
2. **Agent Execution**: Before running an agent, the policy engine validates `AGENT_EXECUTE` permission.
3. **Memory Access**: Read/write operations on shared memory require `MEMORY_READ`/`MEMORY_WRITE` permissions.
4. **Trigger Activation** *(planned)*: Dedicated trigger-level permissions for triggers (webhook, schedule, queue, file watcher) are not yet implemented.
5. **Connector Operations** *(planned)*: Dedicated connector-level permissions for connectors (webhook, Kafka, SQS, Postgres CDC) are not yet implemented.

## Best Practices

- **Principle of Least Privilege**: Grant only the minimum permissions required for each user/role.
- **Resource-Specific Rules**: Use fine-grained ACLs for sensitive tools, agents, and memory resources.
- **Audit Logging**: Enable audit logging to track all policy decisions and access attempts.
- **Regular Reviews**: Periodically review and update policies as your system evolves.
