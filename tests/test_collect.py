import unittest
import json
from types import SimpleNamespace

from collect import REMOTE_PROBE, _PROBE_MAIN, _public_session, apply_turns, peer_online


class CollectTests(unittest.TestCase):
    def test_orca_collector_keeps_every_agent_and_untracked_connected_terminal(self):
        namespace = {}
        exec(_PROBE_MAIN.rsplit("\nmain()", 1)[0], namespace)
        workspace = {"path": "/repo", "repo": "repo", "agents": [{"paneKey": "tab:one", "agentType": "codex"}, {"paneKey": "tab:two", "agentType": "codex"}]}
        terminals = [{"worktreePath": "/repo", "tabId": "tab", "leafId": leaf, "connected": True, "lastOutputAt": 9000} for leaf in ("one", "two", "three")]
        namespace["shutil"] = SimpleNamespace(which=lambda _: "orca-ide")
        def run(command, timeout):
            result = {"worktrees": [workspace]} if "ps" in command else {"terminals": terminals}
            return SimpleNamespace(returncode=0, stdout=json.dumps({"result": result}), stderr="")
        namespace["run"] = run
        rows, warning = namespace["read_orca"]()
        self.assertEqual(len(rows), 3)
        self.assertEqual(len({row["instance_id"] for row in rows}), 3)
        self.assertIsNone(warning)

    def test_orca_emits_each_terminal_agent_and_uses_its_own_activity(self):
        namespace = {}
        exec(_PROBE_MAIN.rsplit("\nmain()", 1)[0], namespace)
        worktree = {"path": "/repo", "repo": "repo", "lastOutputAt": 9000}
        one = namespace["slim_worktree"](worktree, {"paneKey": "tab:one", "agentType": "codex", "state": "working", "updatedAt": 8000})
        two = namespace["slim_worktree"](worktree, {"paneKey": "tab:two", "agentType": "codex", "state": "done", "updatedAt": 1000})
        self.assertNotEqual(one["instance_id"], two["instance_id"])
        self.assertEqual(one["surface"], "Orca")
        self.assertEqual(two["last_output_at"], 1000)

    def test_public_session_preserves_surface_and_native_instance(self):
        row = _public_session({"surface": "Hermes Telegram", "instance_id": "hermes:worker:one", "instance_name": "Worker one", "profile": "worker"}, {"id": "fedora", "label": "Fedora"})
        self.assertEqual(row["surface"], "Hermes Telegram")
        self.assertEqual(row["instance_id"], "hermes:worker:one")
        self.assertEqual(row["profile"], "worker")

    def test_turn_from_another_agent_is_not_consumed_by_a_shared_workspace(self):
        rows = apply_turns([{"path": "/repo", "agent_type": "claude", "in_turn": False}], [{"path": "/repo", "who": "codex"}])
        self.assertEqual(len(rows), 2)
        self.assertFalse(rows[0]["in_turn"])
        self.assertEqual(rows[1]["agent_type"], "codex")

    def test_two_standalone_turn_instances_in_the_same_workspace_stay_separate(self):
        rows = apply_turns([], [{"path": "/repo", "who": "codex", "surface": "CLI turn", "instance_id": f"turn:{pid}"} for pid in (1, 2)])
        self.assertEqual([row["instance_id"] for row in rows], ["turn:1", "turn:2"])

    def test_turn_only_session_retains_project_identity(self):
        rows = apply_turns([], [{"path": "/repo/feature", "who": "codex", "github_repo": "Example/product", "project_name": "Product"}])
        self.assertEqual(rows[0]["github_repo"], "Example/product")
        self.assertEqual(rows[0]["project_name"], "Product")

    PEERS = (
        {"id": "local", "label": "This box", "ssh": None},
        {"id": "fedora", "label": "Fedora", "ssh": ["desktop"], "host": "desktop"},
        {"id": "mac-mini", "label": "Mac mini", "ssh": ["mac-mini"], "host": "mac-mini"},
    )

    def test_tailscale_peers_ignore_lookalike_hosts(self):
        status = {
            "Peer": {
                "a": {"HostName": "desktop", "DNSName": "desktop.tail.ts.", "Online": True},
                "b": {"HostName": "My Mac mini (2)", "DNSName": "mac-mini.tail.ts.", "Online": True},
                "c": {"HostName": "desktop-hermes", "DNSName": "desktop-hermes.tail.ts.", "Online": False},
            }
        }
        self.assertEqual(peer_online(status, self.PEERS), {"fedora": True, "mac-mini": True})

    def test_offline_peer_is_false(self):
        status = {"Peer": [{"HostName": "desktop", "DNSName": "desktop.tail.ts.", "Online": False}]}
        self.assertEqual(peer_online(status, self.PEERS), {"fedora": False})

    def test_turn_fills_an_empty_workspace_and_does_not_replace_a_finished_agent(self):
        worktrees = [
            {
                "repo": "opto_system_software",
                "display_name": "ops/remove-staging-outbox-upgrade",
                "path": "/repos/opto_system_software",
                "agent_type": "",
                "agent_state": "",
                "in_turn": False,
            },
            {
                "repo": "virginia_acuitydoc",
                "display_name": "virginia_acuitydoc",
                "path": "/repos/virginia_acuitydoc",
                "agent_type": "grok",
                "agent_state": "done",
                "in_turn": False,
            },
        ]
        turns = [
            {"path": "/repos/opto_system_software", "who": "codex"},
            {"path": "/repos/virginia_acuitydoc", "who": "grok"},
            {"path": "/repos/new_tool", "who": "claude"},
        ]
        sessions = apply_turns(worktrees, turns)
        by_path = {session["path"]: session for session in sessions}
        self.assertEqual(by_path["/repos/opto_system_software"]["agent_type"], "codex")
        self.assertEqual(by_path["/repos/opto_system_software"]["agent_state"], "working")
        self.assertEqual(by_path["/repos/virginia_acuitydoc"]["agent_state"], "done")
        self.assertEqual(by_path["/repos/virginia_acuitydoc"]["agent_type"], "grok")
        self.assertEqual(by_path["/repos/new_tool"]["agent_type"], "claude")

    def test_remote_probe_parses(self):
        compile(REMOTE_PROBE, "remote_probe", "exec")


class PaneTurnTests(unittest.TestCase):
    def test_turn_lock_on_an_orca_pane_overrides_a_stale_done(self):
        pane = "tab-1:leaf-1"
        worktrees = [
            {"surface": "Orca", "instance_id": "orca:wt:" + pane, "path": "/repo", "agent_type": "codex", "agent_state": "done", "last_message": "Old turn result.", "started_at": 1},
            {"surface": "Orca", "instance_id": "orca:wt:tab-2:leaf-2", "path": "/repo", "agent_type": "codex", "agent_state": "done", "last_message": "Other pane."},
        ]
        waits = [{"kind": "subagent", "label": "Zeno", "detail": "preview_chart_browser"}]
        turns = [{"path": "/repo", "who": "codex", "instance_id": "turn:9", "pane_key": pane, "started_at": 500, "waiting_on": waits}]
        rows = apply_turns(worktrees, turns)
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0]["agent_state"], rows[0]["in_turn"], rows[0]["last_message"], rows[0]["started_at"]), ("working", True, "", 500))
        self.assertEqual(rows[0]["waiting_on"], waits)
        self.assertEqual(rows[1]["agent_state"], "done")
        self.assertNotIn("waiting_on", rows[1])


if __name__ == "__main__":
    unittest.main()
