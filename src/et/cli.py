"""Command-line interface for et (effort tracker)."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

import typer

from et.config import ConfigError, load_config, load_workspace_names
from et.jira_sync import jira_key_from_ref
from et.jira_time import JiraLogTimeError, log_time_for_current_workspace
from et.task import (
    TaskError,
    complete_task_for_current_workspace,
    create_task_from_jira,
)
from et.tracker import TrackerError, find_timer_for_workspace, format_duration, load_timers
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


app = typer.Typer(
    help="et: a small CLI for tracking effort and managing your workspace.",
    no_args_is_help=False,
)
ws_app = typer.Typer(help="Interact with GNOME/Ubuntu workspaces.", no_args_is_help=True)
app.add_typer(ws_app, name="ws")

jira_app = typer.Typer(
    help="Convenience layer around ws (plus the Tracker/Jira integrations) for a single "
    "task's lifecycle.",
    no_args_is_help=True,
)
app.add_typer(jira_app, name="jira")


@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context) -> None:
    """Send library warnings (e.g. from et.jira) to stderr.

    With no subcommand, shows the same info as `et jira log-time` would
    act on (the Jira issue and tracked time for the active workspace) when
    that workspace is part of the managed (non-`static`) pool; otherwise
    falls back to the regular help text.
    """
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    if ctx.invoked_subcommand is not None:
        return

    try:
        index = get_active_workspace_index()
        config = load_config()
    except (ConfigError, WorkspaceError):
        typer.echo(ctx.get_help())
        raise typer.Exit() from None

    entry = config.workspaces[index] if index < len(config.workspaces) else None
    if entry is None or entry.type == "static":
        typer.echo(ctx.get_help())
        raise typer.Exit()

    _print_workspace_jira_info(index, config)
    try:
        _print_workspace_time_spent(index)
    except TrackerError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error


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
    linked to a Jira issue) — use `et jira complete` (or `et jira
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


def _print_task_created(result: TaskCreateResult) -> None:
    key = jira_key_from_ref(result.ref)
    ref_suffix = f" (linked to {key})" if key else ""
    typer.echo(f"Created workspace {result.workspace_index + 1}: '{result.name}'{ref_suffix}")
    if not result.timer_created:
        typer.echo(f"Tracker already existed for workspace {result.workspace_index + 1}")
    typer.echo(f"Switched to workspace {result.workspace_index + 1}")


@jira_app.command("start")
def jira_start() -> None:
    """Start a new task: allocate a workspace slot, its Tracker timer, and switch to it.

    Lists your active Jira issues that aren't already linked to a
    workspace and lets you pick one (its summary becomes the workspace
    name/description and its key is linked). If the selected issue isn't
    already "In Progress", offers to move it there. Growing the workspace
    pool never fails for lack of room: `et jira start` bumps
    `max_workspaces` itself if every existing slot is already taken.
    """
    try:
        config = load_config()
    except ConfigError:
        config = None
    base_url = config.jira.base_url.rstrip("/") if config and config.jira else ""

    def select_issue(issues: list[JiraIssue]) -> JiraIssue | None:
        if not issues:
            typer.echo("No active Jira issues available to start a task from.")
            return None

        typer.echo("Active issues not yet linked to a workspace:")
        for position, issue in enumerate(issues, start=1):
            key_display = (
                _hyperlink(issue.key, f"{base_url}/browse/{issue.key}")
                if base_url
                else issue.key
            )
            status_display = f" ({issue.status})" if issue.status else ""
            typer.echo(
                f"  {position}. {key_display} [{issue.priority}]{status_display} {issue.summary}"
            )

        choice = typer.prompt("Pick an issue number (or 0 to cancel)", default="0")
        try:
            selected = int(choice)
        except ValueError:
            selected = 0
        if selected < 1 or selected > len(issues):
            return None
        return issues[selected - 1]

    def confirm_transition(issue: JiraIssue) -> bool:
        status_display = issue.status or "an unknown state"
        return typer.confirm(
            f"{issue.key} is currently '{status_display}'. Move it to 'In Progress'?"
        )

    try:
        result = create_task_from_jira(select_issue, confirm_transition)
    except (ConfigError, TaskError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    if result is None:
        typer.echo("Cancelled.")
        return

    _print_task_created(result)


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
    """Log the active task's tracked time to its Jira issue.

    Reads the elapsed time from the ET-<n> Tracker timer bound to the
    active workspace, resolves the Jira issue linked to that workspace,
    and logs it as a Jira worklog for that issue (via Jira's own worklog
    API, which also shows up in Tempo timesheets when Tempo is configured
    to sync native Jira worklogs). The tracker is reset to 0 afterwards,
    unless --no-reset is given.
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


@jira_app.command("complete")
def jira_complete(
    comment: str | None = typer.Option(
        None, "--comment", "-m", help="Worklog description/comment to attach in Jira."
    ),
) -> None:
    """Log the active task's tracked time to Jira, then free its workspace slot.

    Equivalent to `et jira log-time` followed by resetting the workspace's
    config entry back to a bare ET-<n> slot (clearing its name/ref/
    description), freeing it for a future `et jira start`. No confirmation
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
