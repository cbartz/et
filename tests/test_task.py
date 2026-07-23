"""Tests for et.task, mocking et.config/et.workspaces/et.tracker/et.jira/et.jira_time."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from et.config import ConfigError, EtConfig, JiraConfig, WorkspaceConfigEntry
from et.jira import JiraError, JiraIssue, JiraTransition
from et.jira_time import JiraLogTimeError, LogTimeResult
from et.task import (
    TaskError,
    complete_task_for_current_workspace,
    create_task_from_jira,
    create_task_workspace,
)
from et.tracker import TrackerError
from et.workspaces import WorkspaceError


def _config(
    workspaces: list[WorkspaceConfigEntry] | None = None,
    *,
    with_jira: bool = True,
) -> EtConfig:
    return EtConfig(
        jira=(
            JiraConfig(
                base_url="https://example.atlassian.net/",
                email="me@example.com",
                pat="secret-token",
                jql="assignee = currentUser()",
            )
            if with_jira
            else None
        ),
        workspaces=workspaces or [],
    )


# --- create_task_workspace -------------------------------------------------


@patch("et.task.workspaces.move_active_window_to_workspace")
@patch("et.task.workspaces.switch_to_workspace")
@patch("et.task.workspaces.rename_all_workspaces")
@patch("et.task.save_config")
@patch("et.task.tracker.prepare_timer_for_workspace", return_value=("ET-1", True))
@patch("et.task.workspaces.set_workspace_count")
@patch("et.task.workspaces.get_workspace_count", return_value=5)
@patch("et.task.load_config")
def test_create_task_workspace_uses_first_free_slot(
    mock_load_config,
    _mock_get_count,
    mock_set_count,
    mock_add_tracker,
    mock_save_config,
    mock_rename_all,
    mock_switch,
    mock_move,
):
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="ET-1", ref="jira:ISD-1"),
            WorkspaceConfigEntry(name="ET-2"),
        ]
    )

    result = create_task_workspace("my-task", description="doing stuff")

    assert result is not None
    assert result.workspace_index == 1
    assert result.name == "my-task"
    assert result.ref is None
    assert result.timer_created is True

    mock_set_count.assert_not_called()
    mock_add_tracker.assert_called_once_with(1, 5)
    saved_config = mock_save_config.call_args[0][0]
    assert saved_config.workspaces[1] == WorkspaceConfigEntry(
        name="my-task", ref=None, description="doing stuff"
    )
    mock_rename_all.assert_called_once_with(["ET-1", "my-task"])
    mock_switch.assert_called_once_with(1)
    mock_move.assert_called_once_with(1)
    assert result.window_moved is True


@patch("et.task.workspaces.move_active_window_to_workspace")
@patch("et.task.workspaces.switch_to_workspace")
@patch("et.task.workspaces.rename_all_workspaces")
@patch("et.task.save_config")
@patch("et.task.tracker.prepare_timer_for_workspace", return_value=("ET-1", True))
@patch("et.task.workspaces.set_workspace_count")
@patch("et.task.workspaces.get_workspace_count", return_value=1)
@patch("et.task.load_config")
def test_create_task_workspace_succeeds_when_window_move_unsupported(
    mock_load_config,
    _mock_get_count,
    _mock_set_count,
    _mock_add_tracker,
    _mock_save_config,
    _mock_rename_all,
    mock_switch,
    mock_move,
):
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="ET-1")])
    mock_move.side_effect = WorkspaceError("wmctrl -r :ACTIVE: -t 0 failed: ")

    result = create_task_workspace("my-task")

    assert result is not None
    assert result.window_moved is False
    mock_switch.assert_called_once_with(0)


@patch("et.task.workspaces.move_active_window_to_workspace")
@patch("et.task.workspaces.switch_to_workspace")
@patch("et.task.workspaces.rename_all_workspaces")
@patch("et.task.save_config")
@patch("et.task.tracker.prepare_timer_for_workspace", return_value=("ET-2", True))
@patch("et.task.workspaces.set_workspace_count")
@patch("et.task.workspaces.get_workspace_count", return_value=3)
@patch("et.task.load_config")
def test_create_task_workspace_uses_implicit_bare_slot_within_count(
    mock_load_config,
    _mock_get_count,
    mock_set_count,
    mock_add_tracker,
    mock_save_config,
    _mock_rename_all,
    _mock_switch,
    _mock_move,
):
    # Only one workspace listed, but GNOME has 3 — slot 1 is an implicit
    # free bare slot, so no growth prompt is needed.
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ET-1", ref="jira:ISD-1")]
    )

    result = create_task_workspace("my-task")

    assert result is not None
    assert result.workspace_index == 1
    mock_set_count.assert_not_called()
    mock_add_tracker.assert_called_once_with(1, 3)


@patch("et.task.workspaces.move_active_window_to_workspace")
@patch("et.task.workspaces.switch_to_workspace")
@patch("et.task.workspaces.rename_all_workspaces")
@patch("et.task.save_config")
@patch("et.task.tracker.prepare_timer_for_workspace", return_value=("ET-3", True))
@patch("et.task.workspaces.set_workspace_count")
@patch("et.task.workspaces.get_workspace_count", return_value=2)
@patch("et.task.load_config")
def test_create_task_workspace_prompts_and_grows_when_all_full(
    mock_load_config,
    _mock_get_count,
    mock_set_count,
    mock_add_tracker,
    mock_save_config,
    _mock_rename_all,
    _mock_switch,
    _mock_move,
):
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="ET-1", ref="jira:ISD-1"),
            WorkspaceConfigEntry(name="ET-2", ref="jira:ISD-2"),
        ]
    )

    seen_count: list[int] = []

    def confirm_grow(count: int) -> bool:
        seen_count.append(count)
        return True

    result = create_task_workspace("my-task", confirm_grow=confirm_grow)

    assert result is not None
    assert result.workspace_index == 2
    assert seen_count == [2]
    mock_set_count.assert_called_once_with(3)
    mock_add_tracker.assert_called_once_with(2, 3)
    saved_config = mock_save_config.call_args[0][0]
    assert len(saved_config.workspaces) == 3


@patch("et.task.workspaces.set_workspace_count")
@patch("et.task.save_config")
@patch("et.task.tracker.prepare_timer_for_workspace")
@patch("et.task.workspaces.get_workspace_count", return_value=2)
@patch("et.task.load_config")
def test_create_task_workspace_returns_none_when_grow_declined(
    mock_load_config,
    _mock_get_count,
    mock_add_tracker,
    mock_save_config,
    mock_set_count,
):
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="ET-1", ref="jira:ISD-1"),
            WorkspaceConfigEntry(name="ET-2", ref="jira:ISD-2"),
        ]
    )

    result = create_task_workspace("my-task", confirm_grow=lambda count: False)

    assert result is None
    mock_set_count.assert_not_called()
    mock_add_tracker.assert_not_called()
    mock_save_config.assert_not_called()


@patch("et.task.workspaces.get_workspace_count", side_effect=WorkspaceError("boom"))
@patch("et.task.load_config")
def test_create_task_workspace_wraps_workspace_error(mock_load_config, _mock_get_count):
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="ET-1")])

    with pytest.raises(TaskError, match="boom"):
        create_task_workspace("my-task")


@patch("et.task.tracker.prepare_timer_for_workspace", side_effect=TrackerError("tracker boom"))
@patch("et.task.workspaces.get_workspace_count", return_value=1)
@patch("et.task.load_config")
def test_create_task_workspace_wraps_tracker_error(
    mock_load_config, _mock_get_count, _mock_add_tracker
):
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="ET-1")])

    with pytest.raises(TaskError, match="tracker boom"):
        create_task_workspace("my-task")


@patch("et.task.save_config", side_effect=ConfigError("config boom"))
@patch("et.task.tracker.prepare_timer_for_workspace", return_value=("ET-1", True))
@patch("et.task.workspaces.get_workspace_count", return_value=1)
@patch("et.task.load_config")
def test_create_task_workspace_wraps_config_error(
    mock_load_config, _mock_get_count, _mock_add_tracker, _mock_save_config
):
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="ET-1")])

    with pytest.raises(TaskError, match="config boom"):
        create_task_workspace("my-task")


# --- create_task_from_jira --------------------------------------------------


def _issue(
    key: str, summary: str = "Some issue", priority: str = "High", status: str = ""
) -> JiraIssue:
    return JiraIssue(key=key, summary=summary, priority=priority, status=status)


@patch("et.task.load_config")
def test_create_task_from_jira_raises_without_jira_config(mock_load_config):
    mock_load_config.return_value = _config(with_jira=False)

    with pytest.raises(TaskError, match="no 'jira' block"):
        create_task_from_jira(select_issue=lambda issues: None)


@patch("et.task.fetch_active_issues", side_effect=JiraError("api down"))
@patch("et.task.load_config")
def test_create_task_from_jira_wraps_jira_error(mock_load_config, _mock_fetch):
    mock_load_config.return_value = _config()

    with pytest.raises(TaskError, match="api down"):
        create_task_from_jira(select_issue=lambda issues: None)


@patch("et.task.fetch_active_issues")
@patch("et.task.load_config")
def test_create_task_from_jira_filters_out_already_tracked_issues(
    mock_load_config, mock_fetch
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-1", ref="jira:ISD-1")]
    )
    mock_fetch.return_value = [_issue("ISD-1"), _issue("ISD-2")]

    seen_candidates: list[JiraIssue] = []

    def select_issue(issues: list[JiraIssue]) -> JiraIssue | None:
        seen_candidates.extend(issues)
        return None

    result = create_task_from_jira(select_issue=select_issue)

    assert result is None
    assert [issue.key for issue in seen_candidates] == ["ISD-2"]


@patch("et.task.create_task_workspace")
@patch("et.task.fetch_active_issues")
@patch("et.task.load_config")
def test_create_task_from_jira_delegates_to_create_task_workspace(
    mock_load_config, mock_fetch, mock_create_task_workspace
):
    mock_load_config.return_value = _config()
    mock_fetch.return_value = [_issue("ISD-2", summary="A rather long issue summary here")]
    mock_create_task_workspace.return_value = "created-result"

    def confirm_grow(count: int) -> bool:
        return True

    result = create_task_from_jira(
        select_issue=lambda issues: issues[0], confirm_grow=confirm_grow
    )

    assert result == "created-result"
    mock_create_task_workspace.assert_called_once_with(
        name="A rather long issue summary he",
        description="A rather long issue summary here",
        ref="jira:ISD-2",
        confirm_grow=confirm_grow,
    )


@patch("et.task.create_task_workspace")
@patch("et.task.fetch_active_issues")
@patch("et.task.load_config")
def test_create_task_from_jira_skips_confirm_transition_when_already_in_progress(
    mock_load_config, mock_fetch, _mock_create_task_workspace
):
    mock_load_config.return_value = _config()
    mock_fetch.return_value = [_issue("ISD-2", status="In Progress")]
    confirm_transition = MagicMock()

    create_task_from_jira(
        select_issue=lambda issues: issues[0], confirm_transition=confirm_transition
    )

    confirm_transition.assert_not_called()


@patch("et.task.transition_issue")
@patch("et.task.fetch_transitions")
@patch("et.task.create_task_workspace")
@patch("et.task.fetch_active_issues")
@patch("et.task.load_config")
def test_create_task_from_jira_transitions_issue_when_confirmed(
    mock_load_config,
    mock_fetch,
    _mock_create_task_workspace,
    mock_fetch_transitions,
    mock_transition_issue,
):
    mock_load_config.return_value = _config()
    mock_fetch.return_value = [_issue("ISD-2", status="To Do")]
    mock_fetch_transitions.return_value = [
        JiraTransition(id="21", name="Done", to_status="Done"),
        JiraTransition(id="11", name="Start progress", to_status="In Progress"),
    ]

    create_task_from_jira(
        select_issue=lambda issues: issues[0], confirm_transition=lambda issue: True
    )

    mock_fetch_transitions.assert_called_once_with(_config().jira, "ISD-2")
    mock_transition_issue.assert_called_once_with(_config().jira, "ISD-2", "11")


@patch("et.task.transition_issue")
@patch("et.task.fetch_transitions")
@patch("et.task.create_task_workspace")
@patch("et.task.fetch_active_issues")
@patch("et.task.load_config")
def test_create_task_from_jira_skips_transition_when_declined(
    mock_load_config,
    mock_fetch,
    _mock_create_task_workspace,
    mock_fetch_transitions,
    mock_transition_issue,
):
    mock_load_config.return_value = _config()
    mock_fetch.return_value = [_issue("ISD-2", status="To Do")]

    create_task_from_jira(
        select_issue=lambda issues: issues[0], confirm_transition=lambda issue: False
    )

    mock_fetch_transitions.assert_not_called()
    mock_transition_issue.assert_not_called()


@patch("et.task.fetch_transitions")
@patch("et.task.create_task_workspace")
@patch("et.task.fetch_active_issues")
@patch("et.task.load_config")
def test_create_task_from_jira_raises_when_no_in_progress_transition_available(
    mock_load_config, mock_fetch, _mock_create_task_workspace, mock_fetch_transitions
):
    mock_load_config.return_value = _config()
    mock_fetch.return_value = [_issue("ISD-2", status="To Do")]
    mock_fetch_transitions.return_value = [JiraTransition(id="21", name="Done", to_status="Done")]

    with pytest.raises(TaskError, match="no transition to 'In Progress' available"):
        create_task_from_jira(
            select_issue=lambda issues: issues[0], confirm_transition=lambda issue: True
        )


@patch("et.task.fetch_transitions", side_effect=JiraError("api down"))
@patch("et.task.create_task_workspace")
@patch("et.task.fetch_active_issues")
@patch("et.task.load_config")
def test_create_task_from_jira_wraps_fetch_transitions_error(
    mock_load_config, mock_fetch, _mock_create_task_workspace, _mock_fetch_transitions
):
    mock_load_config.return_value = _config()
    mock_fetch.return_value = [_issue("ISD-2", status="To Do")]

    with pytest.raises(TaskError, match="api down"):
        create_task_from_jira(
            select_issue=lambda issues: issues[0], confirm_transition=lambda issue: True
        )


# --- complete_task_for_current_workspace ------------------------------------


def _timer(workspace_id: int, name: str, elapsed: int = 0, running: bool = False) -> dict:
    return {
        "id": f"timer-{workspace_id}",
        "name": name,
        "timeElapsed": elapsed,
        "running": running,
        "selected": False,
        "workspaceId": workspace_id,
        "autoResume": True,
    }


@patch("et.task.workspaces.rename_all_workspaces")
@patch("et.task.save_config")
@patch("et.task.tracker.load_timers", return_value=[])
@patch("et.task.load_config")
@patch("et.task.log_time_for_current_workspace")
def test_complete_task_resets_workspace_after_logging(
    mock_log_time, mock_load_config, _mock_load_timers, mock_save_config, mock_rename_all
):
    mock_log_time.return_value = LogTimeResult(
        workspace_index=1, issue_key="ISD-2", seconds_logged=780, tracker_reset=True
    )
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="ET-1"),
            WorkspaceConfigEntry(name="ISD-2", ref="jira:ISD-2", description="stuff"),
        ]
    )

    result = complete_task_for_current_workspace(comment="done")

    mock_log_time.assert_called_once_with(description="done", reset=True)
    assert result.log_result.issue_key == "ISD-2"

    saved_config = mock_save_config.call_args[0][0]
    assert saved_config.workspaces[1] == WorkspaceConfigEntry(name="ET-2")
    mock_rename_all.assert_called_once_with(["ET-1", "ET-2"])


@patch("et.task.workspaces.rename_all_workspaces")
@patch("et.task.save_config")
@patch("et.task.tracker.save_timers_with_reload")
@patch("et.task.tracker.load_timers")
@patch("et.task.load_config")
@patch("et.task.log_time_for_current_workspace")
def test_complete_task_shifts_later_workspaces_and_timers_left(
    mock_log_time,
    mock_load_config,
    mock_load_timers,
    mock_save_timers,
    mock_save_config,
    mock_rename_all,
):
    # Completing task B (slot 1) should pull C (slot 2, with its timer) into
    # slot 1, and leave slot 2 as the new bare slot — instead of leaving a
    # B-shaped gap in the middle.
    mock_log_time.return_value = LogTimeResult(
        workspace_index=1, issue_key="ISD-B", seconds_logged=780, tracker_reset=True
    )
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
            WorkspaceConfigEntry(name="ISD-B", ref="jira:ISD-B", description="stuff"),
            WorkspaceConfigEntry(name="ISD-C", ref="jira:ISD-C", description="more stuff"),
        ]
    )
    stale_b_timer = _timer(1, "ET-2", elapsed=0)
    c_timer = _timer(2, "ET-3", elapsed=999, running=True)
    mock_load_timers.return_value = [stale_b_timer, c_timer]

    result = complete_task_for_current_workspace()

    assert result.log_result.issue_key == "ISD-B"

    saved_config = mock_save_config.call_args[0][0]
    assert saved_config.workspaces == [
        WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
        WorkspaceConfigEntry(name="ISD-C", ref="jira:ISD-C", description="more stuff"),
        WorkspaceConfigEntry(name="ET-3"),
    ]
    mock_rename_all.assert_called_once_with(["ISD-A", "ISD-C", "ET-3"])

    saved_timers = mock_save_timers.call_args[0][0]
    assert stale_b_timer not in saved_timers
    assert c_timer in saved_timers
    assert c_timer["workspaceId"] == 1
    assert c_timer["name"] == "ET-2"
    assert c_timer["timeElapsed"] == 999


@patch("et.task.workspaces.rename_all_workspaces")
@patch("et.task.save_config")
@patch("et.task.tracker.save_timers_with_reload")
@patch("et.task.tracker.load_timers", return_value=[])
@patch("et.task.load_config")
@patch("et.task.log_time_for_current_workspace")
def test_complete_task_is_noop_shift_when_last_slot_completed(
    mock_log_time, mock_load_config, _mock_load_timers, mock_save_timers, mock_save_config, _rename
):
    mock_log_time.return_value = LogTimeResult(
        workspace_index=1, issue_key="ISD-B", seconds_logged=780, tracker_reset=True
    )
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
            WorkspaceConfigEntry(name="ISD-B", ref="jira:ISD-B"),
        ]
    )

    complete_task_for_current_workspace()

    saved_config = mock_save_config.call_args[0][0]
    assert saved_config.workspaces == [
        WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
        WorkspaceConfigEntry(name="ET-2"),
    ]
    mock_save_timers.assert_not_called()


@patch("et.task.workspaces.rename_all_workspaces")
@patch("et.task.save_config")
@patch("et.task.tracker.load_timers", return_value=[])
@patch("et.task.load_config")
@patch("et.task.log_time_for_current_workspace")
def test_complete_task_does_not_shift_static_slots(
    mock_log_time, mock_load_config, _mock_load_timers, mock_save_config, mock_rename_all
):
    mock_log_time.return_value = LogTimeResult(
        workspace_index=0, issue_key="ISD-A", seconds_logged=780, tracker_reset=True
    )
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
            WorkspaceConfigEntry(name="mails", type="static"),
            WorkspaceConfigEntry(name="ISD-C", ref="jira:ISD-C"),
        ]
    )

    complete_task_for_current_workspace()

    saved_config = mock_save_config.call_args[0][0]
    assert saved_config.workspaces == [
        WorkspaceConfigEntry(name="ISD-C", ref="jira:ISD-C"),
        WorkspaceConfigEntry(name="mails", type="static"),
        WorkspaceConfigEntry(name="ET-3"),
    ]


@patch("et.task.log_time_for_current_workspace", side_effect=JiraLogTimeError("no timer"))
def test_complete_task_propagates_log_time_error(mock_log_time):
    with pytest.raises(JiraLogTimeError, match="no timer"):
        complete_task_for_current_workspace()


@patch("et.task.workspaces.rename_all_workspaces", side_effect=WorkspaceError("rename boom"))
@patch("et.task.save_config")
@patch("et.task.tracker.load_timers", return_value=[])
@patch("et.task.load_config")
@patch("et.task.log_time_for_current_workspace")
def test_complete_task_wraps_workspace_error_after_logging(
    mock_log_time, mock_load_config, _mock_load_timers, _mock_save_config, _mock_rename_all
):
    mock_log_time.return_value = LogTimeResult(
        workspace_index=0, issue_key="ISD-2", seconds_logged=780, tracker_reset=True
    )
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="ISD-2", ref="jira:ISD-2")])

    with pytest.raises(TaskError, match="rename boom"):
        complete_task_for_current_workspace()


@patch("et.task.tracker.load_timers", side_effect=TrackerError("no schema"))
@patch("et.task.load_config")
@patch("et.task.log_time_for_current_workspace")
def test_complete_task_wraps_tracker_error_loading_timers(
    mock_log_time, mock_load_config, _mock_load_timers
):
    mock_log_time.return_value = LogTimeResult(
        workspace_index=0, issue_key="ISD-2", seconds_logged=780, tracker_reset=True
    )
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="ISD-2", ref="jira:ISD-2")])

    with pytest.raises(TaskError, match="no schema"):
        complete_task_for_current_workspace()

