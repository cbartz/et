"""Tests for et.jira, mocking requests.get."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from et.config import JiraConfig
from et.jira import JiraError, JiraIssue, fetch_active_issues


def _config(**overrides: object) -> JiraConfig:
    defaults = dict(
        base_url="https://example.atlassian.net/",
        email="me@example.com",
        pat="secret-token",
        jql="assignee = currentUser()",
    )
    defaults.update(overrides)
    return JiraConfig(**defaults)  # type: ignore[arg-type]


def _response(issues: list[dict], next_page_token: str | None = None) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    payload: dict[str, object] = {"issues": issues}
    if next_page_token is not None:
        payload["nextPageToken"] = next_page_token
    response.json.return_value = payload
    return response


def _issue(key: str, summary: str, priority: str) -> dict:
    return {"key": key, "fields": {"summary": summary, "priority": {"name": priority}}}


@patch("et.jira.requests.get")
def test_fetch_active_issues_sorts_by_decreasing_priority(mock_get):
    mock_get.return_value = _response(
        [
            _issue("PROJ-1", "Low priority task", "Low"),
            _issue("PROJ-2", "Urgent task", "Highest"),
            _issue("PROJ-3", "Medium task", "Medium"),
        ]
    )

    issues = fetch_active_issues(_config())

    assert [issue.key for issue in issues] == ["PROJ-2", "PROJ-3", "PROJ-1"]
    assert issues[0] == JiraIssue(key="PROJ-2", summary="Urgent task", priority="Highest")


@patch("et.jira.requests.get")
def test_fetch_active_issues_passes_jql_and_basic_auth(mock_get):
    mock_get.return_value = _response([])
    config = _config(base_url="https://example.atlassian.net")

    fetch_active_issues(config)

    args, kwargs = mock_get.call_args
    assert args[0] == "https://example.atlassian.net/rest/api/3/search/jql"
    assert kwargs["params"] == {"jql": config.jql, "fields": "summary,priority"}
    assert kwargs["auth"] == (config.email, config.pat)


@patch("et.jira.requests.get")
def test_fetch_active_issues_unknown_priority_sorts_last_and_warns(mock_get, capsys):
    mock_get.return_value = _response(
        [
            _issue("PROJ-1", "Custom priority task", "Blocker"),
            _issue("PROJ-2", "Known priority task", "Low"),
        ]
    )

    issues = fetch_active_issues(_config())

    assert [issue.key for issue in issues] == ["PROJ-2", "PROJ-1"]
    assert "PROJ-1" in capsys.readouterr().err


@patch("et.jira.requests.get")
def test_fetch_active_issues_raises_on_non_200_status(mock_get):
    response = MagicMock()
    response.status_code = 401
    response.text = "Unauthorized"
    mock_get.return_value = response

    with pytest.raises(JiraError, match="401"):
        fetch_active_issues(_config())


@patch("et.jira.requests.get")
def test_fetch_active_issues_follows_next_page_token_pagination(mock_get):
    mock_get.side_effect = [
        _response([_issue("PROJ-1", "First page task", "High")], next_page_token="page-2"),
        _response([_issue("PROJ-2", "Second page task", "Highest")]),
    ]

    issues = fetch_active_issues(_config())

    assert mock_get.call_count == 2
    first_kwargs = mock_get.call_args_list[0].kwargs
    second_kwargs = mock_get.call_args_list[1].kwargs
    assert "nextPageToken" not in first_kwargs["params"]
    assert second_kwargs["params"]["nextPageToken"] == "page-2"
    assert {issue.key for issue in issues} == {"PROJ-1", "PROJ-2"}


@patch("et.jira.requests.get", side_effect=requests.ConnectionError("no route to host"))
def test_fetch_active_issues_wraps_network_errors(mock_get):
    with pytest.raises(JiraError, match="no route to host"):
        fetch_active_issues(_config())


@patch("et.jira.requests.get")
def test_fetch_active_issues_respects_custom_priority_order(mock_get):
    mock_get.return_value = _response(
        [
            _issue("PROJ-1", "Task A", "High"),
            _issue("PROJ-2", "Task B", "Low"),
        ]
    )
    config = _config(priority_order=["Low", "High"])

    issues = fetch_active_issues(config)

    assert [issue.key for issue in issues] == ["PROJ-2", "PROJ-1"]
