"""Client for Jira Cloud's REST API, used to fetch the user's active issues.

Talks to Jira Cloud's `/rest/api/3/search` endpoint using HTTP Basic auth
(email + API token — Jira Cloud has no bearer-PAT mode). Has no Typer/CLI
dependency; the HTTP call goes through `requests` so it can be unit tested
by mocking `requests.get`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import requests

from et.config import JiraConfig

SEARCH_PATH = "rest/api/3/search"


class JiraError(RuntimeError):
    """Raised when the Jira API cannot be reached or returns an error."""


@dataclass(frozen=True)
class JiraIssue:
    """One issue fetched from Jira."""

    key: str
    summary: str
    priority: str


def fetch_active_issues(jira_config: JiraConfig) -> list[JiraIssue]:
    """Fetch issues matching `jira_config.jql`, sorted by decreasing priority.

    Sorting uses `jira_config.priority_order` (highest priority first, ties
    broken by the order the API returned them in); issues whose priority
    name isn't in that list sort after all known priorities, with a warning
    printed to stderr for each one.
    """
    url = jira_config.base_url.rstrip("/") + "/" + SEARCH_PATH
    try:
        response = requests.get(
            url,
            params={"jql": jira_config.jql, "fields": "summary,priority"},
            auth=(jira_config.email, jira_config.pat),
            timeout=30,
        )
    except requests.RequestException as exc:
        raise JiraError(f"could not reach Jira at {url}: {exc}") from exc

    if response.status_code != 200:
        raise JiraError(
            f"Jira API request to {url} failed with status {response.status_code}: "
            f"{response.text.strip()[:500]}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise JiraError(f"could not parse Jira API response as JSON: {exc}") from exc

    issues_raw = payload.get("issues") if isinstance(payload, dict) else None
    if not isinstance(issues_raw, list):
        raise JiraError(f"unexpected Jira API response from {url}: no 'issues' list")

    issues: list[JiraIssue] = []
    for raw_issue in issues_raw:
        fields = raw_issue.get("fields", {}) if isinstance(raw_issue, dict) else {}
        priority_field = fields.get("priority") or {}
        issues.append(
            JiraIssue(
                key=raw_issue["key"],
                summary=fields.get("summary") or "",
                priority=priority_field.get("name") or "",
            )
        )

    rank = {name: index for index, name in enumerate(jira_config.priority_order)}
    unranked = len(jira_config.priority_order)

    for issue in issues:
        if issue.priority not in rank:
            print(
                f"Warning: issue {issue.key} has unknown priority "
                f"'{issue.priority}', treating it as lowest priority",
                file=sys.stderr,
            )

    indexed = list(enumerate(issues))
    indexed.sort(key=lambda pair: (rank.get(pair[1].priority, unranked), pair[0]))
    return [issue for _, issue in indexed]
