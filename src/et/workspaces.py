"""Logic for inspecting and renaming GNOME/Ubuntu workspaces.

This module shells out to `wmctrl` (to find the active workspace) and, via
`et.gsettings`, to `gsettings` (to read/write GNOME's workspace-names
setting). It has no Typer/CLI dependency so it can be unit tested by mocking
`subprocess.run`.
"""

from __future__ import annotations

import shutil
import subprocess

from et import gsettings
from et.gsettings import GSettingsError

WORKSPACE_NAMES_SCHEMA = "org.gnome.desktop.wm.preferences"
WORKSPACE_NAMES_KEY = "workspace-names"
NUM_WORKSPACES_KEY = "num-workspaces"
MUTTER_SCHEMA = "org.gnome.mutter"
DYNAMIC_WORKSPACES_KEY = "dynamic-workspaces"


class WorkspaceError(RuntimeError):
    """Raised when a workspace operation cannot be completed."""


def _require_binary(name: str) -> None:
    if shutil.which(name) is None:
        raise WorkspaceError(f"required command not found: {name}")


def get_active_workspace_index() -> int:
    """Return the 0-based index of the currently active workspace."""
    _require_binary("wmctrl")
    result = subprocess.run(
        ["wmctrl", "-d"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise WorkspaceError(f"wmctrl -d failed: {result.stderr.strip()}")

    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        index_str, status = fields[0], fields[1]
        if status == "*":
            return int(index_str)

    raise WorkspaceError("no active workspace found in `wmctrl -d` output")


def get_workspace_names() -> list[str]:
    """Return the current list of GNOME workspace names."""
    try:
        return gsettings.read_string_array(WORKSPACE_NAMES_SCHEMA, WORKSPACE_NAMES_KEY)
    except GSettingsError as exc:
        raise WorkspaceError(str(exc)) from exc


def set_workspace_names(names: list[str]) -> None:
    """Write the given list of workspace names to GNOME's settings."""
    try:
        gsettings.write_string_array(WORKSPACE_NAMES_SCHEMA, WORKSPACE_NAMES_KEY, names)
    except GSettingsError as exc:
        raise WorkspaceError(str(exc)) from exc


def configure_static_workspace_count(count: int) -> None:
    """Switch GNOME to a fixed number of workspaces, disabling dynamic workspaces.

    By default GNOME grows/shrinks the number of workspaces on demand
    (`dynamic-workspaces=true`). This disables that and pins the workspace
    count to exactly `count`, so workspaces 0..count-1 always exist.
    """
    try:
        gsettings.set_boolean(MUTTER_SCHEMA, DYNAMIC_WORKSPACES_KEY, False)
        gsettings.set_int(WORKSPACE_NAMES_SCHEMA, NUM_WORKSPACES_KEY, count)
    except GSettingsError as exc:
        raise WorkspaceError(str(exc)) from exc


def rename_active_workspace(new_name: str) -> int:
    """Rename the active workspace to `new_name`.

    Returns the 0-based index of the workspace that was renamed.
    """
    index = get_active_workspace_index()
    names = get_workspace_names()

    if len(names) <= index:
        names = names + [""] * (index + 1 - len(names))

    names[index] = new_name
    set_workspace_names(names)
    return index


def rename_all_workspaces(new_names: list[str]) -> list[int]:
    """Rename workspaces 0..len(new_names)-1 to `new_names`, in order.

    Any existing workspace at an index >= len(new_names) is left untouched.
    Returns the list of 0-based workspace indices that were renamed.
    """
    names = get_workspace_names()

    if len(names) < len(new_names):
        names = names + [""] * (len(new_names) - len(names))

    for index, name in enumerate(new_names):
        names[index] = name

    set_workspace_names(names)
    return list(range(len(new_names)))
