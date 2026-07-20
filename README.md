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
issue when its slot changes. Workspaces whose tracked issue is no longer
active are reset back to a plain `ET-<n>` slot after confirmation (their timer
is first dumped to `~/timers/by-id/jira-<KEY>.txt`, then reset).

## Configuration

`et` reads `~/.config/et/config.yaml` (override the directory with the
`ET_CONFIG_DIR` environment variable). Example:

```yaml
# Capacity cap shared by `tracker add --all` and `jira get`. Optional (default 10).
max_workspaces: 10

# Jira Cloud REST credentials + query. Required only for `et jira get`.
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
