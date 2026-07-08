"""Delegator-worker flow: LLM-driven task routing to typed workers.

Unlike :class:`CoordinatorWorkerFlow` — which sends the same task to every
worker — the delegator agent here produces a structured ``DelegationPlan``
routing typed work packets to specific workers, and packets execute in
dependency waves (independent packets in one wave run concurrently).

Agent order: agents[0] is the delegator; agents[1:] are workers. A worker is
addressed by its tag: ``agent.config.metadata['worker_tag']`` when set,
otherwise its role.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from genxai.core.agent.runtime import AgentRuntime
from genxai.flows.base import FlowOrchestrator

logger = logging.getLogger(__name__)

DEFAULT_DELEGATION_TASK = (
    "Break the objective into work packets and route each to the most " "suitable worker."
)


def _worker_tag(agent: Any) -> str:
    metadata = getattr(agent.config, "metadata", None) or {}
    return metadata.get("worker_tag") or agent.config.role


def _result_text(result: Any) -> str:
    if isinstance(result, dict):
        return str(result.get("output", ""))
    return str(result)


class DelegatorFlow(FlowOrchestrator):
    """Delegator agent routes structured work packets to workers by tag."""

    def __init__(
        self,
        agents: list[Any],
        name: str = "delegator_flow",
        llm_provider: Any = None,
        max_parse_retries: int = 2,
    ) -> None:
        super().__init__(agents=agents, name=name, llm_provider=llm_provider)
        if len(self.agents) < 2:
            raise ValueError("DelegatorFlow requires a delegator and at least one worker")
        self.max_parse_retries = max_parse_retries

    def _delegation_prompt(self, objective: str, worker_tags: list[str]) -> str:
        # Local import: genxai.builder imports genxai.flows (capability
        # catalog), so importing builder symbols at module scope would cycle.
        from genxai.builder.schemas import DelegationPlan
        from genxai.utils.structured import schema_instructions

        return (
            f"Objective:\n{objective}\n\n"
            f"Available workers (route every packet's 'worker' to one of these "
            f"tags): {worker_tags}\n"
            "Give each packet a unique id, a precise objective, and depends_on "
            "for packets that need another packet's result first.\n\n"
            f"{schema_instructions(DelegationPlan)}"
        )

    async def _delegate(
        self,
        runtime: AgentRuntime,
        objective: str,
        worker_tags: list[str],
        state: dict[str, Any],
    ) -> Any:
        from genxai.builder.schemas import DelegationPlan
        from genxai.utils.structured import parse_json_loosely

        task = self._delegation_prompt(objective, worker_tags)
        last_error = "no response"
        for _attempt in range(self.max_parse_retries + 1):
            result = await self._execute_with_retry(runtime, task=task, context=state)
            text = _result_text(result)
            payload = parse_json_loosely(text)
            if payload is not None:
                try:
                    plan = DelegationPlan.model_validate(payload)
                except ValueError as exc:
                    last_error = str(exc)
                else:
                    unknown = {packet.worker for packet in plan.packets} - set(worker_tags)
                    if not unknown:
                        return plan
                    last_error = (
                        f"packets routed to unknown workers {sorted(unknown)}; "
                        f"valid tags: {worker_tags}"
                    )
            else:
                last_error = "response contained no parseable JSON"

            logger.warning("Delegation parse failed: %s", last_error)
            task = (
                f"{self._delegation_prompt(objective, worker_tags)}\n\n"
                f"Your previous response was invalid: {last_error}\n"
                f"Previous response:\n{text}"
            )

        raise ValueError(
            f"delegator produced no valid DelegationPlan after "
            f"{self.max_parse_retries + 1} attempts: {last_error}"
        )

    async def run(
        self,
        input_data: Any,
        state: dict[str, Any] | None = None,
        max_iterations: int = 100,
    ) -> dict[str, Any]:
        if state is None:
            state = {}
        state["input"] = input_data
        state.setdefault("packet_results", {})

        delegator = self.agents[0]
        workers = self.agents[1:]
        workers_by_tag = {_worker_tag(agent): agent for agent in workers}
        if len(workers_by_tag) != len(workers):
            raise ValueError("worker tags must be unique across worker agents")

        delegator_runtime = AgentRuntime(agent=delegator, llm_provider=self.llm_provider)
        worker_runtimes = {
            tag: AgentRuntime(agent=agent, llm_provider=self.llm_provider)
            for tag, agent in workers_by_tag.items()
        }

        objective = state.get("task") or DEFAULT_DELEGATION_TASK
        if isinstance(input_data, str) and input_data:
            objective = f"{objective}\nInput: {input_data}"

        plan = await self._delegate(delegator_runtime, objective, sorted(workers_by_tag), state)
        state["delegation"] = plan.model_dump()

        for wave in plan.dispatch_waves():
            tasks = []
            for packet in wave:
                dependency_results = {
                    dep: state["packet_results"].get(dep) for dep in packet.depends_on
                }
                packet_task = (
                    f"{packet.objective}\n"
                    f"Packet context: {json.dumps(packet.context, default=str)}\n"
                    f"Results from packets you depend on: "
                    f"{json.dumps(dependency_results, default=str)}"
                )
                tasks.append(
                    self._execute_with_retry(
                        worker_runtimes[packet.worker],
                        task=packet_task,
                        context={**state, "packet_id": packet.id},
                    )
                )
            results = await self._gather_tasks(tasks)
            for packet, result in zip(wave, results, strict=True):
                state["packet_results"][packet.id] = _result_text(result)

        return state
