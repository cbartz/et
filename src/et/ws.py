"""Orchestrates non-trivial `et ws` commands (currently just `et ws delete`).

`shift_workspaces_left` is also reused by `et.task` (`et jira complete`'s
"shift everything after the freed slot left, instead of leaving a gap"
logic), which is why it lives here rather than directly in `et.workspaces`
(which has no config/tracker dependency). Has no Typer/CLI dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from et import tracker, workspaces
from et.config import ConfigError, EtConfig, WorkspaceConfigEntry, load_config, save_config
from et.jira_ref import default_entry, jira_key_from_ref
from et.tracker import TimerEntry, TrackerError, find_timer_for_workspace
from et.workspaces import WorkspaceError


class WsDeleteError(RuntimeError):
    """Raised when the active workspace cannot be deleted."""


@dataclass(frozen=True)
class WsDeleteResult:
    """Summary of what `delete_active_workspace` did."""

    workspace_index: int
    remaining_workspaces: int


def shift_workspaces_left(
    workspaces_list: list[WorkspaceConfigEntry], entries: list[TimerEntry], freed_index: int
) -> bool:
    """Shift every non-`static` slot after `freed_index` one slot to the left.

    `freed_index` (already reset to a bare "ET-<n>" entry by the caller) is
    filled with whatever was in the next non-static slot, that slot is
    filled with the one after it, and so on, leaving a single bare slot at
    the *end* of the non-static range instead of a gap in the middle. Each
    moved workspace's bound Tracker timer (if any) follows it — its
    `workspaceId`/`name` are updated in place — and a stale timer left
    behind at `freed_index` (e.g. the just-reset-to-zero timer of a task
    that was just completed, or an already-deleted workspace's leftover
    timer) is discarded rather than duplicated.

    Mutates `workspaces_list` and `entries` in place. Returns whether
    `entries` changed (so the caller knows whether to save it). No-op if
    `freed_index` isn't a non-static slot, or is already the last one.
    """
    non_static_slots = [i for i, entry in enumerate(workspaces_list) if entry.type != "static"]
    if freed_index not in non_static_slots:
        return False

    freed_position = non_static_slots.index(freed_index)
    timers_changed = False

    for position in range(freed_position, len(non_static_slots) - 1):
        dst = non_static_slots[position]
        src = non_static_slots[position + 1]

        source_entry = workspaces_list[src]
        if source_entry == default_entry(src, source_entry.type):
            # Bare placeholder slot (e.g. one padded in by `et ws delete` for
            # an implicit slot): moving it verbatim would carry its stale
            # "ET-<src+1>" name into `dst`, so give `dst` a fresh, correctly
            # numbered placeholder instead of copying the source's content.
            workspaces_list[dst] = default_entry(dst, workspaces_list[dst].type)
        else:
            workspaces_list[dst] = WorkspaceConfigEntry(
                name=source_entry.name,
                type=workspaces_list[dst].type,
                ref=source_entry.ref,
                description=source_entry.description,
            )
        workspaces_list[src] = default_entry(src, workspaces_list[src].type)

        existing_at_dst = find_timer_for_workspace(entries, dst)
        if existing_at_dst is not None:
            entries.remove(existing_at_dst)
            timers_changed = True

        moved_timer = find_timer_for_workspace(entries, src)
        if moved_timer is not None:
            moved_timer["workspaceId"] = dst
            moved_timer["name"] = f"ET-{dst + 1}"
            timers_changed = True

    return timers_changed


def _trim_trailing_default_entries(workspaces_list: list[WorkspaceConfigEntry]) -> None:
    """Drop trailing bare "ET-<n>" entries (mutating in place).

    A trailing bare, unlinked, dynamic entry is indistinguishable from a
    slot that was never listed in the config at all (every other command
    already treats a missing entry the same as a bare one), so trimming
    them back off keeps the saved config from growing forever just because
    it was padded out for a shift/delete.
    """
    while workspaces_list and workspaces_list[-1] == default_entry(
        len(workspaces_list) - 1, workspaces_list[-1].type
    ):
        workspaces_list.pop()


def delete_active_workspace(*, force: bool = False) -> WsDeleteResult:
    """Delete the active workspace's slot, shifting later ones left to fill the gap.

    Only works on a "free" workspace — non-`static`, and with no Jira `ref`
    linked (the same definition `et jira start` uses to find an empty
    slot to reuse). Raises `WsDeleteError` if the active workspace is
    `static`; use `et jira complete` (or `et jira log-time`) first to free
    a Jira-linked workspace, or pass `force=True` to delete it anyway
    (its Tracker timer, if any, is discarded rather than logged).

    Every non-static workspace after the active one (and its Tracker
    timer) is shifted one slot to the left, same as `et jira complete`, so
    the freed bare slot ends up at the end of the non-static range. That
    now-empty trailing slot is then removed: GNOME's workspace count
    (`num-workspaces`) is decremented by one. The one exception is when the
    highest-numbered workspace is `static` (so shrinking would swallow it) —
    then the count is left untouched and the freed slot simply becomes an
    empty "ET-<n>" workspace instead. Refuses to delete the last remaining
    workspace.

    Raises `ConfigError` if the config file is missing/malformed,
    `WorkspaceError` (unwrapped) if the active workspace can't be
    determined, and `WsDeleteError` if the checks above fail or the
    underlying GNOME/Tracker operations fail.
    """
    config: EtConfig = load_config()
    index = workspaces.get_active_workspace_index()

    try:
        count = workspaces.get_workspace_count()
    except WorkspaceError as exc:
        raise WsDeleteError(str(exc)) from exc

    if count <= 1:
        raise WsDeleteError("cannot delete the last remaining workspace")

    padded_len = max(len(config.workspaces), count, index + 1)
    workspaces_list = list(config.workspaces) + [
        default_entry(slot, "dynamic") for slot in range(len(config.workspaces), padded_len)
    ]

    entry = workspaces_list[index]
    if entry.type == "static":
        raise WsDeleteError(f"workspace {index + 1} is a static workspace and can't be deleted")
    if entry.ref is not None and not force:
        key = jira_key_from_ref(entry.ref) or entry.ref
        raise WsDeleteError(
            f"workspace {index + 1} is linked to {key}; complete or unlink it first "
            "(or pass --force to delete it anyway)"
        )

    try:
        entries = tracker.load_timers()
    except TrackerError as exc:
        raise WsDeleteError(str(exc)) from exc

    workspaces_list[index] = default_entry(index, entry.type)
    timers_changed = shift_workspaces_left(workspaces_list, entries, index)

    # Reclaim the freed slot by shrinking GNOME's workspace count, unless the
    # highest-numbered workspace is static (shrinking removes the last GNOME
    # workspace, so we mustn't when that's a static one we never touch).
    shrink = workspaces_list[count - 1].type != "static"
    new_count = count - 1 if shrink else count
    rename_source = workspaces_list[:new_count] if shrink else workspaces_list
    rename_names = [item.name for item in rename_source]

    saved_list = list(workspaces_list)
    _trim_trailing_default_entries(saved_list)

    try:
        if timers_changed:
            tracker.save_timers_with_reload(entries, "shifting timers after deleting a workspace")
        if shrink:
            workspaces.set_workspace_count(new_count)
        save_config(replace(config, workspaces=saved_list))
        # Rename after any shrink so the workspace-names array matches the
        # (possibly reduced) set of live GNOME workspaces.
        workspaces.rename_all_workspaces(rename_names)
        workspaces.switch_to_workspace(min(index, new_count - 1))
    except (ConfigError, WorkspaceError, TrackerError) as exc:
        raise WsDeleteError(str(exc)) from exc

    return WsDeleteResult(workspace_index=index, remaining_workspaces=new_count)


__all__ = [
    "WsDeleteError",
    "WsDeleteResult",
    "shift_workspaces_left",
    "delete_active_workspace",
]
