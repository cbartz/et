"""Tests for et.tracker, mocking the gsettings, gnome_extensions, and workspaces layers."""

from __future__ import annotations

import json
from contextlib import nullcontext
from datetime import date
from unittest.mock import patch

import pytest

from et.gnome_extensions import GnomeExtensionsError
from et.gsettings import GSettingsError
from et.tracker import (
    TrackerError,
    add_tracker_for_current_workspace,
    add_trackers_for_all_workspaces,
    build_new_timer,
    dump_all_trackers,
    dump_timer_to_file,
    find_timer_for_workspace,
    reset_all_trackers,
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
    assert timer["autoResume"] is True
    assert isinstance(timer["id"], str) and timer["id"]


@patch("et.tracker.reload_around", return_value=nullcontext())
@patch("et.tracker.gsettings.write_string_array")
@patch("et.tracker.gsettings.read_string_array", return_value=[])
@patch("et.tracker.workspaces.get_active_workspace_index", return_value=0)
def test_add_tracker_creates_timer_named_with_et_prefix(
    mock_get_index, mock_read, mock_write, mock_reload
):
    index, name, created = add_tracker_for_current_workspace()
    assert (index, name, created) == (0, "ET-1", True)

    written_entries = [json.loads(raw) for raw in mock_write.call_args[0][2]]
    assert written_entries == [build_new_timer(0, "ET-1") | {"id": written_entries[0]["id"]}]


@patch("et.tracker.reload_around", return_value=nullcontext())
@patch("et.tracker.gsettings.write_string_array")
@patch("et.tracker.gsettings.read_string_array", return_value=[])
@patch("et.tracker.workspaces.get_active_workspace_index", return_value=3)
def test_add_tracker_names_timer_using_one_indexed_workspace_number(
    mock_get_index, mock_read, mock_write, mock_reload
):
    index, name, created = add_tracker_for_current_workspace()
    assert (index, name, created) == (3, "ET-4", True)
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
@patch("et.tracker.workspaces.get_active_workspace_index", return_value=0)
def test_add_tracker_is_noop_when_timer_already_exists(
    mock_get_index, mock_read, mock_write
):
    index, name, created = add_tracker_for_current_workspace()
    assert (index, name, created) == (0, "mails", False)
    mock_write.assert_not_called()


@patch("et.tracker.reload_around", return_value=nullcontext())
@patch("et.tracker.gsettings.write_string_array")
@patch(
    "et.tracker.gsettings.read_string_array",
    return_value=[json.dumps(SETTINGS_SENTINEL)],
)
@patch("et.tracker.workspaces.get_active_workspace_index", return_value=0)
def test_add_tracker_preserves_sentinel_entry(
    mock_get_index, mock_read, mock_write, mock_reload
):
    add_tracker_for_current_workspace()
    written_entries = [json.loads(raw) for raw in mock_write.call_args[0][2]]
    assert written_entries[0] == SETTINGS_SENTINEL
    assert len(written_entries) == 2


@patch("et.tracker.gsettings.read_string_array", side_effect=GSettingsError("no schema"))
@patch("et.tracker.workspaces.get_active_workspace_index", return_value=0)
def test_add_tracker_wraps_gsettings_error(mock_get_index, mock_read):
    with pytest.raises(TrackerError, match="Tracker GNOME extension"):
        add_tracker_for_current_workspace()


@patch("et.tracker.reload_around", side_effect=GnomeExtensionsError("could not disable"))
@patch("et.tracker.gsettings.write_string_array")
@patch("et.tracker.gsettings.read_string_array", return_value=[])
@patch("et.tracker.workspaces.get_active_workspace_index", return_value=0)
def test_add_tracker_wraps_gnome_extensions_error(
    mock_get_index, mock_read, mock_write, mock_reload
):
    with pytest.raises(TrackerError, match="reload the Tracker extension"):
        add_tracker_for_current_workspace()
    mock_write.assert_not_called()


@patch("et.tracker.reload_around", return_value=nullcontext())
@patch("et.tracker.gsettings.write_string_array")
@patch("et.tracker.gsettings.read_string_array", return_value=[])
@patch("et.tracker.workspaces.configure_static_workspace_count")
def test_add_all_configures_static_workspaces_and_creates_ten_timers(
    mock_configure, mock_read, mock_write, mock_reload
):
    results = add_trackers_for_all_workspaces()

    mock_configure.assert_called_once_with(10)
    assert [r[:2] for r in results] == [(i, f"ET-{i + 1}") for i in range(10)]
    assert all(created for (_, _, created) in results)

    written_entries = [json.loads(raw) for raw in mock_write.call_args[0][2]]
    assert [entry["name"] for entry in written_entries] == [f"ET-{i + 1}" for i in range(10)]
    assert all(entry["workspaceId"] == i for i, entry in enumerate(written_entries))


@patch("et.tracker.reload_around", return_value=nullcontext())
@patch("et.tracker.gsettings.write_string_array")
@patch(
    "et.tracker.gsettings.read_string_array",
    return_value=[json.dumps({"id": "a", "name": "ET-1", "workspaceId": 0})],
)
@patch("et.tracker.workspaces.configure_static_workspace_count")
def test_add_all_skips_workspaces_that_already_have_a_timer(
    mock_configure, mock_read, mock_write, mock_reload
):
    results = add_trackers_for_all_workspaces(count=2)

    assert results == [(0, "ET-1", False), (1, "ET-2", True)]
    written_entries = [json.loads(raw) for raw in mock_write.call_args[0][2]]
    assert [entry["name"] for entry in written_entries] == ["ET-1", "ET-2"]


@patch("et.tracker.reload_around", return_value=nullcontext())
@patch("et.tracker.gsettings.write_string_array")
@patch(
    "et.tracker.gsettings.read_string_array",
    return_value=[
        json.dumps({"id": "a", "name": "ET-1", "workspaceId": 0}),
        json.dumps({"id": "b", "name": "ET-2", "workspaceId": 1}),
    ],
)
@patch("et.tracker.workspaces.configure_static_workspace_count")
def test_add_all_writes_nothing_when_every_timer_already_exists(
    mock_configure, mock_read, mock_write, mock_reload
):
    results = add_trackers_for_all_workspaces(count=2)

    assert results == [(0, "ET-1", False), (1, "ET-2", False)]
    mock_write.assert_not_called()


@patch("et.tracker.reload_around", return_value=nullcontext())
@patch("et.tracker.gsettings.write_string_array")
@patch(
    "et.tracker.gsettings.read_string_array",
    return_value=[
        json.dumps(SETTINGS_SENTINEL),
        json.dumps(
            {"id": "a", "name": "ET-1", "workspaceId": 0, "timeElapsed": 125, "running": True}
        ),
        json.dumps({"id": "b", "name": "Test 1", "workspaceId": 1, "timeElapsed": 999}),
    ],
)
def test_reset_all_only_touches_et_prefixed_timers(mock_read, mock_write, mock_reload):
    reset_names = reset_all_trackers()

    assert reset_names == ["ET-1"]
    written_entries = [json.loads(raw) for raw in mock_write.call_args[0][2]]
    et_entry = next(e for e in written_entries if e.get("name") == "ET-1")
    other_entry = next(e for e in written_entries if e.get("name") == "Test 1")
    assert et_entry["timeElapsed"] == 0
    assert et_entry["running"] is False
    assert other_entry["timeElapsed"] == 999


@patch("et.tracker.gsettings.write_string_array")
@patch(
    "et.tracker.gsettings.read_string_array",
    return_value=[json.dumps(SETTINGS_SENTINEL)],
)
def test_reset_all_is_noop_when_no_et_timers_exist(mock_read, mock_write):
    assert reset_all_trackers() == []
    mock_write.assert_not_called()


@patch(
    "et.tracker.gsettings.read_string_array",
    return_value=[
        json.dumps(SETTINGS_SENTINEL),
        json.dumps({"id": "a", "name": "ET-1", "workspaceId": 0, "timeElapsed": 3725}),
        json.dumps({"id": "b", "name": "Test 1", "workspaceId": 1, "timeElapsed": 999}),
    ],
)
def test_dump_all_writes_one_file_per_et_timer(mock_read, tmp_path):
    written = dump_all_trackers(base_dir=tmp_path)

    assert len(written) == 1
    et_file = written[0]
    assert et_file.name == "ET-1.txt"
    assert et_file.parent.name == date.today().isoformat()
    assert et_file.read_text() == "3725\n1h 2m 5s\n"


@patch("et.tracker.gsettings.read_string_array", return_value=[json.dumps(SETTINGS_SENTINEL)])
def test_dump_all_is_noop_when_no_et_timers_exist(mock_read, tmp_path):
    assert dump_all_trackers(base_dir=tmp_path) == []
    assert not (tmp_path / date.today().isoformat()).exists()


def test_dump_timer_to_file_writes_seconds_and_duration(tmp_path):
    entry = {"id": "a", "name": "ET-1", "timeElapsed": 3725}
    path = tmp_path / "nested" / "ET-1.txt"

    dump_timer_to_file(entry, path)

    assert path.read_text() == "3725\n1h 2m 5s\n"


def test_dump_timer_to_file_treats_missing_elapsed_as_zero(tmp_path):
    entry = {"id": "a", "name": "ET-1"}
    path = tmp_path / "ET-1.txt"

    dump_timer_to_file(entry, path)

    assert path.read_text() == "0\n0h 0m 0s\n"
