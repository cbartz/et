"""End-to-end integration tests for the `et` CLI.

Unlike the unit tests (which mock each module's public helpers), these
exercise the *whole* stack — Typer command -> tracker/workspaces ->
gsettings/gnome-extensions -> subprocess — and only replace the two real
process boundaries: `subprocess.run` (the actual `gsettings`/`wmctrl`/
`gnome-extensions` calls) and `shutil.which` (binary discovery).

`FakeSystem` stands in for the machine: it keeps GSettings keys in an
in-memory dict and answers the handful of external commands et shells out
to, so a command's full read/modify/write cycle can be asserted against
persisted state.
"""

from __future__ import annotations

import ast
import json
import subprocess
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from et.cli import app

runner = CliRunner()

WORKSPACE_NAMES = ("org.gnome.desktop.wm.preferences", "workspace-names")
NUM_WORKSPACES = ("org.gnome.desktop.wm.preferences", "num-workspaces")
DYNAMIC_WORKSPACES = ("org.gnome.mutter", "dynamic-workspaces")
TRACKER_TIMERS = ("org.gnome.shell.extensions.tracker", "timers")
TRACKER_UUID = "tracker@aliakseiz.github.com"


class FakeSystem:
    """In-memory stand-in for gsettings/wmctrl/gnome-extensions."""

    def __init__(self, active_workspace: int = 0) -> None:
        self.active_workspace = active_workspace
        self.enabled_extensions = {TRACKER_UUID}
        self.gsettings: dict[tuple[str, str], str] = {
            WORKSPACE_NAMES: "@as []",
            TRACKER_TIMERS: "@as []",
        }

    def run(self, args, capture_output=True, text=True, check=False, env=None):
        program = args[0]
        if program == "gsettings":
            return self._run_gsettings(args)
        if program == "wmctrl":
            return self._run_wmctrl()
        if program == "gnome-extensions":
            return self._run_gnome_extensions(args)
        raise AssertionError(f"unexpected command: {args!r}")

    def _completed(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        return subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=stdout, stderr=stderr
        )

    def _run_gsettings(self, args):
        action = args[1]
        schema, key = args[2], args[3]
        if action == "get":
            return self._completed(stdout=self.gsettings.get((schema, key), "@as []") + "\n")
        if action == "set":
            self.gsettings[(schema, key)] = args[4]
            return self._completed()
        raise AssertionError(f"unexpected gsettings action: {action}")

    def _run_wmctrl(self):
        lines = []
        for index in range(4):
            marker = "*" if index == self.active_workspace else "-"
            lines.append(f"{index}  {marker} DG: 1920x1080  VP: 0,0  WA: 0,0 1920x1080  W{index}")
        return self._completed(stdout="\n".join(lines) + "\n")

    def _run_gnome_extensions(self, args):
        action = args[1]
        if action == "list":
            return self._completed(stdout="\n".join(sorted(self.enabled_extensions)) + "\n")
        uuid = args[2]
        if action == "disable":
            self.enabled_extensions.discard(uuid)
        elif action == "enable":
            self.enabled_extensions.add(uuid)
        return self._completed()

    def read_string_array(self, schema: str, key: str) -> list[str]:
        raw = self.gsettings[(schema, key)]
        if raw.startswith("@as "):
            raw = raw[len("@as "):]
        return ast.literal_eval(raw)


@pytest.fixture
def system(tmp_path, monkeypatch):
    monkeypatch.setenv("ET_CONFIG_DIR", str(tmp_path))
    fake = FakeSystem()
    with (
        patch("shutil.which", return_value="/usr/bin/fake"),
        patch("subprocess.run", side_effect=fake.run),
    ):
        yield fake


def test_ws_rename_persists_active_workspace_name(system):
    system.active_workspace = 2

    result = runner.invoke(app, ["ws", "rename", "focus"])

    assert result.exit_code == 0, result.output
    assert "Renamed workspace 3 to 'focus'" in result.output
    assert system.read_string_array(*WORKSPACE_NAMES) == ["", "", "focus"]


def test_tracker_add_creates_and_persists_timer_for_active_workspace(system):
    system.active_workspace = 0

    result = runner.invoke(app, ["tracker", "add"])

    assert result.exit_code == 0, result.output
    assert "Added tracker 'ET-1' for workspace 1" in result.output

    timers = [json.loads(raw) for raw in system.read_string_array(*TRACKER_TIMERS)]
    assert len(timers) == 1
    assert timers[0]["name"] == "ET-1"
    assert timers[0]["workspaceId"] == 0
    assert timers[0]["autoResume"] is True
    # The extension is left re-enabled after the disable/write/enable dance.
    assert TRACKER_UUID in system.enabled_extensions


def test_tracker_add_all_configures_static_layout_and_ten_timers(system):
    result = runner.invoke(app, ["tracker", "add", "--all"])

    assert result.exit_code == 0, result.output
    assert system.gsettings[DYNAMIC_WORKSPACES] == "false"
    assert system.gsettings[NUM_WORKSPACES] == "10"

    timers = [json.loads(raw) for raw in system.read_string_array(*TRACKER_TIMERS)]
    assert [t["name"] for t in timers] == [f"ET-{i}" for i in range(1, 11)]


def test_tracker_dump_all_writes_elapsed_times_to_disk(system, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    system.gsettings[TRACKER_TIMERS] = repr(
        [json.dumps({"id": "a", "name": "ET-1", "workspaceId": 0, "timeElapsed": 3725})]
    )

    result = runner.invoke(app, ["tracker", "dump", "--all"])

    assert result.exit_code == 0, result.output
    written = list(tmp_path.glob("timers/*/ET-1.txt"))
    assert len(written) == 1
    assert written[0].read_text() == "3725\n1h 2m 5s\n"


def test_tracker_reset_current_workspace_zeroes_persisted_timer(system):
    system.active_workspace = 1
    system.gsettings[TRACKER_TIMERS] = repr(
        [
            json.dumps({"id": "a", "name": "ET-1", "workspaceId": 0, "timeElapsed": 100}),
            json.dumps({"id": "b", "name": "ET-2", "workspaceId": 1, "timeElapsed": 500}),
        ]
    )

    result = runner.invoke(app, ["tracker", "reset"])

    assert result.exit_code == 0, result.output
    assert "Reset tracker 'ET-2' to 0" in result.output

    timers = {
        json.loads(raw)["name"]: json.loads(raw)
        for raw in system.read_string_array(*TRACKER_TIMERS)
    }
    assert timers["ET-2"]["timeElapsed"] == 0
    assert timers["ET-1"]["timeElapsed"] == 100
