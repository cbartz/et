"""Tests for et.tracker, mocking the gsettings and workspaces layers."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from et.gsettings import GSettingsError
from et.tracker import (
    TrackerError,
    add_tracker_for_current_workspace,
    build_new_timer,
    find_timer_for_workspace,
)

SETTINGS_SENTINEL = {"id": "settings", "totalTimeSelected": True}


def test_find_timer_for_workspace_returns_matching_entry():
    entries = [
        SETTINGS_SENTINEL,
        {"id": "a", "name": "mails", "workspaceId": 0},
        {"id": "b", "name": "handson", "workspaceId": 1},
    ]
    assert find_timer_for_workspace(entries, 1) == {"id": "b", "name": "handson", "workspaceId": 1}


def test_find_timer_for_workspace_ignores_sentinel_and_returns_none_when_missing():
    entries = [SETTINGS_SENTINEL, {"id": "a", "name": "mails", "workspaceId": 0}]
    assert find_timer_for_workspace(entries, 5) is None


def test_build_new_timer_matches_tracker_default_shape():
    timer = build_new_timer(2, "focus")
    assert timer["name"] == "focus"
    assert timer["workspaceId"] == 2
    assert timer["timeElapsed"] == 0
    assert timer["running"] is False
    assert timer["selected"] is False
    assert isinstance(timer["id"], str) and timer["id"]


@patch("et.tracker.gsettings.write_string_array")
@patch("et.tracker.gsettings.read_string_array", return_value=[])
@patch("et.tracker.workspaces.get_workspace_names", return_value=["mails", "handson"])
@patch("et.tracker.workspaces.get_active_workspace_index", return_value=0)
def test_add_tracker_creates_timer_using_workspace_name(
    mock_get_index, mock_get_names, mock_read, mock_write
):
    index, name, created = add_tracker_for_current_workspace()
    assert (index, name, created) == (0, "mails", True)

    written_entries = [json.loads(raw) for raw in mock_write.call_args[0][2]]
    assert written_entries == [build_new_timer(0, "mails") | {"id": written_entries[0]["id"]}]


@patch("et.tracker.gsettings.write_string_array")
@patch("et.tracker.gsettings.read_string_array", return_value=[])
@patch("et.tracker.workspaces.get_workspace_names", return_value=[])
@patch("et.tracker.workspaces.get_active_workspace_index", return_value=3)
def test_add_tracker_falls_back_to_workspace_number_when_unnamed(
    mock_get_index, mock_get_names, mock_read, mock_write
):
    index, name, created = add_tracker_for_current_workspace()
    assert (index, name, created) == (3, "Workspace 4", True)
    mock_write.assert_called_once()


@patch("et.tracker.gsettings.write_string_array")
@patch(
    "et.tracker.gsettings.read_string_array",
    return_value=[
        json.dumps(SETTINGS_SENTINEL),
        json.dumps({"id": "existing", "name": "mails", "timeElapsed": 42, "running": False,
                     "selected": False, "workspaceId": 0}),
    ],
)
@patch("et.tracker.workspaces.get_workspace_names", return_value=["mails"])
@patch("et.tracker.workspaces.get_active_workspace_index", return_value=0)
def test_add_tracker_is_noop_when_timer_already_exists(
    mock_get_index, mock_get_names, mock_read, mock_write
):
    index, name, created = add_tracker_for_current_workspace()
    assert (index, name, created) == (0, "mails", False)
    mock_write.assert_not_called()


@patch("et.tracker.gsettings.write_string_array")
@patch(
    "et.tracker.gsettings.read_string_array",
    return_value=[json.dumps(SETTINGS_SENTINEL)],
)
@patch("et.tracker.workspaces.get_workspace_names", return_value=["mails"])
@patch("et.tracker.workspaces.get_active_workspace_index", return_value=0)
def test_add_tracker_preserves_sentinel_entry(
    mock_get_index, mock_get_names, mock_read, mock_write
):
    add_tracker_for_current_workspace()
    written_entries = [json.loads(raw) for raw in mock_write.call_args[0][2]]
    assert written_entries[0] == SETTINGS_SENTINEL
    assert len(written_entries) == 2


@patch("et.tracker.gsettings.read_string_array", side_effect=GSettingsError("no schema"))
@patch("et.tracker.workspaces.get_workspace_names", return_value=["mails"])
@patch("et.tracker.workspaces.get_active_workspace_index", return_value=0)
def test_add_tracker_wraps_gsettings_error(mock_get_index, mock_get_names, mock_read):
    with pytest.raises(TrackerError, match="Tracker GNOME extension"):
        add_tracker_for_current_workspace()
