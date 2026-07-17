# et CLI Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bootstrap a `uv`-managed Python CLI project named `et` (effort tracker) whose first feature is `uv run et ws rename <new-name>`, renaming the currently active GNOME/Ubuntu workspace.

**Architecture:** A `uv` `src`-layout package `et` with a thin Typer CLI (`src/et/cli.py`) delegating to pure, testable logic (`src/et/workspaces.py`) that shells out to `wmctrl` (find active workspace) and `gsettings` (read/write `org.gnome.desktop.wm.preferences workspace-names`). Project lifecycle is driven by a `Justfile` (install-requirements, dev, lint, static, test) and `prek` runs ruff/mypy/pytest on commit.

**Tech Stack:** Python 3.12, `uv`, Typer, `wmctrl`/`gsettings` (shelled out via `subprocess`), `pytest` + `unittest.mock`, `ruff`, `mypy`, `prek`.

## Global Constraints

- Package/project name: `et`. CLI entry point name: `et` (not `et-scratch` or similar — override uv's default script name).
- `src` layout: package code lives under `src/et/`.
- `requires-python = ">=3.12"` (matches the system Python already installed; do not require 3.14).
- Root command is `uv run et ws rename <new-name>` — a `ws` Typer sub-app with a `rename` command, leaving room for future `ws` subcommands.
- `cli.py` stays thin: argument parsing, calling into `workspaces.py`, formatting output/errors only. All GNOME-interaction logic lives in `workspaces.py`.
- `workspaces.py` has no Typer dependency and is unit-testable by mocking `subprocess.run` and `shutil.which` — no real GNOME session required to run the test suite.
- Errors from missing binaries / no active workspace / parse failures raise `WorkspaceError` (defined in `workspaces.py`); `cli.py` catches it, prints `Error: <message>` to stderr, and exits with status 1.
- `Justfile` recipes are plain command lines (uv/just built-ins), no inline bash blocks: `install-requirements`, `dev`, `lint`, `static`, `test`, `test-integ`.
- `prek` (pre-commit-compatible, already available as a `uv tool`) runs `ruff check`, `mypy`, and `pytest` on every commit via `.pre-commit-config.yaml`.

---

### Task 1: Project scaffold and dependencies

**Files:**
- Create: `pyproject.toml` (via `uv init`, then edited)
- Create: `src/et/__init__.py`
- Create: `.gitignore`, `.python-version`, `README.md` (via `uv init`)
- Modify: `pyproject.toml` (fix project name / script name / `requires-python`, add dependencies, add `[tool.ruff]` / `[tool.mypy]` config)

**Interfaces:**
- Produces: an installable `et` package (`import et` works), with `typer`, `ruff`, `mypy`, `pytest`, `prek` available via `uv run`.

- [ ] **Step 1: Scaffold the project with `uv init`**

The working directory `/home/sebastien.georget@canonical.com/work/et` already has a git repo (`git init` was run) and a committed spec doc. Run `uv init` in-place, keeping the existing git history:

```bash
cd /home/sebastien.georget@canonical.com/work/et
uv init --package --name et --python 3.12 .
```

Expected output: `Initialized project et at ...` (it will not re-init git since `.git` already exists).

- [ ] **Step 2: Fix up generated files**

`uv init --package` names the package `et` but wires the script entry point to `et:main` pointing at `src/et/__init__.py`'s `main()` function. Replace the generated `src/et/__init__.py` so it exposes nothing but re-exports are unnecessary — the CLI entry point will point directly at `et.cli:app` instead (created in Task 3). For now, make `src/et/__init__.py` an empty package marker:

```python
"""et: a small CLI for tracking effort and managing your workspace."""
```

- [ ] **Step 3: Edit `pyproject.toml`**

Open `pyproject.toml` and replace its contents with:

```toml
[project]
name = "et"
version = "0.1.0"
description = "et: a small CLI to track effort and manage your Ubuntu/GNOME workspace."
readme = "README.md"
authors = [
    { name = "Sebastien Georget", email = "sebastien.georget@canonical.com" }
]
requires-python = ">=3.12"
dependencies = [
    "typer>=0.12",
]

[project.scripts]
et = "et.cli:app"

[build-system]
requires = ["uv_build>=0.11.21,<0.12.0"]
build-backend = "uv_build"

[dependency-groups]
dev = [
    "ruff>=0.6",
    "mypy>=1.11",
    "pytest>=8.0",
    "prek>=0.3",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP"]

[tool.mypy]
python_version = "3.12"
mypy_path = "src"
files = ["src"]
strict = true

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Note: `et = "et.cli:app"` points at a Typer `app` object (Typer apps are directly callable), so no `main()` wrapper function is needed.

- [ ] **Step 4: Create the `tests/` directory with a package marker**

```bash
mkdir -p tests
touch tests/__init__.py
```

- [ ] **Step 5: Sync dependencies**

```bash
uv sync
```

Expected: resolves and installs `typer`, `ruff`, `mypy`, `pytest`, `prek` and their dependencies into `.venv`, no errors.

- [ ] **Step 6: Verify the package imports**

```bash
uv run python -c "import et; print('ok')"
```

Expected output: `ok`

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src tests .gitignore .python-version README.md
git commit -m "chore: bootstrap uv project scaffold for et"
```

---

### Task 2: Workspace logic (`workspaces.py`) with tests

**Files:**
- Create: `src/et/workspaces.py`
- Create: `tests/test_workspaces.py`

**Interfaces:**
- Consumes: nothing from other tasks (only stdlib `subprocess`, `shutil`, `ast`).
- Produces (used by Task 3's `cli.py`):
  - `class WorkspaceError(RuntimeError)`
  - `get_active_workspace_index() -> int`
  - `get_workspace_names() -> list[str]`
  - `set_workspace_names(names: list[str]) -> None`
  - `rename_active_workspace(new_name: str) -> int` (returns the 0-based index of the workspace that was renamed)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_workspaces.py`:

```python
"""Tests for et.workspaces, mocking wmctrl/gsettings subprocess calls."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from et.workspaces import (
    WorkspaceError,
    get_active_workspace_index,
    get_workspace_names,
    rename_active_workspace,
    set_workspace_names,
)


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


WMCTRL_OUTPUT = (
    "0  - DG: 1920x1080  VP: 0,0  WA: 0,0 1920x1055  Workspace 1\n"
    "1  * DG: 1920x1080  VP: 0,0  WA: 0,0 1920x1055  Workspace 2\n"
    "2  - DG: 1920x1080  VP: 0,0  WA: 0,0 1920x1055  Workspace 3\n"
)


@patch("et.workspaces.shutil.which", return_value="/usr/bin/wmctrl")
@patch("et.workspaces.subprocess.run")
def test_get_active_workspace_index_returns_marked_index(mock_run, _mock_which):
    mock_run.return_value = _completed(stdout=WMCTRL_OUTPUT)
    assert get_active_workspace_index() == 1


@patch("et.workspaces.shutil.which", return_value="/usr/bin/wmctrl")
@patch("et.workspaces.subprocess.run")
def test_get_active_workspace_index_raises_when_no_marker(mock_run, _mock_which):
    mock_run.return_value = _completed(
        stdout="0  - DG: 1920x1080  VP: 0,0  WA: 0,0 1920x1055  Workspace 1\n"
    )
    with pytest.raises(WorkspaceError, match="no active workspace"):
        get_active_workspace_index()


@patch("et.workspaces.shutil.which", return_value=None)
def test_get_active_workspace_index_raises_when_wmctrl_missing(_mock_which):
    with pytest.raises(WorkspaceError, match="wmctrl"):
        get_active_workspace_index()


@patch("et.workspaces.shutil.which", return_value="/usr/bin/gsettings")
@patch("et.workspaces.subprocess.run")
def test_get_workspace_names_parses_list(mock_run, _mock_which):
    mock_run.return_value = _completed(stdout="['', 'Focus', '']\n")
    assert get_workspace_names() == ["", "Focus", ""]


@patch("et.workspaces.shutil.which", return_value="/usr/bin/gsettings")
@patch("et.workspaces.subprocess.run")
def test_get_workspace_names_parses_empty_typed_array(mock_run, _mock_which):
    mock_run.return_value = _completed(stdout="@as []\n")
    assert get_workspace_names() == []


@patch("et.workspaces.shutil.which", return_value="/usr/bin/gsettings")
@patch("et.workspaces.subprocess.run")
def test_set_workspace_names_calls_gsettings_set(mock_run, _mock_which):
    mock_run.return_value = _completed()
    set_workspace_names(["", "Focus"])
    args = mock_run.call_args[0][0]
    assert args[:4] == [
        "gsettings",
        "set",
        "org.gnome.desktop.wm.preferences",
        "workspace-names",
    ]
    assert args[4] == "['', 'Focus']"


@patch("et.workspaces.set_workspace_names")
@patch("et.workspaces.get_workspace_names", return_value=["", ""])
@patch("et.workspaces.get_active_workspace_index", return_value=2)
def test_rename_active_workspace_pads_list_when_needed(
    mock_get_index, mock_get_names, mock_set_names
):
    index = rename_active_workspace("focus")
    assert index == 2
    mock_set_names.assert_called_once_with(["", "", "focus"])


@patch("et.workspaces.set_workspace_names")
@patch("et.workspaces.get_workspace_names", return_value=["a", "b", "c"])
@patch("et.workspaces.get_active_workspace_index", return_value=1)
def test_rename_active_workspace_replaces_existing_name(
    mock_get_index, mock_get_names, mock_set_names
):
    index = rename_active_workspace("focus")
    assert index == 1
    mock_set_names.assert_called_once_with(["a", "focus", "c"])
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_workspaces.py -v
```

Expected: collection error / `ModuleNotFoundError: No module named 'et.workspaces'` (module doesn't exist yet).

- [ ] **Step 3: Implement `src/et/workspaces.py`**

```python
"""Logic for inspecting and renaming GNOME/Ubuntu workspaces.

This module shells out to `wmctrl` (to find the active workspace) and
`gsettings` (to read/write GNOME's workspace-names setting). It has no
Typer/CLI dependency so it can be unit tested by mocking `subprocess.run`.
"""

from __future__ import annotations

import ast
import shutil
import subprocess

WORKSPACE_NAMES_SCHEMA = "org.gnome.desktop.wm.preferences"
WORKSPACE_NAMES_KEY = "workspace-names"


class WorkspaceError(RuntimeError):
    """Raised when a workspace operation cannot be completed."""


def _require_binary(name: str) -> None:
    if shutil.which(name) is None:
        raise WorkspaceError(f"required command not found: {name}")


def get_active_workspace_index() -> int:
    """Return the 0-based index of the currently active workspace."""
    _require_binary("wmctrl")
    result = subprocess.run(
        ["wmctrl", "-d"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise WorkspaceError(f"wmctrl -d failed: {result.stderr.strip()}")

    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        index_str, status = fields[0], fields[1]
        if status == "*":
            return int(index_str)

    raise WorkspaceError("no active workspace found in `wmctrl -d` output")


def get_workspace_names() -> list[str]:
    """Return the current list of GNOME workspace names."""
    _require_binary("gsettings")
    result = subprocess.run(
        ["gsettings", "get", WORKSPACE_NAMES_SCHEMA, WORKSPACE_NAMES_KEY],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise WorkspaceError(f"gsettings get failed: {result.stderr.strip()}")

    raw = result.stdout.strip()
    if raw.startswith("@as "):
        raw = raw[len("@as "):]
    try:
        names = ast.literal_eval(raw)
    except (ValueError, SyntaxError) as exc:
        raise WorkspaceError(f"could not parse workspace-names value: {raw!r}") from exc

    if not isinstance(names, list) or not all(isinstance(item, str) for item in names):
        raise WorkspaceError(f"unexpected workspace-names value: {raw!r}")

    return names


def set_workspace_names(names: list[str]) -> None:
    """Write the given list of workspace names to GNOME's settings."""
    _require_binary("gsettings")
    result = subprocess.run(
        ["gsettings", "set", WORKSPACE_NAMES_SCHEMA, WORKSPACE_NAMES_KEY, repr(names)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise WorkspaceError(f"gsettings set failed: {result.stderr.strip()}")


def rename_active_workspace(new_name: str) -> int:
    """Rename the active workspace to `new_name`.

    Returns the 0-based index of the workspace that was renamed.
    """
    index = get_active_workspace_index()
    names = get_workspace_names()

    if len(names) <= index:
        names = names + [""] * (index + 1 - len(names))

    names[index] = new_name
    set_workspace_names(names)
    return index
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_workspaces.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/et/workspaces.py tests/test_workspaces.py
git commit -m "feat: add workspace rename logic (wmctrl + gsettings)"
```

---

### Task 3: CLI wiring (`cli.py`) and manual smoke test

**Files:**
- Create: `src/et/cli.py`

**Interfaces:**
- Consumes: `et.workspaces.WorkspaceError`, `et.workspaces.rename_active_workspace(new_name: str) -> int` (from Task 2).
- Produces: `et.cli.app` (Typer app), the object referenced by `pyproject.toml`'s `[project.scripts] et = "et.cli:app"`.

- [ ] **Step 1: Implement `src/et/cli.py`**

```python
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
```

- [ ] **Step 2: Verify `--help` works end-to-end**

```bash
uv sync
uv run et --help
uv run et ws --help
uv run et ws rename --help
```

Expected: Typer help text listing the `ws` command group, and `rename` listing a `NEW_NAME` argument, with no tracebacks.

- [ ] **Step 3: Manually verify the error path (no real GNOME session / missing binaries case is fine either way)**

```bash
uv run et ws rename "test workspace"; echo "exit code: $?"
```

Expected: either a successful `Renamed workspace N to 'test workspace'` message (exit 0, if run in a real GNOME session with `wmctrl`/`gsettings` available), or a clean `Error: ...` message on stderr with `exit code: 1` (e.g. `Error: required command not found: wmctrl` if not running under GNOME/X11). No unhandled Python traceback in either case.

- [ ] **Step 4: Commit**

```bash
git add src/et/cli.py
git commit -m "feat: add 'et ws rename' Typer CLI command"
```

---

### Task 4: Justfile lifecycle recipes

**Files:**
- Create: `Justfile`

**Interfaces:**
- Consumes: `uv sync`, `uv run et --help`, `uv run ruff check .`, `uv run mypy src`, `uv run pytest` (all from Tasks 1-3).
- Produces: `just install-requirements`, `just dev`, `just lint`, `just static`, `just test`, `just test-integ` — the standard lifecycle recipes required by repo convention.

- [ ] **Step 1: Create `Justfile`**

```just
install-requirements:
    uv sync --all-extras --dev
    uv run prek install

dev:
    uv run et --help

lint:
    uv run ruff check .

static:
    uv run mypy src

test:
    uv run pytest

test-integ:
    echo "no integration tests yet"
```

- [ ] **Step 2: Verify each recipe runs**

```bash
just install-requirements
just lint
just static
just test
just dev
just test-integ
```

Expected: `install-requirements` syncs and installs the `prek` git hook (`prek installed at .git/hooks/pre-commit` or similar); `lint` and `static` report no errors; `test` shows all tests passing; `dev` prints the CLI `--help` text; `test-integ` prints the placeholder message.

- [ ] **Step 3: Commit**

```bash
git add Justfile
git commit -m "chore: add Justfile lifecycle recipes"
```

---

### Task 5: Pre-commit (`prek`) configuration

**Files:**
- Create: `.pre-commit-config.yaml`

**Interfaces:**
- Consumes: `uv run ruff check`, `uv run mypy src`, `uv run pytest` (from Tasks 1-2, already verified by Task 4's `lint`/`static`/`test` recipes).
- Produces: a git `pre-commit` hook (installed via `prek install`, already run in Task 4 Step 2) that blocks commits failing ruff, mypy, or pytest.

- [ ] **Step 1: Create `.pre-commit-config.yaml`**

```yaml
repos:
  - repo: local
    hooks:
      - id: ruff
        name: ruff check
        entry: uv run ruff check
        language: system
        types: [python]
      - id: mypy
        name: mypy
        entry: uv run mypy src
        language: system
        types: [python]
        pass_filenames: false
      - id: pytest
        name: pytest
        entry: uv run pytest
        language: system
        pass_filenames: false
        always_run: true
```

- [ ] **Step 2: Re-install the hook against the new config and verify it runs**

```bash
uv run prek install
uv run prek run --all-files
```

Expected: `prek install` reports the hook is installed at `.git/hooks/pre-commit`; `prek run --all-files` runs `ruff check`, `mypy`, and `pytest`, all passing (green/`Passed`).

- [ ] **Step 3: Commit**

```bash
git add .pre-commit-config.yaml
git commit -m "chore: run ruff/mypy/pytest via prek on commit"
```

- [ ] **Step 4: Verify the hook fires on a real commit**

Make a trivial no-op change (e.g. touch a comment in `README.md`), stage it, and commit normally to confirm `prek`'s hook output appears:

```bash
echo "" >> README.md
git add README.md
git commit -m "chore: trigger prek hook verification"
```

Expected: commit output shows the `ruff check`, `mypy`, `pytest` hook steps running and passing before the commit succeeds.

---

## Post-Plan Verification

After all tasks are complete, run the full lifecycle once more from a clean state to confirm nothing regressed:

```bash
just lint
just static
just test
```

Expected: all three pass with no errors, confirming the bootstrap is fully working and enforced by `prek` on every future commit.
