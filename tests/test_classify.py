import unittest
from datetime import datetime, timezone

from classify import FRESH_MS, WAITING_MS, classify

NOW = int(datetime(2026, 10, 4, 18, 0, tzinfo=timezone.utc).timestamp() * 1000)


def snapshot(**overrides):
    base = {
        "read_at": NOW,
        "sessions": [],
        "machines": [
            {"id": "local", "label": "This box", "online": True, "read_ok": True, "error": None},
            {"id": "fedora", "label": "Fedora", "online": True, "read_ok": True, "error": None},
            {"id": "mac-mini", "label": "Mac mini", "online": True, "read_ok": True, "error": None},
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
        "prompt": "",
        "last_message": "",
        "tool_name": "",
        "last_output_at": NOW - 1000,
        "started_at": NOW - 60_000,
        "in_turn": True,
        "live": True,
        "archived": False,
    }
    base.update(overrides)
    return base


def run(snap):
    return classify(snap, now_ms=NOW)


def titles(view, state=None):
    rows = view["sessions"]
    if state:
        rows = [row for row in rows if row["state"] == state]
    return [row["title"] for row in rows]


class ClassifyTests(unittest.TestCase):
    def test_instances_of_the_same_agent_in_one_workspace_have_distinct_keys(self):
        rows = run(snapshot(sessions=[session(surface="Codex CLI", instance_id="codex:one", instance_name="Thread one"), session(surface="Codex CLI", instance_id="codex:two", instance_name="Thread two")]))["sessions"]
        self.assertNotEqual(rows[0]["key"], rows[1]["key"])
        self.assertEqual(rows[0]["surface"], "Codex CLI")
        self.assertEqual(rows[1]["instance_name"], "Thread two")

    def test_agent_filters_group_desktop_cowork_and_profiles(self):
        for raw, expected in (("Codex Desktop", ["Codex"]), ("Claude Cowork", ["Claude"]), ("Hermes worker-a", ["Hermes"]), ("codex and claude", ["Codex", "Claude"]), ("", ["Terminal"])):
            with self.subTest(agent=raw):
                row = run(snapshot(sessions=[session(agent_type=raw)]))["sessions"][0]
                self.assertEqual(row["filter_agents"], expected)
                self.assertEqual(row["where"], "This box")

    def test_quiet_locations_and_read_failures_have_correct_filters(self):
        snap = snapshot()
        snap["machines"][2]["read_ok"] = False
        result = run(snap)
        self.assertEqual(result["locations"], ["This box", "Fedora", "Mac mini"])
        self.assertEqual(result["sessions"][0]["where"], "Mac mini")
        self.assertEqual(result["sessions"][0]["filter_agents"], [])

    def test_product_label_preserves_project_repo_and_session(self):
        row = run(snapshot(sessions=[session(
            product="Demo List", project_name="Daily Tracker", repo="daily-tracker",
            github_repo="Example/daily-tracker", display_name="mobile-list-fix",
        )]))["sessions"][0]
        self.assertEqual(row["title"], "Demo List")
        self.assertEqual(row["project_name"], "Daily Tracker")
        self.assertEqual(row["github_repo"], "Example/daily-tracker")
        self.assertEqual(row["session_name"], "mobile-list-fix")

    def test_same_product_sessions_have_distinct_stable_keys(self):
        rows = run(snapshot(sessions=[
            session(product="Demo", path="/repo/one"),
            session(product="Demo", path="/repo/two", agent_state="done"),
        ]))["sessions"]
        self.assertNotEqual(rows[0]["key"], rows[1]["key"])
        changed = run(snapshot(sessions=[session(product="Demo", path="/repo/one", agent_state="done")]))["sessions"][0]
        self.assertEqual(rows[0]["key"], changed["key"])

    def test_working_agent_uses_the_ask(self):
        view = run(
            snapshot(
                sessions=[
                    session(
                        repo="central_command",
                        display_name="central_command",
                        prompt="Restyle the session board.",
                        tool_name="run_terminal_command",
                        started_at=NOW - (12 * 60_000),
                    )
                ]
            )
        )
        row = view["sessions"][0]
        self.assertEqual(row["state"], "working")
        self.assertEqual(row["title"], "central_command")
        self.assertIn("Restyle the session board.", row["line"])
        self.assertIn("running a command", " ".join(row["stands"]))
        self.assertIn("12 min ago", " ".join(row["stands"]))
        self.assertIn("Output arrived just now.", row["stands"])
        self.assertIn("On this box, 1 session is working.", view["overview"])

    def test_waiting_dialog_stays_visible_and_an_old_wait_drops(self):
        fresh = session(
            repo="daily-tracker",
            display_name="mobile-list-fix",
            agent_type="claude",
            agent_state="waiting",
            in_turn=False,
            last_output_at=NOW - (30 * 60_000),
        )
        stale = session(
            repo="old-claude",
            display_name="old-claude",
            agent_type="claude",
            agent_state="waiting",
            in_turn=False,
            last_output_at=NOW - WAITING_MS - 1000,
        )
        view = run(snapshot(sessions=[fresh, stale]))
        self.assertEqual(titles(view), ["mobile-list-fix"])
        self.assertEqual(view["sessions"][0]["state"], "stalled")
        self.assertIn("waiting on a dialog", view["sessions"][0]["line"])

    def test_done_agent_with_fresh_output_is_finished(self):
        view = run(
            snapshot(
                sessions=[
                    session(
                        agent_state="done",
                        in_turn=False,
                        last_message="The tutor login is already in this database. Leave the password as it is.",
                    )
                ]
            )
        )
        row = next(row for row in view["sessions"] if row["state"] == "finished")
        self.assertEqual(row["title"], "my-app")
        self.assertIn("tutor login", row["line"])
        self.assertIn("just finished", view["overview"])

    def test_done_agent_with_old_output_is_omitted(self):
        view = run(
            snapshot(
                sessions=[
                    session(
                        agent_state="done",
                        in_turn=False,
                        last_output_at=NOW - (2 * 60 * 60 * 1000),
                        last_message="Old note.",
                    )
                ]
            )
        )
        self.assertNotIn("my-app", titles(view))

    def test_stale_working_agent_is_stalled(self):
        view = run(
            snapshot(sessions=[session(last_output_at=NOW - (40 * 60 * 1000), prompt="Keep going.")])
        )
        row = next(row for row in view["sessions"] if row["title"] == "my-app")
        self.assertEqual(row["state"], "stalled")
        self.assertIn("40 min ago", " ".join(row["stands"]))

    def test_fresh_terminal_without_agent_stays(self):
        view = run(
            snapshot(
                sessions=[
                    session(
                        agent_type="",
                        agent_state="",
                        in_turn=False,
                        tool_name="",
                        prompt="",
                    )
                ]
            )
        )
        row = next(row for row in view["sessions"] if row["title"] == "my-app")
        self.assertEqual(row["state"], "working")
        self.assertIn("terminal is live", row["line"])

    def test_old_terminal_without_agent_is_omitted(self):
        view = run(
            snapshot(
                sessions=[
                    session(
                        agent_type="",
                        agent_state="",
                        in_turn=False,
                        last_output_at=NOW - (40 * 60 * 1000),
                    )
                ]
            )
        )
        self.assertNotIn("my-app", titles(view))

    def test_quiet_workspace_is_omitted(self):
        view = run(
            snapshot(
                sessions=[
                    session(
                        repo="old_notes",
                        display_name="old_notes",
                        agent_type="",
                        agent_state="",
                        in_turn=False,
                        live=False,
                        last_output_at=None,
                    )
                ]
            )
        )
        self.assertNotIn("old_notes", titles(view))

    def test_generic_branch_name_uses_the_repo(self):
        view = run(snapshot(sessions=[session(display_name="main", repo="erpnext-books", prompt="Post the books.")]))
        self.assertIn("erpnext-books", titles(view, "working"))

    def test_connected_machines_without_sessions_are_not_rows(self):
        view = run(snapshot())
        self.assertEqual(view["sessions"], [])
        self.assertIn("Fedora is connected. No session is running there.", view["overview"])
        self.assertIn("Mac mini is connected. No session is running there.", view["overview"])
        self.assertNotIn("Blind", view["overview"])

    def test_offline_machine_is_not_a_row(self):
        machines = snapshot()["machines"]
        machines[1]["online"] = False
        view = run(snapshot(machines=machines))
        self.assertEqual(view["sessions"], [])
        self.assertIn("Fedora is off Tailscale.", view["overview"])

    def test_failed_read_is_attention(self):
        machines = snapshot()["machines"]
        machines[2]["read_ok"] = False
        machines[2]["error"] = "permission denied"
        view = run(snapshot(machines=machines, sessions=[session(machine_id="mac-mini", machine="Mac mini")]))
        attention = [row for row in view["sessions"] if row["state"] == "attention"]
        self.assertEqual([row["title"] for row in attention], ["Mac mini"])
        self.assertIn("on Tailscale", attention[0]["line"])
        self.assertNotIn("Mac mini", titles(view, "working"))

    def test_turn_without_a_timestamp_stays_working(self):
        view = run(
            snapshot(
                sessions=[
                    session(
                        machine_id="fedora",
                        machine="Fedora",
                        repo="opto_system_software",
                        display_name="ops/remove-staging-outbox-upgrade",
                        agent_type="codex",
                        agent_state="working",
                        in_turn=True,
                        last_output_at=None,
                        prompt="",
                    )
                ]
            )
        )
        row = next(row for row in view["sessions"] if row["state"] == "working")
        self.assertIn("Codex is in a turn", row["line"])
        self.assertIn("On Fedora, 1 session is working.", view["overview"])

    def test_issue_records_are_not_part_of_the_board(self):
        view = run(snapshot(issues=[{"identifier": "DEMO-9", "title": "Approve the pilot"}]))
        blob = " ".join(row["title"] + row["line"] for row in view["sessions"])
        self.assertNotIn("DEMO-9", blob)
        self.assertNotIn("needs_you", view)

    def test_preview_and_tool_input_are_not_row_fields(self):
        view = run(
            snapshot(
                sessions=[
                    session(
                        preview="secret token sk-live",
                        tool_input="aws ssm send-command",
                        last_message="done line",
                        agent_state="done",
                        in_turn=False,
                    )
                ]
            )
        )
        row = next(row for row in view["sessions"] if row["title"] == "my-app")
        blob = " ".join(str(value) for value in row.values())
        self.assertNotIn("sk-live", blob)
        self.assertNotIn("ssm", blob)

    def test_old_snapshot_is_stale(self):
        self.assertTrue(run(snapshot(read_at=NOW - FRESH_MS - 1))["stale"])

    def test_fresh_snapshot_is_not_stale(self):
        self.assertFalse(run(snapshot())["stale"])


class WaitingOnClassifyTests(unittest.TestCase):
    def test_helpers_state_names_the_chain(self):
        waits = [{"kind": "ringer", "label": "Ringer run fix-ui", "detail": "1 of 3 passed", "age_ms": 300_000, "waiting_on": [{"kind": "agent", "label": "codex on fix-ui"}]}]
        view = run(snapshot(sessions=[session(agent_type="claude", last_output_at=NOW - 40 * 60_000, waiting_on=waits)]))
        row = view["sessions"][0]
        self.assertEqual(row["state"], "helpers")
        self.assertEqual(row["line"], "Claude is waiting on Ringer run fix-ui (1 of 3 passed) in my-app.")
        self.assertIn("Waiting on Ringer run fix-ui (1 of 3 passed) for 5 min, which is waiting on codex on fix-ui.", row["stands"])
        self.assertIn("1 session is waiting on helpers", view["overview"])

    def test_several_subagents_are_counted(self):
        waits = [{"kind": "subagent", "label": "One"}, {"kind": "subagent", "label": "Two"}]
        row = run(snapshot(sessions=[session(agent_type="claude", waiting_on=waits)]))["sessions"][0]
        self.assertEqual(row["line"], "Claude is waiting on 2 subagents in my-app.")

    def test_long_command_is_other_work(self):
        waits = [{"kind": "command", "label": "npm test", "age_ms": 120_000, "waiting_on": [{"kind": "command", "label": "jest.js"}]}]
        view = run(snapshot(sessions=[session(agent_type="claude", waiting_on=waits)]))
        row = view["sessions"][0]
        self.assertEqual(row["state"], "blocked")
        self.assertEqual(row["line"], "Claude is waiting on the command “npm test” in my-app.")
        self.assertIn("Waiting on the command “npm test” for 2 min, which is waiting on the command “jest.js”.", row["stands"])
        self.assertIn("waiting on other work", view["overview"])

    def test_command_that_runs_an_agent_counts_as_helpers(self):
        waits = [{"kind": "command", "label": "make eval", "waiting_on": [{"kind": "agent", "label": "Codex exec"}]}]
        self.assertEqual(run(snapshot(sessions=[session(waiting_on=waits)]))["sessions"][0]["state"], "helpers")

    def test_dialog_wait_outranks_helpers(self):
        waits = [{"kind": "subagent", "label": "One"}]
        row = run(snapshot(sessions=[session(agent_state="waiting", in_turn=False, waiting_on=waits)]))["sessions"][0]
        self.assertEqual(row["state"], "stalled")

    def test_waiting_rows_sort_after_working(self):
        rows = run(snapshot(sessions=[session(repo="b", display_name="b", waiting_on=[{"kind": "subagent", "label": "x"}]), session(repo="a", display_name="a")]))["sessions"]
        self.assertEqual([row["state"] for row in rows], ["working", "helpers"])


if __name__ == "__main__":
    unittest.main()
