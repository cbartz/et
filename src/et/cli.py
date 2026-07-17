"""Command-line interface for et (effort tracker)."""

from __future__ import annotations

import typer

from et.tracker import (
    TrackerError,
    add_tracker_for_current_workspace,
    add_trackers_for_all_workspaces,
    dump_all_trackers,
    reset_all_trackers,
)
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
            results = add_trackers_for_all_workspaces()
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
    """Reset ET-<n> trackers to 0 elapsed time."""
    if not all_workspaces:
        typer.echo("Error: --all is required (only bulk reset is currently supported)", err=True)
        raise typer.Exit(code=1)

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
    """Save each ET-<n> tracker's elapsed time to a file under ~/timers/<yyyy-mm-dd>/."""
    if not all_workspaces:
        typer.echo("Error: --all is required (only bulk dump is currently supported)", err=True)
        raise typer.Exit(code=1)

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
