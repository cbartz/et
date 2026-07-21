"""Tests for et.ws, mocking et.config/et.workspaces/et.tracker."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from et.config import ConfigError, EtConfig, WorkspaceConfigEntry
from et.tracker import TrackerError
from et.workspaces import WorkspaceError
from et.ws import (
    WsDeleteError,
    _trim_trailing_default_entries,
    delete_active_workspace,
    shift_workspaces_left,
)


def _config(
    workspaces: list[WorkspaceConfigEntry] | None = None,
    *,
    max_workspaces: int = 10,
) -> EtConfig:
    return EtConfig(max_workspaces=max_workspaces, jira=None, workspaces=workspaces or [])


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


# --- shift_workspaces_left ---------------------------------------------------


def test_shift_workspaces_left_moves_later_slots_and_timers():
    workspaces_list = [
        WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
        WorkspaceConfigEntry(name="ET-2"),
        WorkspaceConfigEntry(name="ISD-C", ref="jira:ISD-C", description="stuff"),
    ]
    stale_timer = _timer(1, "ET-2", elapsed=0)
    c_timer = _timer(2, "ET-3", elapsed=42, running=True)
    entries = [stale_timer, c_timer]

    changed = shift_workspaces_left(workspaces_list, entries, 1)

    assert changed is True
    assert workspaces_list == [
        WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
        WorkspaceConfigEntry(name="ISD-C", ref="jira:ISD-C", description="stuff"),
        WorkspaceConfigEntry(name="ET-3"),
    ]
    assert stale_timer not in entries
    assert c_timer in entries
    assert c_timer["workspaceId"] == 1
    assert c_timer["name"] == "ET-2"


def test_shift_workspaces_left_noop_when_freed_is_static():
    workspaces_list = [WorkspaceConfigEntry(name="mails", type="static")]
    changed = shift_workspaces_left(workspaces_list, [], 0)
    assert changed is False


def test_shift_workspaces_left_noop_when_last_slot():
    workspaces_list = [WorkspaceConfigEntry(name="ET-1")]
    changed = shift_workspaces_left(workspaces_list, [], 0)
    assert changed is False


def test_shift_workspaces_left_preserves_destination_type():
    workspaces_list = [
        WorkspaceConfigEntry(name="mails", type="static"),
        WorkspaceConfigEntry(name="ET-2"),
        WorkspaceConfigEntry(name="ISD-C", ref="jira:ISD-C"),
    ]
    changed = shift_workspaces_left(workspaces_list, [], 1)
    assert changed is False  # no timers to move
    assert workspaces_list == [
        WorkspaceConfigEntry(name="mails", type="static"),
        WorkspaceConfigEntry(name="ISD-C", ref="jira:ISD-C"),
        WorkspaceConfigEntry(name="ET-3"),
    ]


# --- _trim_trailing_default_entries ------------------------------------------


def test_trim_trailing_default_entries_removes_bare_tail():
    workspaces_list = [
        WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
        WorkspaceConfigEntry(name="ET-2"),
        WorkspaceConfigEntry(name="ET-3"),
    ]
    _trim_trailing_default_entries(workspaces_list)
    assert workspaces_list == [WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A")]


def test_trim_trailing_default_entries_keeps_linked_entries():
    workspaces_list = [
        WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
        WorkspaceConfigEntry(name="ISD-B", ref="jira:ISD-B"),
    ]
    _trim_trailing_default_entries(workspaces_list)
    assert workspaces_list == [
        WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
        WorkspaceConfigEntry(name="ISD-B", ref="jira:ISD-B"),
    ]


def test_trim_trailing_default_entries_stops_at_static():
    workspaces_list = [
        WorkspaceConfigEntry(name="mails", type="static"),
        WorkspaceConfigEntry(name="ET-2"),
    ]
    _trim_trailing_default_entries(workspaces_list)
    assert workspaces_list == [WorkspaceConfigEntry(name="mails", type="static")]


# --- delete_active_workspace --------------------------------------------------


@patch("et.ws.workspaces.switch_to_workspace")
@patch("et.ws.workspaces.rename_all_workspaces")
@patch("et.ws.save_config")
@patch("et.ws.workspaces.configure_static_workspace_count")
@patch("et.ws.tracker.save_timers_with_reload")
@patch("et.ws.tracker.load_timers")
@patch("et.ws.workspaces.get_active_workspace_index", return_value=1)
@patch("et.ws.load_config")
def test_delete_active_workspace_shifts_and_shrinks(
    mock_load_config,
    _mock_active_index,
    mock_load_timers,
    mock_save_timers,
    mock_configure_count,
    mock_save_config,
    mock_rename_all,
    mock_switch,
):
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
            WorkspaceConfigEntry(name="ET-2"),
            WorkspaceConfigEntry(name="ISD-C", ref="jira:ISD-C"),
        ],
        max_workspaces=3,
    )
    c_timer = _timer(2, "ET-3", elapsed=99)
    mock_load_timers.return_value = [c_timer]

    result = delete_active_workspace()

    assert result.workspace_index == 1
    assert result.remaining_workspaces == 2

    saved_config = mock_save_config.call_args[0][0]
    assert saved_config.max_workspaces == 2
    assert saved_config.workspaces == [
        WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
        WorkspaceConfigEntry(name="ISD-C", ref="jira:ISD-C"),
    ]
    mock_configure_count.assert_called_once_with(2)
    mock_rename_all.assert_called_once_with(["ISD-A", "ISD-C"])
    mock_switch.assert_called_once_with(1)

    saved_timers = mock_save_timers.call_args[0][0]
    assert c_timer in saved_timers
    assert c_timer["workspaceId"] == 1
    assert c_timer["name"] == "ET-2"


@patch("et.ws.workspaces.switch_to_workspace")
@patch("et.ws.workspaces.rename_all_workspaces")
@patch("et.ws.save_config")
@patch("et.ws.workspaces.configure_static_workspace_count")
@patch("et.ws.tracker.save_timers_with_reload")
@patch("et.ws.tracker.load_timers", return_value=[])
@patch("et.ws.workspaces.get_active_workspace_index", return_value=2)
@patch("et.ws.load_config")
def test_delete_active_workspace_pads_implicit_slots(
    mock_load_config,
    _mock_active_index,
    _mock_load_timers,
    mock_save_timers,
    _mock_configure_count,
    mock_save_config,
    mock_rename_all,
    mock_switch,
):
    # Only one explicit entry configured; deleting slot index 2 (implicit,
    # beyond the explicit list) should be treated as a bare dynamic slot.
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A")],
        max_workspaces=5,
    )

    result = delete_active_workspace()

    assert result.workspace_index == 2
    assert result.remaining_workspaces == 4
    saved_config = mock_save_config.call_args[0][0]
    assert saved_config.max_workspaces == 4
    assert saved_config.workspaces == [WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A")]
    mock_rename_all.assert_called_once_with(["ISD-A"])
    mock_switch.assert_called_once_with(2)
    mock_save_timers.assert_not_called()


@patch("et.ws.workspaces.get_active_workspace_index", return_value=1)
@patch("et.ws.load_config")
def test_delete_active_workspace_rejects_static(mock_load_config, _mock_active_index):
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
            WorkspaceConfigEntry(name="mails", type="static"),
        ],
        max_workspaces=2,
    )
    with pytest.raises(WsDeleteError, match="static"):
        delete_active_workspace()


@patch("et.ws.workspaces.get_active_workspace_index", return_value=0)
@patch("et.ws.load_config")
def test_delete_active_workspace_rejects_linked_ref(mock_load_config, _mock_active_index):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A")],
        max_workspaces=2,
    )
    with pytest.raises(WsDeleteError, match="ISD-A"):
        delete_active_workspace()


@patch("et.ws.load_config")
def test_delete_active_workspace_rejects_when_only_one_left(mock_load_config):
    mock_load_config.return_value = _config([], max_workspaces=1)
    with pytest.raises(WsDeleteError, match="last remaining"):
        delete_active_workspace()


@patch("et.ws.workspaces.get_active_workspace_index", side_effect=WorkspaceError("no active ws"))
@patch("et.ws.load_config")
def test_delete_active_workspace_propagates_workspace_error(
    mock_load_config, _mock_active_index
):
    mock_load_config.return_value = _config([], max_workspaces=5)
    with pytest.raises(WorkspaceError, match="no active ws"):
        delete_active_workspace()


@patch("et.ws.load_config", side_effect=ConfigError("bad config"))
def test_delete_active_workspace_propagates_config_error(_mock_load_config):
    with pytest.raises(ConfigError, match="bad config"):
        delete_active_workspace()


@patch("et.ws.tracker.load_timers", side_effect=TrackerError("tracker down"))
@patch("et.ws.workspaces.get_active_workspace_index", return_value=0)
@patch("et.ws.load_config")
def test_delete_active_workspace_wraps_tracker_error(
    mock_load_config, _mock_active_index, _mock_load_timers
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ET-1")], max_workspaces=2
    )
    with pytest.raises(WsDeleteError, match="tracker down"):
        delete_active_workspace()


@patch("et.ws.workspaces.rename_all_workspaces", side_effect=WorkspaceError("rename boom"))
@patch("et.ws.save_config")
@patch("et.ws.workspaces.configure_static_workspace_count")
@patch("et.ws.tracker.load_timers", return_value=[])
@patch("et.ws.workspaces.get_active_workspace_index", return_value=0)
@patch("et.ws.load_config")
def test_delete_active_workspace_wraps_workspace_error_during_apply(
    mock_load_config,
    _mock_active_index,
    _mock_load_timers,
    _mock_configure_count,
    _mock_save_config,
    _mock_rename_all,
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ET-1")], max_workspaces=2
    )
    with pytest.raises(WsDeleteError, match="rename boom"):
        delete_active_workspace()
