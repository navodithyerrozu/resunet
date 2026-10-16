"""Tests for mapping checkpointed graph outcomes to the API contract."""

from __future__ import annotations

from types import SimpleNamespace

from app.api.v1.routes.resumes import _generation_result
from app.models.agent import ThreadStatus


def test_generation_result_maps_interrupt_to_user_question() -> None:
    response = _generation_result(
        "gen_test",
        {"__interrupt__": (SimpleNamespace(value={"question": "How much faster?"}),)},
        {"status": "awaiting_user", "messages": []},
    )

    assert response.thread_id == "gen_test"
    assert response.status is ThreadStatus.AWAITING_USER
    assert response.question == "How much faster?"
    assert response.needs_input is True


def test_generation_result_maps_completed_bullet_proposals() -> None:
    state = {
        "status": "done",
        "messages": [{"role": "assistant", "content": "Draft ready."}],
        "completed_bullets": [
            {
                "draft": {
                    "action": "Reduced processing time",
                    "metric": "by 30%",
                    "method": "by batching requests",
                    "skills": ["Python"],
                },
                "text": "Reduced processing time by 30% by batching requests",
                "violations": [],
                "repair_attempts": 1,
                "accepted": True,
            }
        ],
        "flagged_bullets": [],
        "persisted_bullet_ids": ["333333333333333333333333"],
    }

    response = _generation_result("gen_test", {}, state)

    assert response.status is ThreadStatus.COMPLETED
    assert response.assistant_message == "Draft ready."
    assert response.needs_input is False
    assert response.proposals[0].text == "Reduced processing time by 30% by batching requests"
    assert response.proposals[0].bullet_id == "333333333333333333333333"
