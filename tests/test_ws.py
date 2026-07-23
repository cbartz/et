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
) -> EtConfig:
    return EtConfig(jira=None, workspaces=workspaces or [])


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
@patch("et.ws.workspaces.set_workspace_count")
@patch("et.ws.save_config")
@patch("et.ws.tracker.save_timers_with_reload")
@patch("et.ws.tracker.load_timers")
@patch("et.ws.workspaces.get_workspace_count", return_value=3)
@patch("et.ws.workspaces.get_active_workspace_index", return_value=1)
@patch("et.ws.load_config")
def test_delete_active_workspace_shifts_and_shrinks(
    mock_load_config,
    _mock_active_index,
    _mock_get_count,
    mock_load_timers,
    mock_save_timers,
    mock_save_config,
    mock_set_count,
    mock_rename_all,
    mock_switch,
):
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
            WorkspaceConfigEntry(name="ET-2"),
            WorkspaceConfigEntry(name="ISD-C", ref="jira:ISD-C"),
        ]
    )
    c_timer = _timer(2, "ET-3", elapsed=99)
    mock_load_timers.return_value = [c_timer]

    result = delete_active_workspace()

    assert result.workspace_index == 1
    assert result.remaining_workspaces == 2

    saved_config = mock_save_config.call_args[0][0]
    assert saved_config.workspaces == [
        WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
        WorkspaceConfigEntry(name="ISD-C", ref="jira:ISD-C"),
    ]
    # The now-empty trailing slot is reclaimed by shrinking GNOME's count.
    mock_set_count.assert_called_once_with(2)
    mock_rename_all.assert_called_once_with(["ISD-A", "ISD-C"])
    mock_switch.assert_called_once_with(1)

    saved_timers = mock_save_timers.call_args[0][0]
    assert c_timer in saved_timers
    assert c_timer["workspaceId"] == 1
    assert c_timer["name"] == "ET-2"


@patch("et.ws.workspaces.switch_to_workspace")
@patch("et.ws.workspaces.rename_all_workspaces")
@patch("et.ws.workspaces.set_workspace_count")
@patch("et.ws.save_config")
@patch("et.ws.tracker.save_timers_with_reload")
@patch("et.ws.tracker.load_timers", return_value=[])
@patch("et.ws.workspaces.get_workspace_count", return_value=5)
@patch("et.ws.workspaces.get_active_workspace_index", return_value=2)
@patch("et.ws.load_config")
def test_delete_active_workspace_pads_implicit_slots(
    mock_load_config,
    _mock_active_index,
    _mock_get_count,
    _mock_load_timers,
    mock_save_timers,
    mock_save_config,
    mock_set_count,
    mock_rename_all,
    mock_switch,
):
    # Only one explicit entry configured; deleting slot index 2 (implicit,
    # beyond the explicit list) should be treated as a bare dynamic slot.
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A")]
    )

    result = delete_active_workspace()

    assert result.workspace_index == 2
    assert result.remaining_workspaces == 4
    saved_config = mock_save_config.call_args[0][0]
    assert saved_config.workspaces == [WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A")]
    mock_set_count.assert_called_once_with(4)
    mock_rename_all.assert_called_once_with(["ISD-A", "ET-2", "ET-3", "ET-4"])
    mock_switch.assert_called_once_with(2)
    mock_save_timers.assert_not_called()


@patch("et.ws.workspaces.get_workspace_count", return_value=2)
@patch("et.ws.workspaces.get_active_workspace_index", return_value=1)
@patch("et.ws.load_config")
def test_delete_active_workspace_rejects_static(
    mock_load_config, _mock_active_index, _mock_get_count
):
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
            WorkspaceConfigEntry(name="mails", type="static"),
        ]
    )
    with pytest.raises(WsDeleteError, match="static"):
        delete_active_workspace()


@patch("et.ws.workspaces.get_workspace_count", return_value=2)
@patch("et.ws.workspaces.get_active_workspace_index", return_value=1)
@patch("et.ws.load_config")
def test_delete_active_workspace_rejects_linked_ref(
    mock_load_config, _mock_active_index, _mock_get_count
):
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
            WorkspaceConfigEntry(name="ISD-B", ref="jira:ISD-B"),
        ]
    )
    with pytest.raises(WsDeleteError, match="ISD-B"):
        delete_active_workspace()


@patch("et.ws.workspaces.get_workspace_count", return_value=1)
@patch("et.ws.workspaces.get_active_workspace_index", return_value=0)
@patch("et.ws.load_config")
def test_delete_active_workspace_rejects_last_remaining(
    mock_load_config, _mock_active_index, _mock_get_count
):
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="ET-1")])
    with pytest.raises(WsDeleteError, match="last remaining"):
        delete_active_workspace()


@patch("et.ws.workspaces.switch_to_workspace")
@patch("et.ws.workspaces.rename_all_workspaces")
@patch("et.ws.workspaces.set_workspace_count")
@patch("et.ws.save_config")
@patch("et.ws.tracker.save_timers_with_reload")
@patch("et.ws.tracker.load_timers")
@patch("et.ws.workspaces.get_workspace_count", return_value=2)
@patch("et.ws.workspaces.get_active_workspace_index", return_value=0)
@patch("et.ws.load_config")
def test_delete_active_workspace_force_discards_linked_ref_and_timer(
    mock_load_config,
    _mock_active_index,
    _mock_get_count,
    mock_load_timers,
    mock_save_timers,
    mock_save_config,
    mock_set_count,
    mock_rename_all,
    mock_switch,
):
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
            WorkspaceConfigEntry(name="ISD-B", ref="jira:ISD-B"),
        ]
    )
    a_timer = _timer(0, "ET-1", elapsed=500, running=True)
    b_timer = _timer(1, "ET-2", elapsed=42)
    mock_load_timers.return_value = [a_timer, b_timer]

    result = delete_active_workspace(force=True)

    assert result.workspace_index == 0
    assert result.remaining_workspaces == 1

    saved_config = mock_save_config.call_args[0][0]
    assert saved_config.workspaces == [WorkspaceConfigEntry(name="ISD-B", ref="jira:ISD-B")]
    mock_set_count.assert_called_once_with(1)
    mock_rename_all.assert_called_once_with(["ISD-B"])
    mock_switch.assert_called_once_with(0)

    saved_timers = mock_save_timers.call_args[0][0]
    assert a_timer not in saved_timers  # discarded, not logged
    assert b_timer in saved_timers
    assert b_timer["workspaceId"] == 0
    assert b_timer["name"] == "ET-1"


@patch("et.ws.workspaces.switch_to_workspace")
@patch("et.ws.workspaces.rename_all_workspaces")
@patch("et.ws.workspaces.set_workspace_count")
@patch("et.ws.save_config")
@patch("et.ws.tracker.save_timers_with_reload")
@patch("et.ws.tracker.load_timers")
@patch("et.ws.workspaces.get_workspace_count", return_value=2)
@patch("et.ws.workspaces.get_active_workspace_index", return_value=1)
@patch("et.ws.load_config")
def test_delete_active_workspace_force_last_slot_discards_timer(
    mock_load_config,
    _mock_active_index,
    _mock_get_count,
    mock_load_timers,
    mock_save_timers,
    mock_save_config,
    mock_set_count,
    mock_rename_all,
    mock_switch,
):
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
            WorkspaceConfigEntry(name="ISD-B", ref="jira:ISD-B"),
        ]
    )
    b_timer = _timer(1, "ET-2", elapsed=500)
    mock_load_timers.return_value = [b_timer]

    result = delete_active_workspace(force=True)

    assert result.workspace_index == 1
    assert result.remaining_workspaces == 1
    saved_config = mock_save_config.call_args[0][0]
    assert saved_config.workspaces == [WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A")]
    mock_set_count.assert_called_once_with(1)
    mock_rename_all.assert_called_once_with(["ISD-A"])
    mock_switch.assert_called_once_with(0)
    # Deleting the last non-static slot has nothing to shift, but the deleted
    # workspace's own timer must still be discarded rather than orphaned.
    mock_save_timers.assert_called_once()
    saved_timers = mock_save_timers.call_args[0][0]
    assert b_timer not in saved_timers
    assert saved_timers == []


@patch("et.ws.workspaces.switch_to_workspace")
@patch("et.ws.workspaces.rename_all_workspaces")
@patch("et.ws.workspaces.set_workspace_count")
@patch("et.ws.save_config")
@patch("et.ws.tracker.save_timers_with_reload")
@patch("et.ws.tracker.load_timers", return_value=[])
@patch("et.ws.workspaces.get_workspace_count", return_value=2)
@patch("et.ws.workspaces.get_active_workspace_index", return_value=0)
@patch("et.ws.load_config")
def test_delete_active_workspace_keeps_count_when_last_is_static(
    mock_load_config,
    _mock_active_index,
    _mock_get_count,
    _mock_load_timers,
    mock_save_timers,
    mock_save_config,
    mock_set_count,
    mock_rename_all,
    mock_switch,
):
    # Deleting slot 0 when the highest-numbered workspace is static: shrinking
    # would swallow the static one, so the count is left untouched and the
    # freed slot just becomes a bare "ET-<n>".
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="scratch-task"),
            WorkspaceConfigEntry(name="mails", type="static"),
        ]
    )

    result = delete_active_workspace()

    assert result.workspace_index == 0
    assert result.remaining_workspaces == 2
    mock_set_count.assert_not_called()
    saved_config = mock_save_config.call_args[0][0]
    assert saved_config.workspaces == [
        WorkspaceConfigEntry(name="ET-1"),
        WorkspaceConfigEntry(name="mails", type="static"),
    ]
    mock_rename_all.assert_called_once_with(["ET-1", "mails"])
    mock_switch.assert_called_once_with(0)


@patch("et.ws.workspaces.get_workspace_count", return_value=2)
@patch("et.ws.workspaces.get_active_workspace_index", return_value=1)
@patch("et.ws.load_config")
def test_delete_active_workspace_force_still_rejects_static(
    mock_load_config, _mock_active_index, _mock_get_count
):
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
            WorkspaceConfigEntry(name="mails", type="static"),
        ]
    )
    with pytest.raises(WsDeleteError, match="static"):
        delete_active_workspace(force=True)


@patch("et.ws.workspaces.get_workspace_count", return_value=5)
@patch("et.ws.workspaces.get_active_workspace_index", side_effect=WorkspaceError("no active ws"))
@patch("et.ws.load_config")
def test_delete_active_workspace_propagates_workspace_error(
    mock_load_config, _mock_active_index, _mock_get_count
):
    mock_load_config.return_value = _config([])
    with pytest.raises(WorkspaceError, match="no active ws"):
        delete_active_workspace()


@patch("et.ws.load_config", side_effect=ConfigError("bad config"))
def test_delete_active_workspace_propagates_config_error(_mock_load_config):
    with pytest.raises(ConfigError, match="bad config"):
        delete_active_workspace()


@patch("et.ws.tracker.load_timers", side_effect=TrackerError("tracker down"))
@patch("et.ws.workspaces.get_workspace_count", return_value=2)
@patch("et.ws.workspaces.get_active_workspace_index", return_value=0)
@patch("et.ws.load_config")
def test_delete_active_workspace_wraps_tracker_error(
    mock_load_config, _mock_active_index, _mock_get_count, _mock_load_timers
):
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="ET-1")])
    with pytest.raises(WsDeleteError, match="tracker down"):
        delete_active_workspace()


@patch("et.ws.workspaces.rename_all_workspaces", side_effect=WorkspaceError("rename boom"))
@patch("et.ws.workspaces.set_workspace_count")
@patch("et.ws.save_config")
@patch("et.ws.tracker.load_timers", return_value=[])
@patch("et.ws.workspaces.get_workspace_count", return_value=2)
@patch("et.ws.workspaces.get_active_workspace_index", return_value=0)
@patch("et.ws.load_config")
def test_delete_active_workspace_wraps_workspace_error_during_apply(
    mock_load_config,
    _mock_active_index,
    _mock_get_count,
    _mock_load_timers,
    _mock_save_config,
    _mock_set_count,
    _mock_rename_all,
):
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="ET-1")])
    with pytest.raises(WsDeleteError, match="rename boom"):
        delete_active_workspace()
