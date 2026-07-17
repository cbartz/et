"""Tests for et.jira_sync's pure planning functions (no mocking needed)."""

from __future__ import annotations

from et.config import WorkspaceConfigEntry
from et.jira import JiraIssue
from et.jira_sync import (
    apply_timer_changes,
    jira_key_from_ref,
    plan_reshuffle,
    truncate_summary,
)


def ws(name="ET-1", type="dynamic", ref=None, description=None):
    return WorkspaceConfigEntry(name=name, type=type, ref=ref, description=description)


def issue(key, summary="Some summary", priority="High"):
    return JiraIssue(key=key, summary=summary, priority=priority)


def test_truncate_summary_hard_cuts_at_20_chars_and_rstrips():
    assert truncate_summary("Fix login timeout on mobile clients") == "Fix login timeout on"
    assert truncate_summary("Short") == "Short"
    assert truncate_summary("Exactly twenty chars") == "Exactly twenty chars"
    assert truncate_summary("Trailing space      more text here") == "Trailing space"


def test_jira_key_from_ref_extracts_key_or_returns_none():
    assert jira_key_from_ref("jira:PROJ-123") == "PROJ-123"
    assert jira_key_from_ref(None) is None
    assert jira_key_from_ref("something-else") is None


def test_plan_reshuffle_assigns_brand_new_issues_to_empty_slots_in_priority_order():
    workspaces = [ws(), ws(), ws()]
    issues = [issue("PROJ-1", "First task"), issue("PROJ-2", "Second task")]

    outcome = plan_reshuffle(workspaces, issues)

    assert outcome.assigned == [(0, "PROJ-1"), (1, "PROJ-2")]
    assert outcome.moved == []
    assert outcome.kept == []
    assert outcome.skipped == []
    assert outcome.workspaces[0].ref == "jira:PROJ-1"
    assert outcome.workspaces[0].name == "First task"
    assert outcome.workspaces[0].description == "First task"
    assert outcome.workspaces[1].ref == "jira:PROJ-2"
    assert outcome.workspaces[2] == workspaces[2]


def test_plan_reshuffle_keeps_issue_already_in_its_correct_priority_slot():
    workspaces = [ws(ref="jira:PROJ-1"), ws()]
    issues = [issue("PROJ-1")]

    outcome = plan_reshuffle(workspaces, issues)

    assert outcome.kept == [(0, "PROJ-1")]
    assert outcome.assigned == []
    assert outcome.moved == []
    assert outcome.workspaces[0].ref == "jira:PROJ-1"


def test_plan_reshuffle_moves_issue_to_new_higher_priority_slot():
    workspaces = [ws(), ws(ref="jira:PROJ-1")]
    issues = [issue("PROJ-1")]  # now the only/top issue -> should land in slot 0

    outcome = plan_reshuffle(workspaces, issues)

    assert outcome.moved == [("PROJ-1", 1, 0)]
    assert outcome.assigned == []
    assert outcome.workspaces[0].ref == "jira:PROJ-1"
    assert outcome.workspaces[1].ref is None


def test_plan_reshuffle_swaps_two_issues_between_slots():
    workspaces = [ws(ref="jira:PROJ-2"), ws(ref="jira:PROJ-1")]
    issues = [issue("PROJ-1", "First"), issue("PROJ-2", "Second")]

    outcome = plan_reshuffle(workspaces, issues)

    assert set(outcome.moved) == {("PROJ-1", 1, 0), ("PROJ-2", 0, 1)}
    assert outcome.workspaces[0].ref == "jira:PROJ-1"
    assert outcome.workspaces[1].ref == "jira:PROJ-2"


def test_plan_reshuffle_skips_lowest_priority_brand_new_issues_over_capacity():
    workspaces = [ws()]
    issues = [issue("PROJ-1", priority="Highest"), issue("PROJ-2", priority="Low")]

    outcome = plan_reshuffle(workspaces, issues)

    assert outcome.assigned == [(0, "PROJ-1")]
    assert outcome.skipped == ["PROJ-2"]


def test_plan_reshuffle_ignores_static_workspaces_entirely():
    workspaces = [ws(type="static", ref="jira:PROJ-OLD"), ws()]
    issues = [issue("PROJ-1")]

    outcome = plan_reshuffle(workspaces, issues)

    assert outcome.workspaces[0] == workspaces[0]
    assert outcome.workspaces[1].ref == "jira:PROJ-1"


def test_plan_reshuffle_clears_stale_ref_when_issue_moves_to_an_earlier_slot():
    # PROJ-A is the only active issue and is currently tracked in slot 1.
    # With only one issue to place, it must land in slot 0 (the lowest
    # available slot) and its *old* slot 1 must be cleared, not left with a
    # stale duplicate "jira:PROJ-A" ref.
    workspaces = [ws(), ws(ref="jira:PROJ-A")]
    issues = [issue("PROJ-A")]

    outcome = plan_reshuffle(workspaces, issues)

    assert outcome.moved == [("PROJ-A", 1, 0)]
    assert outcome.workspaces[0].ref == "jira:PROJ-A"
    assert outcome.workspaces[1].ref is None
    assert outcome.workspaces[1].description is None


def test_plan_reshuffle_keeps_capacity_bumped_still_active_issue_in_place():
    # PROJ-3 is already tracked, but two higher-priority issues now exist and
    # capacity is only 2 non-static slots: PROJ-3 must not be silently
    # dropped/reset, it stays exactly where it is.
    workspaces = [ws(ref="jira:PROJ-3"), ws()]
    issues = [issue("PROJ-1", priority="Highest"), issue("PROJ-2", priority="High"),
              issue("PROJ-3", priority="Low")]

    outcome = plan_reshuffle(workspaces, issues)

    assert outcome.kept == [(0, "PROJ-3")]
    assert outcome.workspaces[0].ref == "jira:PROJ-3"
    assert outcome.workspaces[1].ref == "jira:PROJ-1"
    assert outcome.assigned == [(1, "PROJ-1")]
    assert outcome.skipped == ["PROJ-2"]


def test_plan_reshuffle_handles_zero_non_static_slots():
    workspaces = [ws(type="static")]
    issues = [issue("PROJ-1")]

    outcome = plan_reshuffle(workspaces, issues)

    assert outcome.workspaces == workspaces
    assert outcome.skipped == ["PROJ-1"]
    assert outcome.assigned == []


def test_apply_timer_changes_moves_timer_and_preserves_elapsed_time():
    timers = [
        {"id": "a", "name": "ET-1", "workspaceId": 0, "timeElapsed": 100, "running": True},
    ]
    outcome = plan_reshuffle([ws(ref="jira:PROJ-1"), ws()], [issue("PROJ-1")])
    # Force a move scenario directly via a hand-built outcome-like object to
    # keep this test independent of plan_reshuffle's own slot math:
    from et.jira_sync import ReshuffleOutcome

    moved_outcome = ReshuffleOutcome(
        workspaces=outcome.workspaces, kept=[], assigned=[], moved=[("PROJ-1", 0, 1)], skipped=[]
    )

    new_timers = apply_timer_changes(timers, moved_outcome)

    assert len(new_timers) == 1
    assert new_timers[0]["workspaceId"] == 1
    assert new_timers[0]["name"] == "ET-2"
    assert new_timers[0]["timeElapsed"] == 100
    assert new_timers[0]["running"] is True


def test_apply_timer_changes_creates_timer_for_brand_new_assignment():
    outcome_workspaces = [ws(ref="jira:PROJ-1")]
    from et.jira_sync import ReshuffleOutcome

    outcome = ReshuffleOutcome(
        workspaces=outcome_workspaces, kept=[], assigned=[(0, "PROJ-1")], moved=[], skipped=[]
    )

    new_timers = apply_timer_changes([], outcome)

    assert len(new_timers) == 1
    assert new_timers[0]["workspaceId"] == 0
    assert new_timers[0]["name"] == "ET-1"
    assert new_timers[0]["timeElapsed"] == 0


def test_apply_timer_changes_handles_swapped_slots_without_aliasing_bug():
    timers = [
        {"id": "a", "name": "ET-1", "workspaceId": 0, "timeElapsed": 111, "running": False},
        {"id": "b", "name": "ET-2", "workspaceId": 1, "timeElapsed": 222, "running": True},
    ]
    from et.jira_sync import ReshuffleOutcome

    outcome = ReshuffleOutcome(
        workspaces=[ws(ref="jira:PROJ-1"), ws(ref="jira:PROJ-2")],
        kept=[],
        assigned=[],
        moved=[("PROJ-1", 1, 0), ("PROJ-2", 0, 1)],
        skipped=[],
    )

    new_timers = apply_timer_changes(timers, outcome)

    by_elapsed = {t["timeElapsed"]: t for t in new_timers}
    assert by_elapsed[222]["workspaceId"] == 0  # PROJ-1 was in slot 1, now slot 0
    assert by_elapsed[111]["workspaceId"] == 1  # PROJ-2 was in slot 0, now slot 1
    assert by_elapsed[111]["running"] is False
    assert by_elapsed[222]["running"] is True
