"""Orchestrates the `et task` command group: a friendlier, task-centric
layer on top of `et ws`/`et tracker`/`et jira` (which are unaffected and
still work standalone).

`et task create` allocates a free workspace slot (growing the configured
list, and bumping `max_workspaces` itself if the current cap is already
full, mirroring `et jira get`'s slot logic), creates its Tracker timer,
and switches GNOME to it — by default picking the slot's
name/description/Jira link from the user's active Jira issues
(`--from-jira`, the default; pass `--manual` to name it yourself
instead). `et task complete` logs the active workspace's tracked time to
Jira (reusing `et.jira_time.log_time_for_current_workspace`), resets that
workspace back to a bare "ET-<n>" slot, and shifts every non-`static`
slot after it one slot to the left (moving each one's Tracker timer along
with it) so the freed slot ends up at the end of the non-static range
rather than leaving a gap in the middle. Has no Typer/CLI dependency.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from et import tracker, workspaces
from et.config import ConfigError, EtConfig, WorkspaceConfigEntry, load_config, save_config
from et.jira import JiraError, JiraIssue, fetch_active_issues
from et.jira_sync import JIRA_REF_PREFIX, default_entry, jira_key_from_ref, truncate_summary
from et.jira_time import LogTimeResult, log_time_for_current_workspace
from et.tracker import TrackerError
from et.workspaces import WorkspaceError
from et.ws import shift_workspaces_left


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


def create_task_workspace(
    name: str, description: str | None = None, ref: str | None = None
) -> TaskCreateResult:
    """Allocate a free workspace slot for a new task, name it, and switch to it.

    Picks the first non-`static` slot with no `ref` (as `et jira get`
    does), growing the configured workspace list if none is free —
    including bumping `max_workspaces` itself when the current cap has
    already been reached, so `et task create` never fails for lack of
    room. Saves the updated config, creates the slot's Tracker timer,
    renames the GNOME workspaces to match, and switches the active GNOME
    workspace to the new slot.

    Raises `ConfigError` if the config file is missing/malformed, and
    `WorkspaceError`/`TrackerError` (via `TaskError`) if the underlying
    GNOME/Tracker operations fail.
    """
    config: EtConfig = load_config()
    workspaces_list = list(config.workspaces)

    slot = _find_free_slot(workspaces_list)
    while slot is None and len(workspaces_list) < config.max_workspaces:
        workspaces_list.append(default_entry(len(workspaces_list), "dynamic"))
        slot = _find_free_slot(workspaces_list)

    if slot is None:
        # Every configured slot up to max_workspaces is taken (static, or
        # already linked to a task): grow the cap by one bare slot rather
        # than failing.
        slot = len(workspaces_list)
        workspaces_list.append(default_entry(slot, "dynamic"))

    new_max_workspaces = max(config.max_workspaces, len(workspaces_list))

    workspaces_list[slot] = WorkspaceConfigEntry(
        name=name,
        type=workspaces_list[slot].type,
        ref=ref,
        description=description,
    )

    try:
        workspaces.configure_static_workspace_count(
            max(len(workspaces_list), new_max_workspaces)
        )
        timer_created = tracker.add_tracker_for_workspace(slot)[1]
        save_config(
            replace(config, max_workspaces=new_max_workspaces, workspaces=workspaces_list)
        )
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
        timers_changed = shift_workspaces_left(workspaces_list, entries, index)
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
