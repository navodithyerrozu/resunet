"""Graph assembly for the resume-generation agent.

Topology
--------

    mine ──► draft ──► validate ──┬─(compliant)──────► accept ──┐
                ▲                 ├─(no metric)──────► clarify ─┤
                │                 ├─(fixable)───────► repair ───┘
                │                 └─(budget spent)──► flag ─────┐
                │                                               │
                └───────────────────────────────────────────────┤
                                                                │
                                          (queue empty) ──► finalize ──► END

The loop is the design. A bullet cannot leave `validate` in a non-compliant
state: it either passes, gets repaired, prompts a question to the user, or is
explicitly flagged for review. There is no edge from `validate` to `accept`
that skips the check, which is what makes "every bullet follows XYZ" a
structural property rather than an aspiration.

`repair` routes back through `validate`, not directly to `accept` -- a repaired
draft is re-checked from scratch, so a fix that introduces a new violation is
caught rather than trusted.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.resume.nodes import (
    accept_bullet,
    clarify_metric,
    draft_bullet,
    finalize,
    flag_bullet,
    mine_achievements,
    repair_bullet,
    route_after_validation,
    route_next_achievement,
    validate_bullet,
)
from app.agents.resume.state import ResumeAgentState
from app.core.logging import get_logger

logger = get_logger(__name__)


def build_graph() -> StateGraph[ResumeAgentState, None, ResumeAgentState, ResumeAgentState]:
    """Wire the nodes and edges. Returns an uncompiled graph.

    Separated from compilation so tests can compile with an in-memory
    checkpointer while production compiles with the Mongo one.
    """
    graph: StateGraph[ResumeAgentState, None, ResumeAgentState, ResumeAgentState] = StateGraph(
        ResumeAgentState
    )

    graph.add_node("mine", mine_achievements)
    graph.add_node("draft", draft_bullet)
    graph.add_node("validate", validate_bullet)
    graph.add_node("repair", repair_bullet)
    graph.add_node("clarify", clarify_metric)
    graph.add_node("accept", accept_bullet)
    graph.add_node("flag", flag_bullet)
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "mine")
    graph.add_edge("mine", "draft")
    graph.add_edge("draft", "validate")

    # The enforcement gate.
    graph.add_conditional_edges(
        "validate",
        route_after_validation,
        {
            "accept": "accept",
            "repair": "repair",
            "clarify": "clarify",
            "flag": "flag",
        },
    )

    # Repaired and clarified drafts both re-enter validation rather than
    # short-cutting to accept.
    graph.add_edge("repair", "validate")
    graph.add_edge("clarify", "draft")

    # After each bullet resolves, either take the next achievement or finish.
    for node in ("accept", "flag"):
        graph.add_conditional_edges(
            node,
            route_next_achievement,
            {"draft": "draft", "finalize": "finalize"},
        )

    graph.add_edge("finalize", END)
    return graph


async def get_compiled_graph() -> CompiledStateGraph[
    ResumeAgentState, None, ResumeAgentState, ResumeAgentState
]:
    """Compile the graph with the Mongo checkpointer.

    Compiled per call rather than cached at module scope: the checkpointer
    holds a Motor client bound to the running event loop, and a graph cached
    across loops (as happens under some test runners and worker restarts)
    fails in ways that are genuinely painful to diagnose.

    If profiling shows compilation is hot, cache it keyed by event loop -- not
    globally.
    """
    from app.agents.checkpointer import get_checkpointer

    checkpointer = await get_checkpointer()
    return build_graph().compile(checkpointer=checkpointer)


def render_mermaid() -> str:
    """Mermaid diagram of the topology, for docs and debugging.

    Useful in review: a wrong edge is obvious in a picture and easy to miss in
    a list of `add_edge` calls.
    """
    return build_graph().compile().get_graph().draw_mermaid()
