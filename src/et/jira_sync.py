"""Orchestrates `et jira get`: reconcile active Jira issues with GNOME
workspaces and Tracker timers.

`plan_reshuffle` and `apply_timer_changes` are pure functions with no I/O,
so their slot-assignment math is directly unit-testable. `sync_jira_workspaces`
(added in a later change to this file) is the only function here that
touches the filesystem, GNOME, or the Jira API.
"""

from __future__ import annotations

from dataclasses import dataclass

from et.config import WorkspaceConfigEntry
from et.jira import JiraIssue
from et.tracker import TimerEntry, build_new_timer, find_timer_for_workspace

JIRA_REF_PREFIX = "jira:"
TRUNCATED_NAME_LENGTH = 20


@dataclass(frozen=True)
class ReshuffleOutcome:
    """Result of `plan_reshuffle`: the new workspace list plus a change report."""

    workspaces: list[WorkspaceConfigEntry]
    kept: list[tuple[int, str]]
    assigned: list[tuple[int, str]]
    moved: list[tuple[str, int, int]]
    skipped: list[str]


def truncate_summary(summary: str) -> str:
    """Truncate `summary` to `TRUNCATED_NAME_LENGTH` characters (hard cut)."""
    return summary[:TRUNCATED_NAME_LENGTH].rstrip()


def jira_key_from_ref(ref: str | None) -> str | None:
    """Return the Jira issue key from a "jira:<KEY>" ref, or None if not a Jira ref."""
    if ref is None or not ref.startswith(JIRA_REF_PREFIX):
        return None
    return ref[len(JIRA_REF_PREFIX):]


def plan_reshuffle(
    workspaces_list: list[WorkspaceConfigEntry], issues: list[JiraIssue]
) -> ReshuffleOutcome:
    """Compute the new workspace arrangement for `issues` (pure, no I/O).

    Preconditions (the caller — `sync_jira_workspaces` — is responsible for
    these): every `ref` remaining in `workspaces_list` points to a still-
    active issue (inactive refs must already be cleaned up before calling
    this), and `workspaces_list` already has as many entries as needed
    (growth must already have happened) — this function only rearranges the
    given list, it never appends to it.

    Non-static slots are filled with `issues` in priority order (index 0 =
    highest priority), starting from the lowest slot index. If an issue is
    already assigned to a *different* non-static slot, it's reported as
    `moved`; brand-new issues are `assigned`; unchanged assignments are
    `kept`.

    If there are more issues than non-static slots, the lowest-priority
    excess issues are `skipped` — except an issue that was already tracked
    in a slot and gets bumped out purely by a capacity crunch: rather than
    dropping its tracking, it's left exactly in its current slot (also
    reported in `kept`, not `skipped` or `moved`), so a still-active issue's
    running timer is never silently reset just because higher-priority
    issues appeared. This "kept in place" set is computed via a small
    fixed-point loop below, since removing one bumped issue's slot from the
    placement pool can itself reduce capacity enough to bump another.
    """
    non_static_slots = [
        slot for slot, entry in enumerate(workspaces_list) if entry.type != "static"
    ]
    total_slots = len(non_static_slots)

    key_to_old_slot: dict[str, int] = {}
    for slot in non_static_slots:
        key = jira_key_from_ref(workspaces_list[slot].ref)
        if key is not None:
            key_to_old_slot[key] = slot

    capacity = total_slots
    issues_to_place = issues[:capacity]
    kept_in_place_slots: set[int] = set()
    while True:
        to_place_keys = {candidate.key for candidate in issues_to_place}
        kept_in_place_slots = {
            slot for key, slot in key_to_old_slot.items() if key not in to_place_keys
        }
        new_capacity = total_slots - len(kept_in_place_slots)
        if new_capacity == capacity:
            break
        capacity = new_capacity
        issues_to_place = issues[:capacity]

    to_place_keys = {candidate.key for candidate in issues_to_place}
    kept_in_place_keys = {
        key for key, slot in key_to_old_slot.items() if slot in kept_in_place_slots
    }
    skipped = [
        candidate.key
        for candidate in issues
        if candidate.key not in to_place_keys and candidate.key not in kept_in_place_keys
    ]
    available_slots = [slot for slot in non_static_slots if slot not in kept_in_place_slots]

    new_workspaces = list(workspaces_list)
    kept: list[tuple[int, str]] = []
    for slot in sorted(kept_in_place_slots):
        key = jira_key_from_ref(workspaces_list[slot].ref)
        assert key is not None
        kept.append((slot, key))

    assigned: list[tuple[int, str]] = []
    moved: list[tuple[str, int, int]] = []
    filled_slots: set[int] = set()

    for slot, candidate in zip(available_slots, issues_to_place):
        old_slot = key_to_old_slot.get(candidate.key)
        if old_slot == slot:
            kept.append((slot, candidate.key))
            filled_slots.add(slot)
            continue

        new_workspaces[slot] = WorkspaceConfigEntry(
            name=truncate_summary(candidate.summary),
            type=workspaces_list[slot].type,
            ref=f"{JIRA_REF_PREFIX}{candidate.key}",
            description=candidate.summary,
        )
        if old_slot is not None:
            moved.append((candidate.key, old_slot, slot))
        else:
            assigned.append((slot, candidate.key))
        filled_slots.add(slot)

    for slot in available_slots:
        if slot in filled_slots or workspaces_list[slot].ref is None:
            continue
        new_workspaces[slot] = WorkspaceConfigEntry(
            name=f"ET-{slot + 1}",
            type=workspaces_list[slot].type,
            ref=None,
            description=None,
        )

    return ReshuffleOutcome(
        workspaces=new_workspaces, kept=kept, assigned=assigned, moved=moved, skipped=skipped
    )


def apply_timer_changes(
    all_timers: list[TimerEntry], outcome: ReshuffleOutcome
) -> list[TimerEntry]:
    """Apply `outcome`'s moves/assignments to a copy of `all_timers` (pure, no I/O).

    Moved timers keep their `id`/`timeElapsed`/`running`/`autoResume`, only
    `workspaceId`/`name` change (so elapsed time follows the issue to its
    new slot). Assigned (brand-new) issues get a fresh timer. Kept and
    skipped slots are untouched.

    Moves are resolved in two passes — first snapshotting each old slot's
    timer object, then mutating those captured references — because looking
    up "the timer in slot N" *after* an earlier move has already changed
    some timer's `workspaceId` can otherwise find the wrong (already-moved)
    timer when two issues swap slots.
    """
    entries = list(all_timers)

    old_slot_timers = {
        old_slot: find_timer_for_workspace(entries, old_slot) for _, old_slot, _ in outcome.moved
    }

    for _key, old_slot, new_slot in outcome.moved:
        timer = old_slot_timers[old_slot]
        if timer is None:
            entries.append(build_new_timer(new_slot, f"ET-{new_slot + 1}"))
            continue
        timer["workspaceId"] = new_slot
        timer["name"] = f"ET-{new_slot + 1}"

    for slot, _key in outcome.assigned:
        if find_timer_for_workspace(entries, slot) is None:
            entries.append(build_new_timer(slot, f"ET-{slot + 1}"))

    return entries
