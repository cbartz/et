"""Small shared helpers for Jira refs and bare workspace entries.

`jira_key_from_ref`/`truncate_summary`/`default_entry` are used across
`et.task`, `et.ws`, `et.jira_time`, and `et.cli` to translate between a
workspace config entry's `ref` and a Jira issue key, to shorten an issue
summary into a workspace name, and to build the bare "ET-<n>" placeholder
entry a freed slot resets to. Pure functions with no I/O.
"""

from __future__ import annotations

from et.config import WorkspaceConfigEntry

JIRA_REF_PREFIX = "jira:"
TRUNCATED_NAME_LENGTH = 20


def truncate_summary(summary: str) -> str:
    """Truncate `summary` to `TRUNCATED_NAME_LENGTH` characters (hard cut)."""
    return summary[:TRUNCATED_NAME_LENGTH].rstrip()


def jira_key_from_ref(ref: str | None) -> str | None:
    """Return the Jira issue key from a "jira:<KEY>" ref, or None if not a Jira ref."""
    if ref is None or not ref.startswith(JIRA_REF_PREFIX):
        return None
    return ref[len(JIRA_REF_PREFIX):]


def default_entry(slot: int, workspace_type: str) -> WorkspaceConfigEntry:
    """Build the bare "ET-<n>" placeholder entry for `slot`."""
    return WorkspaceConfigEntry(name=f"ET-{slot + 1}", type=workspace_type)
