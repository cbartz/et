"""Thin wrapper around the `gsettings` CLI for array-of-strings values.

Shells out to `gsettings` to read/write GVariant `as` (array of strings)
values, used both for GNOME's workspace names and for the Tracker
extension's JSON-encoded timer list. Has no Typer/CLI dependency so callers
can unit test by mocking `subprocess.run`.
"""

from __future__ import annotations

import ast
import shutil
import subprocess


class GSettingsError(RuntimeError):
    """Raised when a `gsettings` read/write operation cannot be completed."""


def _require_binary(name: str) -> None:
    if shutil.which(name) is None:
        raise GSettingsError(f"required command not found: {name}")


def read_string_array(schema: str, key: str) -> list[str]:
    """Return the current array-of-strings value of `schema`'s `key`."""
    _require_binary("gsettings")
    result = subprocess.run(
        ["gsettings", "get", schema, key],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise GSettingsError(f"gsettings get failed: {result.stderr.strip()}")

    raw = result.stdout.strip()
    if raw.startswith("@as "):
        raw = raw[len("@as "):]
    try:
        values = ast.literal_eval(raw)
    except (ValueError, SyntaxError) as exc:
        raise GSettingsError(f"could not parse {schema} {key} value: {raw!r}") from exc

    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise GSettingsError(f"unexpected {schema} {key} value: {raw!r}")

    return values


def write_string_array(schema: str, key: str, values: list[str]) -> None:
    """Write the given list of strings to `schema`'s `key`."""
    _require_binary("gsettings")
    result = subprocess.run(
        ["gsettings", "set", schema, key, repr(values)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise GSettingsError(f"gsettings set failed: {result.stderr.strip()}")
