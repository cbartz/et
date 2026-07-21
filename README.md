# et

`et` is a small command-line tool for **tracking effort** and **managing your
Ubuntu/GNOME workspaces**. It renames GNOME workspaces, drives the
[Tracker](https://extensions.gnome.org/extension/3212/tracker/) GNOME Shell
extension's per-workspace timers, and can automatically arrange your
workspaces around your active Jira issues.

## Features

- **`et ws rename`** — rename the active workspace (or all of them from config).
- **`et ws info`** — show the Jira issue linked to the active workspace.
- **`et tracker add`** — create a Tracker timer bound to a workspace.
- **`et tracker reset` / `dump`** — reset or export elapsed times.
- **`et jira get`** — fetch your active Jira issues and reconcile workspaces
  and Tracker timers to match, highest priority first.
- **`et jira log-time`** — log the active workspace's tracked time to its
  linked Jira issue.
- **`et task [create|info|log-time|complete]`** — a friendlier, task-centric
  layer over the above: create a workspace+timer for a task (optionally
  picked straight from your active Jira issues), and complete it by logging
  its time and freeing the slot.

## Requirements

`et` shells out to standard GNOME/Ubuntu tooling, which must be available on
`PATH`:

- [`gsettings`](https://manpages.ubuntu.com/manpages/en/man1/gsettings.1.html)
  — read/write GNOME workspace names and the Tracker extension's timers.
- [`wmctrl`](https://manpages.ubuntu.com/manpages/en/man1/wmctrl.1.html)
  — detect the active workspace (`sudo apt install wmctrl`).
- [`gnome-extensions`](https://manpages.ubuntu.com/manpages/en/man1/gnome-extensions.1.html)
  — reload the Tracker extension around timer writes.
- The **Tracker** GNOME Shell extension (`tracker@aliakseiz.github.com`),
  installed and enabled, for any `tracker`/`jira` timer functionality.

Python **3.12+** is required.

## Installation

This project uses [uv](https://docs.astral.sh/uv/):

```bash
uv sync            # create the virtualenv and install et + dependencies
uv run et --help   # run without installing globally
```

To install the `et` entry point onto your `PATH`:

```bash
uv tool install .
```

## Usage

```bash
et --help
```

### Workspaces

```bash
et ws rename focus          # rename the active workspace to "focus"
et ws rename --all          # rename workspaces 0..n-1 from the config's "workspaces" list
et ws info                  # show the Jira issue linked to the active workspace
```

### Tracker timers

Timers created by `et` are named `ET-<n>` (1-indexed by workspace), so bulk
operations never touch timers you created manually in Tracker's UI.

```bash
et tracker add              # add an ET-<n> timer for the active workspace
et tracker add --all        # pin GNOME to a fixed layout and add ET-1..ET-10
et tracker reset            # reset the active workspace's ET-<n> timer to 0
et tracker reset --all      # reset every ET-<n> timer to 0
et tracker dump             # write the active workspace's timer to ~/timers/<date>/
et tracker dump --all       # write every ET-<n> timer to ~/timers/<date>/
```

Each dumped file contains two lines: the raw elapsed seconds, then a
human-readable duration (e.g. `2h 15m 30s`). The human-readable duration of
each dumped timer is also printed to stdout.

### Jira sync

```bash
et jira get                 # sync workspaces to your active Jira issues (prompts to confirm)
et jira get --no-prompt     # skip confirmations (auto-confirm deletions)
```

`et jira get` renames/describes your non-`static` workspaces after your
active issues (highest priority first), moving Tracker timers along with each
issue when its slot changes. The confirmation prompt annotates each issue with
what will happen to its workspace — `ws unchanged (N)`, `ws move (OLD -> NEW)`,
`ws created (N)`, or `no free workspace slot` — using 1-based workspace
indices. Workspaces whose tracked issue is no longer active are reset back to a
plain `ET-<n>` slot after confirmation (their timer is first dumped to
`~/timers/by-id/jira-<KEY>.txt`, then reset).

```bash
et jira log-time                          # log the active workspace's tracked time to Jira
et jira log-time --comment "Fixed it"     # attach a worklog comment
et jira log-time --no-reset               # log the time but leave the tracker running
```

`et jira log-time` reads the elapsed time from the `ET-<n>` Tracker timer
bound to the active workspace, resolves the Jira issue linked to that
workspace (its `ref`, e.g. from `et jira get`), and logs it as a worklog via
Jira's own worklog API (no separate Tempo credential needed — worklogs
created this way still show up in Tempo timesheets when Tempo is configured
to sync native Jira worklogs). At least a minute of elapsed time is
required. On success the tracker is reset to 0, unless `--no-reset` is
given.

### Tasks

`et task` wraps the commands above into a single lifecycle for one task at a
time — it doesn't replace `ws`/`tracker`/`jira`, which keep working exactly
as before.

```bash
et task info                                # same as `et ws info`
et task create                              # prompts for a name/description
et task create isd-321 -d "Fix login bug"   # or give them directly
et task create --from-jira                  # pick from your active Jira issues
et task log-time                            # same as `et jira log-time`
et task complete                            # log time, then free the workspace
```

`et task create` allocates the first free (non-`static`, unlinked) workspace
slot — growing the configured workspace list up to `max_workspaces` if none
is free, same as `et jira get` — creates its `ET-<n>` Tracker timer, and
switches GNOME to it. `--from-jira` lists your active Jira issues that
aren't already linked to a workspace, lets you pick one, and links the new
workspace to it (same as `et jira get` would).

`et task complete` logs the active workspace's tracked time to Jira (like
`et jira log-time`, no confirmation prompt) and then resets that workspace
back to a bare `ET-<n>` slot, freeing it for a future `et task create`.

## Configuration

`et` reads `~/.config/et/config.yaml` (override the directory with the
`ET_CONFIG_DIR` environment variable). Example:

```yaml
# Capacity cap shared by `tracker add --all` and `jira get`. Optional (default 10).
max_workspaces: 10

# Jira Cloud REST credentials + query. Required for `et jira get` and
# `et jira log-time`.
jira:
  base_url: https://your-org.atlassian.net
  email: you@example.com
  pat: your-jira-api-token          # a Jira Cloud API token, not a password
  jql: assignee = currentUser() AND statusCategory != Done
  # Optional; controls sort order. Defaults to the list below.
  priority_order: [Highest, High, Medium, Low, Lowest]

# Ordered workspace list used by `ws rename --all`, `tracker add --all`, `jira get`.
workspaces:
  - name: mails
  - name: handson
    type: static                    # "static" workspaces are never touched by `jira get`
  - name: isd-321
    ref: jira:ISD-321               # links a workspace to a Jira issue
    description: Fix the login flow
```

Per-entry keys: `name` (required), `type` (`dynamic` (default) or `static`),
`ref` (e.g. `jira:ISD-321`), and `description`. The config file is written
with mode `0600` because it may contain a Jira API token.

> **Note:** `tracker add --all` and `jira get` switch GNOME to a *fixed*
> number of workspaces (`org.gnome.mutter dynamic-workspaces = false`) so the
> `ET-<n>` slots always exist. This is a global GNOME setting change.

> **Known limitation:** `et jira get` applies its changes (Tracker timers,
> then config, then GNOME workspace names) sequentially without a rollback.
> A failure partway through can leave the config and live GNOME state
> temporarily out of sync; re-running the command reconciles them.

## Development

Common tasks are exposed through a [`Justfile`](./Justfile):

```bash
just install-requirements   # uv sync + install pre-commit hooks
just lint                   # ruff
just static                 # mypy --strict
just test                   # pytest + coverage
just test-integ             # end-to-end integration tests
just ops                    # build the distribution artifacts
```

`prek` (pre-commit) runs ruff, mypy, and pytest on every commit.

## License

Licensed under the [Apache License 2.0](./LICENSE).
