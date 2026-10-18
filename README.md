# Central Command

A local, real-time board of live AI agent sessions across your machines.

The page follows the shape of a session board: one column, a short overview, and one collapsed section per live session. The board shows which machines are online, which agents are mid-turn, which sessions wait on a dialog, and which produced output recently. Idle gateways, old tasks, and machines with nothing running stay off the list. The overview still says that a connected machine is quiet.

A session stays on the list while an agent is in a turn, while that session is waiting on a dialog, or while it produced output in the last 15 minutes. A waiting dialog stays for up to 6 hours while its process is still open. Hermes profiles, the Codex desktop app, and Claude Cowork use the same rule. The page is read-only: it does not start an agent or send a continue.

## Setup

Nothing to install. Central Command needs Python 3.10 or newer and the standard library only.

1. Clone this repo.
2. Copy `machines.example.json` to `machines.json` and list the other machines you want to watch:

```json
[
  {"id": "workstation", "label": "Workstation", "ssh": ["workstation"], "host": "workstation"},
  {"id": "laptop", "label": "Laptop", "ssh": ["-o", "IdentitiesOnly=yes", "you@laptop.example.ts.net"], "host": "laptop"}
]
```

   The local machine is always first on the board. `ssh` is the argument list passed to the `ssh` command, so any host from your SSH config works, and a Tailscale hostname is a good fit. `host` is optional; it names the Tailscale peer that sets the online flag when the peer name differs from `id`. Delete `machines.json` to watch only the local machine.
3. Optional: `project-labels.json` gives repos display names. See "What a section means" below.
4. Optional: `drive.json` enables the `python3 drive.py plan|tick` helper that renews an agent stalled on a usage limit by sending one `continue` to its Orca terminal:

```json
{
  "local": {"command": ["orca-ide"]},
  "workstation": {"command": ["/usr/local/bin/orca-ide"], "ssh": ["workstation"]}
}
```

   Keys are machine ids from `machines.json`. Without this file the helper does nothing.

## Run

```bash
python3 serve.py
```

Open http://127.0.0.1:8765.

To keep it running, install a systemd user service:

```ini
# ~/.config/systemd/user/central-command.service
[Unit]
Description=Central Command session board

[Service]
WorkingDirectory=%h/path/to/central_command
ExecStart=/usr/bin/python3 serve.py
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable --now central-command
```

To run it by hand, stop the service first. Two processes cannot share the port.

To open the page from your other devices, serve it on your tailnet:

```bash
tailscale serve --bg 8765
```

That shares the local port with your machines over HTTPS on your private network. It is not on the public internet.

## Reading the board

List is the default. Kanban is the same sessions in columns. The page reads again about every 15 seconds and updates itself.

The Location and Agent filters at the top work together in both layouts. They remember your choices in this browser and keep them on refresh. Agent families group Codex Desktop under Codex, Claude Cowork under Claude, and Hermes profiles under Hermes. Each session shows its location and agent. Counts follow the filters; Clear filters shows all sessions again.

Search finds product names, GitHub repos, session names, and current task text. Press `/` to search and Escape to clear it. Status tabs narrow the list further; the machine chips are location shortcuts. Reset clears all filters. The theme button switches between the 80s arcade look and the standard dark look; both are dark. Refreshes update existing rows so open sessions, search focus, and filter choices stay in place. Reduced motion preferences are respected.

Surface and Instance are separate filters. Surface identifies Orca, Codex CLI/Desktop/VS Code/Exec, Claude Code/Desktop/Cowork/Chat, or a Hermes channel/task source. Instance identifies the particular terminal pane, thread, session, or profile task. Sessions sharing a repo and agent family remain separate; only matching native identifiers on the same surface are deduplicated. Active subagents are included. Secret-bearing titles are hidden while their instance stays visible. Each Orca pane uses its own activity time, so another pane's output does not keep an old session on the board. A partial source read produces a warning.

Two states cover a session that is waiting rather than idle. Waiting on helpers means it is waiting on subagents or another agent it started. Waiting on other work means it is waiting on a command that has run for 20 seconds or more. Open the session to see the chain, such as the workers it is waiting on. Claude subagents count while their transcript was written in the last 3 minutes. Codex subagents count until their rollout ends with `task_complete`, including the Codex home Orca uses. A Codex turn lock in an Orca terminal is matched to its pane through `ORCA_PANE_KEY`; while the lock is held, the pane shows as working even if Orca's hook state says done. Commands show the program and at most two plain words, never flags or values.

Rows describe sessions per surface. An agent can appear in both its terminal and its app when those sources do not share an identifier. The listed collectors are the current coverage; other surfaces need their own source adapter.

## Tests

```bash
python3 -m unittest discover -s tests
```

## What a section means

The summary line says what the session is doing. Open it for where that session stands. The words are a report. The page does not run them.

Each session leads with its product or project name. Under it, the page shows the project, the GitHub owner/repo with a link, and the session or worktree name when it differs. Names come from the workspace README or package metadata; GitHub names come from the local Git origin. This also works for Git worktrees and sessions on the other machines. A workspace without a GitHub origin shows its workspace name. Hermes sessions without a workspace show their profile.

`project-labels.json` records display names when they differ from the repo names: Website / `marketing-site` or Mobile App / `mobile-app-react`, for example. Put the file in `state/` to keep it out of git, or in the repo folder. Add an entry keyed by `owner/repo` or the repo name with `product` and `project_name`, then restart the service. These labels apply across machines.
