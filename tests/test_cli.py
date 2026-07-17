"""Tests for the `et` CLI, focused on presentation behaviour not covered by unit tests
of the underlying config/tracker/workspace/jira modules."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from et.cli import _hyperlink, app
from et.config import EtConfig, JiraConfig, WorkspaceConfigEntry
from et.jira import JiraIssue
from et.jira_sync import JiraSyncResult

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


def _empty_result() -> JiraSyncResult:
    return JiraSyncResult(assigned=[], moved=[], kept=[], deleted=[], skipped=[])


@patch("et.cli.load_config")
@patch("et.cli.sync_jira_workspaces")
def test_jira_get_prints_plain_issue_keys_when_not_a_tty(mock_sync, mock_load_config):
    mock_load_config.return_value = _config()
    issue = JiraIssue(key="PROJ-1", summary="Do the thing", priority="High")

    def fake_sync(confirm_plan, confirm_delete):
        confirm_plan([issue])
        return _empty_result()

    mock_sync.side_effect = fake_sync

    with patch("sys.stdout.isatty", return_value=False):
        result = runner.invoke(app, ["jira", "get"], input="n\n")

    assert "  PROJ-1 [High] Do the thing" in result.stdout
    assert "\x1b]8;;" not in result.stdout


@patch("et.cli._hyperlink", side_effect=lambda text, url: f"<{url}|{text}>")
@patch("et.cli.load_config")
@patch("et.cli.sync_jira_workspaces")
def test_jira_get_uses_hyperlink_helper_with_jira_browse_url(
    mock_sync, mock_load_config, mock_hyperlink
):
    mock_load_config.return_value = _config()
    issue = JiraIssue(key="PROJ-1", summary="Do the thing", priority="High")

    def fake_sync(confirm_plan, confirm_delete):
        confirm_plan([issue])
        return _empty_result()

    mock_sync.side_effect = fake_sync

    result = runner.invoke(app, ["jira", "get"], input="n\n")

    mock_hyperlink.assert_called_once_with(
        "PROJ-1", "https://example.atlassian.net/browse/PROJ-1"
    )
    assert "<https://example.atlassian.net/browse/PROJ-1|PROJ-1>" in result.stdout


@patch("et.cli.get_active_workspace_index")
@patch("et.cli.load_config")
def test_ws_info_shows_description_and_hyperlink_for_jira_linked_workspace(
    mock_load_config, mock_index
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

    with patch("sys.stdout.isatty", return_value=False):
        result = runner.invoke(app, ["ws", "info"])

    assert result.exit_code == 0
    assert "Workspace 2: Fix login timeout" in result.stdout
    assert "Fix login timeout on mobile clients" in result.stdout
    assert "jira:PROJ-1" in result.stdout
    assert "https://example.atlassian.net/browse/PROJ-1" not in result.stdout  # only in tty link


@patch("et.cli._hyperlink", side_effect=lambda text, url: f"<{url}|{text}>")
@patch("et.cli.get_active_workspace_index")
@patch("et.cli.load_config")
def test_ws_info_uses_hyperlink_helper_with_jira_browse_url(
    mock_load_config, mock_index, mock_hyperlink
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

    result = runner.invoke(app, ["ws", "info"])

    mock_hyperlink.assert_called_once_with(
        "jira:PROJ-1", "https://example.atlassian.net/browse/PROJ-1"
    )
    assert "<https://example.atlassian.net/browse/PROJ-1|jira:PROJ-1>" in result.stdout


@patch("et.cli.get_active_workspace_index")
@patch("et.cli.load_config")
def test_ws_info_reports_no_jira_issue_when_workspace_has_no_ref(mock_load_config, mock_index):
    mock_index.return_value = 0
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="misc")])

    result = runner.invoke(app, ["ws", "info"])

    assert result.exit_code == 0
    assert "No Jira issue linked to this workspace." in result.stdout


@patch("et.cli.get_active_workspace_index")
@patch("et.cli.load_config")
def test_ws_info_reports_no_jira_issue_when_workspace_index_beyond_config_list(
    mock_load_config, mock_index
):
    mock_index.return_value = 5
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="misc")])

    result = runner.invoke(app, ["ws", "info"])

    assert result.exit_code == 0
    assert "No Jira issue linked to this workspace." in result.stdout


@patch("et.cli.get_active_workspace_index")
@patch("et.cli.load_config")
def test_ws_info_reports_error_when_active_workspace_lookup_fails(mock_load_config, mock_index):
    from et.workspaces import WorkspaceError

    mock_index.side_effect = WorkspaceError("wmctrl not found")

    result = runner.invoke(app, ["ws", "info"])

    assert result.exit_code == 1
    assert "Error: wmctrl not found" in result.output
