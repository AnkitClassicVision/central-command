"""Read project names and GitHub identity without contacting a remote."""

import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit


IDENTITY_FIELDS = ("product", "project_name", "github_repo")


def github_repo(remote: str) -> str:
    """Keep only owner/repo; never retain credentials from a Git remote."""
    if "://" in remote:
        try:
            parsed = urlsplit(remote)
            if parsed.hostname != "github.com":
                return ""
            path = parsed.path.lstrip("/")
        except ValueError:
            return ""
    else:
        match = re.fullmatch(r"(?:[^@\s]+@)?github\.com:(.+)", remote)
        if not match:
            return ""
        path = match.group(1)
    path = path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return path if re.fullmatch(r"[\w.-]+/[\w.-]+", path, flags=re.ASCII) else ""


def workspace_identity(value) -> dict:
    """Support nested directories and Git worktrees, plus non-Git projects."""
    if not value or not str(value).startswith("/"):
        return {}
    folder = Path(value)
    if not folder.is_dir():
        return {}
    root = next((parent for parent in (folder, *folder.parents) if (parent / ".git").exists()), folder)
    identity = {}
    if (root / ".git").exists():
        try:
            result = subprocess.run(
                ["git", "-C", str(root), "config", "--get", "remote.origin.url"],
                capture_output=True, text=True, check=False, timeout=1,
            )
            repo = github_repo(result.stdout.strip()) if result.returncode == 0 else ""
            if repo:
                identity["github_repo"] = repo
        except (OSError, subprocess.TimeoutExpired):
            pass
    for name in ("README.md", "README.markdown", "readme.md"):
        try:
            with (root / name).open(encoding="utf-8", errors="replace") as source:
                content = source.read(8192)
        except OSError:
            continue
        match = re.search(r"^#\s+(.+?)\s*#*\s*$", content, flags=re.MULTILINE)
        if match:
            title = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", match.group(1))
            title = re.sub(r"[*_`]", "", title)
            identity["project_name"] = " ".join(title.split())[:100]
            break
    if not identity.get("project_name"):
        try:
            with (root / "package.json").open(encoding="utf-8") as source:
                package = json.load(source)
            title = package.get("productName") or package.get("name")
            if isinstance(title, str):
                identity["project_name"] = " ".join(title.split())[:100]
        except (OSError, ValueError, AttributeError):
            pass
    return identity


def enrich_projects(sessions: list[dict]) -> None:
    cache = {}
    for session in sessions:
        path = session.get("workspace_path") or session.get("path") or ""
        if path not in cache:
            cache[path] = workspace_identity(path)
        session.update(cache[path])


def apply_project_labels(session: dict, labels: dict) -> dict:
    repo = session.get("github_repo") or session.get("repo") or ""
    keys = (repo.lower(), repo.rsplit("/", 1)[-1].lower(), str(session.get("repo") or "").lower())
    label = next((labels[key] for key in keys if key in labels), {})
    for field in ("product", "project_name"):
        if label.get(field):
            session[field] = label[field]
    return session
