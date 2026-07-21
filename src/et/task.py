"""Orchestrates the `et task` command group: a friendlier, task-centric
layer on top of `et ws`/`et tracker`/`et jira` (which are unaffected and
still work standalone).

`et task create` allocates a free workspace slot (growing the configured
list up to `max_workspaces` if needed, mirroring `et jira get`'s slot
logic), creates its Tracker timer, and switches GNOME to it — optionally
picking the slot's name/description/Jira link from the user's active Jira
issues (`--from-jira`). `et task complete` logs the active workspace's
tracked time to Jira (reusing `et.jira_time.log_time_for_current_workspace`),
resets that workspace back to a bare "ET-<n>" slot, and shifts every
non-`static` slot after it one slot to the left (moving each one's
Tracker timer along with it) so the freed slot ends up at the end of the
non-static range rather than leaving a gap in the middle. Has no Typer/CLI
dependency.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from et import tracker, workspaces
from et.config import ConfigError, EtConfig, WorkspaceConfigEntry, load_config, save_config
from et.jira import JiraError, JiraIssue, fetch_active_issues
from et.jira_sync import JIRA_REF_PREFIX, default_entry, jira_key_from_ref, truncate_summary
from et.jira_time import LogTimeResult, log_time_for_current_workspace
from et.tracker import TimerEntry, TrackerError, find_timer_for_workspace
from et.workspaces import WorkspaceError


class TaskError(RuntimeError):
    """Raised when a task cannot be created or completed."""


@dataclass(frozen=True)
class TaskCreateResult:
    """Summary of what `create_task_workspace` did."""

    workspace_index: int
    name: str
    ref: str | None
    timer_created: bool


@dataclass(frozen=True)
class TaskCompleteResult:
    """Summary of what `complete_task_for_current_workspace` did."""

    log_result: LogTimeResult


def _find_free_slot(workspaces_list: list[WorkspaceConfigEntry]) -> int | None:
    """Return the index of the first non-static, ref-less slot, if any."""
    for index, entry in enumerate(workspaces_list):
        if entry.type != "static" and entry.ref is None:
            return index
    return None


def _shift_workspaces_left(
    workspaces_list: list[WorkspaceConfigEntry], entries: list[TimerEntry], freed_index: int
) -> bool:
    """Shift every non-`static` slot after `freed_index` one slot to the left.

    `freed_index` (already reset to a bare "ET-<n>" entry by the caller) is
    filled with whatever was in the next non-static slot, that slot is
    filled with the one after it, and so on, leaving a single bare slot at
    the *end* of the non-static range instead of a gap in the middle. Each
    moved workspace's bound Tracker timer (if any) follows it — its
    `workspaceId`/`name` are updated in place — and a stale timer left
    behind at `freed_index` (e.g. the just-reset-to-zero timer of the task
    that was just completed) is discarded rather than duplicated.

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


def create_task_workspace(
    name: str, description: str | None = None, ref: str | None = None
) -> TaskCreateResult:
    """Allocate a free workspace slot for a new task, name it, and switch to it.

    Picks the first non-`static` slot with no `ref` (as `et jira get`
    does), growing the configured workspace list (up to
    `config.max_workspaces`) if none is free. Saves the updated config,
    creates the slot's Tracker timer, renames the GNOME workspaces to
    match, and switches the active GNOME workspace to the new slot.

    Raises `ConfigError` if the config file is missing/malformed,
    `TaskError` if there's no free slot and `max_workspaces` has already
    been reached, and `WorkspaceError`/`TrackerError` if the underlying
    GNOME/Tracker operations fail.
    """
    config: EtConfig = load_config()
    workspaces_list = list(config.workspaces)

    slot = _find_free_slot(workspaces_list)
    while slot is None and len(workspaces_list) < config.max_workspaces:
        workspaces_list.append(default_entry(len(workspaces_list), "dynamic"))
        slot = _find_free_slot(workspaces_list)

    if slot is None:
        raise TaskError(
            f"no free workspace slot available (max_workspaces={config.max_workspaces} reached)"
        )

    workspaces_list[slot] = WorkspaceConfigEntry(
        name=name,
        type=workspaces_list[slot].type,
        ref=ref,
        description=description,
    )

    try:
        workspaces.configure_static_workspace_count(
            max(len(workspaces_list), config.max_workspaces)
        )
        timer_created = tracker.add_tracker_for_workspace(slot)[1]
        save_config(replace(config, workspaces=workspaces_list))
        workspaces.rename_all_workspaces([entry.name for entry in workspaces_list])
        workspaces.switch_to_workspace(slot)
    except (WorkspaceError, TrackerError, ConfigError) as exc:
        raise TaskError(str(exc)) from exc

    return TaskCreateResult(workspace_index=slot, name=name, ref=ref, timer_created=timer_created)


def create_task_from_jira(
    select_issue: Callable[[list[JiraIssue]], JiraIssue | None],
) -> TaskCreateResult | None:
    """Create a task workspace from one of the user's active Jira issues.

    Fetches active Jira issues, filters out any already linked to an
    existing workspace, then calls `select_issue(candidates)` with the
    remaining ones — `select_issue` is responsible for presenting the
    choice to the user (and returns `None` to cancel, in which case this
    returns `None` without changing anything).

    Raises `ConfigError` if the config file is missing/malformed,
    `TaskError` if there's no `jira` config block, and `JiraError` (via
    `TaskError`) if the Jira API call fails.
    """
    config: EtConfig = load_config()
    if config.jira is None:
        raise TaskError(
            "no 'jira' block found in the config file "
            "(add base_url/email/pat/jql under a top-level 'jira:' key)"
        )

    try:
        issues = fetch_active_issues(config.jira)
    except JiraError as exc:
        raise TaskError(str(exc)) from exc

    tracked_keys = {
        jira_key_from_ref(entry.ref)
        for entry in config.workspaces
        if jira_key_from_ref(entry.ref) is not None
    }
    candidates = [issue for issue in issues if issue.key not in tracked_keys]

    issue = select_issue(candidates)
    if issue is None:
        return None

    return create_task_workspace(
        name=truncate_summary(issue.summary),
        description=issue.summary,
        ref=f"{JIRA_REF_PREFIX}{issue.key}",
    )


def complete_task_for_current_workspace(comment: str | None = None) -> TaskCompleteResult:
    """Log the active workspace's tracked time to Jira, then free its slot.

    Delegates the logging step to
    `et.jira_time.log_time_for_current_workspace` (which already resets the
    tracker on success); if that succeeds, the workspace's config entry is
    reset to a bare "ET-<n>" slot (clearing its name/ref/description), and
    every non-`static` slot after it is shifted one slot to the left (with
    its Tracker timer) to close the gap, leaving the newly bare slot at the
    end of the non-static range. GNOME workspace names are renamed to
    match.

    Raises `ConfigError`, `WorkspaceError`, or `JiraLogTimeError` (all
    propagated unchanged from `log_time_for_current_workspace`) if logging
    fails — in which case the workspace is left untouched.
    """
    log_result = log_time_for_current_workspace(description=comment, reset=True)

    config: EtConfig = load_config()
    workspaces_list = list(config.workspaces)
    index = log_result.workspace_index
    if index < len(workspaces_list):
        workspaces_list[index] = default_entry(index, workspaces_list[index].type)

    try:
        entries = tracker.load_timers()
        timers_changed = _shift_workspaces_left(workspaces_list, entries, index)
        if timers_changed:
            tracker.save_timers_with_reload(entries, "shifting timers after completing a task")
        save_config(replace(config, workspaces=workspaces_list))
        workspaces.rename_all_workspaces([entry.name for entry in workspaces_list])
    except (ConfigError, WorkspaceError, TrackerError) as exc:
        raise TaskError(str(exc)) from exc

    return TaskCompleteResult(log_result=log_result)


__all__ = [
    "TaskError",
    "TaskCreateResult",
    "TaskCompleteResult",
    "create_task_workspace",
    "create_task_from_jira",
    "complete_task_for_current_workspace",
]
