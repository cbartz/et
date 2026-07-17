# et CLI — Bootstrap Design

## Purpose

`et` (effort tracker) starts as a small CLI to interact with Ubuntu/GNOME
workspaces. The first capability is renaming the current (active) workspace.

## Scope

- Project bootstrap: `uv`-managed Python package named `et`.
- CLI command: `uv run et ws rename <new-name>` renames the workspace that is
  currently active (the one the command is run from).
- No other workspace operations (list/show/switch) are in scope for this
  first bootstrap.

## Architecture

- **Package manager**: `uv`, `src` layout.
- **Package name**: `et`, importable as `et`, installed under `src/et/`.
- **CLI framework**: Typer. Root app exposes a `ws` sub-app (Typer
  `add_typer`) so future workspace subcommands (`list`, `show`, ...) can be
  added without restructuring.
- **Entry point**: `[project.scripts] et = "et.cli:app"`, invoked as
  `uv run et ...`.
- **Modules**:
  - `src/et/cli.py`: Typer app and command wiring only (thin — parses args,
    calls into `workspaces.py`, formats output/errors). Contains root `app`
    and `ws_app` with the `rename` command.
  - `src/et/workspaces.py`: pure logic, no Typer dependency, easily unit
    testable:
    - `get_active_workspace_index() -> int`: runs `wmctrl -d`, parses the
      line marked with `*` to find the active workspace's 0-based index.
      Raises a domain error (`WorkspaceError`) if `wmctrl` is missing or no
      active workspace line is found.
    - `get_workspace_names() -> list[str]`: reads
      `org.gnome.desktop.wm.preferences workspace-names` via `gsettings get`,
      parsing the GVariant array-of-strings literal into a Python list.
    - `set_workspace_names(names: list[str]) -> None`: writes the list back
      via `gsettings set ... workspace-names "[...]"`.
    - `rename_active_workspace(new_name: str) -> None`: orchestrates the
      above — gets active index, reads current names, pads the list with
      empty strings if the active index is beyond the current list length,
      replaces the entry at that index, writes the list back.
- **Error handling**: `WorkspaceError` (defined in `workspaces.py`) is raised
  for expected failure cases (missing `wmctrl`/`gsettings` binaries, no
  active workspace detected, gsettings read/write failures). `cli.py` catches
  `WorkspaceError` and prints a clean message via `typer.echo(..., err=True)`
  then exits with status 1. Unexpected exceptions propagate normally (surface
  as a stack trace — acceptable for this bootstrap stage).

## Data flow

1. User runs `uv run et ws rename "focus"`.
2. `cli.py` calls `workspaces.rename_active_workspace("focus")`.
3. `workspaces.py` shells out to `wmctrl -d`, finds the active workspace
   index (e.g. `2`).
4. `workspaces.py` shells out to `gsettings get org.gnome.desktop.wm.preferences workspace-names`,
   parses the current list (e.g. `['', '']`).
5. Pads/replaces index `2` with `"focus"` → `['', '', 'focus']`.
6. Writes back via `gsettings set ... workspace-names "['', '', 'focus']"`.
7. `cli.py` prints a confirmation, e.g. `Renamed workspace 3 to 'focus'`.

## Testing

- `pytest` unit tests for `workspaces.py`, mocking `subprocess.run` calls to
  `wmctrl` and `gsettings` (no real GNOME session needed in CI/dev sandbox).
  Cases: happy path rename, no active workspace found, `wmctrl`/`gsettings`
  missing, padding shorter-than-needed name lists.
- `cli.py` is thin enough that it isn't unit tested directly in this first
  bootstrap; behavior is covered indirectly through `workspaces.py` tests.

## Tooling (Justfile, per repo convention)

- `install-requirements`: `uv sync --all-extras --dev` — installs runtime +
  dev dependencies (ruff, mypy, pytest, prek).
- `dev`: `uv run et --help` — smoke-runs the CLI (no long-running server
  applies to a CLI tool).
- `lint`: `uv run ruff check .`
- `static`: `uv run mypy src`
- `test`: `uv run pytest`
- `ops`: omitted for now — no packaging/deployment target yet.

## Pre-commit (prek)

A `.pre-commit-config.yaml` (compatible with `prek`) runs `ruff check`,
`mypy`, and `pytest` on each commit. `prek install` registers the git hook.

## Out of scope

- Workspace listing/switching commands.
- Non-GNOME desktop environments.
- Packaging/distribution (`ops` stage) beyond local `uv` usage.
