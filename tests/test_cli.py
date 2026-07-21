"""Tests for the `et` CLI, focused on presentation behaviour not covered by unit tests
of the underlying config/tracker/workspace/jira modules."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from et.cli import _hyperlink, app
from et.config import EtConfig, JiraConfig, WorkspaceConfigEntry
from et.jira import JiraIssue
from et.jira_sync import JiraSyncResult
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


@patch("et.cli.reset_tracker_for_current_workspace", return_value=(0, "ET-1"))
def test_tracker_reset_current_workspace_reports_reset_timer(mock_reset):
    result = runner.invoke(app, ["tracker", "reset"])

    assert result.exit_code == 0
    assert "Reset tracker 'ET-1' to 0" in result.stdout


@patch("et.cli.reset_tracker_for_current_workspace", return_value=(2, None))
def test_tracker_reset_current_workspace_reports_when_no_timer_bound(mock_reset):
    result = runner.invoke(app, ["tracker", "reset"])

    assert result.exit_code == 0
    assert "No ET-<n> tracker bound to workspace 3 to reset" in result.stdout


@patch("et.cli.reset_all_trackers", return_value=["ET-1", "ET-2"])
def test_tracker_reset_all_reports_each_reset_timer(mock_reset):
    result = runner.invoke(app, ["tracker", "reset", "--all"])

    assert result.exit_code == 0
    assert "Reset tracker 'ET-1' to 0" in result.stdout
    assert "Reset tracker 'ET-2' to 0" in result.stdout


@patch("et.cli.dump_tracker_for_current_workspace")
def test_tracker_dump_current_workspace_reports_written_path(mock_dump, tmp_path):
    path = tmp_path / "ET-1.txt"
    mock_dump.return_value = (0, path, "1h 2m 5s")

    result = runner.invoke(app, ["tracker", "dump"])

    assert result.exit_code == 0
    assert "ET-1: 1h 2m 5s" in result.stdout
    assert f"Wrote {path}" in result.stdout


@patch("et.cli.dump_tracker_for_current_workspace", return_value=(4, None, None))
def test_tracker_dump_current_workspace_reports_when_no_timer_bound(mock_dump):
    result = runner.invoke(app, ["tracker", "dump"])

    assert result.exit_code == 0
    assert "No ET-<n> tracker bound to workspace 5 to dump" in result.stdout


@patch("et.cli.load_config")
@patch("et.cli.sync_jira_workspaces")
def test_jira_get_reports_kept_workspaces(mock_sync, mock_load_config):
    mock_load_config.return_value = _config()
    mock_sync.return_value = JiraSyncResult(
        assigned=[], moved=[], kept=[(1, "PROJ-9")], deleted=[], skipped=[]
    )

    result = runner.invoke(app, ["jira", "get"], input="y\n")

    assert result.exit_code == 0
    assert "Kept workspace 2 on jira:PROJ-9" in result.stdout


@patch("et.cli.load_config")
@patch("et.cli.sync_jira_workspaces")
def test_jira_get_annotates_each_issue_with_its_workspace_action(mock_sync, mock_load_config):
    config = _config(
        workspaces=[
            WorkspaceConfigEntry(name="Keep", type="dynamic", ref="jira:KEEP"),
            WorkspaceConfigEntry(name="Move", type="dynamic", ref="jira:MOVE"),
        ]
    )
    mock_load_config.return_value = config
    issues = [
        JiraIssue(key="KEEP", summary="Keep summary", priority="High"),
        JiraIssue(key="NEW", summary="New summary", priority="Medium"),
        JiraIssue(key="MOVE", summary="Move summary", priority="Low"),
    ]

    def fake_sync(confirm_plan, confirm_delete):
        confirm_plan(issues)
        return _empty_result()

    mock_sync.side_effect = fake_sync

    result = runner.invoke(app, ["jira", "get"], input="n\n")

    assert "KEEP [High] Keep summary  (ws unchanged (1))" in result.stdout
    assert "NEW [Medium] New summary  (ws created (2))" in result.stdout
    assert "MOVE [Low] Move summary  (ws move (2 -> 3))" in result.stdout


@patch("et.cli.log_time_for_current_workspace")
def test_jira_log_time_reports_logged_duration_and_reset(mock_log_time):
    mock_log_time.return_value = LogTimeResult(
        workspace_index=1, issue_key="ISD-321", seconds_logged=4320, tracker_reset=True
    )

    result = runner.invoke(app, ["jira", "log-time"])

    assert result.exit_code == 0
    assert "Logged 1h 12m 0s to jira:ISD-321 (workspace 2)" in result.stdout
    assert "Reset tracker to 0" in result.stdout
    mock_log_time.assert_called_once_with(description=None, reset=True)


@patch("et.cli.log_time_for_current_workspace")
def test_jira_log_time_passes_comment_and_no_reset_flags(mock_log_time):
    mock_log_time.return_value = LogTimeResult(
        workspace_index=0, issue_key="ISD-321", seconds_logged=60, tracker_reset=False
    )

    result = runner.invoke(
        app, ["jira", "log-time", "--comment", "Investigating", "--no-reset"]
    )

    assert result.exit_code == 0
    assert "Logged 0h 1m 0s to jira:ISD-321 (workspace 1)" in result.stdout
    assert "Reset tracker to 0" not in result.stdout
    mock_log_time.assert_called_once_with(description="Investigating", reset=False)


@patch("et.cli.log_time_for_current_workspace")
def test_jira_log_time_reports_error(mock_log_time):
    mock_log_time.side_effect = JiraLogTimeError("no Jira issue linked to workspace 1")

    result = runner.invoke(app, ["jira", "log-time"])

    assert result.exit_code == 1
    assert "Error: no Jira issue linked to workspace 1" in result.output


# --- et task -----------------------------------------------------------------


@patch("et.cli.load_timers")
@patch("et.cli.get_active_workspace_index")
@patch("et.cli.load_config")
def test_task_info_delegates_to_ws_info(mock_load_config, mock_index, mock_load_timers):
    mock_index.return_value = 0
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="misc")])
    mock_load_timers.return_value = []

    result = runner.invoke(app, ["task", "info"])

    assert result.exit_code == 0
    assert "No Jira issue linked to this workspace." in result.stdout
    assert "No tracker for this workspace." in result.stdout


@patch("et.cli.load_timers")
@patch("et.cli.get_active_workspace_index")
@patch("et.cli.load_config")
def test_task_info_shows_elapsed_time_for_bound_tracker(
    mock_load_config, mock_index, mock_load_timers
):
    mock_index.return_value = 1
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="misc"),
            WorkspaceConfigEntry(
                name="ISD-321", ref="jira:ISD-321", description="Fix the thing"
            ),
        ]
    )
    mock_load_timers.return_value = [
        {
            "id": "t1",
            "name": "ET-2",
            "timeElapsed": 4320,
            "running": True,
            "selected": False,
            "workspaceId": 1,
        }
    ]

    with patch("sys.stdout.isatty", return_value=False):
        result = runner.invoke(app, ["task", "info"])

    assert result.exit_code == 0
    assert "jira:ISD-321" in result.stdout
    assert "Time spent: 1h 12m 0s (running)" in result.stdout


@patch("et.cli.load_timers", side_effect=TrackerError("no schema"))
@patch("et.cli.get_active_workspace_index")
@patch("et.cli.load_config")
def test_task_info_reports_tracker_error(mock_load_config, mock_index, _mock_load_timers):
    mock_index.return_value = 0
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="misc")])

    result = runner.invoke(app, ["task", "info"])

    assert result.exit_code == 1
    assert "Error: no schema" in result.output


@patch("et.cli.create_task_workspace")
def test_task_create_with_name_argument_skips_prompts(mock_create):
    mock_create.return_value = TaskCreateResult(
        workspace_index=1, name="my-task", ref=None, timer_created=True
    )

    result = runner.invoke(app, ["task", "create", "my-task", "--description", "doing stuff"])

    assert result.exit_code == 0
    mock_create.assert_called_once_with("my-task", description="doing stuff")
    assert "Created workspace 2: 'my-task'" in result.stdout
    assert "Switched to workspace 2" in result.stdout


@patch("et.cli.create_task_workspace")
def test_task_create_prompts_for_name_and_description_when_omitted(mock_create):
    mock_create.return_value = TaskCreateResult(
        workspace_index=0, name="typed-name", ref=None, timer_created=True
    )

    result = runner.invoke(app, ["task", "create"], input="typed-name\ntyped-description\n")

    assert result.exit_code == 0
    mock_create.assert_called_once_with("typed-name", description="typed-description")


@patch("et.cli.create_task_workspace")
def test_task_create_reports_error(mock_create):
    mock_create.side_effect = TaskError("no free workspace slot available")

    result = runner.invoke(app, ["task", "create", "my-task", "--description", "d"])

    assert result.exit_code == 1
    assert "Error: no free workspace slot available" in result.output


def test_task_create_rejects_name_together_with_from_jira():
    result = runner.invoke(app, ["task", "create", "my-task", "--from-jira"])

    assert result.exit_code == 1
    assert "must not be given together with --from-jira" in result.output


@patch("et.cli.create_task_from_jira")
@patch("et.cli.load_config")
def test_task_create_from_jira_lists_issues_and_creates_from_selection(
    mock_load_config, mock_create_from_jira
):
    mock_load_config.return_value = _config()
    mock_create_from_jira.return_value = TaskCreateResult(
        workspace_index=2, name="ISD-2", ref="jira:ISD-2", timer_created=True
    )

    captured_select_issue = {}

    def fake_create_from_jira(select_issue):
        captured_select_issue["fn"] = select_issue
        issues = [JiraIssue(key="ISD-2", summary="Second issue", priority="High")]
        with patch("sys.stdout.isatty", return_value=False):
            select_issue(issues)
        return mock_create_from_jira.return_value

    mock_create_from_jira.side_effect = fake_create_from_jira

    result = runner.invoke(app, ["task", "create", "--from-jira"], input="1\n")

    assert result.exit_code == 0
    assert "Active issues not yet linked to a workspace:" in result.stdout
    assert "ISD-2" in result.stdout
    assert "Created workspace 3: 'ISD-2' (linked to ISD-2)" in result.stdout


@patch("et.cli.create_task_from_jira")
def test_task_create_from_jira_cancelled_returns_none(mock_create_from_jira):
    mock_create_from_jira.return_value = None

    result = runner.invoke(app, ["task", "create", "--from-jira"])

    assert result.exit_code == 0
    assert "Cancelled." in result.stdout


@patch("et.cli.create_task_from_jira")
def test_task_create_from_jira_reports_error(mock_create_from_jira):
    mock_create_from_jira.side_effect = TaskError("no 'jira' block found in the config file")

    result = runner.invoke(app, ["task", "create", "--from-jira"])

    assert result.exit_code == 1
    assert "Error: no 'jira' block found" in result.output


@patch("et.cli.log_time_for_current_workspace")
def test_task_log_time_delegates_to_jira_log_time(mock_log_time):
    mock_log_time.return_value = LogTimeResult(
        workspace_index=1, issue_key="ISD-321", seconds_logged=4320, tracker_reset=True
    )

    result = runner.invoke(app, ["task", "log-time", "--comment", "note"])

    assert result.exit_code == 0
    assert "Logged 1h 12m 0s to jira:ISD-321 (workspace 2)" in result.stdout
    mock_log_time.assert_called_once_with(description="note", reset=True)


@patch("et.cli.complete_task_for_current_workspace")
def test_task_complete_logs_time_and_frees_workspace(mock_complete):
    mock_complete.return_value = TaskCompleteResult(
        log_result=LogTimeResult(
            workspace_index=1, issue_key="ISD-321", seconds_logged=780, tracker_reset=True
        )
    )

    result = runner.invoke(app, ["task", "complete", "--comment", "wrapping up"])

    assert result.exit_code == 0
    mock_complete.assert_called_once_with(comment="wrapping up")
    assert "Logged 0h 13m 0s to jira:ISD-321 (workspace 2)" in result.stdout
    assert "Freed workspace 2" in result.stdout


@patch("et.cli.complete_task_for_current_workspace")
def test_task_complete_reports_error(mock_complete):
    mock_complete.side_effect = JiraLogTimeError("no Jira issue linked to workspace 1")

    result = runner.invoke(app, ["task", "complete"])

    assert result.exit_code == 1
    assert "Error: no Jira issue linked to workspace 1" in result.output
