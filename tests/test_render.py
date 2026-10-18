import unittest

from render import _stamp, render_page


def view(**overrides):
    base = {
        "stale": False,
        "read_at_label": "2026-10-04 14:00",
        "overview": "No session is running.",
        "sessions": [],
    }
    base.update(overrides)
    return base


def session(**overrides):
    base = {
        "state": "working",
        "title": "central_command",
        "line": "Restyle the board.",
        "where": "This box",
        "age": "just now",
        "stands": ["Grok is working on this box.", "Output arrived just now."],
    }
    base.update(overrides)
    return base


class RenderTests(unittest.TestCase):
    def test_waiting_states_have_tabs_columns_and_badges(self):
        page = render_page(view(sessions=[session(state="helpers", key="a"), session(state="blocked", key="b")]))
        self.assertIn('data-state="helpers"', page)
        self.assertIn('id="col-helpers">Waiting on helpers</h2>', page)
        self.assertIn('id="col-blocked">Waiting on other work</h2>', page)
        self.assertIn('<i class="dot"></i>On helpers</span>', page)
        self.assertIn('<i class="dot"></i>Waiting</span>', page)

    def test_surface_and_instance_filters_and_labels(self):
        page = render_page(view(sessions=[session(key="this:codex:one", surface="Codex CLI", instance_id="codex:one", instance_name="Thread one")]))
        self.assertIn('id="filter-surface"', page)
        self.assertIn('id="filter-instance"', page)
        self.assertIn('value="this:codex:one"', page)
        self.assertIn('data-surface="Codex CLI"', page)
        self.assertIn('data-instance="this:codex:one"', page)
        self.assertIn('Surface: Codex CLI', page)
        self.assertIn('Instance: Thread one', page)

    def test_separate_location_and_agent_controls_include_quiet_machines(self):
        page = render_page(view(locations=["This box", "Fedora", "Mac mini"], sessions=[session(agent="Codex Desktop", filter_agents=["Codex"])]))
        self.assertIn('id="filter-location"', page)
        self.assertIn('id="filter-agent"', page)
        self.assertIn('<option value="Mac mini">Mac mini</option>', page)
        self.assertIn('<option value="Codex">Codex</option>', page)
        self.assertIn('data-location="This box"', page)
        self.assertIn('data-agents="[&quot;Codex&quot;]"', page)
        self.assertIn("Agent: Codex Desktop", page)

    def test_project_identity_in_list_and_board(self):
        row = session(title="Demo", project_name="Demo Engine", repo="my-app", github_repo="Example/my-app", session_name="fix/login")
        page = render_page(view(sessions=[row]))
        self.assertEqual(page.count("Project: Demo Engine"), 2)
        self.assertEqual(page.count("https://github.com/Example/my-app"), 4)
        self.assertEqual(page.count("Session: fix/login"), 2)

    def test_identity_escapes_text_and_rejects_invalid_repo_links(self):
        page = render_page(view(sessions=[session(project_name="<b>Project</b>", repo="<repo>", github_repo='bad/\" onclick=\"alert(1)', session_name="<session>")]))
        self.assertIn("Project: &lt;b&gt;Project&lt;/b&gt;", page)
        self.assertIn("Workspace: &lt;repo&gt;", page)
        self.assertIn("Session: &lt;session&gt;", page)
        self.assertNotIn("https://github.com/bad", page)

    def test_project_identity_changes_refresh_stamp(self):
        self.assertNotEqual(_stamp(view(sessions=[session(github_repo="Example/old")])), _stamp(view(sessions=[session(github_repo="Example/new")])))

    def test_escapes_session_text(self):
        page = render_page(view(sessions=[session(title="<b>name</b>", line="<script>", stands=["a < b"])]))
        self.assertIn('<span class="line">&lt;script&gt;</span>', page)
        self.assertNotIn("<span class=\"line\"><script>", page)
        self.assertIn("&lt;b&gt;name&lt;/b&gt;", page)
        self.assertIn("a &lt; b", page)

    def test_one_column_of_sessions(self):
        page = render_page(
            view(
                overview="On this box, 1 session is working.",
                sessions=[session(), session(state="attention", title="Mac mini", line="The read failed.")],
            )
        )
        self.assertIn("<h1>Sessions</h1>", page)
        self.assertIn('class="session working"', page)
        self.assertIn('class="session attention"', page)
        self.assertIn("Where it stands", page)
        self.assertIn("This page only reads", page)
        self.assertIn(">Kanban<", page)
        self.assertIn('class="kanban"', page)
        self.assertIn(">Working<", page)
        self.assertNotIn("Needs you", page)
        self.assertNotIn("DEMO", page)
        self.assertNotIn(">Quiet<", page)
        self.assertNotIn(">Blind<", page)
        self.assertIn("open", page)

    def test_stale_banner(self):
        page = render_page(view(stale=True))
        self.assertIn("older than 15 minutes", page)

    def test_empty_list(self):
        page = render_page(view())
        self.assertIn("Nothing is running.", page)
        self.assertIn("No session is running.", page)


if __name__ == "__main__":
    unittest.main()
