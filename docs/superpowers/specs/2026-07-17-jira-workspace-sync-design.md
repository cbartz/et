# Jira-Driven Workspace Sync — Design

## Summary

Add `et jira get`, a command that fetches the user's active Jira issues
(ordered by decreasing priority) and syncs them onto GNOME workspaces
managed by `et`: renaming workspaces to a truncated issue summary, storing
the full issue title as a "description" and a `jira:<key>` "ref" in
`~/.config/et/config.yaml`, and moving the corresponding Tracker timer
along when an issue's assigned workspace changes between runs. Workspaces
explicitly marked `type: static` in the config are never touched.

## Config schema evolution

`~/.config/et/config.yaml` (introduced for `et ws rename --all`) gains two
new top-level keys and two new per-workspace fields:

```yaml
max_workspaces: 10          # capacity cap for tracker add --all and jira get growth

jira:
  base_url: https://warthogs.atlassian.net/
  email: sebastien.georget@canonical.com
  pat: <your Jira API token>
  jql: "assignee = currentUser() AND statusCategory != Done"
  priority_order: [Highest, High, Medium, Low, Lowest]   # optional, this is the default

workspaces:
  - name: mails
    type: static             # excluded from `jira get` and `tracker add --all`
  - name: handson
  - name: isd-321
  - name: "Fix login timeo"       # truncated to 20 chars
    description: "Fix login timeout on mobile clients"
    ref: "jira:PROJ-123"
```

- `max_workspaces` (int, default `10` if the config file or key is absent):
  the cap used by both `et tracker add --all` (replacing its current
  hard-coded `10`) and by `et jira get`'s slot-growth logic.
- `jira.base_url`, `jira.email`, `jira.pat`: Jira Cloud REST API v3
  credentials. Basic auth (`email:pat`, base64-encoded) is what Jira Cloud
  actually expects — there is no bearer-PAT mode for Cloud, only for
  Server/Data Center, so "PAT" here just means the API token from
  `id.atlassian.com/manage-profile/security/api-tokens`.
- `jira.jql`: user-supplied JQL defining "active issues" (no default is
  synthesized — required if `jira get` is used). Ordering in the JQL
  itself is not relied upon; `et` sorts client-side (see below).
- `jira.priority_order`: ordered list of priority names from highest to
  lowest, used to sort fetched issues. Defaults to
  `[Highest, High, Medium, Low, Lowest]` if omitted. Any issue whose
  priority name isn't in this list sorts after all known priorities (in
  original API order), with a printed warning.
- Per-workspace `type` (str, optional): `"dynamic"` (default, when the key
  is absent or explicitly `"dynamic"`) or `"static"`. Only `"static"` is
  ever excluded from `jira get`/`tracker add --all` management; any other
  value is a config error.
- Per-workspace `ref` (str, optional): opaque reference string. For Jira,
  always `"jira:<ISSUE-KEY>"`. Not managed by `ws rename --all` (which
  only touches `name`).
- Per-workspace `description` (str, optional): free-form text, not applied
  to the real GNOME workspace (GNOME workspaces have no description
  concept) — it's config-only bookkeeping so `et` (and the user, reading
  the YAML) can see the full issue title behind a truncated name.

**PAT storage**: `~/.config/et/config.yaml` already lives outside the git
repository (like `~/.netrc` or `~/.aws/credentials`), so storing the token
directly in it — per your explicit choice — is acceptable for
development. `et` will `chmod 600` the file whenever it writes it, and the
docs will call out that this file must never be committed or shared.

## `et.jira` module

New module, no Typer dependency, mirroring the style of `et.tracker`:

```python
@dataclass(frozen=True)
class JiraIssue:
    key: str            # "PROJ-123"
    summary: str        # exact issue title
    priority: str       # e.g. "High"

class JiraError(RuntimeError): ...

def fetch_active_issues(jira_config: JiraConfig) -> list[JiraIssue]:
    """Query base_url/rest/api/3/search with jql via Basic auth, return
    issues sorted by priority_order (ties broken by original API order),
    unknown priorities sorted last with a warning printed to stderr."""
```

Uses `requests` (new runtime dependency — no existing HTTP client in the
project) for the API call. Non-2xx responses, network errors, and missing
`jira` config all raise `JiraError` with an actionable message.

## `et.config` additions

- `JiraConfig` (dataclass: `base_url`, `email`, `pat`, `jql`,
  `priority_order`), `WorkspaceConfigEntry` (dataclass: `name`,
  `type` ("dynamic"/"static"), `ref: str | None`, `description: str | None`).
- `load_config() -> EtConfig` (dataclass bundling `max_workspaces: int`,
  `jira: JiraConfig | None`, `workspaces: list[WorkspaceConfigEntry]`) —
  generalizes the existing `load_workspace_names()` (kept as a thin
  wrapper returning `[w.name for w in load_config().workspaces]`, so `ws
  rename --all` needs no changes).
- `save_config(config: EtConfig) -> None`: writes the YAML back
  (round-tripping unknown/unused keys is out of scope — `et` fully owns
  this file's schema), setting file mode `0o600`.

## `et jira get` algorithm

1. **Fetch**: `load_config()`, require a `jira` block (else `ConfigError`),
   call `fetch_active_issues()`.
2. **Confirm** (skippable with `--no-prompt`): print a table of the fetched
   issues (key, priority, name truncated to 20 chars) and ask "Proceed
   with this assignment? [Y/n]"; abort with no changes on decline.
3. **Cleanup pass**: for every *non-static* workspace entry whose `ref`
   starts with `jira:` and whose key is absent from the fetched active
   list: prompt `Delete workspace <n> ('<name>', tracking <key>) — issue
   no longer active. Delete? [y/N]` (auto-confirmed under `--no-prompt`).
   On confirm: dump that workspace's Tracker timer to
   `~/timers/by-id/jira-<key>.txt` (same two-line seconds+duration format
   as `dump --all`), zero+stop the timer (same semantics as `reset --all`,
   scoped to this one timer), and clear the entry's `ref`/`description`,
   resetting `name` to the default `ET-<n>`. On decline: leave the entry
   untouched; it's still "occupied" and excluded from step 5.
4. **Capacity/growth**: count non-static slots not excluded by step 3.
   If there are more active issues than available slots, append new
   `type: dynamic` entries (name defaulted to `ET-<n>`) up to
   `max_workspaces` total slots; call
   `workspaces.configure_static_workspace_count(max_workspaces)` so GNOME
   has enough real workspaces. If still not enough room after growth,
   process only the top-priority issues that fit and print one warning
   line per skipped issue (`Skipped jira:<key> (no free workspace slots)`).
5. **Reshuffle**: build the target mapping of non-static slot index →
   issue, walking eligible slots in ascending index order and issues in
   priority order. For each target (slot, issue) pair:
   - If unchanged (same ref already at that slot): no-op.
   - If the issue was already tracked at a *different* slot: relocate —
     take that slot's existing Tracker timer entry (by `workspaceId`
     match) and update its `workspaceId` and `name` to the new slot
     (keeping `id`, `timeElapsed`, `running`, `autoResume` as-is so
     elapsed time and running state follow the issue), move the config
     entry's `ref`/`description`/`name` to the new slot's config entry,
     and reset the old slot's config entry back to defaults (`ET-<n>`,
     no `ref`/`description`).
   - If brand new (no existing timer/config entry anywhere): create a
     fresh Tracker timer (`build_new_timer`, `autoResume: true`) and a new
     config entry (`ref`, `description`, truncated `name`).

   Implementation note: two issues can swap slots in the same run (e.g.
   issue A moves 3→5 while issue B moves 5→3). To avoid clobbering data
   mid-reshuffle, capture the full ref→old-slot mapping and the original
   timer/config-entry objects in a snapshot *before* mutating anything,
   then build the entire new `workspaces` list and Tracker timers list
   from that snapshot in one pass, rather than mutating slots in place
   one at a time.
6. **Apply**: write the updated `workspaces` list back to `config.yaml`
   (via `save_config`), call `workspaces.rename_all_workspaces()` with the
   final ordered name list (unchanged for static slots), and save all
   Tracker timer changes in one `reload_around()` disable/write/enable
   pass.
7. **Report**: print one line per action — `Assigned workspace <n> to
   jira:<key> ('<truncated>')`, `Moved jira:<key> from workspace <a> to
   <b>`, `Deleted workspace <n> (jira:<key> no longer active)`, `Skipped
   jira:<key> (no free workspace slots)`.

## `et tracker add --all` change

Its hard-coded `DEFAULT_WORKSPACE_COUNT = 10` becomes: use
`load_config().max_workspaces` if a config file exists, else fall back to
`10` — so both commands share one capacity knob without requiring a config
file for the simple case.

## Truncation

`summary[:20].rstrip()` — a hard character cut with trailing whitespace
stripped, no ellipsis. Simple and matches "let's start with 20
characters"; can be revisited later if it reads awkwardly in practice.

## Error handling

All new failure modes (missing `jira` config block, malformed
`priority_order`/`type` values, Jira HTTP/network errors, config
read/write errors) raise typed exceptions (`JiraError`, extended
`ConfigError`) caught in `cli.py` with the existing `Error: ...` + exit-1
pattern used by every other command.

## Testing

- `tests/test_jira.py`: `fetch_active_issues()` with `requests` mocked
  (success, sorting by priority, unknown-priority warning, HTTP error,
  network error).
- `tests/test_config.py`: extended for `load_config()`/`save_config()`
  round-tripping the new schema (`max_workspaces`, `jira` block,
  per-workspace `type`/`ref`/`description`), and malformed-value cases.
- `tests/test_tracker.py` and/or a new `tests/test_jira_sync.py`: the
  reshuffle/cleanup/growth orchestration logic, with `gsettings`,
  `reload_around`, and `fetch_active_issues` all mocked — covering: no-op
  when nothing changed, relocation carrying timer state, new-issue
  creation, inactive-ref deletion (confirmed and declined), capacity
  growth, and skip-with-warning when over capacity.
- No live Jira calls in tests; live end-to-end verification (if desired)
  happens manually against the real `warthogs.atlassian.net` instance
  with a real (but scoped/test) API token, the same way prior tracker
  features were manually verified.
