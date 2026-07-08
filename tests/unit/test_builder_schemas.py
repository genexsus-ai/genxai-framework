"""Unit tests for the builder plan/delegation domain models."""

import pytest
from pydantic import ValidationError

from genxai.builder.schemas import (
    DelegationPlan,
    PlanStep,
    WorkflowPlan,
    WorkPacket,
)

_TEAM = [
    {"role": "Writer", "goal": "Draft"},
    {"role": "Critic", "goal": "Review"},
]


def _step(step_id: str, **kwargs) -> PlanStep:
    defaults = {
        "id": step_id,
        "title": step_id.replace("_", " ").title(),
        "description": f"Step {step_id}",
    }
    defaults.update(kwargs)
    return PlanStep(**defaults)


def _plan(steps: list[PlanStep]) -> WorkflowPlan:
    return WorkflowPlan(name="Test Plan", steps=steps)


class TestWorkflowPlan:
    def test_valid_plan(self) -> None:
        plan = _plan(
            [
                _step("gather", capabilities=["web_scraper"]),
                _step("write", depends_on=["gather"]),
            ]
        )
        assert plan.capability_names() == {"web_scraper"}
        assert plan.execution_order() == ["gather", "write"]

    def test_duplicate_step_ids_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duplicate step id"):
            _plan([_step("a"), _step("a")])

    def test_unknown_dependency_rejected(self) -> None:
        with pytest.raises(ValidationError, match="unknown id"):
            _plan([_step("a", depends_on=["missing"])])

    def test_self_dependency_rejected(self) -> None:
        with pytest.raises(ValidationError, match="depends on itself"):
            _plan([_step("a", depends_on=["a"])])

    def test_dependency_cycle_rejected(self) -> None:
        with pytest.raises(ValidationError, match="cycle"):
            _plan(
                [
                    _step("a", depends_on=["c"]),
                    _step("b", depends_on=["a"]),
                    _step("c", depends_on=["b"]),
                ]
            )

    def test_flow_step_requires_pattern(self) -> None:
        with pytest.raises(ValidationError, match="flow_pattern"):
            _step("team", kind="flow", team=_TEAM)

    def test_flow_step_requires_team(self) -> None:
        with pytest.raises(ValidationError, match="team"):
            _step("team", kind="flow", flow_pattern="critic_review")

    def test_decision_step_requires_condition(self) -> None:
        with pytest.raises(ValidationError, match="condition"):
            _step("branch", kind="decision")

    def test_flow_pattern_counted_as_capability(self) -> None:
        plan = _plan([_step("team", kind="flow", flow_pattern="critic_review", team=_TEAM)])
        assert "critic_review" in plan.capability_names()

    def test_execution_order_respects_dependencies(self) -> None:
        plan = _plan(
            [
                _step("d", depends_on=["b", "c"]),
                _step("b", depends_on=["a"]),
                _step("c", depends_on=["a"]),
                _step("a"),
            ]
        )
        order = plan.execution_order()
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("d") == 3

    def test_empty_steps_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowPlan(name="Empty", steps=[])


class TestDelegationPlan:
    def _packet(self, packet_id: str, **kwargs) -> WorkPacket:
        defaults = {
            "id": packet_id,
            "worker": "node_designer",
            "objective": f"Do {packet_id}",
        }
        defaults.update(kwargs)
        return WorkPacket(**defaults)

    def test_duplicate_packet_ids_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duplicate packet id"):
            DelegationPlan(packets=[self._packet("p1"), self._packet("p1")])

    def test_packet_cycle_rejected(self) -> None:
        with pytest.raises(ValidationError, match="cycle"):
            DelegationPlan(
                packets=[
                    self._packet("p1", depends_on=["p2"]),
                    self._packet("p2", depends_on=["p1"]),
                ]
            )

    def test_validate_against_plan_detects_unknown_and_uncovered_steps(self) -> None:
        plan = _plan([_step("a"), _step("b")])

        delegation = DelegationPlan(packets=[self._packet("p1", step_ids=["a", "ghost"])])
        with pytest.raises(ValueError, match="unknown step ids"):
            delegation.validate_against_plan(plan)

        delegation = DelegationPlan(packets=[self._packet("p1", step_ids=["a"])])
        with pytest.raises(ValueError, match="not covered"):
            delegation.validate_against_plan(plan)

        delegation = DelegationPlan(packets=[self._packet("p1", step_ids=["a", "b"])])
        delegation.validate_against_plan(plan)  # no error

    def test_dispatch_waves_group_independent_packets(self) -> None:
        delegation = DelegationPlan(
            packets=[
                self._packet("design_nodes"),
                self._packet("design_agents"),
                self._packet("wire_edges", depends_on=["design_nodes", "design_agents"]),
            ]
        )
        waves = delegation.dispatch_waves()
        assert [sorted(p.id for p in wave) for wave in waves] == [
            ["design_agents", "design_nodes"],
            ["wire_edges"],
        ]
