"""Graph execution engine for orchestrating agent workflows."""

import asyncio
import copy
import logging
import time
from collections import defaultdict, deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from genxai.core.agent.registry import AgentRegistry
from genxai.core.agent.runtime import AgentRuntime
from genxai.core.graph.checkpoints import (
    WorkflowCheckpoint,
    WorkflowCheckpointManager,
    create_checkpoint,
)
from genxai.core.graph.edges import Edge
from genxai.core.graph.interpolation import TemplateResolutionError, resolve_templates
from genxai.core.graph.nodes import Node, NodeConfig, NodeStatus, NodeType
from genxai.core.memory.shared import SharedMemoryBus
from genxai.tools.registry import ToolRegistry
from genxai.utils.runtime_services import (
    record_exception,
    record_workflow_execution,
    record_workflow_node_execution,
    span,
)

logger = logging.getLogger(__name__)


class GraphExecutionError(Exception):
    """Exception raised during graph execution."""

    pass


class Graph:
    """Main graph class for orchestrating agent workflows."""

    def __init__(self, name: str = "workflow") -> None:
        """Initialize the graph.

        Args:
            name: Name of the workflow graph
        """
        self.name = name
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self._adjacency_list: dict[str, list[Edge]] = defaultdict(list)
        self._reverse_adjacency: dict[str, list[str]] = defaultdict(list)
        self.shared_memory: SharedMemoryBus | None = None
        # Per-run join bookkeeping (rebuilt at the start of each run()).
        # Edges are keyed by their index in self.edges because Edge.__hash__
        # collides for duplicate (source, target) pairs.
        self._edge_index: dict[int, int] = {}
        self._incoming_edge_indices: dict[str, list[int]] = {}
        self._back_edge_keys: set[int] = set()

    def add_node(self, node: Node) -> None:
        """Add a node to the graph.

        Args:
            node: Node to add

        Raises:
            ValueError: If node with same ID already exists
        """
        if node.id in self.nodes:
            raise ValueError(f"Node with id '{node.id}' already exists")

        self.nodes[node.id] = node
        logger.debug(f"Added node: {node.id} (type: {node.type})")

    def add_edge(self, edge: Edge) -> None:
        """Add an edge to the graph.

        Args:
            edge: Edge to add

        Raises:
            ValueError: If source or target node doesn't exist
        """
        if edge.source not in self.nodes:
            raise ValueError(f"Source node '{edge.source}' not found")
        if edge.target not in self.nodes:
            raise ValueError(f"Target node '{edge.target}' not found")

        self.edges.append(edge)
        self._adjacency_list[edge.source].append(edge)
        self._reverse_adjacency[edge.target].append(edge.source)
        logger.debug(f"Added edge: {edge.source} -> {edge.target}")

    def set_shared_memory(self, shared_memory: SharedMemoryBus | None) -> None:
        """Attach a shared memory bus to the graph for agent execution."""
        self.shared_memory = shared_memory

    def get_node(self, node_id: str) -> Node | None:
        """Get a node by ID.

        Args:
            node_id: ID of the node

        Returns:
            Node if found, None otherwise
        """
        return self.nodes.get(node_id)

    def get_outgoing_edges(self, node_id: str) -> list[Edge]:
        """Get all outgoing edges from a node.

        Args:
            node_id: ID of the node

        Returns:
            List of outgoing edges
        """
        return self._adjacency_list.get(node_id, [])

    def get_incoming_nodes(self, node_id: str) -> list[str]:
        """Get all nodes with edges pointing to this node.

        Args:
            node_id: ID of the node

        Returns:
            List of incoming node IDs
        """
        return self._reverse_adjacency.get(node_id, [])

    def validate(self) -> bool:
        """Validate the graph structure.

        Returns:
            True if graph is valid

        Raises:
            GraphExecutionError: If graph is invalid
        """
        # Check for at least one node
        if not self.nodes:
            raise GraphExecutionError("Graph must have at least one node")

        # Check for cycles (optional - we allow cycles)
        # Check for disconnected components
        visited = self._dfs_visit(next(iter(self.nodes.keys())))
        if len(visited) != len(self.nodes):
            logger.warning("Graph has disconnected components")

        # Check that all edges reference valid nodes
        for edge in self.edges:
            if edge.source not in self.nodes or edge.target not in self.nodes:
                raise GraphExecutionError(
                    f"Edge references non-existent node: {edge.source} -> {edge.target}"
                )

        logger.info(f"Graph '{self.name}' validated successfully")
        return True

    def _dfs_visit(self, start_node: str) -> set[str]:
        """Perform DFS traversal from start node.

        Args:
            start_node: Starting node ID

        Returns:
            Set of visited node IDs
        """
        visited: set[str] = set()
        stack = [start_node]

        while stack:
            node_id = stack.pop()
            if node_id in visited:
                continue

            visited.add(node_id)

            # Add neighbors (both outgoing and incoming for undirected check)
            for edge in self.get_outgoing_edges(node_id):
                if edge.target not in visited:
                    stack.append(edge.target)

            for incoming in self.get_incoming_nodes(node_id):
                if incoming not in visited:
                    stack.append(incoming)

        return visited

    def topological_sort(self) -> list[str]:
        """Perform topological sort on the graph.

        Returns:
            List of node IDs in topological order

        Raises:
            GraphExecutionError: If graph has cycles
        """
        in_degree = dict.fromkeys(self.nodes, 0)

        for edge in self.edges:
            in_degree[edge.target] += 1

        queue: deque[str] = deque([node_id for node_id, degree in in_degree.items() if degree == 0])
        result: list[str] = []

        while queue:
            node_id = queue.popleft()
            result.append(node_id)

            for edge in self.get_outgoing_edges(node_id):
                in_degree[edge.target] -= 1
                if in_degree[edge.target] == 0:
                    queue.append(edge.target)

        if len(result) != len(self.nodes):
            raise GraphExecutionError("Graph contains cycles - cannot perform topological sort")

        return result

    async def run(
        self,
        input_data: Any,
        max_iterations: int = 100,
        state: dict[str, Any] | None = None,
        resume_from: WorkflowCheckpoint | None = None,
        llm_provider: Any = None,
        event_callback: Callable[[dict[str, Any]], Any] | None = None,
    ) -> dict[str, Any]:
        """Execute the graph workflow.

        Args:
            input_data: Input data for the workflow
            max_iterations: Maximum number of iterations (for cycle detection)
            state: Initial state dictionary

        Returns:
            Final state after execution

        Raises:
            GraphExecutionError: If execution fails
        """
        if not self.nodes:
            raise GraphExecutionError("Cannot run empty graph")

        self.validate()

        start_time = time.time()
        status = "success"

        # Initialize state
        if resume_from:
            state = resume_from.state.copy()
            state["input"] = input_data
            state.setdefault("iterations", 0)
        else:
            if state is None:
                state = {}
            state["input"] = input_data
            state["iterations"] = 0
        state.setdefault("node_events", [])

        if resume_from:
            for node_id, node_status in resume_from.node_statuses.items():
                if node_id in self.nodes:
                    self.nodes[node_id].status = NodeStatus(node_status)
            state.setdefault("_edge_resolutions", {})
        else:
            # Fresh run: clear statuses left over from a previous run so the
            # graph is reusable (completed nodes would otherwise be skipped).
            for node in self.nodes.values():
                node.status = NodeStatus.PENDING
                node.result = None
                node.error = None
            state["_edge_resolutions"] = {}

        # Build join bookkeeping: edge indices, per-node incoming edges, and
        # back edges (excluded from join waits so cycles don't deadlock).
        self._edge_index = {id(edge): i for i, edge in enumerate(self.edges)}
        self._incoming_edge_indices = defaultdict(list)
        for i, edge in enumerate(self.edges):
            self._incoming_edge_indices[edge.target].append(i)
        self._back_edge_keys = self._compute_back_edges()

        # Find entry points (nodes with no incoming edges)
        entry_points = [
            node_id for node_id in self.nodes if not self.get_incoming_nodes(node_id)
        ]

        if not entry_points:
            # If no clear entry point, look for INPUT node
            entry_points = [
                node_id for node_id, node in self.nodes.items() if node.type == NodeType.INPUT
            ]

        if not entry_points:
            raise GraphExecutionError("No entry point found in graph")

        logger.info(f"Starting graph execution: {self.name}")
        logger.debug(f"Entry points: {entry_points}")

        if llm_provider is not None:
            state["llm_provider"] = llm_provider

        # Execute from entry points
        try:
            with span("genxai.workflow.execute", {"workflow_id": self.name}):
                for entry_point in entry_points:
                    await self._execute_node(entry_point, state, max_iterations, event_callback)
        except Exception as exc:
            status = "error"
            record_exception(exc)
            raise
        finally:
            duration = time.time() - start_time
            record_workflow_execution(
                workflow_id=self.name,
                duration=duration,
                status=status,
            )

        logger.info(f"Graph execution completed: {self.name}")
        state["node_events"] = state.get("node_events", [])
        return state

    def create_checkpoint(self, name: str, state: dict[str, Any]) -> WorkflowCheckpoint:
        """Create a checkpoint from current workflow state."""
        node_statuses = {node_id: node.status for node_id, node in self.nodes.items()}
        return create_checkpoint(name=name, workflow=self.name, state=state, node_statuses=node_statuses)

    def save_checkpoint(self, name: str, state: dict[str, Any], path: Path) -> Path:
        """Persist a checkpoint to disk."""
        manager = WorkflowCheckpointManager(path)
        checkpoint = self.create_checkpoint(name=name, state=state)
        return manager.save(checkpoint)

    def load_checkpoint(self, name: str, path: Path) -> WorkflowCheckpoint:
        """Load a checkpoint from disk."""
        manager = WorkflowCheckpointManager(path)
        return manager.load(name)

    async def _execute_node(
        self,
        node_id: str,
        state: dict[str, Any],
        max_iterations: int,
        event_callback: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        """Execute a single node and its descendants.

        Args:
            node_id: ID of the node to execute
            state: Current state
            max_iterations: Maximum iterations allowed

        Raises:
            GraphExecutionError: If execution fails or max iterations exceeded
        """
        if state.get("iterations", 0) >= max_iterations:
            raise GraphExecutionError(f"Maximum iterations ({max_iterations}) exceeded")

        state["iterations"] = state.get("iterations", 0) + 1

        node = self.nodes[node_id]

        # Skip if already completed, or currently executing on a concurrent
        # branch (two parallel branches converging on a shared descendant
        # would otherwise execute it twice).
        if node.status in (NodeStatus.COMPLETED, NodeStatus.RUNNING):
            return

        # Join semantics: defer until every forward incoming edge has been
        # resolved (satisfied or declined). The branch that resolves the last
        # edge re-attempts this node, so it runs exactly once with all
        # upstream results in state.
        if not self._node_ready(node_id, state):
            return

        # Mark as running
        node.status = NodeStatus.RUNNING
        logger.debug(f"Executing node: {node_id}")
        node_start = time.time()
        running_event = {
            "node_id": node_id,
            "status": NodeStatus.RUNNING.value,
            "timestamp": time.time(),
        }
        state.setdefault("node_events", []).append(running_event)
        if event_callback:
            callback_result = event_callback(running_event)
            if asyncio.iscoroutine(callback_result):
                await callback_result

        try:
            # Execute node (placeholder - will be implemented with actual executors)
            with span(
                "genxai.workflow.node",
                {"workflow_id": self.name, "node_id": node_id, "node_type": node.type.value},
            ):
                result = await self._run_with_policy(node, state, max_iterations)
            node.result = result
            node.status = NodeStatus.COMPLETED
            logger.debug(f"Node completed: {node_id}")

            node_duration_ms = int((time.time() - node_start) * 1000)

            record_workflow_node_execution(
                workflow_id=self.name,
                node_id=node_id,
                status="success",
            )
            completed_event = {
                "node_id": node_id,
                "status": NodeStatus.COMPLETED.value,
                "timestamp": time.time(),
                "duration_ms": node_duration_ms,
            }
            state.setdefault("node_events", []).append(completed_event)
            if event_callback:
                callback_result = event_callback(completed_event)
                if asyncio.iscoroutine(callback_result):
                    await callback_result

            state.setdefault("node_results", {})[node_id] = {
                "output": result,
                "status": NodeStatus.COMPLETED.value,
                "duration_ms": node_duration_ms,
            }

            # Update state with result
            state[node_id] = result

            await self._resolve_and_traverse(node_id, state, max_iterations, event_callback)

        except Exception as e:
            node.status = NodeStatus.FAILED
            node.error = str(e)
            logger.error(f"Node execution failed: {node_id} - {e}")
            node_duration_ms = int((time.time() - node_start) * 1000)
            record_workflow_node_execution(
                workflow_id=self.name,
                node_id=node_id,
                status="error",
            )
            failed_event = {
                "node_id": node_id,
                "status": NodeStatus.FAILED.value,
                "timestamp": time.time(),
                "error": str(e),
                "duration_ms": node_duration_ms,
            }
            state.setdefault("node_events", []).append(failed_event)
            if event_callback:
                callback_result = event_callback(failed_event)
                if asyncio.iscoroutine(callback_result):
                    await callback_result
            state.setdefault("node_results", {})[node_id] = {
                "output": None,
                "status": NodeStatus.FAILED.value,
                "duration_ms": node_duration_ms,
                "error": str(e),
            }

            policy = node.config.data.get("execution") or {}
            if policy.get("continue_on_error"):
                # n8n-style "continue on fail": record the failure as the
                # node's result and keep the workflow going so downstream
                # nodes (and edge conditions) can react to it.
                error_result = {"success": False, "error": str(e)}
                node.result = error_result
                state[node_id] = error_result
                state["node_results"][node_id]["output"] = error_result
                logger.warning(
                    "Node %s failed but continue_on_error is set; continuing", node_id
                )
                await self._resolve_and_traverse(node_id, state, max_iterations, event_callback)
                return

            raise GraphExecutionError(f"Node {node_id} failed: {e}") from e

    async def _run_with_policy(
        self, node: Node, state: dict[str, Any], max_iterations: int
    ) -> Any:
        """Execute node logic under its per-node execution policy.

        The policy lives in node.config.data["execution"]:
            {"retry_count": int, "timeout_seconds": float,
             "backoff_seconds": float, "continue_on_error": bool}
        Defaults preserve prior behavior (no retry, no timeout).
        """
        policy = node.config.data.get("execution") or {}
        retries = max(int(policy.get("retry_count", 0)), 0)
        timeout = policy.get("timeout_seconds")
        backoff = float(policy.get("backoff_seconds", 1.0))

        attempt = 0
        while True:
            try:
                coro = self._execute_node_logic(node, state, max_iterations)
                if timeout:
                    return await asyncio.wait_for(coro, timeout=float(timeout))
                return await coro
            except asyncio.CancelledError:
                raise
            except Exception:
                if attempt >= retries:
                    raise
                attempt += 1
                logger.warning(
                    "Node %s attempt %d/%d failed; retrying in %.1fs",
                    node.id,
                    attempt,
                    retries + 1,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff *= 2

    async def _resolve_and_traverse(
        self,
        node_id: str,
        state: dict[str, Any],
        max_iterations: int,
        event_callback: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        """Resolve outgoing edges as satisfied/declined and execute targets.

        Declined edges cascade (see _propagate_decline) so joins behind
        untaken branches resolve instead of waiting forever.
        """
        outgoing_edges = self.get_outgoing_edges(node_id)
        resolutions = state.setdefault("_edge_resolutions", {})
        parallel_edges = [e for e in outgoing_edges if e.metadata.get("parallel", False)]
        sequential_edges = [e for e in outgoing_edges if not e.metadata.get("parallel", False)]

        # Parallel edges: resolve upfront, then execute taken ones concurrently
        tasks = []
        declined_targets: list[str] = []
        for edge in parallel_edges:
            edge_key = str(self._edge_index.get(id(edge), -1))
            if edge.evaluate_condition(state):
                resolutions[edge_key] = "satisfied"
                tasks.append(self._execute_node(edge.target, state, max_iterations, event_callback))
            else:
                resolutions[edge_key] = "declined"
                declined_targets.append(edge.target)
        for target in declined_targets:
            await self._propagate_decline(target, state, max_iterations, event_callback)
        if tasks:
            await self._gather_with_config(tasks, state)

        # Sequential edges: evaluate lazily in priority order, so a later
        # edge's condition can read results of earlier siblings.
        for edge in sorted(sequential_edges, key=lambda e: e.priority):
            edge_key = str(self._edge_index.get(id(edge), -1))
            if edge.evaluate_condition(state):
                resolutions[edge_key] = "satisfied"
                await self._execute_node(edge.target, state, max_iterations, event_callback)
            else:
                resolutions[edge_key] = "declined"
                await self._propagate_decline(edge.target, state, max_iterations, event_callback)

    def _node_ready(self, node_id: str, state: dict[str, Any]) -> bool:
        """Check whether all forward incoming edges of a node are resolved.

        Back edges (cycles) are excluded so cycle entry nodes don't wait on
        downstream nodes that haven't run yet.
        """
        resolutions = state.get("_edge_resolutions", {})
        for edge_idx in self._incoming_edge_indices.get(node_id, []):
            if edge_idx in self._back_edge_keys:
                continue
            if str(edge_idx) not in resolutions:
                return False
        return True

    async def _propagate_decline(
        self,
        node_id: str,
        state: dict[str, Any],
        max_iterations: int,
        event_callback: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        """Handle a declined incoming edge for a node.

        If other incoming edges are still unresolved, do nothing (the branch
        that resolves the last one re-attempts). If all are resolved and at
        least one was satisfied, the node is ready — execute it. If all were
        declined, the node is on a dead branch: mark it SKIPPED and cascade
        declines through its outgoing edges.
        """
        node = self.nodes.get(node_id)
        if node is None or node.status != NodeStatus.PENDING:
            return

        resolutions = state.get("_edge_resolutions", {})
        incoming = [
            i for i in self._incoming_edge_indices.get(node_id, [])
            if i not in self._back_edge_keys
        ]
        if any(str(i) not in resolutions for i in incoming):
            return
        if any(resolutions.get(str(i)) == "satisfied" for i in incoming):
            await self._execute_node(node_id, state, max_iterations, event_callback)
            return

        # Dead branch: every incoming edge declined
        node.status = NodeStatus.SKIPPED
        logger.debug(f"Node skipped (all incoming edges declined): {node_id}")
        skipped_event = {
            "node_id": node_id,
            "status": NodeStatus.SKIPPED.value,
            "timestamp": time.time(),
        }
        state.setdefault("node_events", []).append(skipped_event)
        if event_callback:
            callback_result = event_callback(skipped_event)
            if asyncio.iscoroutine(callback_result):
                await callback_result

        for edge in self.get_outgoing_edges(node_id):
            edge_key = str(self._edge_index.get(id(edge), -1))
            resolutions[edge_key] = "declined"
        for edge in self.get_outgoing_edges(node_id):
            await self._propagate_decline(edge.target, state, max_iterations, event_callback)

    def _compute_back_edges(self) -> set[int]:
        """Identify back edges (edges closing a cycle) via iterative DFS.

        Returns the set of edge indices whose target is an ancestor on the
        current DFS path. These are excluded from join readiness checks.
        """
        back_edges: set[int] = set()
        WHITE, GRAY, BLACK = 0, 1, 2
        color = dict.fromkeys(self.nodes, WHITE)

        # Prefer entry points as DFS roots so back-edge orientation matches
        # actual execution order; fall back to any unvisited node.
        entry_points = [n for n in self.nodes if not self.get_incoming_nodes(n)]
        roots = entry_points + [n for n in self.nodes if n not in entry_points]

        for root in roots:
            if color[root] != WHITE:
                continue
            color[root] = GRAY
            stack = [(root, iter(self.get_outgoing_edges(root)))]
            while stack:
                current, edge_iter = stack[-1]
                advanced = False
                for edge in edge_iter:
                    if color[edge.target] == GRAY:
                        back_edges.add(self._edge_index[id(edge)])
                    elif color[edge.target] == WHITE:
                        color[edge.target] = GRAY
                        stack.append((edge.target, iter(self.get_outgoing_edges(edge.target))))
                        advanced = True
                        break
                if not advanced:
                    color[current] = BLACK
                    stack.pop()

        return back_edges

    async def _execute_node_logic(
        self, node: Node, state: dict[str, Any], max_iterations: int
    ) -> Any:
        """Execute the actual logic of a node.

        Args:
            node: Node to execute
            state: Current state

        Returns:
            Result of node execution
        """
        if node.type == NodeType.INPUT:
            return copy.deepcopy(state.get("input"))

        if node.type == NodeType.OUTPUT:
            return copy.deepcopy(state)

        if node.type == NodeType.AGENT:
            return await self._execute_agent_node(node, state)

        if node.type == NodeType.TOOL:
            return await self._execute_tool_node(node, state)

        if node.type == NodeType.SUBGRAPH:
            return await self._execute_subgraph_node(node, state, max_iterations)

        if node.type == NodeType.LOOP:
            return await self._execute_loop_node(node, state, max_iterations)

        if node.type == NodeType.FLOW:
            return await self._execute_flow_node(node, state, max_iterations)

        # Default fallback for unsupported nodes
        return {"node_id": node.id, "type": node.type.value}

    async def _execute_agent_node(self, node: Node, state: dict[str, Any]) -> dict[str, Any]:
        """Execute an AgentNode using AgentRuntime.

        Args:
            node: Agent node to execute
            state: Current workflow state

        Returns:
            Agent execution result
        """
        agent_id = node.config.data.get("agent_id")
        if not agent_id:
            raise GraphExecutionError(
                f"Agent node '{node.id}' missing agent_id in config.data"
            )

        agent = AgentRegistry.get(agent_id)
        if agent is None:
            raise GraphExecutionError(f"Agent '{agent_id}' not found in registry")

        task = node.config.data.get("task") or state.get("task") or "Process input"
        if isinstance(task, str):
            try:
                task = resolve_templates(task, state)
            except TemplateResolutionError as exc:
                raise GraphExecutionError(f"Agent node '{node.id}': {exc}") from exc

        llm_provider = state.get("llm_provider")
        runtime = AgentRuntime(
            agent=agent,
            llm_provider=llm_provider,
            enable_memory=True,
            shared_memory=self.shared_memory,
        )
        if agent.config.tools:
            tools: dict[str, Any] = {}
            for tool_name in agent.config.tools:
                tool = ToolRegistry.get(tool_name)
                if tool:
                    tools[tool_name] = tool
            runtime.set_tools(tools)

        context = dict(state)
        if self.shared_memory is not None:
            context["shared_memory"] = self.shared_memory
        return await self._execute_with_config(runtime, task=task, context=context, state=state)

    def _get_execution_config(self, state: dict[str, Any]) -> dict[str, Any]:
        config = state.get("execution_config") or {}
        return {
            "timeout_seconds": config.get("timeout_seconds", 120.0),
            "retry_count": config.get("retry_count", 3),
            "backoff_base": config.get("backoff_base", 1.0),
            "backoff_multiplier": config.get("backoff_multiplier", 2.0),
            "cancel_on_failure": config.get("cancel_on_failure", True),
        }

    async def _execute_with_config(
        self,
        runtime: AgentRuntime,
        task: str,
        context: dict[str, Any],
        state: dict[str, Any],
    ) -> Any:
        config = self._get_execution_config(state)
        delay = config["backoff_base"]
        for attempt in range(config["retry_count"] + 1):
            try:
                coro = runtime.execute(task=task, context=context)
                timeout = config["timeout_seconds"]
                if timeout:
                    return await asyncio.wait_for(coro, timeout=timeout)
                return await coro
            except asyncio.CancelledError:
                raise
            except Exception:
                if attempt >= config["retry_count"]:
                    raise
                await asyncio.sleep(delay)
                delay *= config["backoff_multiplier"]

    async def _gather_with_config(self, coros: list[Any], state: dict[str, Any]) -> list[Any]:
        config = self._get_execution_config(state)
        tasks = [asyncio.create_task(coro) for coro in coros]
        if not tasks:
            return []
        if not config["cancel_on_failure"]:
            return await asyncio.gather(*tasks, return_exceptions=True)

        results: list[Any] = [None] * len(tasks)
        index_map = {task: idx for idx, task in enumerate(tasks)}
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

        for task in done:
            idx = index_map[task]
            exc = task.exception()
            if exc:
                for pending_task in pending:
                    pending_task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                raise exc
            results[idx] = task.result()

        if pending:
            pending_results = await asyncio.gather(*pending, return_exceptions=True)
            for task, result in zip(pending, pending_results):
                results[index_map[task]] = result

        return results

    async def _execute_tool_node(self, node: Node, state: dict[str, Any]) -> Any:
        """Execute a ToolNode using ToolRegistry.

        Args:
            node: Tool node to execute
            state: Current workflow state

        Returns:
            Tool execution result
        """
        tool_name = node.config.data.get("tool_name")
        if not tool_name:
            raise GraphExecutionError(
                f"Tool node '{node.id}' missing tool_name in config.data"
            )

        tool = ToolRegistry.get(tool_name)
        if tool is None:
            raise GraphExecutionError(f"Tool '{tool_name}' not found in registry")

        tool_params = node.config.data.get("tool_params", {})
        if tool_params is None:
            tool_params = {}
        if not isinstance(tool_params, dict):
            raise GraphExecutionError(
                f"Tool node '{node.id}' tool_params must be a dict"
            )

        try:
            tool_params = resolve_templates(tool_params, state)
        except TemplateResolutionError as exc:
            raise GraphExecutionError(f"Tool node '{node.id}': {exc}") from exc

        result = await tool.execute(**tool_params)
        return result.model_dump() if hasattr(result, "model_dump") else result

    async def _execute_subgraph_node(
        self, node: Node, state: dict[str, Any], max_iterations: int
    ) -> Any:
        """Execute a nested workflow defined in the state metadata."""
        workflow_id = node.config.data.get("workflow_id")
        if not workflow_id:
            raise GraphExecutionError(
                f"Subgraph node '{node.id}' missing workflow_id in config.data"
            )

        subgraphs = state.get("subgraphs", {})
        workflow_def = subgraphs.get(workflow_id)
        if not workflow_def and "subgraphs" in state:
            workflow_def = state["subgraphs"].get(workflow_id)
        if not workflow_def and "metadata" in state:
            workflow_def = state.get("metadata", {}).get("subgraphs", {}).get(workflow_id)
        if not workflow_def:
            raise GraphExecutionError(
                f"Subgraph workflow '{workflow_id}' not found in state.subgraphs"
            )

        subgraph = Graph(name=f"subgraph:{workflow_id}")
        for node_def in workflow_def.get("nodes", []):
            node_type = node_def.get("type")
            node_id = node_def.get("id")
            if node_type == "input":
                subgraph.add_node(Node(id=node_id, type=NodeType.INPUT, config=NodeConfig(type=NodeType.INPUT)))
            elif node_type == "output":
                subgraph.add_node(Node(id=node_id, type=NodeType.OUTPUT, config=NodeConfig(type=NodeType.OUTPUT)))
            elif node_type == "agent":
                subgraph.add_node(Node(id=node_id, type=NodeType.AGENT, config=NodeConfig(type=NodeType.AGENT, data=node_def.get("config", {}))))
            elif node_type == "tool":
                subgraph.add_node(Node(id=node_id, type=NodeType.TOOL, config=NodeConfig(type=NodeType.TOOL, data=node_def.get("config", {}))))
            else:
                subgraph.add_node(Node(id=node_id, type=NodeType.CONDITION, config=NodeConfig(type=NodeType.CONDITION, data=node_def.get("config", {}))))

        for edge_def in workflow_def.get("edges", []):
            subgraph.add_edge(Edge(source=edge_def["source"], target=edge_def["target"], condition=edge_def.get("condition")))

        result_state = await subgraph.run(
            input_data=state.get("input"),
            max_iterations=max_iterations,
            state={"parent_state": state},
        )
        # Drop the parent-state backreference: run() returns the same dict
        # object it was given, so leaving it in place would make the outer
        # state embed itself once this result is stored under node.id —
        # a circular reference that breaks JSON serialization of results.
        result_state = {k: v for k, v in result_state.items() if k != "parent_state"}
        return {"workflow_id": workflow_id, "state": result_state}

    async def _execute_flow_node(
        self, node: Node, state: dict[str, Any], max_iterations: int
    ) -> Any:
        """Execute a multi-agent flow pattern (genxai.flows) as a single node.

        config.data:
            flow_type: key into genxai.flows.FLOW_TYPES
            agents: ordered agent specs (role/goal/backstory/llm_model/
                temperature/tools); order carries pattern meaning
            params: extra flow-constructor kwargs, filtered by signature
            task: optional task template, resolved against workflow state
            state: optional dict seeded into the flow's state (template-resolved),
                for pattern keys like critic_task or bid_task
            input: optional input template; defaults to the workflow input
        """
        import inspect

        # Lazy import: genxai.flows imports this module
        from genxai.core.agent.base import AgentFactory
        from genxai.flows import FLOW_TYPES

        data = node.config.data
        flow_type = data.get("flow_type")
        flow_cls = FLOW_TYPES.get(flow_type)
        if flow_cls is None:
            raise GraphExecutionError(
                f"Flow node '{node.id}': unknown flow_type '{flow_type}'. "
                f"Available: {sorted(FLOW_TYPES)}"
            )

        agent_specs = data.get("agents") or []
        if not agent_specs:
            raise GraphExecutionError(f"Flow node '{node.id}' has no agents configured")

        agents = []
        for index, spec in enumerate(agent_specs):
            agent = AgentFactory.create_agent(
                id=spec.get("id") or f"{node.id}_agent_{index + 1}",
                role=spec.get("role", "Agent"),
                goal=spec.get("goal", "Process tasks"),
                backstory=spec.get("backstory", ""),
                tools=spec.get("tools", []),
                llm_model=spec.get("llm_model", "gpt-4"),
                llm_temperature=spec.get("temperature", 0.7),
            )
            AgentRegistry.register(agent)
            agents.append(agent)

        # Keep only params the flow's constructor accepts; warn on the rest.
        raw_params = dict(data.get("params") or {})
        accepted = set(inspect.signature(flow_cls.__init__).parameters) - {
            "self", "agents", "name", "llm_provider"
        }
        params = {k: v for k, v in raw_params.items() if k in accepted}
        if dropped := set(raw_params) - set(params):
            logger.warning(
                "Flow node '%s': dropping params not accepted by %s: %s",
                node.id, flow_cls.__name__, sorted(dropped),
            )

        try:
            flow = flow_cls(
                agents=agents,
                name=f"{node.id}:{flow_type}",
                llm_provider=state.get("llm_provider"),
                **params,
            )
        except (TypeError, ValueError) as exc:
            raise GraphExecutionError(f"Flow node '{node.id}': {exc}") from exc

        # Seed the flow's private state: a resolved task plus any pattern
        # keys (critic_task, bid_task, ...) from config.data["state"].
        flow_state: dict[str, Any] = {}
        try:
            if task := data.get("task"):
                flow_state["task"] = resolve_templates(task, state)
            for key, value in (data.get("state") or {}).items():
                flow_state[key] = resolve_templates(value, state)
            if "input" in data:
                input_data = resolve_templates(data["input"], state)
            else:
                input_data = copy.deepcopy(state.get("input"))
        except TemplateResolutionError as exc:
            raise GraphExecutionError(f"Flow node '{node.id}': {exc}") from exc

        result = await flow.run(
            input_data=input_data,
            state=flow_state,
            max_iterations=max_iterations,
        )
        return {"flow_type": flow_type, "result": result}

    async def _execute_loop_node(
        self, node: Node, state: dict[str, Any], max_iterations: int
    ) -> Any:
        """Execute a loop node, running its body each iteration.

        The loop body is configured via config.data["body"]:
            {"type": "tool", "tool_name": ..., "tool_params": {...}}
            {"type": "agent", "agent_id": ..., "task": ...}
            {"type": "subgraph", "workflow_id": ...}

        The loop exits when config.data["condition"] (a state key) becomes
        truthy, the loop's own max_iterations is reached, or the workflow's
        global iteration budget is exhausted. Each iteration's result is
        stored in state under "loop_<node_id>_last_result" so conditions and
        downstream nodes can read it.
        """
        condition_key = node.config.data.get("condition")
        loop_limit = int(node.config.data.get("max_iterations", 5))
        body = node.config.data.get("body")
        loop_iterations = 0
        results = []

        while loop_iterations < loop_limit:
            loop_iterations += 1
            state[f"loop_{node.id}_iteration"] = loop_iterations
            state["iterations"] = state.get("iterations", 0) + 1

            iteration_result = None
            if body:
                iteration_result = await self._execute_loop_body(node, body, state, max_iterations)
                state[f"loop_{node.id}_last_result"] = iteration_result

            results.append({"iteration": loop_iterations, "result": iteration_result})
            if condition_key and state.get(condition_key):
                break
            if state.get("iterations", 0) >= max_iterations:
                break

        return {"iterations": loop_iterations, "results": results}

    async def _execute_loop_body(
        self, node: Node, body: dict[str, Any], state: dict[str, Any], max_iterations: int
    ) -> Any:
        """Execute one iteration of a loop node's body."""
        if not isinstance(body, dict):
            raise GraphExecutionError(
                f"Loop node '{node.id}' body must be a dict with a 'type' key"
            )
        body_type = body.get("type")
        body_data = {k: v for k, v in body.items() if k != "type"}

        if body_type == "tool":
            body_node = Node(
                id=f"{node.id}__body",
                type=NodeType.TOOL,
                config=NodeConfig(type=NodeType.TOOL, data=body_data),
            )
            return await self._execute_tool_node(body_node, state)

        if body_type == "agent":
            body_node = Node(
                id=f"{node.id}__body",
                type=NodeType.AGENT,
                config=NodeConfig(type=NodeType.AGENT, data=body_data),
            )
            return await self._execute_agent_node(body_node, state)

        if body_type == "subgraph":
            body_node = Node(
                id=f"{node.id}__body",
                type=NodeType.SUBGRAPH,
                config=NodeConfig(type=NodeType.SUBGRAPH, data=body_data),
            )
            return await self._execute_subgraph_node(body_node, state, max_iterations)

        raise GraphExecutionError(
            f"Loop node '{node.id}' has unsupported body type: {body_type!r} "
            "(expected 'tool', 'agent', or 'subgraph')"
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert graph to dictionary representation.

        Returns:
            Dictionary representation of the graph
        """
        return {
            "name": self.name,
            "nodes": [
                {
                    "id": node.id,
                    "type": node.type.value,
                    "config": node.config.model_dump(),
                    "status": node.status.value,
                }
                for node in self.nodes.values()
            ],
            "edges": [
                {
                    "source": edge.source,
                    "target": edge.target,
                    "metadata": edge.metadata,
                    "priority": edge.priority,
                }
                for edge in self.edges
            ],
        }

    def __repr__(self) -> str:
        """String representation of the graph."""
        return f"Graph(name={self.name}, nodes={len(self.nodes)}, edges={len(self.edges)})"

    def draw_ascii(self) -> str:
        """Generate ASCII art representation of the graph.

        Returns:
            String containing ASCII art visualization of the graph
        """
        if not self.nodes:
            return "Empty graph"

        lines = []
        lines.append(f"Graph: {self.name}")
        lines.append("=" * 60)
        lines.append("")

        # Find entry points
        entry_points = [
            node_id for node_id in self.nodes if not self.get_incoming_nodes(node_id)
        ]

        if not entry_points:
            entry_points = [
                node_id
                for node_id, node in self.nodes.items()
                if node.type == NodeType.INPUT
            ]

        if not entry_points and self.nodes:
            entry_points = [next(iter(self.nodes.keys()))]

        # Build tree structure
        visited = set()
        for entry in entry_points:
            self._draw_node_tree(entry, lines, visited, prefix="", is_last=True)

        lines.append("")
        lines.append("=" * 60)
        lines.append(f"Total Nodes: {len(self.nodes)} | Total Edges: {len(self.edges)}")

        return "\n".join(lines)

    def _draw_node_tree(
        self, node_id: str, lines: list[str], visited: set[str], prefix: str, is_last: bool
    ) -> None:
        """Recursively draw node tree structure.

        Args:
            node_id: Current node ID
            lines: List to append output lines to
            visited: Set of visited node IDs
            prefix: Current line prefix for indentation
            is_last: Whether this is the last child
        """
        if node_id not in self.nodes:
            return

        node = self.nodes[node_id]

        # Draw current node
        connector = "└── " if is_last else "├── "
        status_symbol = {
            NodeStatus.PENDING: "○",
            NodeStatus.RUNNING: "◐",
            NodeStatus.COMPLETED: "●",
            NodeStatus.FAILED: "✗",
            NodeStatus.SKIPPED: "⊘",
        }.get(node.status, "?")

        node_line = f"{prefix}{connector}{status_symbol} {node.id} [{node.type.value}]"
        lines.append(node_line)

        # Avoid infinite loops in cyclic graphs
        if node_id in visited:
            extension = "    " if is_last else "│   "
            lines.append(f"{prefix}{extension}↻ (cycle detected)")
            return

        visited.add(node_id)

        # Get outgoing edges
        outgoing = self.get_outgoing_edges(node_id)
        if not outgoing:
            return

        # Group edges by type
        parallel_edges = [e for e in outgoing if e.metadata.get("parallel", False)]
        sequential_edges = [e for e in outgoing if not e.metadata.get("parallel", False)]

        # Draw parallel edges
        if parallel_edges:
            extension = "    " if is_last else "│   "
            lines.append(f"{prefix}{extension}║")
            lines.append(f"{prefix}{extension}╠══ [PARALLEL]")

            for i, edge in enumerate(parallel_edges):
                is_last_parallel = i == len(parallel_edges) - 1 and not sequential_edges
                new_prefix = prefix + ("    " if is_last else "│   ")
                lines.append(f"{new_prefix}║")
                self._draw_node_tree(
                    edge.target, lines, visited.copy(), new_prefix, is_last_parallel
                )

        # Draw sequential edges
        for i, edge in enumerate(sequential_edges):
            is_last_edge = i == len(sequential_edges) - 1
            new_prefix = prefix + ("    " if is_last else "│   ")

            if edge.condition:
                lines.append(f"{new_prefix}│")
                lines.append(f"{new_prefix}├── [IF condition]")

            self._draw_node_tree(edge.target, lines, visited.copy(), new_prefix, is_last_edge)

    def to_mermaid(self) -> str:
        """Generate Mermaid diagram syntax for the graph.

        Returns:
            String containing Mermaid flowchart syntax
        """
        if not self.nodes:
            return "graph TD\n    empty[Empty Graph]"

        lines = ["graph TD"]

        # Define nodes with appropriate shapes
        for node_id, node in self.nodes.items():
            label = f"{node_id}\\n[{node.type.value}]"

            # Choose shape based on node type
            if node.type == NodeType.INPUT:
                shape = f'    {node_id}(["{label}"])'
            elif node.type == NodeType.OUTPUT:
                shape = f'    {node_id}(["{label}"])'
            elif node.type == NodeType.CONDITION:
                shape = f'    {node_id}{{{{{label}}}}}'
            elif node.type == NodeType.AGENT:
                shape = f'    {node_id}["{label}"]'
            elif node.type == NodeType.TOOL:
                shape = f'    {node_id}["{label}"]'
            else:
                shape = f'    {node_id}["{label}"]'

            lines.append(shape)

        lines.append("")

        # Define edges
        for edge in self.edges:
            if edge.condition:
                lines.append(f"    {edge.source} -->|conditional| {edge.target}")
            elif edge.metadata.get("parallel", False):
                lines.append(f"    {edge.source} -.parallel.-> {edge.target}")
            else:
                lines.append(f"    {edge.source} --> {edge.target}")

        return "\n".join(lines)

    def to_dot(self) -> str:
        """Generate GraphViz DOT format for the graph.

        Returns:
            String containing DOT format graph definition
        """
        if not self.nodes:
            return "digraph empty { }"

        lines = [f'digraph "{self.name}" {{']
        lines.append("    rankdir=TB;")
        lines.append("    node [fontname=Arial, fontsize=10];")
        lines.append("    edge [fontname=Arial, fontsize=9];")
        lines.append("")

        # Define node styles by type
        node_styles = {
            NodeType.INPUT: 'shape=ellipse, style=filled, fillcolor=lightblue',
            NodeType.OUTPUT: 'shape=ellipse, style=filled, fillcolor=lightgreen',
            NodeType.CONDITION: 'shape=diamond, style=filled, fillcolor=lightyellow',
            NodeType.AGENT: 'shape=box, style="rounded,filled", fillcolor=lightcoral',
            NodeType.TOOL: 'shape=box, style=filled, fillcolor=lightgray',
            NodeType.HUMAN: 'shape=box, style=filled, fillcolor=lightpink',
            NodeType.SUBGRAPH: 'shape=box3d, style=filled, fillcolor=lavender',
        }

        # Define nodes
        for node_id, node in self.nodes.items():
            style = node_styles.get(node.type, 'shape=box')
            label = f"{node_id}\\n[{node.type.value}]"

            # Add status indicator
            if node.status != NodeStatus.PENDING:
                label += f"\\n({node.status.value})"

            lines.append(f'    {node_id} [label="{label}", {style}];')

        lines.append("")

        # Define edges
        for edge in self.edges:
            attrs = []

            if edge.condition:
                attrs.append('label="conditional"')
                attrs.append('style=dashed')

            if edge.metadata.get("parallel", False):
                attrs.append('label="parallel"')
                attrs.append('color=blue')

            if edge.priority != 0:
                attrs.append(f'weight={edge.priority}')

            attr_str = ", ".join(attrs) if attrs else ""
            if attr_str:
                lines.append(f"    {edge.source} -> {edge.target} [{attr_str}];")
            else:
                lines.append(f"    {edge.source} -> {edge.target};")

        lines.append("}")

        return "\n".join(lines)

    def print_structure(self) -> None:
        """Print a simple text summary of the graph structure."""
        print(f"\nGraph: {self.name}")
        print("=" * 60)
        print(f"Nodes: {len(self.nodes)}")
        print(f"Edges: {len(self.edges)}")
        print()

        if self.nodes:
            print("Node List:")
            print("-" * 60)
            for node_id, node in self.nodes.items():
                status = node.status.value
                print(f"  • {node_id:20} [{node.type.value:10}] ({status})")
            print()

        if self.edges:
            print("Edge List:")
            print("-" * 60)
            for edge in self.edges:
                condition = "conditional" if edge.condition else "unconditional"
                parallel = " [PARALLEL]" if edge.metadata.get("parallel", False) else ""
                print(f"  • {edge.source:15} → {edge.target:15} ({condition}){parallel}")
            print()

        # Find entry and exit points
        entry_points = [
            node_id for node_id in self.nodes if not self.get_incoming_nodes(node_id)
        ]
        exit_points = [
            node_id for node_id in self.nodes if not self.get_outgoing_edges(node_id)
        ]

        if entry_points:
            print(f"Entry Points: {', '.join(entry_points)}")
        if exit_points:
            print(f"Exit Points: {', '.join(exit_points)}")

        print("=" * 60)
        print()


class WorkflowEngine(Graph):
    """Public, user-facing workflow engine.

    This is a thin compatibility wrapper around :class:`~genxai.core.graph.engine.Graph`
    to match the API expected by integration tests and external users.
    """

    def __init__(self, name: str = "workflow") -> None:
        super().__init__(name=name)

    async def execute(self, start_node: str, llm_provider: Any = None, **kwargs: Any) -> dict[str, Any]:
        """Execute a workflow starting from a given node.

        Notes:
            - WorkflowEngine uses the core Graph execution pipeline, which now
              executes AgentNode + ToolNode via AgentRuntime/ToolRegistry.
            - Integration tests pass `llm_provider`, but Graph does not need it.
              It's accepted here for compatibility.
        """
        # Initialize state with start node as the only entry point.
        state: dict[str, Any] = kwargs.pop("state", {}) if "state" in kwargs else {}
        input_data = kwargs.pop("input_data", None)
        if input_data is not None:
            state["input"] = input_data

        # Ensure max_iterations propagates.
        max_iterations = kwargs.pop("max_iterations", 100)

        if llm_provider is not None:
            state["llm_provider"] = llm_provider

        # Execute from specified start node.
        await self._execute_node(start_node, state, max_iterations)
        return {
            "status": "completed",
            "node_results": {k: v for k, v in state.items() if k not in {"iterations"}},
            "state": state,
        }
