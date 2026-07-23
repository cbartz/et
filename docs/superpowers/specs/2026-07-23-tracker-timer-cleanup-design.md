# Tracker timer cleanup on workspace creation

## Problem

Tracker persists all timers in a single GSettings key. Over successive runs,
et can leave stale `ET-<n>` timers behind:

- When a free workspace slot is reused for a new task, its previously-created
  `ET-<n>` timer was reused verbatim, so the new task inherited the previous
  task's accumulated `timeElapsed` (and possibly a `running` state).
- Timers bound to workspaces that no longer exist (e.g. after the workspace
  count shrank) lingered indefinitely.

## Goal

When et creates/reuses a workspace timer, guarantee it starts from zero and
clean up orphaned et-created timers.

## Design

A single entry point in `et.tracker` replaces `add_tracker_for_workspace`:

```
prepare_timer_for_workspace(index: int, workspace_count: int) -> tuple[str, bool]
```

Behaviour, in order, on the loaded timer list:

1. **Remove orphaned et timers.** Drop any timer whose name matches
   `^ET-\d+$` **and** whose `workspaceId` is out of range (`< 0` or
   `>= workspace_count`). User-named timers and the `settings` sentinel
   (which has no `name`) are never touched.
2. **Reset a reused slot.** If a timer already exists for `index`, set its
   `timeElapsed = 0` and `running = False` so a reused slot never inherits a
   previous task's time.
3. **Create if missing.** Otherwise append a fresh timer for `index`.

The disable/write/enable reload dance (`save_timers_with_reload`) runs only if
something actually changed (orphan removed, time reset, or timer created), so a
reused-but-already-zero slot triggers no needless GSettings write.

Returns `(timer_name, created)`; `created` is `False` when an existing timer
was reused (after being reset).

### Orphan definition

An `ET-<n>` timer is orphaned when its `workspaceId` is beyond the current live
GNOME workspace count. Timers with a non-int / absent `workspaceId` are left
alone.

## Call site

`et.task.create_task_workspace` passes the live workspace count:
`count + 1` when it just grew the workspace count, else `count`.

## Testing

- `test_tracker.py`: create-new, reset-on-reuse, no-op-when-already-zero,
  orphan-removal-beyond-count (with a user-named timer and the sentinel left
  intact), sentinel preservation, and error wrapping.
- `test_task.py`: updated signature/assertions for `prepare_timer_for_workspace`.

## Out of scope

- Removing in-range timers bound to empty/bare slots.
- Any change to `et ws delete` timer handling (already fixed separately).
