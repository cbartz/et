"""Command-line interface for et (effort tracker)."""

from __future__ import annotations

import typer

from et.tracker import TrackerError, add_tracker_for_current_workspace
from et.workspaces import WorkspaceError, rename_active_workspace

app = typer.Typer(
    help="et: a small CLI for tracking effort and managing your workspace.",
    no_args_is_help=True,
)
ws_app = typer.Typer(help="Interact with GNOME/Ubuntu workspaces.", no_args_is_help=True)
app.add_typer(ws_app, name="ws")

tracker_app = typer.Typer(help="Manage Tracker extension timers.", no_args_is_help=True)
app.add_typer(tracker_app, name="tracker")


@ws_app.command("rename")
def rename(new_name: str) -> None:
    """Rename the current (active) workspace to NEW_NAME."""
    try:
        index = rename_active_workspace(new_name)
    except WorkspaceError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo(f"Renamed workspace {index + 1} to '{new_name}'")


@tracker_app.command("add")
def tracker_add() -> None:
    """Add a Tracker timer bound to the current (active) workspace, if none exists yet.

    The new timer auto-starts whenever its workspace is active (and
    auto-pauses otherwise), same as a manually-started workspace-bound timer.
    """
    try:
        index, name, created = add_tracker_for_current_workspace()
    except (WorkspaceError, TrackerError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    if created:
        typer.echo(f"Added tracker '{name}' for workspace {index + 1}")
    else:
        typer.echo(f"Tracker '{name}' already exists for workspace {index + 1}")
