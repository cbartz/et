"""Command-line interface for et (effort tracker)."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

import typer

from et.config import ConfigError, get_max_workspaces, load_config, load_workspace_names
from et.jira_sync import JiraSyncError, jira_key_from_ref, sync_jira_workspaces
from et.tracker import (
    TrackerError,
    add_tracker_for_current_workspace,
    add_trackers_for_all_workspaces,
    dump_all_trackers,
    dump_tracker_for_current_workspace,
    reset_all_trackers,
    reset_tracker_for_current_workspace,
)
from et.workspaces import (
    WorkspaceError,
    get_active_workspace_index,
    rename_active_workspace,
    rename_all_workspaces,
)

if TYPE_CHECKING:
    from et.jira import JiraIssue


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
    no_args_is_help=True,
)
ws_app = typer.Typer(help="Interact with GNOME/Ubuntu workspaces.", no_args_is_help=True)
app.add_typer(ws_app, name="ws")

tracker_app = typer.Typer(help="Manage Tracker extension timers.", no_args_is_help=True)
app.add_typer(tracker_app, name="tracker")
jira_app = typer.Typer(help="Sync GNOME workspaces with active Jira issues.", no_args_is_help=True)
app.add_typer(jira_app, name="jira")


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


@ws_app.command("info")
def info() -> None:
    """Show the Jira issue (description + link) linked to the active workspace, if any."""
    try:
        index = get_active_workspace_index()
        config = load_config()
    except (ConfigError, WorkspaceError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

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

    Without --all, dumps only the ET-<n> timer bound to the active workspace.
    """
    if not all_workspaces:
        try:
            index, path = dump_tracker_for_current_workspace()
        except (WorkspaceError, TrackerError) as error:
            typer.echo(f"Error: {error}", err=True)
            raise typer.Exit(code=1) from error

        if path is None:
            typer.echo(f"No ET-<n> tracker bound to workspace {index + 1} to dump")
        else:
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

    for path in written:
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
            base_url = config.jira.base_url.rstrip("/") if config.jira else ""
        except ConfigError:
            base_url = ""

        typer.echo("Active issues, highest priority first:")
        for issue in issues:
            key_display = (
                _hyperlink(issue.key, f"{base_url}/browse/{issue.key}") if base_url else issue.key
            )
            typer.echo(f"  {key_display} [{issue.priority}] {issue.summary}")
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
