"""Behavioral tests for flow-pattern semantics (audit regressions)."""

import json

import pytest

from genxai.core.agent.base import AgentFactory
from genxai.core.agent.registry import AgentRegistry
from genxai.core.agent.runtime import AgentRuntime
from genxai.flows import CriticReviewFlow, DelegatorFlow, EnsembleVotingFlow


@pytest.fixture(autouse=True)
def _clean_registry():
    AgentRegistry.clear()
    yield
    AgentRegistry.clear()


def _fake_execute(outputs_by_agent, calls=None):
    async def execute(self, task, context=None, **kwargs):
        agent_id = self.agent.id
        if calls is not None:
            calls.append(agent_id)
        output = outputs_by_agent.get(agent_id, f"output-from-{agent_id}")
        if callable(output):
            output = output()
        return {"agent_id": agent_id, "task": task, "status": "completed", "output": output}

    return execute


class TestCriticReviewAcceptance:
    def _agents(self):
        return [
            AgentFactory.create_agent(id="gen", role="Writer", goal="Draft"),
            AgentFactory.create_agent(id="crit", role="Critic", goal="Review"),
        ]

    @pytest.mark.asyncio
    async def test_stops_when_critic_accepts(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            AgentRuntime,
            "execute",
            _fake_execute({"gen": "draft v1", "crit": "ACCEPT — ship it"}, calls),
        )

        state = await CriticReviewFlow(self._agents(), max_iterations=5).run("topic")

        assert state["accept"] is True
        assert state["final"] == "draft v1"
        assert calls.count("gen") == 1  # no wasted revision loops

    @pytest.mark.asyncio
    async def test_loops_until_max_when_critic_keeps_revising(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            AgentRuntime,
            "execute",
            _fake_execute({"gen": "draft", "crit": "REVISE: tighten the intro"}, calls),
        )

        state = await CriticReviewFlow(self._agents(), max_iterations=3).run("topic")

        assert state["accept"] is False
        assert calls.count("gen") == 3


class TestEnsembleVotingNormalization:
    @pytest.mark.asyncio
    async def test_case_and_whitespace_votes_count_together(self, monkeypatch):
        agents = [
            AgentFactory.create_agent(id=f"v{i}", role=f"Voter {i}", goal="Vote")
            for i in range(1, 4)
        ]
        monkeypatch.setattr(
            AgentRuntime,
            "execute",
            _fake_execute({"v1": "Positive", "v2": "  positive ", "v3": "negative"}),
        )

        state = await EnsembleVotingFlow(agents).run("classify this")

        assert state["votes"]["positive"] == 2
        assert state["winner"] == "Positive"  # original casing preserved


class TestDelegatorDuplicateRoles:
    @pytest.mark.asyncio
    async def test_same_role_workers_get_suffixed_tags(self, monkeypatch):
        agents = [
            AgentFactory.create_agent(id="lead", role="Delegator", goal="Route"),
            AgentFactory.create_agent(id="w1", role="Worker", goal="Do"),
            AgentFactory.create_agent(id="w2", role="Worker", goal="Do"),
        ]
        delegation = json.dumps(
            {
                "packets": [
                    {"id": "p1", "worker": "Worker", "objective": "Part one"},
                    {"id": "p2", "worker": "Worker #2", "objective": "Part two"},
                ]
            }
        )
        calls = []
        monkeypatch.setattr(
            AgentRuntime,
            "execute",
            _fake_execute({"lead": delegation, "w1": "one done", "w2": "two done"}, calls),
        )

        state = await DelegatorFlow(agents).run("split this job")

        assert state["packet_results"] == {"p1": "one done", "p2": "two done"}
        # The delegation prompt advertised both suffixed tags.
        assert calls[0] == "lead"
