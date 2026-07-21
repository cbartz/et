"""Tests for the `et` CLI, focused on presentation behaviour not covered by unit tests
of the underlying config/tracker/workspace/jira modules."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from et.cli import _hyperlink, app
from et.config import EtConfig, JiraConfig, WorkspaceConfigEntry
from et.jira import JiraIssue
from et.jira_time import JiraLogTimeError, LogTimeResult
from et.task import TaskCompleteResult, TaskCreateResult, TaskError
from et.tracker import TrackerError

runner = CliRunner()


def test_hyperlink_wraps_text_in_osc8_escape_codes_on_a_tty():
    with patch("sys.stdout.isatty", return_value=True):
        assert _hyperlink("PROJ-1", "https://example.atlassian.net/browse/PROJ-1") == (
            "\x1b]8;;https://example.atlassian.net/browse/PROJ-1\x1b\\PROJ-1\x1b]8;;\x1b\\"
        )


def test_hyperlink_returns_plain_text_when_not_a_tty():
    with patch("sys.stdout.isatty", return_value=False):
        assert _hyperlink("PROJ-1", "https://example.atlassian.net/browse/PROJ-1") == "PROJ-1"


def _config(workspaces: list[WorkspaceConfigEntry] | None = None) -> EtConfig:
    return EtConfig(
        jira=JiraConfig(
            base_url="https://example.atlassian.net/",
            email="me@example.com",
            pat="token",
            jql="assignee = currentUser()",
        ),
        workspaces=workspaces or [],
        max_workspaces=10,
    )


@patch("et.cli.load_timers")
@patch("et.cli.get_active_workspace_index")
@patch("et.cli.load_config")
def test_bare_invocation_shows_jira_info_and_time_spent_for_non_static_workspace(
    mock_load_config, mock_index, mock_load_timers
):
    mock_index.return_value = 1
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="misc"),
            WorkspaceConfigEntry(
                name="Fix login timeout",
                ref="jira:PROJ-1",
                description="Fix login timeout on mobile clients",
            ),
        ]
    )
    mock_load_timers.return_value = []

    with patch("sys.stdout.isatty", return_value=False):
        result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert "Workspace 2: Fix login timeout" in result.stdout
    assert "Fix login timeout on mobile clients" in result.stdout
    assert "jira:PROJ-1" in result.stdout
    assert "https://example.atlassian.net/browse/PROJ-1" not in result.stdout  # only in tty link
    assert "No tracker for this workspace." in result.stdout


@patch("et.cli._hyperlink", side_effect=lambda text, url: f"<{url}|{text}>")
@patch("et.cli.load_timers", return_value=[])
@patch("et.cli.get_active_workspace_index")
@patch("et.cli.load_config")
def test_bare_invocation_uses_hyperlink_helper_with_jira_browse_url(
    mock_load_config, mock_index, _mock_load_timers, mock_hyperlink
):
    mock_index.return_value = 0
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(
                name="Fix login timeout",
                ref="jira:PROJ-1",
                description="Fix login timeout on mobile clients",
            ),
        ]
    )

    result = runner.invoke(app, [])

    mock_hyperlink.assert_called_once_with(
        "jira:PROJ-1", "https://example.atlassian.net/browse/PROJ-1"
    )
    assert "<https://example.atlassian.net/browse/PROJ-1|jira:PROJ-1>" in result.stdout


@patch("et.cli.load_timers", return_value=[])
@patch("et.cli.get_active_workspace_index")
@patch("et.cli.load_config")
def test_bare_invocation_reports_no_jira_issue_when_workspace_has_no_ref(
    mock_load_config, mock_index, _mock_load_timers
):
    mock_index.return_value = 0
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="misc")])

    result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert "No Jira issue linked to this workspace." in result.stdout


@patch("et.cli.get_active_workspace_index")
@patch("et.cli.load_config")
def test_bare_invocation_shows_help_when_workspace_is_static(mock_load_config, mock_index):
    mock_index.return_value = 0
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="mails", type="static")])

    result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert "Interact with GNOME/Ubuntu workspaces." in result.stdout


@patch("et.cli.get_active_workspace_index")
@patch("et.cli.load_config")
def test_bare_invocation_shows_help_when_workspace_index_beyond_config_list(
    mock_load_config, mock_index
):
    mock_index.return_value = 5
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="misc")])

    result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert "Interact with GNOME/Ubuntu workspaces." in result.stdout


@patch("et.cli.get_active_workspace_index")
def test_bare_invocation_shows_help_when_active_workspace_lookup_fails(mock_index):
    from et.workspaces import WorkspaceError

    mock_index.side_effect = WorkspaceError("wmctrl not found")

    result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert "Interact with GNOME/Ubuntu workspaces." in result.stdout


@patch("et.cli.load_timers", side_effect=TrackerError("no schema"))
@patch("et.cli.get_active_workspace_index")
@patch("et.cli.load_config")
def test_bare_invocation_reports_tracker_error(mock_load_config, mock_index, _mock_load_timers):
    mock_index.return_value = 0
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="misc")])

    result = runner.invoke(app, [])

    assert result.exit_code == 1
    assert "Error: no schema" in result.output


@patch("et.cli.load_timers", return_value=[])
@patch("et.cli.get_active_workspace_index")
@patch("et.cli.load_config")
def test_info_command_shows_jira_info_and_time_spent_for_non_static_workspace(
    mock_load_config, mock_index, _mock_load_timers
):
    mock_index.return_value = 0
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(
                name="Fix login timeout",
                ref="jira:PROJ-1",
                description="Fix login timeout on mobile clients",
            ),
        ]
    )

    with patch("sys.stdout.isatty", return_value=False):
        result = runner.invoke(app, ["info"])

    assert result.exit_code == 0
    assert "Workspace 1: Fix login timeout" in result.stdout
    assert "jira:PROJ-1" in result.stdout


@patch("et.cli.get_active_workspace_index")
@patch("et.cli.load_config")
def test_info_command_shows_full_app_help_when_workspace_is_static(mock_load_config, mock_index):
    mock_index.return_value = 0
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="mails", type="static")])

    result = runner.invoke(app, ["info"])

    assert result.exit_code == 0
    # Falls back to the top-level app help, not just the `info` subcommand's own usage.
    assert "Interact with GNOME/Ubuntu workspaces." in result.stdout


@patch("et.cli.delete_active_workspace")
def test_ws_delete_reports_summary(mock_delete):
    from et.ws import WsDeleteResult

    mock_delete.return_value = WsDeleteResult(workspace_index=1, remaining_workspaces=4)

    result = runner.invoke(app, ["ws", "delete"])

    assert result.exit_code == 0
    assert "Deleted workspace 2" in result.stdout
    assert "Now managing 4 workspaces" in result.stdout


@patch("et.cli.delete_active_workspace")
def test_ws_delete_reports_error(mock_delete):
    from et.ws import WsDeleteError

    mock_delete.side_effect = WsDeleteError("workspace 1 is linked to ISD-1; complete it first")

    result = runner.invoke(app, ["ws", "delete"])

    assert result.exit_code == 1
    assert "Error: workspace 1 is linked to ISD-1; complete it first" in result.output


@patch("et.cli.delete_active_workspace")
def test_ws_delete_force_passes_flag_through(mock_delete):
    from et.ws import WsDeleteResult

    mock_delete.return_value = WsDeleteResult(workspace_index=0, remaining_workspaces=1)

    result = runner.invoke(app, ["ws", "delete", "--force"])

    assert result.exit_code == 0
    mock_delete.assert_called_once_with(force=True)
    assert "Deleted workspace 1" in result.stdout


# --- et jira -----------------------------------------------------------------


@patch("et.cli.create_task_from_jira")
def test_jira_start_lists_issues_and_creates_from_selection(mock_create_from_jira):
    mock_create_from_jira.return_value = TaskCreateResult(
        workspace_index=2, name="ISD-2", ref="jira:ISD-2", timer_created=True, window_moved=True
    )

    def fake_create_from_jira(select_issue, confirm_transition):
        del confirm_transition
        issues = [
            JiraIssue(key="ISD-2", summary="Second issue", priority="High", status="In Progress")
        ]
        with patch("sys.stdout.isatty", return_value=False):
            select_issue(issues)
        return mock_create_from_jira.return_value

    mock_create_from_jira.side_effect = fake_create_from_jira

    result = runner.invoke(app, ["jira", "start"], input="1\n")

    assert result.exit_code == 0
    assert "Active issues not yet linked to a workspace:" in result.stdout
    assert "ISD-2" in result.stdout
    assert "Created workspace 3: 'ISD-2' (linked to ISD-2)" in result.stdout
    assert "Note: could not move this terminal window" not in result.stdout


@patch("et.cli.create_task_from_jira")
def test_jira_start_notes_when_window_could_not_be_moved(mock_create_from_jira):
    mock_create_from_jira.return_value = TaskCreateResult(
        workspace_index=2, name="ISD-2", ref="jira:ISD-2", timer_created=True, window_moved=False
    )

    def fake_create_from_jira(select_issue, confirm_transition):
        del confirm_transition
        issues = [
            JiraIssue(key="ISD-2", summary="Second issue", priority="High", status="In Progress")
        ]
        with patch("sys.stdout.isatty", return_value=False):
            select_issue(issues)
        return mock_create_from_jira.return_value

    mock_create_from_jira.side_effect = fake_create_from_jira

    result = runner.invoke(app, ["jira", "start"], input="1\n")

    assert result.exit_code == 0
    assert "Note: could not move this terminal window to the new workspace" in result.stdout


@patch("et.cli.create_task_from_jira")
def test_jira_start_cancelled_returns_none(mock_create_from_jira):
    mock_create_from_jira.return_value = None

    result = runner.invoke(app, ["jira", "start"])

    assert result.exit_code == 0
    assert "Cancelled." in result.stdout


@patch("et.cli.create_task_from_jira")
def test_jira_start_reports_error(mock_create_from_jira):
    mock_create_from_jira.side_effect = TaskError("no 'jira' block found in the config file")

    result = runner.invoke(app, ["jira", "start"])

    assert result.exit_code == 1
    assert "Error: no 'jira' block found" in result.output


@patch("et.cli.create_task_from_jira")
def test_jira_start_prompts_to_move_issue_to_in_progress_when_confirmed(mock_create_from_jira):
    captured = {}

    def fake_create_from_jira(select_issue, confirm_transition):
        del select_issue
        issue = JiraIssue(key="ISD-2", summary="Second issue", priority="High", status="To Do")
        captured["confirmed"] = confirm_transition(issue)
        return None

    mock_create_from_jira.side_effect = fake_create_from_jira

    result = runner.invoke(app, ["jira", "start"], input="y\n")

    assert result.exit_code == 0
    assert "ISD-2 is currently 'To Do'. Move it to 'In Progress'?" in result.stdout
    assert captured["confirmed"] is True


@patch("et.cli.create_task_from_jira")
def test_jira_start_does_not_transition_when_declined(mock_create_from_jira):
    captured = {}

    def fake_create_from_jira(select_issue, confirm_transition):
        del select_issue
        issue = JiraIssue(key="ISD-2", summary="Second issue", priority="High", status="To Do")
        captured["confirmed"] = confirm_transition(issue)
        return None

    mock_create_from_jira.side_effect = fake_create_from_jira

    result = runner.invoke(app, ["jira", "start"], input="n\n")

    assert result.exit_code == 0
    assert captured["confirmed"] is False


@patch("et.cli.log_time_for_current_workspace")
def test_jira_log_time_logs_and_reports_duration(mock_log_time):
    mock_log_time.return_value = LogTimeResult(
        workspace_index=1, issue_key="ISD-321", seconds_logged=4320, tracker_reset=True
    )

    result = runner.invoke(app, ["jira", "log-time", "--comment", "note"])

    assert result.exit_code == 0
    assert "Logged 1h 12m 0s to jira:ISD-321 (workspace 2)" in result.stdout
    mock_log_time.assert_called_once_with(description="note", reset=True)


@patch("et.cli.complete_task_for_current_workspace")
def test_jira_complete_logs_time_and_frees_workspace(mock_complete):
    mock_complete.return_value = TaskCompleteResult(
        log_result=LogTimeResult(
            workspace_index=1, issue_key="ISD-321", seconds_logged=780, tracker_reset=True
        )
    )

    result = runner.invoke(app, ["jira", "complete", "--comment", "wrapping up"])

    assert result.exit_code == 0
    mock_complete.assert_called_once_with(comment="wrapping up")
    assert "Logged 0h 13m 0s to jira:ISD-321 (workspace 2)" in result.stdout
    assert "Freed workspace 2" in result.stdout


@patch("et.cli.complete_task_for_current_workspace")
def test_jira_complete_reports_error(mock_complete):
    mock_complete.side_effect = JiraLogTimeError("no Jira issue linked to workspace 1")

    result = runner.invoke(app, ["jira", "complete"])

    assert result.exit_code == 1
    assert "Error: no Jira issue linked to workspace 1" in result.output

