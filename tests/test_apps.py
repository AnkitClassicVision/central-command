import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from apps import FRESH_MS, WAITING_MS, merge_app_sessions, read_app_sessions
from classify import FRESH_MS as CLASSIFY_FRESH
from classify import WAITING_MS as CLASSIFY_WAITING

NOW = 1_790_000_000_000


class AppSessionTests(unittest.TestCase):
    def test_hermes_task_only_profile_is_discovered(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            database = home / ".hermes/profiles/worker/kanban.db"
            database.parent.mkdir(parents=True)
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE tasks (id TEXT, title TEXT, status TEXT, last_heartbeat_at INTEGER)")
            connection.execute("INSERT INTO tasks VALUES (?, ?, ?, ?)", ("one", "Current task", "running", NOW))
            connection.commit()
            connection.close()
            os.utime(database, (NOW / 1000, NOW / 1000))
            rows = read_app_sessions(home, NOW)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["surface"], "Hermes Tasks")
            self.assertEqual(rows[0]["profile"], "worker")

    def test_windows_match_the_classifier(self):
        self.assertEqual(FRESH_MS, CLASSIFY_FRESH)
        self.assertEqual(WAITING_MS, CLASSIFY_WAITING)

    def test_hermes_keeps_a_live_session_and_drops_the_rest(self):
        root = Path("/tmp") / self.id().replace(".", "_")
        shutil.rmtree(root, ignore_errors=True)
        hermes = root / ".hermes"
        hermes.mkdir(parents=True)
        database = hermes / "state.db"
        connection = sqlite3.connect(database)
        connection.execute(
            """
            CREATE TABLE sessions (
                id TEXT, source TEXT, title TEXT, cwd TEXT, ended_at REAL,
                last_activity_at REAL, last_activity_description TEXT, archived INTEGER
            )
            """
        )
        fresh = NOW / 1000 - 30
        old = NOW / 1000 - 3600
        connection.executemany(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("live", "telegram", "Fix the feed", "", None, fresh, "receiving stream response", 0),
                ("old", "telegram", "Old chat", "", None, old, "", 0),
                ("child", "subagent", "Hidden child", "", None, fresh, "", 0),
                ("secret", "cli", "Rotate the api_key now", "", None, fresh, "user token pasted here", 0),
            ],
        )
        connection.commit()
        connection.close()
        os.utime(database, (fresh, fresh))

        board = hermes / "kanban.db"
        connection = sqlite3.connect(board)
        connection.execute(
            """
            CREATE TABLE tasks (
                id TEXT, title TEXT, body TEXT, status TEXT,
                workspace_path TEXT, last_heartbeat_at INTEGER, worker_pid INTEGER
            )
            """
        )
        connection.execute(
            "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("task", "Ship the note", "do not copy this body", "todo", "/repos/notes", None, None),
        )
        connection.commit()
        connection.close()
        os.utime(board, (fresh, fresh))

        sessions = read_app_sessions(root, NOW)
        self.assertEqual([item["display_name"] for item in sessions], ["Fix the feed", "Hidden child", "Hermes session"])
        self.assertEqual([item["surface"] for item in sessions], ["Hermes Telegram", "Hermes Subagent", "Hermes CLI"])
        self.assertEqual(len({item["instance_id"] for item in sessions}), 3)
        self.assertEqual(sessions[0]["agent_type"], "Hermes")
        self.assertEqual(sessions[0]["tool_name"], "receiving a reply")
        blob = json.dumps(sessions)
        self.assertNotIn("do not copy", blob)
        self.assertNotIn("api_key", blob)
        self.assertNotIn("token", blob)

    def test_codex_desktop_is_separate_from_a_subagent(self):
        home = Path("/tmp") / self.id().replace(".", "_")
        shutil.rmtree(home, ignore_errors=True)
        codex = home / ".codex"
        codex.mkdir(parents=True)
        database = codex / "state_5.sqlite"
        connection = sqlite3.connect(database)
        connection.execute(
            """
            CREATE TABLE threads (
                id TEXT, title TEXT, name TEXT, cwd TEXT, source TEXT,
                thread_source TEXT, originator TEXT, archived INTEGER,
                updated_at_ms INTEGER, recency_at_ms INTEGER,
                preview TEXT, first_user_message TEXT
            )
            """
        )
        connection.executemany(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "desk",
                    "Hiring loop",
                    "",
                    "/repos/hiring",
                    "vscode",
                    "automation",
                    "Codex Desktop",
                    0,
                    NOW - 1000,
                    NOW - 1000,
                    "secret preview",
                    "secret ask",
                ),
                (
                    "child",
                    "Review",
                    "",
                    "/repos/hiring",
                    '{"subagent":"review"}',
                    "subagent",
                    "Codex Desktop",
                    0,
                    NOW - 1000,
                    NOW - 1000,
                    "",
                    "",
                ),
                (
                    "old",
                    "Yesterday",
                    "",
                    "/repos/old",
                    "vscode",
                    "user",
                    "Codex Desktop",
                    0,
                    NOW - FRESH_MS - 5000,
                    NOW - FRESH_MS - 5000,
                    "",
                    "",
                ),
            ],
        )
        connection.commit()
        connection.close()

        sessions = read_app_sessions(home, NOW)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0]["agent_type"], "Codex Desktop")
        self.assertEqual(sessions[0]["display_name"], "Hiring loop")
        self.assertEqual(sessions[1]["surface"], "Codex Subagent")
        self.assertNotEqual(sessions[0]["instance_id"], sessions[1]["instance_id"])
        self.assertNotIn("secret", json.dumps(sessions))

    def test_claude_wait_and_fresh_cowork(self):
        home = Path("/tmp") / self.id().replace(".", "_")
        shutil.rmtree(home, ignore_errors=True)
        sessions_dir = home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True)
        cwd = home / "daily-tracker"
        payload = {
            "pid": os.getpid(),
            "sessionId": "abc",
            "cwd": str(cwd),
            "kind": "interactive",
            "entrypoint": "cli",
            "status": "waiting",
            "name": "mobile-list-fix",
            "updatedAt": NOW - (30 * 60_000),
            "waitingFor": "dialog open",
        }
        (sessions_dir / "123.json").write_text(json.dumps(payload), encoding="utf-8")
        transcript_dir = home / ".claude" / "projects" / "".join(
            char if char.isalnum() else "-" for char in str(cwd)
        )
        transcript_dir.mkdir(parents=True)
        transcript = transcript_dir / "abc.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")
        stamp = (NOW - (30 * 60_000)) / 1000
        os.utime(transcript, (stamp, stamp))

        cowork = (
            home
            / "Library/Application Support/Claude/local-agent-mode-sessions/org/user/local_1.json"
        )
        cowork.parent.mkdir(parents=True)
        cowork.write_text(
            json.dumps({"title": "Sort the inbox", "mode": "cowork", "messages": ["do not copy"]}),
            encoding="utf-8",
        )
        os.utime(cowork, (NOW / 1000, NOW / 1000))
        manifest = cowork.parent / "rpm" / "manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"lastUpdated": NOW, "plugins": []}), encoding="utf-8")
        os.utime(manifest, (NOW / 1000, NOW / 1000))

        sessions = read_app_sessions(home, NOW)
        by_agent = {item["agent_type"]: item for item in sessions}
        self.assertEqual(by_agent["claude"]["agent_state"], "waiting")
        self.assertEqual(by_agent["claude"]["display_name"], "mobile-list-fix")
        self.assertEqual(by_agent["Claude Cowork"]["display_name"], "Sort the inbox")
        self.assertNotIn("do not copy", json.dumps(sessions))

    def test_merge_keeps_distinct_surfaces_in_the_same_workspace(self):
        worktrees = [
            {
                "repo": "hiring",
                "display_name": "hiring",
                "path": "/repos/hiring",
                "agent_type": "codex",
                "agent_state": "working",
                "prompt": "Keep the Orca note.",
                "in_turn": True,
            }
        ]
        apps = [
            {
                "repo": "hiring",
                "display_name": "Hiring loop",
                "path": "/repos/hiring",
                "agent_type": "Codex Desktop",
                "agent_state": "working",
                "prompt": "",
                "in_turn": True,
            },
            {
                "repo": "Hermes",
                "display_name": "Fix the feed",
                "path": "hermes:Hermes:live",
                "agent_type": "hermes",
                "agent_state": "working",
                "prompt": "",
                "in_turn": True,
            },
        ]
        merged = merge_app_sessions(worktrees, apps)
        self.assertEqual(len(merged), 3)
        self.assertEqual(merged[0]["prompt"], "Keep the Orca note.")
        self.assertEqual(merged[1]["agent_type"], "Codex Desktop")
        self.assertEqual(merged[2]["agent_type"], "hermes")

    def test_merge_preserves_two_instances_on_one_surface_and_deduplicates_exact_ids(self):
        rows = [
            {"path": "/repo", "agent_type": "codex", "surface": "Codex CLI", "instance_id": "codex:one"},
            {"path": "/repo", "agent_type": "codex", "surface": "Codex CLI", "instance_id": "codex:two"},
            {"path": "/repo", "agent_type": "codex", "surface": "Codex CLI", "instance_id": "codex:one"},
        ]
        merged = merge_app_sessions([], rows)
        self.assertEqual([row["instance_id"] for row in merged], ["codex:one", "codex:two"])


def claude_home(test, *, status="idle", updated=None):
    home = Path("/tmp") / test.id().replace(".", "_")
    shutil.rmtree(home, ignore_errors=True)
    sessions_dir = home / ".claude" / "sessions"
    sessions_dir.mkdir(parents=True)
    cwd = home / "central_command"
    payload = {
        "pid": os.getpid(),
        "sessionId": "abc",
        "cwd": str(cwd),
        "kind": "interactive",
        "entrypoint": "cli",
        "status": status,
        "name": "waiting-board",
        "updatedAt": updated if updated is not None else NOW - 40 * 60_000,
    }
    (sessions_dir / "1.json").write_text(json.dumps(payload), encoding="utf-8")
    slug = "".join(char if char.isalnum() else "-" for char in str(cwd))
    return home, home / ".claude" / "projects" / slug


def wrapper(command):
    return "/usr/bin/bash -c source /home/demo/.claude/shell-snapshots/snapshot-bash-1.sh 2>/dev/null || true && eval '" + command + "' < /dev/null && pwd -P >| /tmp/claude-cwd"


class WaitingOnTests(unittest.TestCase):
    def claude_row(self, home, processes):
        rows = [row for row in read_app_sessions(home, NOW, processes=processes) if row["agent_type"] == "claude"]
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_quiet_claude_session_stays_while_a_ringer_run_works(self):
        home, _ = claude_home(self)
        runs = home / ".ringer" / "runs"
        runs.mkdir(parents=True)
        run = {
            "pid": 300,
            "run_name": "fix-ui",
            "state": "live",
            "finished": False,
            "tasks": [
                {"key": "review", "status": "pass", "engine": "claude", "model": "opus"},
                {"key": "fix-ui", "status": "running", "engine": "codex", "model": "gpt-5.6"},
                {"key": "docs", "status": "queued", "engine": "kimi", "model": "kimi-code/k3"},
            ],
        }
        (runs / "fix-ui.json").write_text(json.dumps(run), encoding="utf-8")
        me = os.getpid()
        processes = [
            {"pid": me, "ppid": 1, "age_ms": 3_600_000, "args": "/home/demo/.local/bin/claude --dangerously-skip-permissions"},
            {"pid": 150, "ppid": me, "age_ms": 3_599_000, "args": "node /home/demo/.npm/bin/some-mcp-server"},
            {"pid": 200, "ppid": me, "age_ms": 120_000, "args": wrapper("python3 ringer.py run m.json | tail -5")},
            {"pid": 300, "ppid": 200, "age_ms": 119_000, "args": "python3 /home/demo/ringer/ringer.py run m.json"},
            {"pid": 301, "ppid": 200, "age_ms": 119_000, "args": "tail -5"},
        ]
        row = self.claude_row(home, processes)
        self.assertEqual(len(row["waiting_on"]), 1)
        ringer = row["waiting_on"][0]
        self.assertEqual(ringer["kind"], "ringer")
        self.assertEqual(ringer["label"], "Ringer run fix-ui")
        self.assertEqual(ringer["detail"], "1 of 3 passed, 1 queued")
        self.assertEqual([item["label"] for item in ringer["waiting_on"]], ["codex (gpt-5.6) on fix-ui"])

    def test_long_command_chain_without_secrets(self):
        home, _ = claude_home(self)
        me = os.getpid()
        processes = [
            {"pid": me, "ppid": 1, "age_ms": 3_600_000, "args": "claude"},
            {"pid": 200, "ppid": me, "age_ms": 90_000, "args": wrapper("API_TOKEN=sk-live npm test --token=sk-live")},
            {"pid": 210, "ppid": 200, "age_ms": 89_000, "args": "npm test --token=sk-live"},
            {"pid": 220, "ppid": 210, "age_ms": 88_000, "args": "node /repo/node_modules/.bin/jest.js --ci"},
            {"pid": 230, "ppid": me, "age_ms": 3_000, "args": wrapper("ls")},
        ]
        row = self.claude_row(home, processes)
        self.assertEqual(row["waiting_on"], [{"kind": "command", "label": "npm test", "age_ms": 89_000, "waiting_on": [{"kind": "command", "label": "jest.js", "age_ms": 88_000}]}])
        self.assertNotIn("sk-live", json.dumps(row))

    def test_child_agent_and_what_it_waits_on(self):
        home, _ = claude_home(self)
        me = os.getpid()
        processes = [
            {"pid": me, "ppid": 1, "age_ms": 3_600_000, "args": "claude"},
            {"pid": 200, "ppid": me, "age_ms": 300_000, "args": wrapper("codex exec --yolo review")},
            {"pid": 210, "ppid": 200, "age_ms": 299_000, "args": "/usr/local/bin/codex exec --yolo review"},
            {"pid": 215, "ppid": 210, "age_ms": 299_000, "args": "/usr/local/bin/codex-code-mode-host"},
            {"pid": 220, "ppid": 210, "age_ms": 60_000, "args": "cargo test"},
        ]
        row = self.claude_row(home, processes)
        agent = row["waiting_on"][0]
        self.assertEqual((agent["kind"], agent["label"]), ("agent", "Codex exec"))
        self.assertEqual([item["label"] for item in agent["waiting_on"]], ["cargo test"])

    def test_claude_subagent_transcript_counts_while_it_writes(self):
        home, projects = claude_home(self)
        folder = projects / "abc" / "subagents"
        folder.mkdir(parents=True)
        (folder / "agent-1.meta.json").write_text(json.dumps({"agentType": "Explore", "description": "Map the collectors"}), encoding="utf-8")
        record = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Grep", "input": {"pattern": "do not copy"}}]}}
        (folder / "agent-1.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
        (folder / "agent-2.meta.json").write_text(json.dumps({"agentType": "Explore", "description": "Old one"}), encoding="utf-8")
        (folder / "agent-2.jsonl").write_text("{}\n", encoding="utf-8")
        for name in ("agent-1.jsonl", "agent-1.meta.json"):
            os.utime(folder / name, (NOW / 1000 - 30, NOW / 1000 - 30))
        for name in ("agent-2.jsonl", "agent-2.meta.json"):
            os.utime(folder / name, (NOW / 1000 - 600, NOW / 1000 - 600))
        row = self.claude_row(home, [])
        self.assertEqual(row["waiting_on"], [{"kind": "subagent", "label": "Map the collectors", "detail": "Explore agent running Grep", "age_ms": 30_000}])
        self.assertNotIn("do not copy", json.dumps(row))

    def test_quiet_claude_session_without_helpers_drops_off(self):
        home, _ = claude_home(self)
        self.assertEqual([row for row in read_app_sessions(home, NOW, processes=[]) if row["agent_type"] == "claude"], [])

    def test_codex_parent_returns_while_its_subagent_writes(self):
        home = Path("/tmp") / self.id().replace(".", "_")
        shutil.rmtree(home, ignore_errors=True)
        (home / ".codex").mkdir(parents=True)
        connection = sqlite3.connect(home / ".codex" / "state_5.sqlite")
        connection.execute("CREATE TABLE threads (id TEXT, title TEXT, cwd TEXT, source TEXT, thread_source TEXT, archived INTEGER, recency_at_ms INTEGER, agent_nickname TEXT)")
        connection.execute("CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT PRIMARY KEY, status TEXT)")
        connection.executemany(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("parent", "Books migration", "/repos/books", "cli", "user", 0, NOW - 40 * 60_000, ""),
                ("child", "erpnext-books", "/repos/books", '{"subagent":"x"}', "subagent", 0, NOW - 10_000, "Mendel"),
            ],
        )
        connection.execute("INSERT INTO thread_spawn_edges VALUES ('parent', 'child', 'open')")
        connection.commit()
        connection.close()
        rows = {row["instance_id"]: row for row in read_app_sessions(home, NOW, processes=[])}
        self.assertEqual(rows["codex:parent"]["waiting_on"], [{"kind": "subagent", "label": "Mendel", "detail": "erpnext-books"}])
        self.assertEqual(rows["codex:child"]["waiting_on"], [])


if __name__ == "__main__":
    unittest.main()
