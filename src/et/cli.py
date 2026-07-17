"""Command-line interface for et (effort tracker)."""

from __future__ import annotations

import typer

from et.workspaces import WorkspaceError, rename_active_workspace

app = typer.Typer(
    help="et: a small CLI for tracking effort and managing your workspace.",
    no_args_is_help=True,
)
ws_app = typer.Typer(help="Interact with GNOME/Ubuntu workspaces.", no_args_is_help=True)
app.add_typer(ws_app, name="ws")


@ws_app.command("rename")
def rename(new_name: str) -> None:
    """Rename the current (active) workspace to NEW_NAME."""
    try:
        index = rename_active_workspace(new_name)
    except WorkspaceError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo(f"Renamed workspace {index + 1} to '{new_name}'")
