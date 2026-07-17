"""Client for Jira Cloud's REST API, used to fetch the user's active issues.

Talks to Jira Cloud's `/rest/api/3/search/jql` endpoint (the old
`/rest/api/3/search` endpoint was retired by Atlassian and now returns HTTP
410) using HTTP Basic auth (email + API token — Jira Cloud has no
bearer-PAT mode). The new endpoint paginates via a `nextPageToken` cursor
rather than `startAt`/`total`, so all pages are fetched and concatenated.
Has no Typer/CLI dependency; the HTTP call goes through `requests` so it
can be unit tested by mocking `requests.get`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import requests

from et.config import JiraConfig

SEARCH_PATH = "rest/api/3/search/jql"


class JiraError(RuntimeError):
    """Raised when the Jira API cannot be reached or returns an error."""


@dataclass(frozen=True)
class JiraIssue:
    """One issue fetched from Jira."""

    key: str
    summary: str
    priority: str


def _fetch_issue_pages(jira_config: JiraConfig, url: str) -> list[object]:
    """Fetch every page of raw issue dicts for `jira_config.jql`, via `nextPageToken`."""
    all_issues_raw: list[object] = []
    next_page_token: str | None = None

    while True:
        params: dict[str, str] = {"jql": jira_config.jql, "fields": "summary,priority"}
        if next_page_token is not None:
            params["nextPageToken"] = next_page_token

        try:
            response = requests.get(
                url,
                params=params,
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

        all_issues_raw.extend(issues_raw)

        next_page_token = payload.get("nextPageToken") if isinstance(payload, dict) else None
        if not next_page_token or not issues_raw:
            break

    return all_issues_raw


def fetch_active_issues(jira_config: JiraConfig) -> list[JiraIssue]:
    """Fetch issues matching `jira_config.jql`, sorted by decreasing priority.

    Sorting uses `jira_config.priority_order` (highest priority first, ties
    broken by the order the API returned them in); issues whose priority
    name isn't in that list sort after all known priorities, with a warning
    printed to stderr for each one.
    """
    url = jira_config.base_url.rstrip("/") + "/" + SEARCH_PATH
    issues_raw = _fetch_issue_pages(jira_config, url)

    issues: list[JiraIssue] = []
    for raw_issue in issues_raw:
        if not isinstance(raw_issue, dict):
            continue
        fields = raw_issue.get("fields", {})
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
