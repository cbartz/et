"""Tests for et.gsettings, mocking the gsettings subprocess calls."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from et.gsettings import GSettingsError, read_string_array, write_string_array


def _completed(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


@patch("et.gsettings.shutil.which", return_value="/usr/bin/gsettings")
@patch("et.gsettings.subprocess.run")
def test_read_string_array_parses_list(mock_run, _mock_which):
    mock_run.return_value = _completed(stdout="['', 'Focus', '']\n")
    assert read_string_array("org.example.schema", "some-key") == ["", "Focus", ""]


@patch("et.gsettings.shutil.which", return_value="/usr/bin/gsettings")
@patch("et.gsettings.subprocess.run")
def test_read_string_array_parses_empty_typed_array(mock_run, _mock_which):
    mock_run.return_value = _completed(stdout="@as []\n")
    assert read_string_array("org.example.schema", "some-key") == []


@patch("et.gsettings.shutil.which", return_value=None)
def test_read_string_array_raises_when_gsettings_missing(_mock_which):
    with pytest.raises(GSettingsError, match="gsettings"):
        read_string_array("org.example.schema", "some-key")


@patch("et.gsettings.shutil.which", return_value="/usr/bin/gsettings")
@patch("et.gsettings.subprocess.run")
def test_read_string_array_raises_on_nonzero_exit(mock_run, _mock_which):
    mock_run.return_value = _completed(
        stderr="No such schema \u201corg.example.schema\u201d\n", returncode=1
    )
    with pytest.raises(GSettingsError, match="gsettings get failed"):
        read_string_array("org.example.schema", "some-key")


@patch("et.gsettings.shutil.which", return_value="/usr/bin/gsettings")
@patch("et.gsettings.subprocess.run")
def test_read_string_array_raises_on_unparsable_output(mock_run, _mock_which):
    mock_run.return_value = _completed(stdout="not a python literal\n")
    with pytest.raises(GSettingsError, match="could not parse"):
        read_string_array("org.example.schema", "some-key")


@patch("et.gsettings.shutil.which", return_value="/usr/bin/gsettings")
@patch("et.gsettings.subprocess.run")
def test_write_string_array_calls_gsettings_set(mock_run, _mock_which):
    mock_run.return_value = _completed()
    write_string_array("org.example.schema", "some-key", ["", "Focus"])
    args = mock_run.call_args[0][0]
    assert args == ["gsettings", "set", "org.example.schema", "some-key", "['', 'Focus']"]


@patch("et.gsettings.shutil.which", return_value="/usr/bin/gsettings")
@patch("et.gsettings.subprocess.run")
def test_write_string_array_raises_on_nonzero_exit(mock_run, _mock_which):
    mock_run.return_value = _completed(stderr="some failure\n", returncode=1)
    with pytest.raises(GSettingsError, match="gsettings set failed"):
        write_string_array("org.example.schema", "some-key", ["x"])
