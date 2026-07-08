"""Unit tests for DelegatorFlow: LLM-driven packet routing to typed workers."""

import json

import pytest

from genxai.core.agent.base import AgentFactory
from genxai.core.agent.registry import AgentRegistry
from genxai.core.agent.runtime import AgentRuntime
from genxai.flows import FLOW_TYPES, DelegatorFlow


@pytest.fixture(autouse=True)
def _clean_registry():
    AgentRegistry.clear()
    yield
    AgentRegistry.clear()


def _fake_execute(outputs_by_agent, calls=None):
    """Fake AgentRuntime.execute returning canned per-agent outputs."""

    async def execute(self, task, context=None, **kwargs):
        agent_id = self.agent.id
        if calls is not None:
            calls.append({"agent_id": agent_id, "task": task})
        output = outputs_by_agent.get(agent_id, f"output-from-{agent_id}")
        if callable(output):
            output = output()
        return {"agent_id": agent_id, "task": task, "status": "completed", "output": output}

    return execute


def _agents():
    return [
        AgentFactory.create_agent(id="lead", role="Delegator", goal="Route work"),
        AgentFactory.create_agent(
            id="w_research",
            role="Researcher",
            goal="Research",
            metadata={"worker_tag": "researcher"},
        ),
        AgentFactory.create_agent(
            id="w_write",
            role="Writer",
            goal="Write",
            metadata={"worker_tag": "writer"},
        ),
    ]


def _delegation_json(packets) -> str:
    return json.dumps({"packets": packets})


_TWO_PACKETS = _delegation_json(
    [
        {"id": "p_research", "worker": "researcher", "objective": "Find facts"},
        {
            "id": "p_write",
            "worker": "writer",
            "objective": "Write from the research",
            "depends_on": ["p_research"],
        },
    ]
)


class TestDelegatorFlow:
    def test_registered_in_flow_types(self):
        assert FLOW_TYPES["delegator_worker"] is DelegatorFlow

    def test_requires_delegator_and_worker(self):
        with pytest.raises(ValueError, match="at least one worker"):
            DelegatorFlow([AgentFactory.create_agent(id="solo", role="r", goal="g")])

    @pytest.mark.asyncio
    async def test_packets_route_to_tagged_workers_in_waves(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            AgentRuntime,
            "execute",
            _fake_execute(
                {
                    "lead": _TWO_PACKETS,
                    "w_research": "research result",
                    "w_write": "final article",
                },
                calls,
            ),
        )

        flow = DelegatorFlow(_agents())
        state = await flow.run("Write an article about solar power")

        assert state["packet_results"] == {
            "p_research": "research result",
            "p_write": "final article",
        }
        assert [call["agent_id"] for call in calls] == ["lead", "w_research", "w_write"]
        # The dependent packet's task carries its dependency's result.
        assert "research result" in calls[2]["task"]
        # Delegation is recorded in state for observability.
        assert len(state["delegation"]["packets"]) == 2

    @pytest.mark.asyncio
    async def test_invalid_delegation_retried_with_feedback(self, monkeypatch):
        calls = []
        responses = iter(["not json at all", _TWO_PACKETS])
        monkeypatch.setattr(
            AgentRuntime,
            "execute",
            _fake_execute({"lead": lambda: next(responses)}, calls),
        )

        flow = DelegatorFlow(_agents())
        state = await flow.run("topic")

        assert "p_write" in state["packet_results"]
        retry_task = calls[1]["task"]
        assert "previous response was invalid" in retry_task

    @pytest.mark.asyncio
    async def test_unknown_worker_tag_exhausts_retries(self, monkeypatch):
        bad = _delegation_json([{"id": "p1", "worker": "ghost_worker", "objective": "Do things"}])
        monkeypatch.setattr(AgentRuntime, "execute", _fake_execute({"lead": bad}))

        flow = DelegatorFlow(_agents(), max_parse_retries=1)
        with pytest.raises(ValueError, match="ghost_worker"):
            await flow.run("topic")

    @pytest.mark.asyncio
    async def test_worker_tag_falls_back_to_role(self, monkeypatch):
        agents = [
            AgentFactory.create_agent(id="lead2", role="Delegator", goal="Route"),
            AgentFactory.create_agent(id="w1", role="Analyst", goal="Analyze"),
        ]
        delegation = _delegation_json(
            [{"id": "p1", "worker": "Analyst", "objective": "Analyze the data"}]
        )
        monkeypatch.setattr(
            AgentRuntime,
            "execute",
            _fake_execute({"lead2": delegation, "w1": "analysis done"}),
        )

        state = await DelegatorFlow(agents).run("data")

        assert state["packet_results"] == {"p1": "analysis done"}
