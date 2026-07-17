"""Loading et's configuration file (`~/.config/et/config.yaml`).

Currently the only setting is a list of workspace names, used by
`et ws rename --all` to bulk-rename GNOME workspaces to match the config.
Has no Typer/CLI dependency so callers can unit test without touching the
real filesystem (via the `ET_CONFIG_DIR` environment variable override).
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

CONFIG_DIR_ENV_VAR = "ET_CONFIG_DIR"
CONFIG_FILE_NAME = "config.yaml"


class ConfigError(RuntimeError):
    """Raised when the et config file is missing or malformed."""


def get_config_dir() -> Path:
    """Return the directory et's config file lives in (~/.config/et by default)."""
    override = os.environ.get(CONFIG_DIR_ENV_VAR)
    if override:
        return Path(override)
    return Path.home() / ".config" / "et"


def get_config_path() -> Path:
    """Return the full path to et's config file."""
    return get_config_dir() / CONFIG_FILE_NAME


def load_workspace_names() -> list[str]:
    """Return the ordered list of workspace names configured for `ws rename --all`.

    Reads the "workspaces" key from the config file: a list of mappings,
    each with a "name" key, e.g.:

        workspaces:
          - name: mails
          - name: handson
          - name: isd-321

    Raises ConfigError if the file is missing, unreadable, or malformed.
    """
    path = get_config_path()
    if not path.is_file():
        raise ConfigError(
            f"config file not found: {path} "
            f"(create it with a top-level 'workspaces' list of {{name: ...}} entries)"
        )

    try:
        raw = path.read_text()
    except OSError as exc:
        raise ConfigError(f"could not read config file {path}: {exc}") from exc

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not parse config file {path}: {exc}") from exc

    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"config file {path} must contain a mapping at the top level")

    workspaces = data.get("workspaces", [])
    if not isinstance(workspaces, list):
        raise ConfigError(f"config file {path}: 'workspaces' must be a list")

    names: list[str] = []
    for index, entry in enumerate(workspaces):
        if not isinstance(entry, dict) or "name" not in entry:
            raise ConfigError(
                f"config file {path}: workspaces[{index}] must be a mapping with a 'name' key"
            )
        name = entry["name"]
        if not isinstance(name, str):
            raise ConfigError(f"config file {path}: workspaces[{index}].name must be a string")
        names.append(name)

    return names
