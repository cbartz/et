"""Thin wrapper around the `gsettings` CLI for array-of-strings values.

Shells out to `gsettings` to read/write GVariant `as` (array of strings)
values, used both for GNOME's workspace names and for the Tracker
extension's JSON-encoded timer list. Has no Typer/CLI dependency so callers
can unit test by mocking `subprocess.run`.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess


class GSettingsError(RuntimeError):
    """Raised when a `gsettings` read/write operation cannot be completed."""


def _require_binary(name: str) -> None:
    if shutil.which(name) is None:
        raise GSettingsError(f"required command not found: {name}")


def _build_env(schema_dir: str | None) -> dict[str, str] | None:
    """Build a subprocess environment with GSETTINGS_SCHEMA_DIR set, if needed.

    `schema_dir` lets callers point at a schema compiled outside the standard
    system locations (e.g. a per-user GNOME Shell extension's own compiled
    schema), which the plain `gsettings` CLI otherwise can't see.
    """
    if schema_dir is None:
        return None
    return {**os.environ, "GSETTINGS_SCHEMA_DIR": schema_dir}


def read_string_array(schema: str, key: str, schema_dir: str | None = None) -> list[str]:
    """Return the current array-of-strings value of `schema`'s `key`.

    If `schema_dir` is given, `gsettings` is invoked with `GSETTINGS_SCHEMA_DIR`
    set to it, so schemas compiled outside the standard system locations
    (e.g. a user-installed GNOME Shell extension's own schema) can be found.
    """
    _require_binary("gsettings")
    result = subprocess.run(
        ["gsettings", "get", schema, key],
        capture_output=True,
        text=True,
        check=False,
        env=_build_env(schema_dir),
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


def _set_raw(schema: str, key: str, raw_value: str, schema_dir: str | None = None) -> None:
    """Write a raw GVariant-syntax value string to `schema`'s `key`."""
    _require_binary("gsettings")
    result = subprocess.run(
        ["gsettings", "set", schema, key, raw_value],
        capture_output=True,
        text=True,
        check=False,
        env=_build_env(schema_dir),
    )
    if result.returncode != 0:
        raise GSettingsError(f"gsettings set failed: {result.stderr.strip()}")


def write_string_array(
    schema: str, key: str, values: list[str], schema_dir: str | None = None
) -> None:
    """Write the given list of strings to `schema`'s `key`.

    See `read_string_array` for the meaning of `schema_dir`.
    """
    _set_raw(schema, key, repr(values), schema_dir=schema_dir)


def set_boolean(schema: str, key: str, value: bool, schema_dir: str | None = None) -> None:
    """Write a boolean value to `schema`'s `key`."""
    _set_raw(schema, key, "true" if value else "false", schema_dir=schema_dir)


def set_int(schema: str, key: str, value: int, schema_dir: str | None = None) -> None:
    """Write an integer value to `schema`'s `key`."""
    _set_raw(schema, key, str(value), schema_dir=schema_dir)


def _get_raw(schema: str, key: str, schema_dir: str | None = None) -> str:
    """Return the raw, stripped stdout of `gsettings get schema key`."""
    _require_binary("gsettings")
    result = subprocess.run(
        ["gsettings", "get", schema, key],
        capture_output=True,
        text=True,
        check=False,
        env=_build_env(schema_dir),
    )
    if result.returncode != 0:
        raise GSettingsError(f"gsettings get failed: {result.stderr.strip()}")
    return result.stdout.strip()


def read_boolean(schema: str, key: str, schema_dir: str | None = None) -> bool:
    """Return the current boolean value of `schema`'s `key`."""
    raw = _get_raw(schema, key, schema_dir=schema_dir)
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise GSettingsError(f"unexpected {schema} {key} boolean value: {raw!r}")


def read_int(schema: str, key: str, schema_dir: str | None = None) -> int:
    """Return the current integer value of `schema`'s `key`.

    Tolerates GVariant type-annotated output (e.g. "uint32 4") by taking the
    trailing token.
    """
    raw = _get_raw(schema, key, schema_dir=schema_dir)
    token = raw.split()[-1] if raw else raw
    try:
        return int(token)
    except ValueError as exc:
        raise GSettingsError(f"could not parse {schema} {key} value: {raw!r}") from exc
