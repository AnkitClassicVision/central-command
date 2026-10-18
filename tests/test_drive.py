import unittest
from datetime import datetime, timezone

from classify import FRESH_MS
import drive
from drive import COOLDOWN_MS, SELF_PATH, classify_session, choose_terminal, drive_once, parse_screen

NOW = int(datetime(2026, 10, 4, 18, 0, tzinfo=timezone.utc).timestamp() * 1000)


def snapshot(**overrides):
    base = {
        "read_at": NOW,
        "sessions": [],
        "machines": [
            {"id": "local", "read_ok": True},
            {"id": "fedora", "read_ok": True},
            {"id": "mac-mini", "read_ok": True},
        ],
    }
    base.update(overrides)
    return base


def session(**overrides):
    base = {
        "machine_id": "local",
        "machine": "This box",
        "repo": "my-app",
        "display_name": "my-app",
        "path": "/home/demo/my-app",
        "agent_type": "grok",
        "agent_state": "working",
        "prompt": "UNIQUE_PROMPT_DO_NOT_LEAK",
        "last_message": "UNIQUE_MESSAGE_DO_NOT_LEAK",
        "last_output_at": NOW - FRESH_MS - 60_000,
        "in_turn": True,
        "live": True,
        "archived": False,
    }
    base.update(overrides)
    return base


def terminal(**overrides):
    base = {
        "handle": "term_a",
        "connected": True,
        "writable": True,
        "orphaned": False,
        "worktreePath": "/home/demo/my-app",
        "agentIdentity": "grok",
        "lastOutputAt": 10,
    }
    base.update(overrides)
    return base


class FakeGateway:
    def __init__(self, terminals, idle=True, send_ok=True, screen_lines=None):
        self.terminals = terminals
        self.idle_ok = idle
        self.send_ok = send_ok
        self.screen_lines = screen_lines or []
        self.sent = []

    def list(self, machine_id, path):
        return self.terminals

    def screen(self, machine_id, handle):
        return self.screen_lines

    def idle(self, machine_id, handle):
        return self.idle_ok

    def send(self, machine_id, handle):
        self.sent.append((machine_id, handle))
        return self.send_ok


class DriveTests(unittest.TestCase):
    def setUp(self):
        self._drive_machines = drive.DRIVE_MACHINES
        drive.DRIVE_MACHINES = {"local": {}, "fedora": {}}

    def tearDown(self):
        drive.DRIVE_MACHINES = self._drive_machines

    def test_fresh_working_and_finished_stay_off_the_list(self):
        snap = snapshot()
        self.assertIsNone(classify_session(session(last_output_at=NOW - 1000), snap, NOW))
        self.assertIsNone(
            classify_session(session(agent_state="done", in_turn=False, last_output_at=NOW - 1000), snap, NOW)
        )

    def test_dialog_error_hermes_mac_and_this_desk_are_held(self):
        snap = snapshot()
        cases = [
            session(agent_state="waiting", in_turn=False, display_name="daily-tracker"),
            session(agent_state="error"),
            session(agent_type="Hermes full-access-worker-a", path="hermes:worker:1"),
            session(machine_id="mac-mini", machine="Mac mini", path="/Users/demo/work"),
            session(path=SELF_PATH, display_name="central_command", repo="central_command"),
        ]
        reasons = [classify_session(item, snap, NOW)["reason"] for item in cases]
        actions = [classify_session(item, snap, NOW)["action"] for item in cases]
        self.assertEqual(actions[0], "ask")
        self.assertIn("decision", reasons[0])
        self.assertEqual(actions[1], "ask")
        self.assertIn("error", reasons[1])
        self.assertIn("Hermes owns", reasons[2])
        self.assertIn("Mac mini", reasons[3])
        self.assertIn("read-only", reasons[4])

    def test_secret_title_is_blanked(self):
        item = classify_session(session(display_name="Rotate the api_key now", repo="ops"), snapshot(), NOW)
        self.assertEqual(item["title"], "session")
        self.assertNotIn("api_key", item["title"])

    def test_plan_does_not_send_or_leak_the_prompt(self):
        gateway = FakeGateway([terminal()])
        lines, _log = drive_once(
            snapshot(sessions=[session()]),
            NOW,
            enabled=False,
            log={"at": {}},
            gateway=gateway,
        )
        blob = "\n".join(lines)
        self.assertIn("Would continue my-app", blob)
        self.assertNotIn("UNIQUE_PROMPT", blob)
        self.assertNotIn("UNIQUE_MESSAGE", blob)
        self.assertEqual(gateway.sent, [])

    def test_send_once_to_the_oldest_idle_terminal(self):
        older = session(path="/home/demo/older", display_name="older", last_output_at=NOW - FRESH_MS - 500_000)
        newer = session(path="/home/demo/newer", display_name="newer", last_output_at=NOW - FRESH_MS - 120_000)
        gateway = FakeGateway(
            [
                terminal(handle="term_old", worktreePath="/home/demo/older", orphaned=True, lastOutputAt=1),
                terminal(handle="term_live", worktreePath="/home/demo/older", orphaned=False, lastOutputAt=2),
            ]
        )
        lines, log = drive_once(
            snapshot(sessions=[newer, older]),
            NOW,
            enabled=True,
            log={"at": {}},
            gateway=gateway,
        )
        self.assertEqual(gateway.sent, [("local", "term_live")])
        self.assertIn("Sent continue to older", "\n".join(lines))
        self.assertIn("1 other quiet session waits for the next pass.", "\n".join(lines))
        self.assertIn("send:local:/home/demo/older", log["at"])

    def test_cooldown_blocks_a_second_continue(self):
        gateway = FakeGateway([terminal()])
        log = {"at": {"send:local:/home/demo/my-app": NOW - 1000}}
        lines, _log = drive_once(
            snapshot(sessions=[session()]),
            NOW,
            enabled=True,
            log=log,
            gateway=gateway,
        )
        self.assertEqual(gateway.sent, [])
        self.assertEqual(lines, [])
        self.assertLess(1000, COOLDOWN_MS)

    def test_busy_terminal_is_not_sent_a_continue(self):
        gateway = FakeGateway([terminal()], idle=False)
        lines, log = drive_once(
            snapshot(sessions=[session()]),
            NOW,
            enabled=True,
            log={"at": {}},
            gateway=gateway,
        )
        self.assertEqual(gateway.sent, [])
        self.assertIn("not idle", "\n".join(lines))
        self.assertNotIn("send:local:/home/demo/my-app", log["at"])

    def test_stale_board_sends_nothing(self):
        gateway = FakeGateway([terminal()])
        lines, _log = drive_once(
            snapshot(read_at=NOW - 10 * 60 * 1000, sessions=[session()]),
            NOW,
            enabled=True,
            log={"at": {}},
            gateway=gateway,
        )
        self.assertEqual(lines, ["The board is stale. No continue was sent."])
        self.assertEqual(gateway.sent, [])

    def test_a_choice_comes_back_and_does_not_continue(self):
        gateway = FakeGateway(
            [terminal(worktreePath="/home/demo/orca/daily-tracker", agentIdentity="claude")],
            screen_lines=[
                "idle check: no action",
                "Background work is running",
                "1. Exit and stop tasks",
                "2. Move to background and exit",
                "3. Stay",
            ],
        )
        waiting = session(
            path="/home/demo/orca/daily-tracker",
            display_name="daily-tracker",
            repo="daily-tracker",
            agent_type="claude",
            agent_state="waiting",
            in_turn=False,
        )
        lines, log = drive_once(
            snapshot(sessions=[waiting]),
            NOW,
            enabled=True,
            log={"at": {}},
            gateway=gateway,
        )
        blob = "\n".join(lines)
        self.assertNotIn("needs you", blob)
        self.assertNotIn("Exit and stop tasks", blob)
        self.assertNotIn("UNIQUE_PROMPT", blob)
        self.assertEqual(gateway.sent, [])
        self.assertEqual(lines, [])
        self.assertEqual(log["at"], {})

    def test_usage_stall_gets_continue(self):
        gateway = FakeGateway([terminal()], screen_lines=["The model is overloaded."])
        lines, _log = drive_once(
            snapshot(sessions=[session()]),
            NOW,
            enabled=True,
            log={"at": {}},
            gateway=gateway,
        )
        self.assertEqual(gateway.sent, [("local", "term_a")])
        self.assertIn("Sent continue to my-app", "\n".join(lines))

    def test_parse_screen_sorts_a_choice_from_chatter_and_hides_a_secret(self):
        choice = parse_screen(
            [
                "idle check: no action",
                "Background work is running",
                "1. Exit and stop tasks",
                "2. Stay",
            ]
        )
        self.assertEqual(choice["kind"], "ask")
        self.assertIn("Background work is running", choice["text"])
        self.assertNotIn("idle check", choice["text"])
        secret = parse_screen(["1. Paste the api_key now"])
        self.assertEqual(secret["text"], "It needs a decision from you.")
        self.assertEqual(parse_screen(["You hit a rate limit"])["kind"], "usage")

    def test_choose_terminal_requires_the_agent_family(self):
        picked = choose_terminal(
            [terminal(agentIdentity="claude-agent-teams", handle="term_claude")],
            "/home/demo/my-app",
            "grok",
        )
        self.assertIsNone(picked)


if __name__ == "__main__":
    unittest.main()
