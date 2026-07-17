"""Tests for et.workspaces, mocking wmctrl/gsettings subprocess calls."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from et.gsettings import GSettingsError
from et.workspaces import (
    WorkspaceError,
    configure_static_workspace_count,
    get_active_workspace_index,
    get_workspace_names,
    rename_active_workspace,
    rename_all_workspaces,
    set_workspace_names,
)


def _completed(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


WMCTRL_OUTPUT = (
    "0  - DG: 1920x1080  VP: 0,0  WA: 0,0 1920x1055  Workspace 1\n"
    "1  * DG: 1920x1080  VP: 0,0  WA: 0,0 1920x1055  Workspace 2\n"
    "2  - DG: 1920x1080  VP: 0,0  WA: 0,0 1920x1055  Workspace 3\n"
)


@patch("et.workspaces.shutil.which", return_value="/usr/bin/wmctrl")
@patch("et.workspaces.subprocess.run")
def test_get_active_workspace_index_returns_marked_index(mock_run, _mock_which):
    mock_run.return_value = _completed(stdout=WMCTRL_OUTPUT)
    assert get_active_workspace_index() == 1


@patch("et.workspaces.shutil.which", return_value="/usr/bin/wmctrl")
@patch("et.workspaces.subprocess.run")
def test_get_active_workspace_index_raises_when_no_marker(mock_run, _mock_which):
    mock_run.return_value = _completed(
        stdout="0  - DG: 1920x1080  VP: 0,0  WA: 0,0 1920x1055  Workspace 1\n"
    )
    with pytest.raises(WorkspaceError, match="no active workspace"):
        get_active_workspace_index()


@patch("et.workspaces.shutil.which", return_value=None)
def test_get_active_workspace_index_raises_when_wmctrl_missing(_mock_which):
    with pytest.raises(WorkspaceError, match="wmctrl"):
        get_active_workspace_index()


@patch(
    "et.workspaces.gsettings.read_string_array",
    return_value=["", "Focus", ""],
)
def test_get_workspace_names_parses_list(mock_read):
    assert get_workspace_names() == ["", "Focus", ""]
    mock_read.assert_called_once_with(
        "org.gnome.desktop.wm.preferences", "workspace-names"
    )


@patch("et.workspaces.gsettings.read_string_array", side_effect=GSettingsError("boom"))
def test_get_workspace_names_wraps_gsettings_error(mock_read):
    with pytest.raises(WorkspaceError, match="boom"):
        get_workspace_names()


@patch("et.workspaces.gsettings.write_string_array")
def test_set_workspace_names_calls_gsettings_helper(mock_write):
    set_workspace_names(["", "Focus"])
    mock_write.assert_called_once_with(
        "org.gnome.desktop.wm.preferences", "workspace-names", ["", "Focus"]
    )


@patch("et.workspaces.set_workspace_names")
@patch("et.workspaces.get_workspace_names", return_value=["", ""])
@patch("et.workspaces.get_active_workspace_index", return_value=2)
def test_rename_active_workspace_pads_list_when_needed(
    mock_get_index, mock_get_names, mock_set_names
):
    index = rename_active_workspace("focus")
    assert index == 2
    mock_set_names.assert_called_once_with(["", "", "focus"])


@patch("et.workspaces.set_workspace_names")
@patch("et.workspaces.get_workspace_names", return_value=["a", "b", "c"])
@patch("et.workspaces.get_active_workspace_index", return_value=1)
def test_rename_active_workspace_replaces_existing_name(
    mock_get_index, mock_get_names, mock_set_names
):
    index = rename_active_workspace("focus")
    assert index == 1
    mock_set_names.assert_called_once_with(["a", "focus", "c"])


@patch("et.workspaces.gsettings.set_int")
@patch("et.workspaces.gsettings.set_boolean")
def test_configure_static_workspace_count_disables_dynamic_and_sets_count(
    mock_set_boolean, mock_set_int
):
    configure_static_workspace_count(10)
    mock_set_boolean.assert_called_once_with("org.gnome.mutter", "dynamic-workspaces", False)
    mock_set_int.assert_called_once_with(
        "org.gnome.desktop.wm.preferences", "num-workspaces", 10
    )


@patch("et.workspaces.gsettings.set_int", side_effect=GSettingsError("boom"))
@patch("et.workspaces.gsettings.set_boolean")
def test_configure_static_workspace_count_wraps_gsettings_error(mock_set_boolean, mock_set_int):
    with pytest.raises(WorkspaceError, match="boom"):
        configure_static_workspace_count(10)


@patch("et.workspaces.set_workspace_names")
@patch("et.workspaces.get_workspace_names", return_value=["old-a", "old-b", "old-c"])
def test_rename_all_workspaces_replaces_leading_names(mock_get_names, mock_set_names):
    indices = rename_all_workspaces(["mails", "handson"])
    assert indices == [0, 1]
    mock_set_names.assert_called_once_with(["mails", "handson", "old-c"])


@patch("et.workspaces.set_workspace_names")
@patch("et.workspaces.get_workspace_names", return_value=["old-a"])
def test_rename_all_workspaces_pads_when_config_has_more_entries(mock_get_names, mock_set_names):
    indices = rename_all_workspaces(["mails", "handson", "isd-321"])
    assert indices == [0, 1, 2]
    mock_set_names.assert_called_once_with(["mails", "handson", "isd-321"])


@patch("et.workspaces.set_workspace_names")
@patch("et.workspaces.get_workspace_names", return_value=[])
def test_rename_all_workspaces_with_empty_list_is_noop_write(mock_get_names, mock_set_names):
    indices = rename_all_workspaces([])
    assert indices == []
    mock_set_names.assert_called_once_with([])
