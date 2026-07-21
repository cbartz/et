"""Tests for et.jira_ref's pure helper functions (no mocking needed)."""

from __future__ import annotations

from et.jira_ref import default_entry, jira_key_from_ref, truncate_summary


def test_truncate_summary_hard_cuts_at_30_chars_and_rstrips():
    assert (
        truncate_summary("Fix login timeout on mobile clients") == "Fix login timeout on mobile cl"
    )
    assert truncate_summary("Short") == "Short"
    assert truncate_summary("Exactly thirty characters here") == "Exactly thirty characters here"
    assert truncate_summary("Trailing space trimmed off    tail") == "Trailing space trimmed off"


def test_jira_key_from_ref_extracts_key_or_returns_none():
    assert jira_key_from_ref("jira:PROJ-123") == "PROJ-123"
    assert jira_key_from_ref(None) is None
    assert jira_key_from_ref("something-else") is None


def test_default_entry_builds_bare_placeholder():
    entry = default_entry(2, "dynamic")
    assert entry.name == "ET-3"
    assert entry.type == "dynamic"
    assert entry.ref is None
    assert entry.description is None

    assert default_entry(0, "static").type == "static"
