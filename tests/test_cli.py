"""Tests for the `et` CLI, focused on presentation behaviour not covered by unit tests
of the underlying config/tracker/workspace/jira modules."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from et.cli import _hyperlink, app
from et.config import EtConfig, JiraConfig
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


def _config() -> EtConfig:
    return EtConfig(
        jira=JiraConfig(
            base_url="https://example.atlassian.net/",
            email="me@example.com",
            pat="token",
            jql="assignee = currentUser()",
        ),
        workspaces=[],
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
