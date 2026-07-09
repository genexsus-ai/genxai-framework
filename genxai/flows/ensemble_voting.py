"""Ensemble voting flow orchestrator."""

from typing import Any

from genxai.core.agent.runtime import AgentRuntime
from genxai.flows.base import FlowOrchestrator


class EnsembleVotingFlow(FlowOrchestrator):
    """Run all agents and aggregate outputs via simple voting."""

    def __init__(
        self,
        agents: list[Any],
        name: str = "ensemble_voting_flow",
        llm_provider: Any = None,
    ) -> None:
        super().__init__(agents=agents, name=name, llm_provider=llm_provider)

    async def run(
        self,
        input_data: Any,
        state: dict[str, Any] | None = None,
        max_iterations: int = 100,
    ) -> dict[str, Any]:
        if state is None:
            state = {}
        state["input"] = input_data
        state.setdefault("votes", {})

        runtimes = {
            agent.id: AgentRuntime(agent=agent, llm_provider=self.llm_provider)
            for agent in self.agents
        }

        task = state.get("task", "Provide your answer")
        tasks = [
            self._execute_with_retry(runtimes[agent.id], task=task, context=state)
            for agent in self.agents
        ]
        results = await self._gather_tasks(tasks)
        originals: dict[str, str] = {}
        for result in results:
            output = str(getattr(result, "get", lambda *_: "")("output", "")).strip()
            # Vote on a normalized form so trivial case/whitespace differences
            # between otherwise-identical answers still count together.
            key = " ".join(output.split()).lower()
            originals.setdefault(key, output)
            state["votes"].setdefault(key, 0)
            state["votes"][key] += 1

        if state["votes"]:
            winning_key = max(state["votes"], key=state["votes"].get)
            state["winner"] = originals[winning_key]
        return state
