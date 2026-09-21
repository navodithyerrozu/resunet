"""Tests for adapter resolution and the approval invariants.

The assertions here are about *policy*, not DOM handling -- no browser is
launched. What is being pinned is the set of rules that make unattended
submission safe to ship:

* an unknown board resolves to the generic adapter, not an exception
* the generic adapter mandates human approval, and nothing can turn that off
* a vendor adapter is preferred over the fallback whenever it matches

Each of these is one line of code away from being silently wrong, and the failure
mode is an irreversible submission to a third party rather than a stack trace.
"""

from __future__ import annotations

import pytest

from worker import adapters
from worker.adapters import GenericAdapter, resolve_adapter
from worker.adapters.base import STEPS, BoardAdapter


class TestResolution:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://boards.greenhouse.io/acme/jobs/4001", "greenhouse"),
            ("https://job-boards.greenhouse.io/acme/jobs/4001", "greenhouse"),
            ("https://jobs.lever.co/acme/1a2b3c4d", "lever"),
            ("https://jobs.lever.co/acme/1a2b3c4d/apply", "lever"),
            ("https://careers.acme.com/openings/senior-engineer", "generic"),
            ("https://acme.bamboohr.com/careers/42", "generic"),
        ],
    )
    def test_url_maps_to_expected_board(self, url: str, expected: str) -> None:
        assert resolve_adapter(url).board == expected

    def test_unknown_board_falls_back_rather_than_raising(self) -> None:
        """A hard failure here would make the product useless outside two vendors.

        An unrecognised form is still worth attempting *behind mandatory human
        approval*, which is what the generic adapter provides.
        """
        adapter = resolve_adapter("https://something-nobody-has-seen.example/apply")
        assert adapter.board == "generic"
        assert adapter.always_require_approval is True

    def test_generic_is_last_in_the_registry(self) -> None:
        """Order is load-bearing: `GenericAdapter.matches()` returns True for
        everything, so anything registered after it is unreachable."""
        boards = [a.board for a in adapters._ADAPTERS]
        assert boards[-1] == "generic"
        assert boards.count("generic") == 1

    def test_no_duplicate_board_names(self) -> None:
        boards = [a.board for a in adapters._ADAPTERS]
        assert len(boards) == len(set(boards))

    def test_supported_boards_excludes_the_fallback(self) -> None:
        """This list is user-facing -- "generic" is not a board anyone applies to."""
        assert "generic" not in adapters.supported_boards()
        assert set(adapters.supported_boards()) == {"greenhouse", "lever"}

    def test_lookup_by_board_name(self) -> None:
        assert adapters.adapter_for_board("lever") is not None
        assert adapters.adapter_for_board("nonexistent") is None


class TestApprovalInvariants:
    def test_generic_adapter_mandates_approval(self) -> None:
        """The rule the whole heuristic path depends on.

        A heuristic match on an unknown form is a guess, and the failure mode is
        putting a phone number in the salary field and submitting. If this ever
        reads False, unattended guessing has been enabled.
        """
        assert GenericAdapter.always_require_approval is True

    def test_approval_flag_is_a_class_level_declaration(self) -> None:
        """Not an instance attribute set in `__init__`, so it cannot be mutated
        per-submission by a code path that thinks it knows better."""
        assert "always_require_approval" in vars(GenericAdapter)

    def test_every_adapter_declares_its_approval_stance(self) -> None:
        for adapter in adapters._ADAPTERS:
            assert isinstance(adapter.always_require_approval, bool)
            assert isinstance(adapter.requires_login, bool)

    def test_login_requiring_adapters_are_explicit(self) -> None:
        """`requires_login` gates whether the worker decrypts a credential at all.

        A default of True on an adapter that does not need one would make the
        worker touch the credential store unnecessarily on every submission.
        """
        for adapter in adapters._ADAPTERS:
            if adapter.requires_login:
                # A board needing a login needs a real login implementation.
                assert type(adapter).login is not BoardAdapter.login


class TestProtocolConformance:
    """Structural typing means a missing method surfaces at call time, mid-run.

    Checking conformance here moves that failure to the test suite, where a
    half-submitted application is not the diagnostic.
    """

    def test_all_adapters_satisfy_the_protocol(self) -> None:
        for adapter in adapters._ADAPTERS:
            assert isinstance(adapter, BoardAdapter), f"{adapter.board} is not conforming"

    @pytest.mark.parametrize("adapter", adapters._ADAPTERS, ids=lambda a: a.board)
    def test_pipeline_steps_are_implemented(self, adapter: BoardAdapter) -> None:
        for step in STEPS:
            if step == "await_approval":
                continue  # orchestration, owned by the task rather than the adapter
            assert callable(getattr(adapter, step, None)), f"{adapter.board} lacks {step}"

    def test_steps_are_ordered_with_submit_last(self) -> None:
        """`verify_submitted` must follow `submit`, and `await_approval` must come
        before it -- an approval gate after the irreversible click is decoration."""
        assert STEPS.index("await_approval") < STEPS.index("submit")
        assert STEPS.index("submit") < STEPS.index("verify_submitted")
        assert STEPS.index("upload_resume") < STEPS.index("submit")
