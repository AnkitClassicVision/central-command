import subprocess
import tempfile
import unittest
from pathlib import Path

from projects import apply_project_labels, github_repo, workspace_identity


class ProjectTests(unittest.TestCase):
    def test_github_identity_discards_remote_credentials(self):
        for remote in (
            "git@github.com:Example/product.git",
            "https://user:fake-password@github.com/Example/product.git",
            "ssh://git@github.com/Example/product.git",
        ):
            self.assertEqual(github_repo(remote), "Example/product")
        for remote in ("https://github.com.evil.test/Example/product", "https://gitlab.com/Example/product", "https://github.com/Example/product/extra"):
            self.assertEqual(github_repo(remote), "")

    def test_nested_workspace_and_worktree_keep_canonical_repo(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            root.mkdir()
            self.git(root, "init")
            self.git(root, "config", "user.email", "test@example.test")
            self.git(root, "config", "user.name", "Test")
            self.git(root, "remote", "add", "origin", "git@github.com:Example/product.git")
            (root / "README.md").write_text("# **Product** [Dashboard](https://example.test)\n")
            self.git(root, "add", "README.md")
            self.git(root, "commit", "-m", "fixture")
            worktree = Path(temp) / "feature-folder"
            self.git(root, "worktree", "add", "-b", "feature", str(worktree))
            nested = worktree / "src"
            nested.mkdir()
            self.assertEqual(workspace_identity(str(nested)), {
                "github_repo": "Example/product",
                "project_name": "Product Dashboard",
            })

    def test_non_git_project_uses_readme_and_missing_path_is_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            # A .git sentinel keeps the root lookup inside the temp dir even on
            # machines that keep a git repo above the temp folder.
            (root / ".git").write_text("", encoding="utf-8")
            (root / "README.md").write_text("# Central Command\n")
            self.assertEqual(workspace_identity(temp), {"project_name": "Central Command"})
            self.assertEqual(workspace_identity(str(root / "missing")), {})
        self.assertEqual(workspace_identity("hermes:profile:session"), {})

    def test_labels_use_canonical_repo_before_worktree_name(self):
        labels = {
            "my-app": {"product": "Demo"},
            "example/my-app": {"product": "Demo", "project_name": "Demo Engine"},
        }
        row = apply_project_labels({"repo": "feature-worktree", "github_repo": "Example/my-app"}, labels)
        self.assertEqual(row["product"], "Demo")
        self.assertEqual(row["project_name"], "Demo Engine")
        self.assertNotIn("product", apply_project_labels({"repo": "unknown"}, labels))

    @staticmethod
    def git(root, *args):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
