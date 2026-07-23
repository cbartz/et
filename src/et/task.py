"""Orchestrates the `et jira` command group: a friendlier, task-centric
layer on top of the Tracker and Jira integrations (`et.tracker`,
`et.jira`, `et.jira_ref`, `et.jira_time`), plus `et ws`.

`et jira start` allocates a free workspace slot among GNOME's fixed set of
workspaces (prompting, via an injected `confirm_grow` callback, to add one
more workspace when they're all taken), creates its Tracker timer, and
switches GNOME to it — moving the terminal window it's run from along with
it — picking the slot's name/description/Jira link from the user's active
Jira issues (and offering to move the selected issue to "In Progress" if it
isn't already). `et jira complete` logs the active workspace's tracked time
to Jira (reusing `et.jira_time.log_time_for_current_workspace`), resets that
workspace back to a bare "ET-<n>" slot, and shifts every non-`static`
slot after it one slot to the left (moving each one's Tracker timer along
with it) so the freed slot ends up at the end of the non-static range
rather than leaving a gap in the middle. Has no Typer/CLI dependency.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from et import tracker, workspaces
from et.config import (
    ConfigError,
    EtConfig,
    JiraConfig,
    WorkspaceConfigEntry,
    load_config,
    save_config,
)
from et.jira import (
    JiraError,
    JiraIssue,
    fetch_active_issues,
    fetch_transitions,
    transition_issue,
)
from et.jira_ref import JIRA_REF_PREFIX, default_entry, jira_key_from_ref, truncate_summary
from et.jira_time import LogTimeResult, log_time_for_current_workspace
from et.tracker import TrackerError
from et.workspaces import WorkspaceError
from et.ws import shift_workspaces_left

IN_PROGRESS_STATUS = "in progress"


class TaskError(RuntimeError):
    """Raised when a task cannot be created or completed."""


@dataclass(frozen=True)
class TaskCreateResult:
    """Summary of what `create_task_workspace` did."""

    workspace_index: int
    name: str
    ref: str | None
    timer_created: bool
    window_moved: bool


@dataclass(frozen=True)
class TaskCompleteResult:
    """Summary of what `complete_task_for_current_workspace` did."""

    log_result: LogTimeResult


def _find_free_slot(workspaces_list: list[WorkspaceConfigEntry], count: int) -> int | None:
    """Return the first free (non-static, ref-less) slot within `count` workspaces.

    Only considers slots `0..count-1` (the workspaces GNOME actually has).
    Slots past the end of `workspaces_list` but still within `count` count as
    free bare slots. Returns None when all `count` workspaces are taken.
    """
    for index, entry in enumerate(workspaces_list[:count]):
        if entry.type != "static" and entry.ref is None:
            return index
    if len(workspaces_list) < count:
        return len(workspaces_list)
    return None


def create_task_workspace(
    name: str,
    description: str | None = None,
    ref: str | None = None,
    *,
    confirm_grow: Callable[[int], bool] = lambda count: False,
) -> TaskCreateResult | None:
    """Allocate a free workspace slot for a new task, name it, and switch to it.

    Picks the first non-`static`, unlinked slot among the fixed set of GNOME
    workspaces (`num-workspaces`). If every workspace is already taken, calls
    `confirm_grow(count)`; when it returns True the workspace count is bumped
    by one (via `set_workspace_count`) and the new slot is used, otherwise
    this returns `None` without changing anything. Saves the updated config,
    creates the slot's Tracker timer, renames the GNOME workspaces to match,
    switches the active GNOME workspace to the new slot, and best-effort moves
    the currently focused window (typically the terminal the command was run
    from) there too — this last step is skipped without failing the whole
    command if unsupported (e.g. for native Wayland clients, which have no
    addressable X11 window for `wmctrl` to move); `TaskCreateResult.window_moved`
    reports whether it worked.

    Raises `ConfigError` if the config file is missing/malformed, and
    `WorkspaceError`/`TrackerError` (via `TaskError`) if the underlying
    GNOME/Tracker operations fail.
    """
    config: EtConfig = load_config()

    try:
        count = workspaces.get_workspace_count()
    except WorkspaceError as exc:
        raise TaskError(str(exc)) from exc

    slot = _find_free_slot(config.workspaces, count)
    grew = False
    if slot is None:
        if not confirm_grow(count):
            return None
        slot = count
        grew = True

    workspaces_list = list(config.workspaces)
    while len(workspaces_list) <= slot:
        workspaces_list.append(default_entry(len(workspaces_list), "dynamic"))

    workspaces_list[slot] = WorkspaceConfigEntry(
        name=name,
        type=workspaces_list[slot].type,
        ref=ref,
        description=description,
    )

    try:
        if grew:
            workspaces.set_workspace_count(count + 1)
        timer_created = tracker.prepare_timer_for_workspace(
            slot, count + 1 if grew else count
        )[1]
        save_config(replace(config, workspaces=workspaces_list))
        workspaces.rename_all_workspaces([entry.name for entry in workspaces_list])
        workspaces.switch_to_workspace(slot)
    except (WorkspaceError, TrackerError, ConfigError) as exc:
        raise TaskError(str(exc)) from exc

    # Best-effort: moving the focused window across workspaces requires an
    # addressable X11 window, which native Wayland clients (e.g. many
    # terminal emulators under GNOME/Wayland) don't have. Don't fail task
    # creation — which has already fully succeeded at this point — just
    # because this cosmetic step isn't supported in the current session.
    try:
        workspaces.move_active_window_to_workspace(slot)
        window_moved = True
    except WorkspaceError:
        window_moved = False

    return TaskCreateResult(
        workspace_index=slot,
        name=name,
        ref=ref,
        timer_created=timer_created,
        window_moved=window_moved,
    )



def _transition_to_in_progress(jira_config: JiraConfig, issue_key: str) -> None:
    """Move `issue_key` to its "In Progress" transition, if one is available.

    Raises `TaskError` if no such transition exists, or (via `JiraError`)
    if the Jira API call fails.
    """
    try:
        transitions = fetch_transitions(jira_config, issue_key)
    except JiraError as exc:
        raise TaskError(str(exc)) from exc

    target = next(
        (t for t in transitions if t.to_status.strip().lower() == IN_PROGRESS_STATUS), None
    )
    if target is None:
        raise TaskError(f"no transition to 'In Progress' available for {issue_key}")

    try:
        transition_issue(jira_config, issue_key, target.id)
    except JiraError as exc:
        raise TaskError(str(exc)) from exc


def create_task_from_jira(
    select_issue: Callable[[list[JiraIssue]], JiraIssue | None],
    confirm_transition: Callable[[JiraIssue], bool] | None = None,
    confirm_grow: Callable[[int], bool] = lambda count: False,
) -> TaskCreateResult | None:
    """Create a task workspace from one of the user's active Jira issues.

    Fetches active Jira issues, filters out any already linked to an
    existing workspace, then calls `select_issue(candidates)` with the
    remaining ones — `select_issue` is responsible for presenting the
    choice to the user (and returns `None` to cancel, in which case this
    returns `None` without changing anything).

    If the selected issue isn't already "In Progress" and `confirm_transition`
    is given, calls `confirm_transition(issue)` — if it returns `True`, the
    issue is moved to its "In Progress" transition before the workspace is
    created.

    `confirm_grow(count)` is forwarded to `create_task_workspace`: it's
    called only when all `count` workspaces are taken, to ask whether to add
    one more (returning `None` here if declined).

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

    if (
        issue.status.strip().lower() != IN_PROGRESS_STATUS
        and confirm_transition is not None
        and confirm_transition(issue)
    ):
        _transition_to_in_progress(config.jira, issue.key)

    return create_task_workspace(
        name=truncate_summary(issue.summary),
        description=issue.summary,
        ref=f"{JIRA_REF_PREFIX}{issue.key}",
        confirm_grow=confirm_grow,
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
