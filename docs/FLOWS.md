# Flow Orchestrators

Flow orchestrators are lightweight wrappers that build and execute
graph workflows for common coordination patterns. They expose a
simple API without introducing new engine abstractions.

## Available Flows

### `RoundRobinFlow`
Executes agents in a fixed order (A → B → C → ...).

```python
from genxai import AgentFactory, RoundRobinFlow

agents = [
    AgentFactory.create_agent(id="analyst", role="Analyst", goal="Analyze"),
    AgentFactory.create_agent(id="writer", role="Writer", goal="Write"),
]

flow = RoundRobinFlow(agents)
result_state = await flow.run({"topic": "market trends"})
```

### `SelectorFlow`
Chooses the next agent dynamically using a selector callback.

```python
from genxai import AgentFactory, SelectorFlow

def choose_next(state, agent_ids):
    return agent_ids[state.get("selector_hop", 0) % len(agent_ids)]

agents = [
    AgentFactory.create_agent(id="planner", role="Planner", goal="Plan"),
    AgentFactory.create_agent(id="builder", role="Builder", goal="Build"),
]

flow = SelectorFlow(agents, selector=choose_next, max_hops=3)
result_state = await flow.run({"goal": "Launch"})
```

### `P2PFlow`
Runs a peer-to-peer loop with lightweight consensus stopping.

```python
from genxai import AgentFactory, P2PFlow

agents = [
    AgentFactory.create_agent(id="a1", role="Analyst", goal="Contribute"),
    AgentFactory.create_agent(id="a2", role="Reviewer", goal="Review"),
]

flow = P2PFlow(
    agents,
    max_rounds=4,
    consensus_threshold=0.75,
    convergence_window=2,
    quality_threshold=0.8,
)
result_state = await flow.run({"topic": "Architecture"})
```

### `ParallelFlow`
Executes agents in parallel from a shared input.

```python
from genxai import AgentFactory, ParallelFlow

agents = [
    AgentFactory.create_agent(id="a1", role="Analyst", goal="Analyze"),
    AgentFactory.create_agent(id="a2", role="Reviewer", goal="Review"),
]

flow = ParallelFlow(agents)
result_state = await flow.run({"topic": "Parallel insights"})
```

### `ConditionalFlow`
Routes to a single agent based on a condition function.

```python
from genxai import AgentFactory, ConditionalFlow

def choose_agent(state):
    return "analyst" if state.get("priority") == "high" else "reviewer"

agents = [
    AgentFactory.create_agent(id="analyst", role="Analyst", goal="Analyze"),
    AgentFactory.create_agent(id="reviewer", role="Reviewer", goal="Review"),
]

flow = ConditionalFlow(agents, condition=choose_agent)
result_state = await flow.run({"priority": "high"})
```

### `LoopFlow`
Iterates an agent until a condition key becomes truthy or a max count is reached.

```python
from genxai import AgentFactory, LoopFlow

agents = [AgentFactory.create_agent(id="loop_agent", role="Loop", goal="Iterate")]

flow = LoopFlow(agents, condition_key="done", max_iterations=3)
result_state = await flow.run({"done": False})
```

### `RouterFlow`
Routes to an agent using deterministic routing rules.

```python
from genxai import AgentFactory, RouterFlow

def router(state):
    return "planner" if state.get("route") == "plan" else "executor"

agents = [
    AgentFactory.create_agent(id="planner", role="Planner", goal="Plan"),
    AgentFactory.create_agent(id="executor", role="Executor", goal="Execute"),
]

flow = RouterFlow(agents, router=router)
result_state = await flow.run({"route": "plan"})
```

### `EnsembleVotingFlow`
Runs multiple agents and returns the most common output.

```python
from genxai import AgentFactory, EnsembleVotingFlow

agents = [
    AgentFactory.create_agent(id="a1", role="Analyst", goal="Answer"),
    AgentFactory.create_agent(id="a2", role="Reviewer", goal="Answer"),
]

flow = EnsembleVotingFlow(agents)
result_state = await flow.run({"question": "Best architecture?"})
```

### `CriticReviewFlow`
Generator → critic loop with bounded iterations.

```python
from genxai import AgentFactory, CriticReviewFlow

agents = [
    AgentFactory.create_agent(id="writer", role="Writer", goal="Draft"),
    AgentFactory.create_agent(id="critic", role="Critic", goal="Review"),
]

flow = CriticReviewFlow(agents, max_iterations=2)
result_state = await flow.run({"topic": "Launch"})
```

You can short-circuit the review loop by setting `state["accept"] = True`
from within your workflow state.

### `CoordinatorWorkerFlow`
Coordinator assigns work to worker agents.

```python
from genxai import AgentFactory, CoordinatorWorkerFlow

agents = [
    AgentFactory.create_agent(id="coordinator", role="Coordinator", goal="Plan"),
    AgentFactory.create_agent(id="worker1", role="Worker", goal="Execute"),
]

flow = CoordinatorWorkerFlow(agents)
result_state = await flow.run({"task": "Ship release"})
```

### `MapReduceFlow`
Multiple mappers followed by reducer aggregation.

```python
from genxai import AgentFactory, MapReduceFlow

agents = [
    AgentFactory.create_agent(id="mapper1", role="Mapper", goal="Process"),
    AgentFactory.create_agent(id="mapper2", role="Mapper", goal="Process"),
    AgentFactory.create_agent(id="reducer", role="Reducer", goal="Summarize"),
]

flow = MapReduceFlow(agents)
result_state = await flow.run({"data": "sharded input"})
```

### `SubworkflowFlow`
Runs a pre-built graph as a flow.

```python
from genxai import Graph, SubworkflowFlow

flow = SubworkflowFlow(Graph(name="subgraph"))
result_state = await flow.run({"topic": "Subworkflow"})
```

### `AuctionFlow`
Agents bid to take the task; highest bid executes.

```python
from genxai import AgentFactory, AuctionFlow

agents = [
    AgentFactory.create_agent(id="bidder1", role="Bidder", goal="Bid"),
    AgentFactory.create_agent(id="bidder2", role="Bidder", goal="Bid"),
]

flow = AuctionFlow(agents)
result_state = await flow.run({"task": "Handle request"})
```

## Using flows inside graph workflows

Flows can also run as a single node inside a graph workflow via `FlowNode`
(`NodeType.FLOW`). The engine looks up the orchestrator class in
`genxai.flows.FLOW_TYPES`, builds the agents with `AgentFactory` from the
ordered specs, and stores the flow's output under the node id.

Eight patterns are addressable by name: `round_robin`, `parallel`, `auction`,
`coordinator_worker`, `critic_review`, `ensemble_voting`, `map_reduce`, and
`p2p`. The conditional, router, selector, and subworkflow flows are excluded
from `FLOW_TYPES` because they need Python callables (or a pre-built graph)
that cannot be expressed in a serialized node config.

```python
from genxai.core.graph.nodes import FlowNode

node = FlowNode(
    id="review_loop",
    flow_type="critic_review",
    agents=[
        {"role": "Writer", "goal": "Draft the summary"},
        {"role": "Critic", "goal": "Review the draft"},
    ],
    params={"max_iterations": 2},
    task="Summarize: {{ input }}",
)
```

Or as a serialized node dict in a workflow definition:

```json
{
  "id": "review_loop",
  "type": "flow",
  "config": {
    "flow_type": "critic_review",
    "agents": [
      {"role": "Writer", "goal": "Draft the summary"},
      {"role": "Critic", "goal": "Review the draft"}
    ],
    "params": {"max_iterations": 2},
    "task": "Summarize: {{ input }}"
  }
}
```

Behavior details:

- **Agent specs**: each entry supports `role`, `goal`, and optional
  `backstory`, `llm_model`, `temperature`, and `tools`. Order carries meaning
  per pattern: coordinator first for `coordinator_worker`, critic second for
  `critic_review`, reducer last for `map_reduce`, and so on.
- **Params**: extra constructor kwargs for the flow (e.g. `max_rounds`,
  `consensus_threshold`). Keys the flow's constructor does not accept are
  dropped with a warning.
- **Templates**: `{{ ... }}` placeholders in `task`, `state`, and `input` are
  resolved against the workflow state before the flow runs. `input` defaults
  to the workflow's input when omitted.
- **Result**: the node stores `{"flow_type": ..., "result": ...}` in the
  workflow state under the node's id (also visible in
  `state["node_results"]["<node_id>"]["output"]`), so downstream nodes can
  reference it, e.g. `{{ review_loop.result }}`.

> Note: an earlier bug made several runtime flows (including auction,
> critic-review, and map-reduce) uninstantiable due to an abstract
> `build_graph` declaration. This has been fixed; all eight `FLOW_TYPES`
> patterns above can be constructed and run.

## Notes
- These flows register agents in `AgentRegistry` automatically.
- Flow orchestrators include default execution safeguards: 120s timeout per agent call,
  3 retries with exponential backoff (1s base, 2x multiplier), and optional cancellation
  of parallel tasks on first failure (configurable via `cancel_on_failure`).
- Override safeguards by passing settings into any flow constructor (see examples).
- `SelectorFlow` uses a callback to pick the next agent each hop.
- `P2PFlow` executes agents directly to allow decentralized patterns.
- P2P termination checks include consensus, convergence, timeout, and quality thresholds.
- Additional flows include parallel, conditional routing, loop, router, ensemble voting,
  critic-review, coordinator-worker, map-reduce, subworkflow, and auction patterns.