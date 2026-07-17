"""Logic for managing the GNOME Shell "Tracker" extension's timers.

Tracker (tracker@aliakseiz.github.com) persists all of its timers as a list
of JSON-encoded strings in a single GSettings key. This module reads/writes
that key via `et.gsettings` and knows how to find or create the timer
associated with a given workspace. It has no Typer/CLI dependency and reuses
`et.workspaces` to determine the active workspace and its GNOME name.
"""

from __future__ import annotations

import json
import uuid

from et import gsettings, workspaces
from et.gsettings import GSettingsError

TRACKER_SCHEMA = "org.gnome.shell.extensions.tracker"
TRACKER_TIMERS_KEY = "timers"

# A Tracker entry is either a timer (id, name, timeElapsed, running, selected,
# workspaceId) or the special {"id": "settings", "totalTimeSelected": ...}
# sentinel. Values are JSON-compatible (str, int, bool, or None).
TimerEntry = dict[str, object]


class TrackerError(RuntimeError):
    """Raised when a Tracker timer operation cannot be completed."""


def _load_timers() -> list[TimerEntry]:
    """Return the raw list of Tracker entries (timers and the settings sentinel)."""
    try:
        raw_entries = gsettings.read_string_array(TRACKER_SCHEMA, TRACKER_TIMERS_KEY)
    except GSettingsError as exc:
        raise TrackerError(
            f"could not read Tracker timers (is the Tracker GNOME extension "
            f"installed and enabled?): {exc}"
        ) from exc

    entries = []
    for raw_entry in raw_entries:
        try:
            entries.append(json.loads(raw_entry))
        except json.JSONDecodeError as exc:
            raise TrackerError(f"could not parse Tracker timer entry: {raw_entry!r}") from exc

    return entries


def _save_timers(entries: list[TimerEntry]) -> None:
    """Write the given list of Tracker entries back to GSettings."""
    raw_entries = [json.dumps(entry) for entry in entries]
    try:
        gsettings.write_string_array(TRACKER_SCHEMA, TRACKER_TIMERS_KEY, raw_entries)
    except GSettingsError as exc:
        raise TrackerError(f"could not write Tracker timers: {exc}") from exc


def find_timer_for_workspace(entries: list[TimerEntry], workspace_id: int) -> TimerEntry | None:
    """Return the first entry associated with `workspace_id`, if any."""
    for entry in entries:
        if entry.get("workspaceId") == workspace_id:
            return entry
    return None


def build_new_timer(workspace_id: int, name: str) -> TimerEntry:
    """Build a new Tracker timer dict for `workspace_id`, matching Tracker's own shape."""
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "timeElapsed": 0,
        "running": False,
        "selected": False,
        "workspaceId": workspace_id,
    }


def add_tracker_for_current_workspace() -> tuple[int, str, bool]:
    """Add a Tracker timer for the active workspace, if one doesn't already exist.

    Returns a tuple of (workspace_index, timer_name, created), where `created`
    is False if a timer was already associated with the active workspace (in
    which case no write happens and `timer_name` is the existing timer's name).
    """
    index = workspaces.get_active_workspace_index()
    ws_names = workspaces.get_workspace_names()
    default_name = (
        ws_names[index] if index < len(ws_names) and ws_names[index] else f"Workspace {index + 1}"
    )

    entries = _load_timers()
    existing = find_timer_for_workspace(entries, index)
    if existing is not None:
        return index, str(existing["name"]), False

    new_timer = build_new_timer(index, default_name)
    entries.append(new_timer)
    _save_timers(entries)
    return index, default_name, True
