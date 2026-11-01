"""Tests for the agent graph topology.

These assert the *shape* of the graph rather than its behaviour, because the
shape is the enforcement mechanism. The specific invariant worth protecting:
there must be no path from drafting to acceptance that bypasses validation. A
refactor that adds such an edge would silently remove the product's core
guarantee while every behavioural test still passed.
"""

from __future__ import annotations

from app.agents.resume.graph import build_graph, route_after_mining
from app.agents.resume.nodes.validate import (
    route_after_validation,
    route_next_achievement,
)
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

    def test_mine_can_route_to_clarify_before_drafting(self) -> None:
        """No path from mine to draft may skip the clarification gate.

        Regression test for the 2026-11-01 live failure: a metric-less note
        was drafted with an invented 25% because mine went straight to draft
        and the validator only checks that a number exists, not its source.
        """
        graph = build_graph().compile().get_graph()
        targets = {e.target for e in graph.edges if e.source == "mine"}
        assert targets == {"draft", "clarify"}

    def test_accept_can_route_to_clarify_for_next_achievement(self) -> None:
        graph = build_graph().compile().get_graph()
        targets = {e.target for e in graph.edges if e.source == "accept"}
        assert targets == {"draft", "clarify", "finalize"}


class TestMiningRouting:
    """route_after_mining is pure logic, tested without any LLM."""

    def _state(self, **kwargs: object) -> ResumeAgentState:
        state = initial_state(user_id="u1", thread_id="t1", raw_notes="notes")
        state.update(kwargs)  # type: ignore[typeddict-item]
        return state

    def _achievement(self, needs: bool) -> dict[str, object]:
        return {
            "id": "ach_0",
            "raw_text": "worked on the borrowing system",
            "has_metric": not needs,
            "needs_clarification": needs,
        }

    def test_metric_less_achievement_goes_to_clarify(self) -> None:
        state = self._state(
            achievements=[self._achievement(True)], current_achievement_index=0
        )
        assert route_after_mining(state) == "clarify"

    def test_metric_achievement_goes_to_draft(self) -> None:
        state = self._state(
            achievements=[self._achievement(False)], current_achievement_index=0
        )
        assert route_after_mining(state) == "draft"

    def test_answered_achievement_goes_to_draft(self) -> None:
        state = self._state(
            achievements=[self._achievement(True)],
            current_achievement_index=0,
            user_response="late returns fell by 30%",
        )
        assert route_after_mining(state) == "draft"


class TestNextAchievementRouting:
    def _state(self, **kwargs: object) -> ResumeAgentState:
        state = initial_state(user_id="u1", thread_id="t1", raw_notes="notes")
        state.update(kwargs)  # type: ignore[typeddict-item]
        return state

    def _achievement(self, needs: bool) -> dict[str, object]:
        return {
            "id": "ach_1",
            "raw_text": "worked on the borrowing system",
            "has_metric": not needs,
            "needs_clarification": needs,
        }

    def test_next_metric_less_achievement_goes_to_clarify(self) -> None:
        state = self._state(
            achievements=[self._achievement(False), self._achievement(True)],
            current_achievement_index=1,
        )
        assert route_next_achievement(state) == "clarify"

    def test_next_metric_achievement_goes_to_draft(self) -> None:
        state = self._state(
            achievements=[self._achievement(False), self._achievement(False)],
            current_achievement_index=1,
        )
        assert route_next_achievement(state) == "draft"

    def test_exhausted_queue_finalizes(self) -> None:
        state = self._state(
            achievements=[self._achievement(False)], current_achievement_index=1
        )
        assert route_next_achievement(state) == "finalize"


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


class TestMiningDigitGate:
    """A fragment with no digit cannot carry a metric, whatever the model says."""

    async def _mine(self, has_metric: bool, raw_text: str) -> list[dict[str, object]]:
        from unittest.mock import AsyncMock, patch

        from app.agents.resume.nodes import mine as mine_module
        from app.agents.resume.nodes.mine import _MinedAchievement, _MiningResult

        fake_model = AsyncMock()
        fake_model.ainvoke.return_value = _MiningResult(
            achievements=[
                _MinedAchievement(raw_text=raw_text, has_metric=has_metric)
            ]
        )
        with patch.object(mine_module, "structured", return_value=fake_model):
            state = initial_state(user_id="u1", thread_id="t1", raw_notes=raw_text)
            result = await mine_module.mine_achievements(state)
        return result["achievements"]  # type: ignore[return-value]

    def test_model_claim_without_digit_still_needs_clarification(self) -> None:
        import asyncio

        achievements = asyncio.run(
            self._mine(True, "worked on the borrowing system")
        )
        assert achievements[0]["needs_clarification"] is True

    def test_fragment_with_digit_keeps_model_claim(self) -> None:
        import asyncio

        achievements = asyncio.run(
            self._mine(True, "search time dropped by 42%")
        )
        assert achievements[0]["needs_clarification"] is False


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
