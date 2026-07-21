"""Logic for managing the GNOME Shell "Tracker" extension's timers.

Tracker (tracker@aliakseiz.github.com) persists all of its timers as a list
of JSON-encoded strings in a single GSettings key. This module reads/writes
that key via `et.gsettings` and knows how to find or create the timer
associated with a given workspace. It has no Typer/CLI dependency. New
timers are named "ET-<workspace number>" (1-indexed) rather than the
workspace's GNOME name, since workspace names can be renamed or unset and
would make timer names unstable over time.

A running Tracker instance keeps its own in-memory copy of its timers and
resaves it (verbatim) in reaction to almost any GSettings change, silently
erasing brand-new entries it doesn't already know about. To avoid that, new
timers are written while Tracker is briefly disabled via `et.gnome_extensions`,
then Tracker is re-enabled so it fully reloads from GSettings from scratch.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from et import gsettings
from et.gnome_extensions import GnomeExtensionsError, reload_around
from et.gsettings import GSettingsError

TRACKER_SCHEMA = "org.gnome.shell.extensions.tracker"
TRACKER_TIMERS_KEY = "timers"
TRACKER_EXTENSION_UUID = "tracker@aliakseiz.github.com"

# A Tracker entry is either a timer (id, name, timeElapsed, running, selected,
# workspaceId) or the special {"id": "settings", "totalTimeSelected": ...}
# sentinel. Values are JSON-compatible (str, int, bool, or None).
TimerEntry = dict[str, object]


class TrackerError(RuntimeError):
    """Raised when a Tracker timer operation cannot be completed."""


def _find_extension_schema_dir() -> str | None:
    """Locate Tracker's compiled schema directory, if installed as an extension.

    GNOME Shell extensions installed per-user (the common case for
    extensions.gnome.org installs) ship their own compiled schema under
    `~/.local/share/gnome-shell/extensions/<uuid>/schemas/`. GNOME Shell
    itself knows how to load these, but the plain `gsettings` CLI only looks
    in the standard system schema directories and doesn't find them unless
    `GSETTINGS_SCHEMA_DIR` points at that directory. System-wide extension
    installs (e.g. via a distro package) are also checked as a fallback.
    """
    data_home = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    candidates = [
        data_home / "gnome-shell" / "extensions" / TRACKER_EXTENSION_UUID / "schemas",
        Path("/usr/share/gnome-shell/extensions") / TRACKER_EXTENSION_UUID / "schemas",
    ]
    for candidate in candidates:
        if (candidate / "gschemas.compiled").is_file():
            return str(candidate)
    return None


def load_timers() -> list[TimerEntry]:
    """Return the raw list of Tracker entries (timers and the settings sentinel)."""
    try:
        raw_entries = gsettings.read_string_array(
            TRACKER_SCHEMA, TRACKER_TIMERS_KEY, schema_dir=_find_extension_schema_dir()
        )
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


def save_timers(entries: list[TimerEntry]) -> None:
    """Write the given list of Tracker entries back to GSettings."""
    raw_entries = [json.dumps(entry) for entry in entries]
    try:
        gsettings.write_string_array(
            TRACKER_SCHEMA,
            TRACKER_TIMERS_KEY,
            raw_entries,
            schema_dir=_find_extension_schema_dir(),
        )
    except GSettingsError as exc:
        raise TrackerError(f"could not write Tracker timers: {exc}") from exc


def find_timer_for_workspace(entries: list[TimerEntry], workspace_id: int) -> TimerEntry | None:
    """Return the first entry associated with `workspace_id`, if any."""
    for entry in entries:
        if entry.get("workspaceId") == workspace_id:
            return entry
    return None


def build_new_timer(workspace_id: int, name: str) -> TimerEntry:
    """Build a new Tracker timer dict for `workspace_id`, matching Tracker's own shape.

    `autoResume` is set to `True` so Tracker auto-starts this timer whenever
    its workspace becomes active (and auto-pauses it otherwise) — the same
    state Tracker itself sets when a user manually presses play on a
    workspace-bound timer. Without it, Tracker only tracks the association
    but never starts the timer on its own.
    """
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "timeElapsed": 0,
        "running": False,
        "selected": False,
        "workspaceId": workspace_id,
        "autoResume": True,
    }


def _ensure_timer_for_workspace(entries: list[TimerEntry], index: int) -> tuple[str, bool]:
    """Find or append (in-place) a timer for workspace `index` in `entries`.

    Returns (timer_name, created). Does not write to GSettings; callers are
    responsible for saving `entries` if anything was created.
    """
    existing = find_timer_for_workspace(entries, index)
    if existing is not None:
        return str(existing["name"]), False

    name = f"ET-{index + 1}"
    entries.append(build_new_timer(index, name))
    return name, True


def save_timers_with_reload(entries: list[TimerEntry], action: str) -> None:
    """Save `entries` via the disable/write/enable reload dance, wrapping errors."""
    try:
        with reload_around(TRACKER_EXTENSION_UUID):
            save_timers(entries)
    except GnomeExtensionsError as exc:
        raise TrackerError(
            f"could not reload the Tracker extension after {action} "
            f"(the extension may need a manual reload to pick this up): {exc}"
        ) from exc


def add_tracker_for_workspace(index: int) -> tuple[str, bool]:
    """Add a Tracker timer for workspace `index`, if one doesn't already exist.

    Returns a tuple of (timer_name, created), where `created` is False if a
    timer was already associated with `index` (in which case no write
    happens and `timer_name` is the existing timer's name).
    """
    entries = load_timers()
    name, created = _ensure_timer_for_workspace(entries, index)
    if not created:
        return name, False

    save_timers_with_reload(entries, "writing the new timer")
    return name, True


def format_duration(seconds: float) -> str:
    """Format a number of seconds as e.g. "2h 15m 30s"."""
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}h {minutes}m {secs}s"
