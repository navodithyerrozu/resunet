"""Tests for the agent graph topology.

These assert the *shape* of the graph rather than its behaviour, because the
shape is the enforcement mechanism. The specific invariant worth protecting:
there must be no path from drafting to acceptance that bypasses validation. A
refactor that adds such an edge would silently remove the product's core
guarantee while every behavioural test still passed.
"""

from __future__ import annotations

from app.agents.resume.graph import build_graph
from app.agents.resume.nodes.validate import route_after_validation
from app.agents.resume.state import ResumeAgentState, initial_state
from app.models.bullet import XYZViolation, XYZViolationCode


class TestTopology:
    def test_all_expected_nodes_present(self) -> None:
        nodes = set(build_graph().compile().get_graph().nodes)
        assert {
            "mine", "draft", "validate", "repair", "clarify", "accept", "flag", "finalize"
        } <= nodes

    def test_no_edge_bypasses_validation(self) -> None:
        """`accept` must be reachable only from `validate`.

        This is the assertion that matters most in the file.
        """
        graph = build_graph().compile().get_graph()
        sources_into_accept = {e.source for e in graph.edges if e.target == "accept"}
        assert sources_into_accept == {"validate"}

    def test_repair_returns_to_validation(self) -> None:
        """A repaired draft is re-checked, never trusted."""
        graph = build_graph().compile().get_graph()
        targets = {e.target for e in graph.edges if e.source == "repair"}
        assert targets == {"validate"}

    def test_graph_compiles_without_checkpointer(self) -> None:
        """Compilation must not require Mongo -- keeps tests and codegen cheap."""
        assert build_graph().compile() is not None


class TestValidationRouting:
    """The conditional edge is pure logic, so it is tested without any LLM."""

    def _state(self, **kwargs: object) -> ResumeAgentState:
        state = initial_state(
            user_id="u1", thread_id="t1", raw_notes="notes"
        )
        state.update(kwargs)  # type: ignore[typeddict-item]
        return state

    def test_compliant_draft_is_accepted(self) -> None:
        assert route_after_validation(self._state(current_violations=[])) == "accept"

    def test_missing_metric_asks_the_user(self) -> None:
        """Routes to clarify *before* spending repair attempts.

        No amount of re-prompting invents a number that was never supplied, so
        retrying first would burn tokens to reach the same question -- and would
        pressure the model toward fabricating one.
        """
        state = self._state(
            current_violations=[
                XYZViolation(code=XYZViolationCode.MISSING_METRIC, message="no metric")
            ],
            repair_attempts=0,
        )
        assert route_after_validation(state) == "clarify"

    def test_metric_gap_repairs_once_user_has_answered(self) -> None:
        """After the user supplies a number, asking again would be a loop."""
        state = self._state(
            current_violations=[
                XYZViolation(code=XYZViolationCode.MISSING_METRIC, message="no metric")
            ],
            user_response="failures went from 4.2% to 0.8%",
            repair_attempts=0,
        )
        assert route_after_validation(state) == "repair"

    def test_wording_defects_go_to_repair(self) -> None:
        state = self._state(
            current_violations=[
                XYZViolation(code=XYZViolationCode.WEAK_VERB, message="weak opener")
            ],
            repair_attempts=0,
        )
        assert route_after_validation(state) == "repair"

    def test_exhausted_budget_flags_for_review(self) -> None:
        """Bounded retries: flagged, never silently accepted or dropped."""
        state = self._state(
            current_violations=[
                XYZViolation(code=XYZViolationCode.WEAK_VERB, message="weak opener")
            ],
            repair_attempts=99,
        )
        assert route_after_validation(state) == "flag"


class TestInitialState:
    def test_accumulating_fields_are_initialised(self) -> None:
        """Unset reducer fields fail inside LangGraph, not in our code."""
        state = initial_state(user_id="u1", thread_id="t1", raw_notes="x")
        for field in ("messages", "completed_bullets", "flagged_bullets"):
            assert state[field] == []  # type: ignore[literal-required]

    def test_state_is_json_serializable(self) -> None:
        """Checkpointed state must serialize -- no ObjectId, no datetime."""
        import json

        state = initial_state(user_id="u1", thread_id="t1", raw_notes="x")
        assert json.dumps(state)
