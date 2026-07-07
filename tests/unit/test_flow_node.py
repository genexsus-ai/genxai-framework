"""Tests for FlowNode: multi-agent flow patterns embedded as graph nodes."""

import pytest

from genxai.core.agent.base import AgentFactory
from genxai.core.agent.registry import AgentRegistry
from genxai.core.agent.runtime import AgentRuntime
from genxai.core.graph.engine import Graph, GraphExecutionError
from genxai.core.graph.edges import Edge
from genxai.core.graph.nodes import FlowNode, InputNode, NodeType, OutputNode
from genxai.flows import FLOW_TYPES


@pytest.fixture(autouse=True)
def _clean_registry():
    AgentRegistry.clear()
    yield
    AgentRegistry.clear()


def _fake_execute(outputs_by_agent, calls=None):
    """Build a fake AgentRuntime.execute returning canned per-agent outputs."""

    async def execute(self, task, context=None, **kwargs):
        agent_id = self.agent.id
        if calls is not None:
            calls.append({"agent_id": agent_id, "task": task})
        output = outputs_by_agent.get(agent_id, f"output-from-{agent_id}")
        if callable(output):
            output = output()
        return {
            "agent_id": agent_id,
            "task": task,
            "status": "completed",
            "output": output,
        }

    return execute


def _two_agents():
    return [
        AgentFactory.create_agent(id="fa_1", role="r1", goal="g1"),
        AgentFactory.create_agent(id="fa_2", role="r2", goal="g2"),
    ]


class TestFlowInstantiability:
    """Regression: runtime-orchestrated flows must be instantiable.

    build_graph() was @abstractmethod but six flows only override run(),
    which made them uninstantiable ABCs.
    """

    def test_all_registered_flow_types_instantiate(self):
        for name, cls in FLOW_TYPES.items():
            flow = cls(_two_agents())
            assert flow.agents, name

    def test_runtime_flow_build_graph_raises_not_implemented(self):
        flow = FLOW_TYPES["ensemble_voting"](_two_agents())
        with pytest.raises(NotImplementedError):
            flow.build_graph()


class TestFlowNodeConstruction:
    def test_config_data_shape(self):
        node = FlowNode(
            id="flow_1",
            flow_type="critic_review",
            agents=[{"role": "writer", "goal": "draft"}],
            params={"max_iterations": 2},
            task="Write about {{ input.topic }}",
        )
        assert node.type == NodeType.FLOW
        assert node.config.data["flow_type"] == "critic_review"
        assert node.config.data["params"] == {"max_iterations": 2}
        assert node.config.data["task"] == "Write about {{ input.topic }}"

    def test_task_omitted_when_not_given(self):
        node = FlowNode(id="flow_1", flow_type="parallel", agents=[{"role": "a", "goal": "b"}])
        assert "task" not in node.config.data


class TestFlowNodeExecution:
    @pytest.mark.asyncio
    async def test_ensemble_voting_runs_in_graph(self, monkeypatch):
        """input -> flow(ensemble_voting, 3 agents) -> output; majority wins."""
        outputs = {
            "flow_1_agent_1": "blue",
            "flow_1_agent_2": "red",
            "flow_1_agent_3": "blue",
        }
        calls = []
        monkeypatch.setattr(AgentRuntime, "execute", _fake_execute(outputs, calls))

        graph = Graph(name="test")
        graph.add_node(InputNode(id="inp"))
        graph.add_node(
            FlowNode(
                id="flow_1",
                flow_type="ensemble_voting",
                agents=[
                    {"role": "voter", "goal": "pick a color"},
                    {"role": "voter", "goal": "pick a color"},
                    {"role": "voter", "goal": "pick a color"},
                ],
                task="Pick: {{ input.question }}",
            )
        )
        graph.add_node(OutputNode(id="out"))
        graph.add_edge(Edge(source="inp", target="flow_1"))
        graph.add_edge(Edge(source="flow_1", target="out"))

        state = await graph.run(input_data={"question": "favorite color?"})

        node_result = state["flow_1"]
        assert node_result["flow_type"] == "ensemble_voting"
        assert node_result["result"]["winner"] == "blue"
        assert node_result["result"]["votes"] == {"blue": 2, "red": 1}
        # Task template resolved against workflow state before reaching agents
        assert all(c["task"] == "Pick: favorite color?" for c in calls)
        assert len(calls) == 3

    @pytest.mark.asyncio
    async def test_critic_review_params_honored_and_unknown_dropped(self, monkeypatch):
        """max_iterations reaches the flow; unknown params are dropped, not fatal."""
        calls = []
        monkeypatch.setattr(AgentRuntime, "execute", _fake_execute({}, calls))

        graph = Graph(name="test")
        graph.add_node(
            FlowNode(
                id="review_1",
                flow_type="critic_review",
                agents=[
                    {"role": "generator", "goal": "draft"},
                    {"role": "critic", "goal": "critique"},
                ],
                params={"max_iterations": 2, "bogus_param": 99},
                task="Draft the doc",
            )
        )
        state = await graph.run(input_data={"topic": "x"})

        result = state["review_1"]["result"]
        assert len(result["drafts"]) == 2  # generator ran max_iterations times
        assert result["final"] == result["drafts"][-1]
        # generator + critic per iteration
        assert len(calls) == 4

    @pytest.mark.asyncio
    async def test_unknown_flow_type_raises(self):
        graph = Graph(name="test")
        node = FlowNode(
            id="flow_1", flow_type="does_not_exist", agents=[{"role": "a", "goal": "b"}]
        )
        graph.add_node(node)
        with pytest.raises(GraphExecutionError, match="unknown flow_type"):
            await graph._execute_flow_node(node, {"input": {}}, 100)

    @pytest.mark.asyncio
    async def test_no_agents_raises(self):
        graph = Graph(name="test")
        node = FlowNode(id="flow_1", flow_type="ensemble_voting", agents=[])
        graph.add_node(node)
        with pytest.raises(GraphExecutionError, match="no agents"):
            await graph._execute_flow_node(node, {"input": {}}, 100)

    @pytest.mark.asyncio
    async def test_pattern_state_keys_seeded(self, monkeypatch):
        """config.data['state'] entries (e.g. critic_task) reach the flow's state."""
        calls = []
        monkeypatch.setattr(AgentRuntime, "execute", _fake_execute({}, calls))

        graph = Graph(name="test")
        node = FlowNode(
            id="review_1",
            flow_type="critic_review",
            agents=[
                {"role": "generator", "goal": "draft"},
                {"role": "critic", "goal": "critique"},
            ],
            params={"max_iterations": 1},
            task="Generate {{ input.thing }}",
        )
        node.config.data["state"] = {"critic_task": "Critique the {{ input.thing }}"}
        graph.add_node(node)

        await graph.run(input_data={"thing": "essay"})

        tasks = [c["task"] for c in calls]
        assert tasks == ["Generate essay", "Critique the essay"]


class TestExecutorMapping:
    def test_build_graph_maps_flow_dict(self):
        from genxai.core.graph.executor import WorkflowExecutor

        executor = WorkflowExecutor(register_builtin_tools=False)
        graph = executor._build_graph(
            nodes=[
                {"id": "inp", "type": "input", "config": {}},
                {
                    "id": "team_1",
                    "type": "flow",
                    "config": {
                        "flow_type": "map_reduce",
                        "agents": [
                            {"role": "mapper", "goal": "map"},
                            {"role": "reducer", "goal": "reduce"},
                        ],
                        "params": {},
                        "task": "Process {{ input.data }}",
                        "state": {"extra_key": "value"},
                    },
                },
                {"id": "out", "type": "output", "config": {}},
            ],
            edges=[
                {"source": "inp", "target": "team_1"},
                {"source": "team_1", "target": "out"},
            ],
        )

        node = graph.nodes["team_1"]
        assert node.type == NodeType.FLOW
        assert node.config.data["flow_type"] == "map_reduce"
        assert len(node.config.data["agents"]) == 2
        assert node.config.data["state"] == {"extra_key": "value"}
