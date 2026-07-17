"""Logic for inspecting and renaming GNOME/Ubuntu workspaces.

This module shells out to `wmctrl` (to find the active workspace) and
`gsettings` (to read/write GNOME's workspace-names setting). It has no
Typer/CLI dependency so it can be unit tested by mocking `subprocess.run`.
"""

from __future__ import annotations

import ast
import shutil
import subprocess

WORKSPACE_NAMES_SCHEMA = "org.gnome.desktop.wm.preferences"
WORKSPACE_NAMES_KEY = "workspace-names"


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
    _require_binary("gsettings")
    result = subprocess.run(
        ["gsettings", "get", WORKSPACE_NAMES_SCHEMA, WORKSPACE_NAMES_KEY],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise WorkspaceError(f"gsettings get failed: {result.stderr.strip()}")

    raw = result.stdout.strip()
    if raw.startswith("@as "):
        raw = raw[len("@as "):]
    try:
        names = ast.literal_eval(raw)
    except (ValueError, SyntaxError) as exc:
        raise WorkspaceError(f"could not parse workspace-names value: {raw!r}") from exc

    if not isinstance(names, list) or not all(isinstance(item, str) for item in names):
        raise WorkspaceError(f"unexpected workspace-names value: {raw!r}")

    return names


def set_workspace_names(names: list[str]) -> None:
    """Write the given list of workspace names to GNOME's settings."""
    _require_binary("gsettings")
    result = subprocess.run(
        ["gsettings", "set", WORKSPACE_NAMES_SCHEMA, WORKSPACE_NAMES_KEY, repr(names)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise WorkspaceError(f"gsettings set failed: {result.stderr.strip()}")


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
