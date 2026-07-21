"""Command-line interface for et (effort tracker)."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

import typer

from et.config import ConfigError, get_max_workspaces, load_config, load_workspace_names
from et.jira_sync import JiraSyncError, jira_key_from_ref, preview_reshuffle, sync_jira_workspaces
from et.jira_time import JiraLogTimeError, log_time_for_current_workspace
from et.task import (
    TaskError,
    complete_task_for_current_workspace,
    create_task_from_jira,
    create_task_workspace,
)
from et.tracker import (
    TrackerError,
    add_tracker_for_current_workspace,
    add_trackers_for_all_workspaces,
    dump_all_trackers,
    dump_tracker_for_current_workspace,
    find_timer_for_workspace,
    format_duration,
    load_timers,
    reset_all_trackers,
    reset_tracker_for_current_workspace,
)
from et.workspaces import (
    WorkspaceError,
    get_active_workspace_index,
    rename_active_workspace,
    rename_all_workspaces,
)
from et.ws import WsDeleteError, delete_active_workspace

if TYPE_CHECKING:
    from et.config import EtConfig
    from et.jira import JiraIssue
    from et.task import TaskCreateResult


def _hyperlink(text: str, url: str) -> str:
    """Wrap `text` in an OSC 8 terminal hyperlink pointing to `url`.

    Falls back to plain `text` when stdout isn't a terminal (e.g. piped or
    redirected output), since the escape codes would otherwise be visible.
    """
    if not sys.stdout.isatty():
        return text
    return f"\x1b]8;;{url}\x1b\\{text}\x1b]8;;\x1b\\"


def _preview_workspace_actions(
    config: EtConfig | None, issues: list[JiraIssue]
) -> dict[str, str]:
    """Map each issue key to a human-readable note about its workspace change.

    Notes are 1-indexed to match the rest of the CLI's workspace numbering:
    "ws unchanged (N)", "ws move (OLD -> NEW)", "ws created (N)", and
    "no free workspace slot" for issues that won't fit. Returns an empty map
    when `config` is unavailable (so annotations are simply omitted).
    """
    if config is None:
        return {}

    outcome = preview_reshuffle(config, issues)
    actions: dict[str, str] = {}
    for slot, key in outcome.kept:
        actions[key] = f"ws unchanged ({slot + 1})"
    for slot, key in outcome.assigned:
        actions[key] = f"ws created ({slot + 1})"
    for key, old_slot, new_slot in outcome.moved:
        actions[key] = f"ws move ({old_slot + 1} -> {new_slot + 1})"
    for key in outcome.skipped:
        actions[key] = "no free workspace slot"
    return actions


app = typer.Typer(
    help="et: a small CLI for tracking effort and managing your workspace.",
    no_args_is_help=True,
)
ws_app = typer.Typer(help="Interact with GNOME/Ubuntu workspaces.", no_args_is_help=True)
app.add_typer(ws_app, name="ws")

tracker_app = typer.Typer(help="Manage Tracker extension timers.", no_args_is_help=True)
app.add_typer(tracker_app, name="tracker")
jira_app = typer.Typer(help="Sync GNOME workspaces with active Jira issues.", no_args_is_help=True)
app.add_typer(jira_app, name="jira")

task_app = typer.Typer(
    help="Convenience layer around ws/tracker/jira for a single task's lifecycle.",
    no_args_is_help=True,
)
app.add_typer(task_app, name="task")


@app.callback()
def _configure_logging() -> None:
    """Send library warnings (e.g. from et.jira) to stderr."""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")


@ws_app.command("rename")
def rename(
    new_name: str | None = typer.Argument(
        None, help="New name for the active workspace (omit when using --all)."
    ),
    all_workspaces: bool = typer.Option(
        False,
        "--all",
        help="Rename all workspaces using the 'workspaces' list from ~/.config/et/config.yaml.",
    ),
) -> None:
    """Rename the current (active) workspace to NEW_NAME, or all workspaces with --all."""
    if all_workspaces:
        if new_name is not None:
            typer.echo("Error: NEW_NAME must not be given together with --all", err=True)
            raise typer.Exit(code=1)

        try:
            names = load_workspace_names()
            indices = rename_all_workspaces(names)
        except (ConfigError, WorkspaceError) as error:
            typer.echo(f"Error: {error}", err=True)
            raise typer.Exit(code=1) from error

        for index, name in zip(indices, names, strict=True):
            typer.echo(f"Renamed workspace {index + 1} to '{name}'")
        return

    if new_name is None:
        typer.echo("Error: NEW_NAME is required unless --all is given", err=True)
        raise typer.Exit(code=1)

    try:
        index = rename_active_workspace(new_name)
    except WorkspaceError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo(f"Renamed workspace {index + 1} to '{new_name}'")


def _print_workspace_jira_info(index: int, config: EtConfig) -> None:
    """Print the Jira issue (description + link) linked to workspace `index`, if any."""
    entry = config.workspaces[index] if index < len(config.workspaces) else None
    key = jira_key_from_ref(entry.ref) if entry else None

    if entry is None or key is None:
        typer.echo("No Jira issue linked to this workspace.")
        return

    typer.echo(f"Workspace {index + 1}: {entry.name}")
    typer.echo(entry.description or "(no description)")

    base_url = config.jira.base_url.rstrip("/") if config.jira else ""
    if base_url:
        typer.echo(_hyperlink(f"jira:{key}", f"{base_url}/browse/{key}"))
    else:
        typer.echo(f"jira:{key}")


def _print_workspace_time_spent(index: int) -> None:
    """Print the elapsed time of the ET-<n> tracker bound to workspace `index`, if any."""
    entries = load_timers()
    timer = find_timer_for_workspace(entries, index)
    if timer is None:
        typer.echo("No tracker for this workspace.")
        return

    elapsed = timer.get("timeElapsed", 0)
    seconds = elapsed if isinstance(elapsed, (int, float)) else 0
    running = " (running)" if timer.get("running") else ""
    typer.echo(f"Time spent: {format_duration(seconds)}{running}")


@ws_app.command("info")
def info() -> None:
    """Show the Jira issue (description + link) linked to the active workspace, if any."""
    try:
        index = get_active_workspace_index()
        config = load_config()
    except (ConfigError, WorkspaceError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    _print_workspace_jira_info(index, config)


@ws_app.command("delete")
def ws_delete(
    force: bool = typer.Option(
        False,
        "--force",
        help="Delete even if the workspace is still linked to a Jira issue; its tracker is lost.",
    ),
) -> None:
    """Delete the active workspace, shifting later workspaces left to fill the gap.

    Only works when the active workspace is free (not `static`, and not
    linked to a Jira issue) — use `et task complete` (or `et jira
    log-time`) first if it's still tracking something, or pass `--force`
    to delete it anyway (its Tracker timer, if any, is discarded rather
    than logged). Every non-static workspace after it (and its Tracker
    timer) shifts one slot to the left, then the now-freed last slot is
    removed entirely: `max_workspaces` is decremented by 1 and GNOME's
    actual workspace count shrinks to match.
    """
    try:
        result = delete_active_workspace(force=force)
    except (ConfigError, WorkspaceError, WsDeleteError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo(f"Deleted workspace {result.workspace_index + 1}")
    typer.echo(f"Now managing {result.remaining_workspaces} workspaces")


@tracker_app.command("add")
def tracker_add(
    all_workspaces: bool = typer.Option(
        False,
        "--all",
        help="Configure 10 static workspaces and create a timer for each.",
    ),
) -> None:
    """Add a Tracker timer bound to the current (active) workspace, if none exists yet.

    The new timer auto-starts whenever its workspace is active (and
    auto-pauses otherwise), same as a manually-started workspace-bound timer.
    """
    if all_workspaces:
        try:
            results = add_trackers_for_all_workspaces(count=get_max_workspaces())
        except (WorkspaceError, TrackerError) as error:
            typer.echo(f"Error: {error}", err=True)
            raise typer.Exit(code=1) from error

        for index, name, created in results:
            verb = "Added" if created else "Already exists:"
            typer.echo(f"{verb} tracker '{name}' for workspace {index + 1}")
        return

    try:
        index, name, created = add_tracker_for_current_workspace()
    except (WorkspaceError, TrackerError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    if created:
        typer.echo(f"Added tracker '{name}' for workspace {index + 1}")
    else:
        typer.echo(f"Tracker '{name}' already exists for workspace {index + 1}")


@tracker_app.command("reset")
def tracker_reset(
    all_workspaces: bool = typer.Option(
        False,
        "--all",
        help="Reset every ET-<n> tracker's elapsed time to 0 and stop it.",
    ),
) -> None:
    """Reset ET-<n> trackers to 0 elapsed time.

    Without --all, resets only the ET-<n> timer bound to the active workspace.
    """
    if not all_workspaces:
        try:
            index, name = reset_tracker_for_current_workspace()
        except (WorkspaceError, TrackerError) as error:
            typer.echo(f"Error: {error}", err=True)
            raise typer.Exit(code=1) from error

        if name is None:
            typer.echo(f"No ET-<n> tracker bound to workspace {index + 1} to reset")
        else:
            typer.echo(f"Reset tracker '{name}' to 0")
        return

    try:
        reset_names = reset_all_trackers()
    except TrackerError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    if not reset_names:
        typer.echo("No ET-<n> trackers found to reset")
        return

    for name in reset_names:
        typer.echo(f"Reset tracker '{name}' to 0")


@tracker_app.command("dump")
def tracker_dump(
    all_workspaces: bool = typer.Option(
        False,
        "--all",
        help="Dump every ET-<n> tracker's elapsed time to ~/timers/<yyyy-mm-dd>/.",
    ),
) -> None:
    """Save each ET-<n> tracker's elapsed time to a file under ~/timers/<yyyy-mm-dd>/.

    Each tracker's elapsed time is also printed to stdout in a human-readable
    form (e.g. "ET-1: 2h 15m 30s"). Without --all, dumps only the ET-<n> timer
    bound to the active workspace.
    """
    if not all_workspaces:
        try:
            index, path, duration = dump_tracker_for_current_workspace()
        except (WorkspaceError, TrackerError) as error:
            typer.echo(f"Error: {error}", err=True)
            raise typer.Exit(code=1) from error

        if path is None:
            typer.echo(f"No ET-<n> tracker bound to workspace {index + 1} to dump")
        else:
            typer.echo(f"{path.stem}: {duration}")
            typer.echo(f"Wrote {path}")
        return

    try:
        written = dump_all_trackers()
    except TrackerError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    if not written:
        typer.echo("No ET-<n> trackers found to dump")
        return

    for name, path, duration in written:
        typer.echo(f"{name}: {duration}")
        typer.echo(f"Wrote {path}")


@jira_app.command("get")
def jira_get(
    no_prompt: bool = typer.Option(
        False,
        "--no-prompt",
        help="Skip the issue-list confirmation and auto-confirm workspace deletions.",
    ),
) -> None:
    """Fetch active Jira issues and sync GNOME workspaces to match.

    Renames/describes non-static workspaces after your active issues
    (highest priority first), moving Tracker timers along with each issue
    when its slot changes. Workspaces whose tracked issue is no longer
    active are reset back to a plain ET-<n> slot after confirmation (their
    timer is first dumped to ~/timers/by-id/jira-<KEY>.txt and reset).
    """

    def confirm_plan(issues: list[JiraIssue]) -> bool:
        if no_prompt:
            return True
        if not issues:
            typer.echo("No active Jira issues found.")
            return typer.confirm("Proceed anyway (clears any previously tracked issues)?")

        try:
            config = load_config()
        except ConfigError:
            config = None
        base_url = config.jira.base_url.rstrip("/") if config and config.jira else ""

        workspace_actions = _preview_workspace_actions(config, issues)

        typer.echo("Active issues, highest priority first:")
        for issue in issues:
            key_display = (
                _hyperlink(issue.key, f"{base_url}/browse/{issue.key}") if base_url else issue.key
            )
            action = workspace_actions.get(issue.key)
            suffix = f"  ({action})" if action else ""
            typer.echo(f"  {key_display} [{issue.priority}] {issue.summary}{suffix}")
        return typer.confirm("Proceed with syncing these onto your workspaces?")

    def confirm_delete(slot: int, name: str, key: str) -> bool:
        if no_prompt:
            return True
        return typer.confirm(
            f"Workspace {slot + 1} ('{name}', tracking {key}) is no longer active. Delete it?"
        )

    try:
        result = sync_jira_workspaces(confirm_plan=confirm_plan, confirm_delete=confirm_delete)
    except (ConfigError, JiraSyncError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    for slot, key in result.assigned:
        typer.echo(f"Assigned workspace {slot + 1} to jira:{key}")
    for key, old_slot, new_slot in result.moved:
        typer.echo(f"Moved jira:{key} from workspace {old_slot + 1} to {new_slot + 1}")
    for slot, key in result.kept:
        typer.echo(f"Kept workspace {slot + 1} on jira:{key}")
    for slot, key in result.deleted:
        typer.echo(f"Deleted workspace {slot + 1} (jira:{key} no longer active)")
    for key in result.skipped:
        typer.echo(f"Skipped jira:{key} (no free workspace slots)", err=True)

    if not (
        result.assigned or result.moved or result.kept or result.deleted or result.skipped
    ):
        typer.echo("Nothing to do — workspaces already match your active issues.")


@jira_app.command("log-time")
def jira_log_time(
    comment: str | None = typer.Option(
        None, "--comment", "-m", help="Worklog description/comment to attach in Jira."
    ),
    no_reset: bool = typer.Option(
        False,
        "--no-reset",
        help="Don't reset the tracker after logging (leaves its elapsed time as-is).",
    ),
) -> None:
    """Log the active workspace's tracked time to its Jira issue.

    Reads the elapsed time from the ET-<n> Tracker timer bound to the active
    workspace, resolves the Jira issue linked to that workspace (see `et
    jira get`/`et ws info`), and logs it as a Jira worklog for that issue
    (via Jira's own worklog API, which also shows up in Tempo timesheets
    when Tempo is configured to sync native Jira worklogs). The tracker is
    reset to 0 afterwards, unless --no-reset is given.
    """
    try:
        result = log_time_for_current_workspace(description=comment, reset=not no_reset)
    except (ConfigError, WorkspaceError, JiraLogTimeError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    duration = format_duration(result.seconds_logged)
    typer.echo(
        f"Logged {duration} to jira:{result.issue_key} (workspace {result.workspace_index + 1})"
    )
    if result.tracker_reset:
        typer.echo("Reset tracker to 0")


def _print_task_created(result: TaskCreateResult) -> None:
    key = jira_key_from_ref(result.ref)
    ref_suffix = f" (linked to {key})" if key else ""
    typer.echo(f"Created workspace {result.workspace_index + 1}: '{result.name}'{ref_suffix}")
    if not result.timer_created:
        typer.echo(f"Tracker already existed for workspace {result.workspace_index + 1}")
    typer.echo(f"Switched to workspace {result.workspace_index + 1}")


@task_app.command("info")
def task_info() -> None:
    """Show the Jira issue and tracked time for the active task's workspace.

    Same as `et ws info`, plus the elapsed time of the ET-<n> Tracker timer
    bound to the active workspace (if any).
    """
    try:
        index = get_active_workspace_index()
        config = load_config()
    except (ConfigError, WorkspaceError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    _print_workspace_jira_info(index, config)

    try:
        _print_workspace_time_spent(index)
    except TrackerError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error


@task_app.command("create")
def task_create(
    name: str | None = typer.Argument(
        None, help="Name for the new task's workspace (--manual only; prompted for if omitted)."
    ),
    description: str | None = typer.Option(
        None, "--description", "-d", help="Optional description for the new task (--manual only)."
    ),
    manual: bool = typer.Option(
        False, "--manual", help="Name the task yourself instead of picking a Jira issue."
    ),
    from_jira: bool = typer.Option(
        False,
        "--from-jira",
        help="Pick an active Jira issue to create the task from (the default).",
    ),
) -> None:
    """Create a new task: allocate a workspace slot, its Tracker timer, and switch to it.

    Growing the workspace pool never fails for lack of room: `et task
    create` bumps `max_workspaces` itself if every existing slot is
    already taken.

    By default (or with --from-jira), lists your active Jira issues that
    aren't already linked to a workspace and lets you pick one (its
    summary becomes the workspace name/description and its key is
    linked, same as `et jira get`). With --manual, prompts interactively
    for a name/description when not given as arguments/options instead.
    """
    if manual and from_jira:
        typer.echo("Error: --manual and --from-jira are mutually exclusive", err=True)
        raise typer.Exit(code=1)

    if not manual and (name is not None or description is not None):
        typer.echo("Error: NAME/--description require --manual", err=True)
        raise typer.Exit(code=1)

    if not manual:
        try:
            config = load_config()
        except ConfigError:
            config = None
        base_url = config.jira.base_url.rstrip("/") if config and config.jira else ""

        def select_issue(issues: list[JiraIssue]) -> JiraIssue | None:
            if not issues:
                typer.echo("No active Jira issues available to create a task from.")
                return None

            typer.echo("Active issues not yet linked to a workspace:")
            for position, issue in enumerate(issues, start=1):
                key_display = (
                    _hyperlink(issue.key, f"{base_url}/browse/{issue.key}")
                    if base_url
                    else issue.key
                )
                typer.echo(f"  {position}. {key_display} [{issue.priority}] {issue.summary}")

            choice = typer.prompt("Pick an issue number (or 0 to cancel)", default="0")
            try:
                selected = int(choice)
            except ValueError:
                selected = 0
            if selected < 1 or selected > len(issues):
                return None
            return issues[selected - 1]

        try:
            from_jira_result = create_task_from_jira(select_issue)
        except (ConfigError, TaskError) as error:
            typer.echo(f"Error: {error}", err=True)
            raise typer.Exit(code=1) from error

        if from_jira_result is None:
            typer.echo("Cancelled.")
            return

        _print_task_created(from_jira_result)
        return

    if name is None:
        name = typer.prompt("Task name")
    if description is None:
        description = (
            typer.prompt("Description (optional)", default="", show_default=False) or None
        )

    try:
        result = create_task_workspace(name, description=description)
    except (ConfigError, TaskError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    _print_task_created(result)



@task_app.command("log-time")
def task_log_time(
    comment: str | None = typer.Option(
        None, "--comment", "-m", help="Worklog description/comment to attach in Jira."
    ),
    no_reset: bool = typer.Option(
        False,
        "--no-reset",
        help="Don't reset the tracker after logging (leaves its elapsed time as-is).",
    ),
) -> None:
    """Log the active task's tracked time to its Jira issue (same as `et jira log-time`)."""
    jira_log_time(comment=comment, no_reset=no_reset)


@task_app.command("complete")
def task_complete(
    comment: str | None = typer.Option(
        None, "--comment", "-m", help="Worklog description/comment to attach in Jira."
    ),
) -> None:
    """Log the active task's tracked time to Jira, then free its workspace slot.

    Equivalent to `et jira log-time` followed by resetting the workspace's
    config entry back to a bare ET-<n> slot (clearing its name/ref/
    description), freeing it for a future `et task create`. No confirmation
    prompt.
    """
    try:
        result = complete_task_for_current_workspace(comment=comment)
    except (ConfigError, WorkspaceError, JiraLogTimeError, TaskError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    log_result = result.log_result
    duration = format_duration(log_result.seconds_logged)
    typer.echo(
        f"Logged {duration} to jira:{log_result.issue_key} "
        f"(workspace {log_result.workspace_index + 1})"
    )
    typer.echo(f"Freed workspace {log_result.workspace_index + 1}")
