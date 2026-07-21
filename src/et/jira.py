"""Client for Jira Cloud's REST API, used to fetch the user's active issues
and (for `et jira log-time`) to log work against an issue.

Talks to Jira Cloud's `/rest/api/3/search/jql` endpoint (the old
`/rest/api/3/search` endpoint was retired by Atlassian and now returns HTTP
410) using HTTP Basic auth (email + API token — Jira Cloud has no
bearer-PAT mode). The new endpoint paginates via a `nextPageToken` cursor
rather than `startAt`/`total`, so all pages are fetched and concatenated.
Has no Typer/CLI dependency; the HTTP call goes through `requests` so it
can be unit tested by mocking `requests.get`/`requests.post`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

from et.config import JiraConfig

SEARCH_PATH = "rest/api/3/search/jql"
WORKLOG_PATH_TEMPLATE = "rest/api/3/issue/{key}/worklog"

logger = logging.getLogger(__name__)


class JiraError(RuntimeError):
    """Raised when the Jira API cannot be reached or returns an error."""


@dataclass(frozen=True)
class JiraIssue:
    """One issue fetched from Jira."""

    key: str
    summary: str
    priority: str


def _text_comment(text: str) -> dict[str, object]:
    """Wrap plain text in the minimal Atlassian Document Format Jira expects for comments."""
    return {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }


def create_worklog(
    jira_config: JiraConfig, issue_key: str, seconds: int, comment: str | None = None
) -> dict[str, object]:
    """Log `seconds` of work against `issue_key` via Jira's own worklog API.

    This is Jira's native worklog feature (`POST
    /rest/api/3/issue/{key}/worklog`), not Tempo's — but worklogs created
    this way still show up in Tempo timesheets when Tempo is configured to
    sync native Jira worklogs, which avoids needing a separate Tempo API
    token. Returns the created worklog's raw JSON. Raises `JiraError` if the
    request cannot be made or Jira rejects it.
    """
    url = jira_config.base_url.rstrip("/") + "/" + WORKLOG_PATH_TEMPLATE.format(key=issue_key)
    payload: dict[str, object] = {"timeSpentSeconds": seconds}
    if comment:
        payload["comment"] = _text_comment(comment)

    try:
        response = requests.post(
            url,
            json=payload,
            auth=(jira_config.email, jira_config.pat),
            timeout=30,
        )
    except requests.RequestException as exc:
        raise JiraError(f"could not reach Jira at {url}: {exc}") from exc

    if response.status_code not in (200, 201):
        raise JiraError(
            f"Jira API request to {url} failed with status {response.status_code}: "
            f"{response.text.strip()[:500]}"
        )

    try:
        payload_response = response.json()
    except ValueError as exc:
        raise JiraError(f"could not parse Jira API response as JSON: {exc}") from exc

    if not isinstance(payload_response, dict):
        raise JiraError(f"unexpected Jira API response from {url}: not a JSON object")

    return payload_response


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
        key = raw_issue.get("key")
        if not isinstance(key, str) or not key:
            logger.warning("skipping Jira issue with missing or invalid 'key': %r", raw_issue)
            continue
        fields = raw_issue.get("fields", {})
        priority_field = fields.get("priority") or {}
        issues.append(
            JiraIssue(
                key=key,
                summary=fields.get("summary") or "",
                priority=priority_field.get("name") or "",
            )
        )

    rank = {name: index for index, name in enumerate(jira_config.priority_order)}
    unranked = len(jira_config.priority_order)

    for issue in issues:
        if issue.priority not in rank:
            logger.warning(
                "issue %s has unknown priority '%s', treating it as lowest priority",
                issue.key,
                issue.priority,
            )

    indexed = list(enumerate(issues))
    indexed.sort(key=lambda pair: (rank.get(pair[1].priority, unranked), pair[0]))
    return [issue for _, issue in indexed]
