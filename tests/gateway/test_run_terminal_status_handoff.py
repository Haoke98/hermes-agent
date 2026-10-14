"""``terminal_run_status`` must not flatten a max-iteration handoff into a failure.

A turn whose iteration budget ran out but which still closed with a usable summary is a
*resumable boundary*, not an error: the predicate that says so
(:func:`agent.turn_failure_copy.is_max_iteration_handoff`) is already what cron delivery and the
TUI goal judge use. The Runs API used to map ``completed=False`` to ``failed``, so every
``/v1/runs`` client rendered a hard failure and dropped the summary that rides the terminal frame
as ``output``.
"""

from gateway.platforms.api_server_runs import terminal_run_status


def _handoff_result(**overrides):
    result = {
        "completed": False,
        "failed": False,
        "interrupted": False,
        "partial": False,
        "turn_exit_reason": "max_iterations_reached(90/90)",
        "final_response": "I ran out of steps for this turn. Send `continue` to keep going.",
    }
    result.update(overrides)
    return result


class TestMaxIterationHandoffIsNotAFailure:
    """The bug: a budget handoff was reported as ``failed``."""

    def test_budget_handoff_reports_completed_with_a_marker(self):
        status, fields = terminal_run_status(_handoff_result())

        assert status == "completed"
        assert fields["completed"] is True
        assert fields["handoff"] is True
        assert fields["handoff_reason"] == "max_iterations_reached(90/90)"

    def test_handoff_keeps_the_turn_exit_reason_out_of_the_failure_fields(self):
        _status, fields = terminal_run_status(_handoff_result())

        # ``turn_exit_reason`` is only emitted for turns that did NOT finish; the handoff
        # carries its own, more specific, ``handoff_reason`` instead.
        assert "turn_exit_reason" not in fields

    def test_a_summary_less_budget_exit_is_still_a_failure(self):
        status, fields = terminal_run_status(_handoff_result(final_response=""))

        assert status == "failed"
        assert fields["completed"] is False
        assert fields["turn_exit_reason"] == "max_iterations_reached(90/90)"
        assert "handoff" not in fields

    def test_provider_failure_never_becomes_a_handoff(self):
        status, fields = terminal_run_status(_handoff_result(failed=True))

        assert status == "failed"
        assert fields["completed"] is False
        assert "handoff" not in fields

    def test_incomplete_provider_failure_is_not_a_handoff(self):
        # ``failed`` is how non-retryable provider/API errors come back (#63180).
        status, _fields = terminal_run_status(
            _handoff_result(failed=True, final_response="")
        )

        assert status == "failed"

    def test_interrupted_turn_stays_cancelled(self):
        status, fields = terminal_run_status(_handoff_result(interrupted=True))

        assert status == "cancelled"
        assert fields["interrupted"] is True
        assert "handoff" not in fields

    def test_other_unfinished_reasons_still_fail(self):
        status, fields = terminal_run_status(
            _handoff_result(turn_exit_reason="local_processing_error(boom)")
        )

        assert status == "failed"
        assert fields["turn_exit_reason"] == "local_processing_error(boom)"


class TestCompletedNeverRidesNextToPartial:
    """The invariant the docstring promises, preserved for the new branch."""

    def test_partial_handoff_shape_still_fails(self):
        status, fields = terminal_run_status(_handoff_result(partial=True))

        assert status == "failed"
        assert fields["partial"] is True
        assert fields["completed"] is False
        assert "handoff" not in fields

    def test_completed_and_partial_are_never_both_true(self):
        for result in (
            _handoff_result(),
            _handoff_result(partial=True),
            {"completed": True},
            {"completed": False, "turn_exit_reason": "budget_exhausted"},
        ):
            _status, fields = terminal_run_status(result)
            assert not (fields["completed"] and fields["partial"])


class TestOrdinaryTurnsAreUnchanged:
    """Regression guard: the common paths keep their old answers."""

    def test_clean_completion(self):
        status, fields = terminal_run_status({"completed": True, "final_response": "done"})

        assert status == "completed"
        assert fields == {"completed": True, "partial": False, "interrupted": False}

    def test_undelivered_steer_still_rides_along(self):
        status, fields = terminal_run_status({"completed": True, "pending_steer": "next"})

        assert status == "completed"
        assert fields["pending_steer"] == "next"

    def test_no_handoff_marker_leaks_into_ordinary_outcomes(self):
        # The new branch adds ``handoff`` for a handoff and for nothing else.
        for result in (
            {"completed": True, "final_response": "done"},
            {"completed": False, "turn_exit_reason": "budget_exhausted"},
            {"completed": False, "final_response": ""},
            {"completed": False, "failed": True},
        ):
            _status, fields = terminal_run_status(result)
            assert "handoff" not in fields
            assert "handoff_reason" not in fields

