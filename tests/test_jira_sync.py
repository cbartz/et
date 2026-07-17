"""Tests for et.jira_sync's pure planning functions (no mocking needed)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from et.config import ConfigError, EtConfig, JiraConfig, WorkspaceConfigEntry
from et.jira import JiraError, JiraIssue
from et.jira_sync import (
    JiraSyncError,
    JiraSyncResult,
    apply_timer_changes,
    jira_key_from_ref,
    plan_reshuffle,
    sync_jira_workspaces,
    truncate_summary,
)
from et.tracker import TrackerError
from et.workspaces import WorkspaceError


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


def _jira_config(**overrides):
    defaults = dict(
        base_url="https://example.atlassian.net/",
        email="me@example.com",
        pat="secret-token",
        jql="assignee = currentUser()",
    )
    defaults.update(overrides)
    return JiraConfig(**defaults)


@patch("et.jira_sync.workspaces.rename_all_workspaces")
@patch("et.jira_sync.workspaces.configure_static_workspace_count")
@patch("et.jira_sync.tracker.save_timers_with_reload")
@patch("et.jira_sync.tracker.load_timers", return_value=[])
@patch("et.jira_sync.save_config")
@patch("et.jira_sync.fetch_active_issues")
@patch("et.jira_sync.load_config")
def test_sync_assigns_brand_new_issue_to_first_slot(
    mock_load_config,
    mock_fetch,
    mock_save_config,
    mock_load_timers,
    mock_save_timers,
    mock_configure,
    mock_rename,
):
    mock_load_config.return_value = EtConfig(
        max_workspaces=10, jira=_jira_config(), workspaces=[ws()]
    )
    mock_fetch.return_value = [issue("PROJ-1", "First task")]

    result = sync_jira_workspaces()

    assert result.assigned == [(0, "PROJ-1")]
    assert result.deleted == []
    mock_save_config.assert_called_once()
    mock_rename.assert_called_once_with(["First task"])
    mock_save_timers.assert_called_once()


@patch("et.jira_sync.workspaces.rename_all_workspaces")
@patch("et.jira_sync.workspaces.configure_static_workspace_count")
@patch("et.jira_sync.tracker.save_timers_with_reload")
@patch(
    "et.jira_sync.tracker.load_timers",
    return_value=[{"id": "a", "name": "ET-1", "workspaceId": 0, "timeElapsed": 555}],
)
@patch("et.jira_sync.save_config")
@patch("et.jira_sync.fetch_active_issues")
@patch("et.jira_sync.load_config")
def test_sync_deletes_workspace_for_no_longer_active_issue_after_confirmation(
    mock_load_config,
    mock_fetch,
    mock_save_config,
    mock_load_timers,
    mock_save_timers,
    mock_configure,
    mock_rename,
    tmp_path,
):
    mock_load_config.return_value = EtConfig(
        max_workspaces=10,
        jira=_jira_config(),
        workspaces=[ws(name="Old task", ref="jira:PROJ-OLD", description="Old task")],
    )
    mock_fetch.return_value = []

    with patch("et.jira_sync.Path.home", return_value=tmp_path):
        result = sync_jira_workspaces(confirm_delete=lambda slot, name, key: True)

    assert result.deleted == [(0, "PROJ-OLD")]
    dumped = tmp_path / "timers" / "by-id" / "jira-PROJ-OLD.txt"
    assert dumped.read_text() == "555\n0h 9m 15s\n"


@patch("et.jira_sync.workspaces.rename_all_workspaces")
@patch("et.jira_sync.workspaces.configure_static_workspace_count")
@patch("et.jira_sync.tracker.save_timers_with_reload")
@patch("et.jira_sync.tracker.load_timers", return_value=[])
@patch("et.jira_sync.save_config")
@patch("et.jira_sync.fetch_active_issues")
@patch("et.jira_sync.load_config")
def test_sync_keeps_workspace_when_deletion_not_confirmed(
    mock_load_config,
    mock_fetch,
    mock_save_config,
    mock_load_timers,
    mock_save_timers,
    mock_configure,
    mock_rename,
):
    mock_load_config.return_value = EtConfig(
        max_workspaces=10,
        jira=_jira_config(),
        workspaces=[ws(name="Old task", ref="jira:PROJ-OLD", description="Old task")],
    )
    mock_fetch.return_value = []

    result = sync_jira_workspaces(confirm_delete=lambda slot, name, key: False)

    assert result.deleted == []
    mock_rename.assert_called_once_with(["Old task"])


@patch("et.jira_sync.fetch_active_issues")
@patch("et.jira_sync.load_config")
def test_sync_aborts_with_no_changes_when_plan_not_confirmed(mock_load_config, mock_fetch):
    mock_load_config.return_value = EtConfig(
        max_workspaces=10, jira=_jira_config(), workspaces=[ws()]
    )
    mock_fetch.return_value = [issue("PROJ-1")]

    result = sync_jira_workspaces(confirm_plan=lambda issues: False)

    assert result == JiraSyncResult(assigned=[], moved=[], kept=[], deleted=[], skipped=[])


@patch("et.jira_sync.load_config")
def test_sync_raises_jira_sync_error_when_no_jira_config(mock_load_config):
    mock_load_config.return_value = EtConfig(max_workspaces=10, jira=None, workspaces=[ws()])

    with pytest.raises(JiraSyncError, match="jira"):
        sync_jira_workspaces()


@patch("et.jira_sync.load_config")
def test_sync_wraps_config_error(mock_load_config):
    mock_load_config.side_effect = ConfigError("bad file")

    with pytest.raises(ConfigError):
        sync_jira_workspaces()


@patch("et.jira_sync.fetch_active_issues", side_effect=JiraError("network down"))
@patch("et.jira_sync.load_config")
def test_sync_wraps_jira_error(mock_load_config, mock_fetch):
    mock_load_config.return_value = EtConfig(
        max_workspaces=10, jira=_jira_config(), workspaces=[ws()]
    )

    with pytest.raises(JiraSyncError, match="network down"):
        sync_jira_workspaces()


@patch("et.jira_sync.tracker.load_timers", side_effect=TrackerError("tracker broken"))
@patch("et.jira_sync.fetch_active_issues", return_value=[])
@patch("et.jira_sync.load_config")
def test_sync_wraps_tracker_error(mock_load_config, mock_fetch, mock_load_timers):
    mock_load_config.return_value = EtConfig(
        max_workspaces=10, jira=_jira_config(), workspaces=[ws()]
    )

    with pytest.raises(JiraSyncError, match="tracker broken"):
        sync_jira_workspaces()


@patch(
    "et.jira_sync.workspaces.configure_static_workspace_count",
    side_effect=WorkspaceError("workspace broken"),
)
@patch("et.jira_sync.tracker.load_timers", return_value=[])
@patch("et.jira_sync.fetch_active_issues", return_value=[])
@patch("et.jira_sync.load_config")
def test_sync_wraps_workspace_error(
    mock_load_config, mock_fetch, mock_load_timers, mock_configure
):
    mock_load_config.return_value = EtConfig(
        max_workspaces=10, jira=_jira_config(), workspaces=[ws()]
    )

    with pytest.raises(JiraSyncError, match="workspace broken"):
        sync_jira_workspaces()


@patch("et.jira_sync.workspaces.rename_all_workspaces")
@patch("et.jira_sync.workspaces.configure_static_workspace_count")
@patch("et.jira_sync.tracker.save_timers_with_reload")
@patch("et.jira_sync.tracker.load_timers", return_value=[])
@patch("et.jira_sync.save_config")
@patch("et.jira_sync.fetch_active_issues")
@patch("et.jira_sync.load_config")
def test_sync_grows_workspace_list_up_to_max_workspaces(
    mock_load_config,
    mock_fetch,
    mock_save_config,
    mock_load_timers,
    mock_save_timers,
    mock_configure,
    mock_rename,
):
    mock_load_config.return_value = EtConfig(
        max_workspaces=3, jira=_jira_config(), workspaces=[ws()]
    )
    mock_fetch.return_value = [issue("PROJ-1"), issue("PROJ-2"), issue("PROJ-3")]

    result = sync_jira_workspaces()

    assert len(result.assigned) == 3
    mock_configure.assert_called_once_with(3)
