"""Read live Hermes, Codex, and Claude Desktop sessions.

Orca still owns its own worktrees. This module adds the sessions Orca does
not see: a Hermes profile that just spoke, a Codex desktop thread, a Claude
Code session that is waiting on a dialog, and a Claude Cowork session whose
file was just written. Idle daemons and old task lists stay out. Message
bodies, tool input, and credentials stay out.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
from pathlib import Path

FRESH_MS = 15 * 60 * 1000
WAITING_MS = 6 * 60 * 60 * 1000
HELPER_FRESH_MS = 3 * 60 * 1000
HELPER_MIN_MS = 20 * 1000
HELPER_DEPTH = 3
HELPER_WIDTH = 5

_SKIP_SOURCES = {"eval"}
_HERMES_ACTIONS = {
    "receiving stream response": "receiving a reply",
}
_TITLE_KEYS = ("title", "name", "summary", "taskName", "goal")
_SECRET_KEYS = ("token", "secret", "password", "credential", "authorization", "api_key", "apikey")
_SHELLS = {"bash", "sh", "zsh", "dash", "fish"}
_PIPE_FILTERS = {"tail", "head", "tee", "grep", "rg", "cat", "less", "sort", "uniq", "wc", "cut", "tr", "sed", "awk", "jq", "xargs"}
_AGENT_NAMES = {"claude": "Claude", "codex": "Codex", "hermes": "Hermes", "kimi": "Kimi", "gemini": "Gemini", "grok": "Grok", "pi": "Pi", "opencode": "OpenCode", "agy": "Antigravity"}
_INFRA_MARKERS = ("code-mode-host", "chrome-native-host", "app-server", "mcp", "systemd-inhibit")
_SAFE_WORD = re.compile(r"[A-Za-z0-9._:@/+-]{1,40}")


def read_app_sessions(home: Path, now_ms: int, processes: list[dict] | None = None) -> list[dict]:
    if processes is None:
        processes = read_processes()
    tree = _ProcessTree(processes, _ringer_runs(home, now_ms))
    sessions = []
    sessions.extend(_hermes_sessions(home, now_ms))
    sessions.extend(_codex_sessions(home, now_ms))
    sessions.extend(_claude_code_sessions(home, now_ms, tree))
    sessions.extend(_claude_desktop_sessions(home, now_ms))
    return sessions


def merge_app_sessions(worktrees: list[dict], apps: list[dict]) -> list[dict]:
    """Deduplicate the same native instance without collapsing shared workspaces."""
    merged = [item for item in worktrees if isinstance(item, dict)]
    index = {_session_key(item): item for item in merged}
    for app in apps:
        if not isinstance(app, dict):
            continue
        current = index.get(_session_key(app))
        if current is None:
            merged.append(app)
            index[_session_key(app)] = app
            continue
        if not current.get("agent_type") and app.get("agent_type"):
            current["agent_type"] = app.get("agent_type")
            if not current.get("agent_state"):
                current["agent_state"] = app.get("agent_state") or ""
        if app.get("in_turn") and current.get("agent_state") not in {"done", "idle", "waiting"}:
            current["in_turn"] = True
        if not current.get("prompt") and app.get("prompt"):
            current["prompt"] = app.get("prompt")
        if not current.get("display_name") and app.get("display_name"):
            current["display_name"] = app.get("display_name")
        if not current.get("waiting_on") and app.get("waiting_on"):
            current["waiting_on"] = app.get("waiting_on")
    return merged


def _hermes_sessions(home: Path, now_ms: int) -> list[dict]:
    sessions = []
    for profile, root in _hermes_roots(home):
        database = root / "state.db"
        if _recent_file(database, now_ms):
            sessions.extend(_hermes_db(database, profile, now_ms))
        board = root / "kanban.db"
        if _recent_file(board, now_ms):
            sessions.extend(_hermes_tasks(board, profile, now_ms))
    return sessions


def _hermes_roots(home: Path):
    root = home / ".hermes"
    if root.is_dir():
        yield "Hermes", root
    profiles = root / "profiles"
    if not profiles.is_dir():
        return
    for child in sorted(profiles.iterdir()):
        if child.is_dir() and ((child / "state.db").exists() or (child / "kanban.db").exists()):
            yield child.name, child


def _hermes_db(database: Path, profile: str, now_ms: int) -> list[dict]:
    connection = _connect(database)
    if connection is None:
        return []
    try:
        columns = _columns(connection, "sessions")
        if "last_activity_at" not in columns or "id" not in columns:
            return []
        chosen = ["id", "source", "title", "last_activity_at"]
        for name in ("cwd", "ended_at", "last_activity_description", "archived"):
            if name in columns:
                chosen.append(name)
        cutoff = (now_ms - FRESH_MS) / 1000
        rows = connection.execute(
            f"SELECT {', '.join(chosen)} FROM sessions WHERE last_activity_at > ?",
            (cutoff,),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()

    sessions = []
    for row in rows:
        item = dict(zip(chosen, row))
        source = str(item.get("source") or "").lower()
        if source in _SKIP_SOURCES:
            continue
        if item.get("archived"):
            continue
        activity = _ms(item.get("last_activity_at"))
        if activity is None or now_ms - activity > FRESH_MS:
            continue
        raw_title = str(item.get("title") or source or "Hermes")
        title = _clip(raw_title)
        title = title or "Hermes session"
        ended = item.get("ended_at") not in (None, "", 0)
        action = _HERMES_ACTIONS.get(str(item.get("last_activity_description") or "").strip().lower(), "")
        folder = _folder(item.get("cwd"))
        sessions.append(
            _row(
                repo=folder or profile,
                workspace_path=item.get("cwd"),
                display_name=title,
                path=f"hermes:{profile}:{item.get('id')}",
                agent_type="Hermes" if profile == "Hermes" else f"Hermes {profile}",
                surface={"cli": "Hermes CLI", "telegram": "Hermes Telegram", "discord": "Hermes Discord", "slack": "Hermes Slack", "web": "Hermes Web", "api": "Hermes API", "subagent": "Hermes Subagent"}.get(source, "Hermes"),
                instance_id=f"hermes:{profile}:{item.get('id')}",
                instance_name=f"{profile} · {str(item.get('id'))[-8:]}",
                profile=profile,
                agent_state="done" if ended else "working",
                prompt="",
                tool_name=action,
                last_output_at=activity,
                in_turn=not ended,
            )
        )
    return sessions


def _hermes_tasks(database: Path, profile: str, now_ms: int) -> list[dict]:
    connection = _connect(database)
    if connection is None:
        return []
    try:
        columns = _columns(connection, "tasks")
        if "title" not in columns or "status" not in columns:
            return []
        chosen = [name for name in ("id", "title", "status", "workspace_path", "last_heartbeat_at", "worker_pid") if name in columns]
        rows = connection.execute(f"SELECT {', '.join(chosen)} FROM tasks").fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()

    sessions = []
    for row in rows:
        item = dict(zip(chosen, row))
        status = str(item.get("status") or "").lower()
        if status in {"done", "archived", "cancelled", "canceled"}:
            continue
        heartbeat = _ms(item.get("last_heartbeat_at"))
        fresh_beat = heartbeat is not None and now_ms - heartbeat <= FRESH_MS
        running = status in {"running", "in_progress", "claimed", "doing", "started"}
        worker_alive = _pid_alive(item.get("worker_pid"))
        if not (fresh_beat or running or (worker_alive and status not in {"todo", "blocked"})):
            continue
        title = _clip(item.get("title") or "Hermes task")
        when = heartbeat or now_ms
        sessions.append(
            _row(
                repo=_folder(item.get("workspace_path")) or profile,
                workspace_path=item.get("workspace_path"),
                display_name=title,
                path=f"hermes-task:{profile}:{item.get('id')}",
                agent_type="Hermes" if profile == "Hermes" else f"Hermes {profile}",
                surface="Hermes Tasks",
                instance_id=f"hermes-task:{profile}:{item.get('id')}",
                instance_name=f"{profile} · Task {item.get('id')}",
                profile=profile,
                agent_state="working",
                prompt="",
                tool_name="running a task" if running or worker_alive else "",
                last_output_at=when,
                in_turn=True,
            )
        )
    return sessions


def _codex_sessions(home: Path, now_ms: int) -> list[dict]:
    databases = sorted((home / ".codex").glob("state_*.sqlite"), key=lambda p: int(p.stem.split("_")[-1]) if p.stem.split("_")[-1].isdigit() else 0, reverse=True)
    database = databases[0] if databases else home / ".codex" / "state_5.sqlite"
    connection = _connect(database)
    if connection is None:
        return []
    try:
        columns = _columns(connection, "threads")
        stamp = "recency_at_ms" if "recency_at_ms" in columns else "updated_at_ms"
        if stamp not in columns or "id" not in columns:
            return []
        chosen = ["id", "cwd", stamp]
        for name in ("title", "name", "source", "thread_source", "originator", "archived", "agent_nickname", "agent_path"):
            if name in columns:
                chosen.append(name)
        rows = connection.execute(
            f"SELECT {', '.join(chosen)} FROM threads WHERE {stamp} > ?",
            (now_ms - FRESH_MS,),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()

    sessions = []
    for row in rows:
        item = dict(zip(chosen, row))
        if item.get("archived"):
            continue
        source = str(item.get("source") or "")
        thread_source = str(item.get("thread_source") or "")
        originator = str(item.get("originator") or "")
        marker = f"{source} {thread_source} {originator}".lower()
        if thread_source == "guardian_review":
            continue
        when = _ms(item.get(stamp))
        if when is None:
            continue
        desktop = "desktop" in marker or "electron" in marker
        child = "subagent" in marker
        surface = "Codex Subagent" if child else "Codex Desktop" if desktop else "Codex VS Code" if source == "vscode" else "Codex Exec" if source == "exec" else "Codex CLI" if source == "cli" else "Codex"
        title = _clip(item.get("title") or item.get("name") or _folder(item.get("cwd")) or "Codex")
        cwd = str(item.get("cwd") or "")
        sessions.append(
            _row(
                repo=_folder(cwd) or "Codex",
                display_name=title,
                path=cwd or f"codex:{item.get('id')}",
                agent_type="Codex Desktop" if desktop else "codex",
                surface=surface,
                instance_id=f"codex:{item.get('id')}",
                instance_name=_clip(item.get("agent_nickname") or _folder(item.get("agent_path"))) or f"Thread {str(item.get('id'))[-8:]}",
                agent_state="working",
                prompt="",
                tool_name="",
                last_output_at=when,
                in_turn=True,
            )
        )
    _attach_codex_children(database, sessions, chosen, stamp, now_ms)
    return sessions


def _attach_codex_children(database: Path, sessions: list[dict], chosen: list[str], stamp: str, now_ms: int) -> None:
    """Mark a Codex thread as waiting while a subagent thread it spawned has not finished."""
    connection = _connect(database)
    if connection is None:
        return
    try:
        children = _codex_open_subagents(connection, now_ms)
        by_id = {item["instance_id"][len("codex:"):]: item for item in sessions}
        missing = sorted(parent for parent in children if parent not in by_id)
        parents = []
        if missing:
            marks = ", ".join("?" for _ in missing)
            parents = connection.execute(f"SELECT {', '.join(chosen)} FROM threads WHERE id IN ({marks})", tuple(missing)).fetchall()
    except sqlite3.Error:
        return
    finally:
        connection.close()

    for row in parents:
        item = dict(zip(chosen, row))
        if item.get("archived") or now_ms - (_ms(item.get(stamp)) or 0) > WAITING_MS:
            continue
        cwd = str(item.get("cwd") or "")
        marker = f"{item.get('source') or ''} {item.get('thread_source') or ''} {item.get('originator') or ''}".lower()
        desktop = "desktop" in marker or "electron" in marker
        parent = _row(
            repo=_folder(cwd) or "Codex",
            display_name=_clip(item.get("title") or item.get("name") or _folder(cwd) or "Codex"),
            path=cwd or f"codex:{item.get('id')}",
            agent_type="Codex Desktop" if desktop else "codex",
            surface="Codex Subagent" if "subagent" in marker else "Codex Desktop" if desktop else "Codex",
            instance_id=f"codex:{item.get('id')}",
            instance_name=_clip(item.get("agent_nickname") or _folder(item.get("agent_path"))) or f"Thread {str(item.get('id'))[-8:]}",
            agent_state="working",
            last_output_at=_ms(item.get(stamp)),
            in_turn=True,
        )
        sessions.append(parent)
        by_id[str(item.get("id"))] = parent

    for thread_id, item in by_id.items():
        nodes = _codex_nodes(children, thread_id, now_ms, 0)
        if nodes:
            item["waiting_on"] = nodes


def codex_turn_waits(codex_home: Path, cwd: str, now_ms: int) -> list[dict]:
    """Subagents of the newest top-level Codex thread in a folder, for a turn that has no thread id."""
    databases = sorted(codex_home.glob("state_*.sqlite"), key=lambda p: int(p.stem.split("_")[-1]) if p.stem.split("_")[-1].isdigit() else 0, reverse=True)
    connection = _connect(databases[0]) if databases else None
    if connection is None:
        return []
    try:
        children = _codex_open_subagents(connection, now_ms)
        if not children:
            return []
        columns = _columns(connection, "threads")
        stamp = "recency_at_ms" if "recency_at_ms" in columns else "updated_at_ms"
        marks = ", ".join("?" for _ in children)
        rows = connection.execute(
            f"SELECT id, {stamp} FROM threads WHERE id IN ({marks}) AND cwd = ? AND id NOT IN (SELECT child_thread_id FROM thread_spawn_edges)",
            (*children, cwd),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()
    if not rows:
        return []
    root = max(rows, key=lambda row: row[1] or 0)[0]
    return _codex_nodes(children, root, now_ms, 0)


def _codex_open_subagents(connection, now_ms: int) -> dict[str, list[dict]]:
    """Parent thread id to the subagent threads under it that have not reached task_complete."""
    if "parent_thread_id" not in _columns(connection, "thread_spawn_edges"):
        return {}
    columns = _columns(connection, "threads")
    stamp = "recency_at_ms" if "recency_at_ms" in columns else "updated_at_ms"
    if stamp not in columns:
        return {}
    picked = [name for name in ("agent_nickname", "agent_path", "title", "rollout_path", "created_at_ms", "archived") if name in columns]
    rows = connection.execute(
        f"SELECT e.parent_thread_id, t.id, t.{stamp}{''.join(', t.' + name for name in picked)} "
        f"FROM thread_spawn_edges e JOIN threads t ON t.id = e.child_thread_id WHERE t.{stamp} > ?",
        (now_ms - FRESH_MS,),
    ).fetchall()
    children: dict[str, list[dict]] = {}
    for parent, child, when, *rest in rows:
        item = dict(zip(picked, rest))
        if item.get("archived"):
            continue
        rollout = item.get("rollout_path")
        if rollout:
            if not _codex_rollout_open(Path(str(rollout)), now_ms):
                continue
        elif now_ms - (_ms(when) or 0) > HELPER_FRESH_MS:
            continue
        children.setdefault(str(parent), []).append({"id": str(child), **item})
    for kids in children.values():
        kids.sort(key=lambda kid: kid.get("created_at_ms") or 0)
    return children


def _codex_nodes(children: dict[str, list[dict]], thread_id: str, now_ms: int, depth: int) -> list[dict]:
    nodes = []
    for child in children.get(thread_id, [])[:HELPER_WIDTH]:
        started = _ms(child.get("created_at_ms"))
        node = _helper(
            "subagent",
            _clip(child.get("agent_nickname"), 60) or "Codex subagent",
            detail=_clip(_folder(child.get("agent_path")) or child.get("title"), 60),
            age_ms=now_ms - started if started else None,
        )
        if depth < HELPER_DEPTH:
            below = _codex_nodes(children, child["id"], now_ms, depth + 1)
            if below:
                node["waiting_on"] = below
        nodes.append(node)
    return nodes


def _codex_rollout_open(path: Path, now_ms: int) -> bool:
    """A Codex thread is done once its rollout ends with task_complete."""
    try:
        if now_ms - int(path.stat().st_mtime * 1000) > FRESH_MS:
            return False
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - 16_384))
            lines = handle.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return False
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = record.get("payload") if isinstance(record, dict) else None
        kind = payload.get("type") if isinstance(payload, dict) else None
        return kind not in {"task_complete", "turn_aborted", "shutdown_complete"}
    return True


def _claude_code_sessions(home: Path, now_ms: int, tree: "_ProcessTree | None" = None) -> list[dict]:
    folder = home / ".claude" / "sessions"
    if not folder.is_dir():
        return []
    sessions = []
    for path in folder.glob("*.json"):
        payload = _read_json(path, limit=200_000)
        if not isinstance(payload, dict):
            continue
        kind = str(payload.get("kind") or "").lower()
        entry = str(payload.get("entrypoint") or "").lower()
        if "chrome" in kind or "chrome" in entry:
            continue
        status = str(payload.get("status") or "").lower()
        cwd = str(payload.get("cwd") or "")
        if cwd.rstrip("/").endswith("/.claude/chrome"):
            continue
        updated = _ms(payload.get("updatedAt") or payload.get("statusUpdatedAt"))
        transcript = _claude_transcript_ms(home, payload.get("sessionId"), cwd)
        if transcript is not None:
            updated = transcript if updated is None else max(updated, transcript)
        if updated is None:
            continue
        age = now_ms - updated
        alive = _pid_alive(payload.get("pid"))
        waiting = status in {"waiting", "blocked", "needs_input"} and alive and age <= WAITING_MS
        fresh = age <= FRESH_MS
        helpers = []
        if alive:
            helpers = _claude_subagents(home, payload.get("sessionId"), cwd, now_ms)
            if tree is not None:
                helpers.extend(tree.helpers_of(payload.get("pid")))
            helpers = helpers[:HELPER_WIDTH]
        if not waiting and not fresh and not helpers:
            continue
        if waiting and not fresh:
            state = "waiting"
            in_turn = False
        elif helpers:
            state = "working"
            in_turn = True
        elif status in {"done", "idle", "exited", "completed"}:
            state = "done"
            in_turn = False
        else:
            state = "working"
            in_turn = True
        name = _clip(payload.get("name") or _folder(cwd) or "Claude")
        sessions.append(
            _row(
                repo=_folder(cwd) or "Claude",
                display_name=name,
                path=cwd or f"claude:{payload.get('sessionId') or path.stem}",
                agent_type="claude",
                surface="Claude VS Code" if "vscode" in entry else "Claude Cursor" if "cursor" in entry else "Claude Code",
                instance_id=f"claude:{payload.get('sessionId') or path.stem}",
                instance_name=f"Session {str(payload.get('sessionId') or path.stem)[-8:]}",
                agent_state=state,
                prompt="",
                tool_name="",
                last_output_at=updated,
                in_turn=in_turn,
                waiting_on=helpers,
            )
        )
    return sessions


def _claude_subagents(home: Path, session_id, cwd: str, now_ms: int) -> list[dict]:
    """Subagents run inside the Claude process, so their transcripts are the only sign of life."""
    if not session_id or not cwd:
        return []
    slug = "".join(char if char.isalnum() else "-" for char in str(cwd))
    folder = home / ".claude" / "projects" / slug / str(session_id) / "subagents"
    if not folder.is_dir():
        return []
    found = []
    for path in folder.glob("agent-*.jsonl"):
        try:
            modified = int(path.stat().st_mtime * 1000)
        except OSError:
            continue
        if now_ms - modified > HELPER_FRESH_MS:
            continue
        meta_path = path.with_name(path.stem + ".meta.json")
        meta = _read_json(meta_path, limit=20_000)
        meta = meta if isinstance(meta, dict) else {}
        try:
            started = int(meta_path.stat().st_mtime * 1000)
        except OSError:
            started = None
        kind = _clip(meta.get("agentType"), 40)
        tool = _last_tool(path)
        detail = " ".join(part for part in (f"{kind} agent" if kind else "", f"running {tool}" if tool else "") if part)
        found.append((started or modified, _helper("subagent", _clip(meta.get("description"), 80) or "Subagent", detail=detail, age_ms=now_ms - started if started else None)))
    found.sort(key=lambda pair: pair[0])
    return [item for _, item in found]


def _last_tool(path: Path) -> str:
    """Name the tool a subagent is in the middle of, without reading its input."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 65_536))
            lines = handle.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        content = (record.get("message") or {}).get("content") if isinstance(record.get("message"), dict) else None
        if record.get("type") == "assistant" and isinstance(content, list):
            names = [item.get("name") for item in content if isinstance(item, dict) and item.get("type") == "tool_use"]
            return _clip(names[-1], 40) if names and names[-1] else ""
        return ""
    return ""


def read_processes() -> list[dict]:
    """One ps read per probe. ps works the same way on Linux and macOS."""
    try:
        completed = subprocess.run(["ps", "-axww", "-o", "pid=,ppid=,etime=,args="], check=False, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return []
    processes = []
    for line in (completed.stdout or "").splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        if parts[3].startswith("[") or parts[3].endswith("<defunct>"):
            continue
        processes.append({"pid": int(parts[0]), "ppid": int(parts[1]), "age_ms": _etime_ms(parts[2]), "args": parts[3]})
    return processes


def _etime_ms(text: str) -> int:
    days = 0
    if "-" in text:
        head, text = text.split("-", 1)
        days = int(head) if head.isdigit() else 0
    seconds = 0
    for piece in text.split(":"):
        seconds = seconds * 60 + (int(piece) if piece.isdigit() else 0)
    return (days * 86_400 + seconds) * 1000


def _ringer_runs(home: Path, now_ms: int) -> dict[int, dict]:
    folder = home / ".ringer" / "runs"
    if not folder.is_dir():
        return {}
    runs = {}
    for path in folder.glob("*.json"):
        try:
            if now_ms - int(path.stat().st_mtime * 1000) > WAITING_MS:
                continue
        except OSError:
            continue
        payload = _read_json(path, limit=5_000_000)
        if not isinstance(payload, dict) or payload.get("finished") or str(payload.get("state") or "live") != "live":
            continue
        try:
            runs[int(payload.get("pid"))] = payload
        except (TypeError, ValueError):
            continue
    return runs


class _ProcessTree:
    """Describe what an agent process is waiting on, and what that is waiting on."""

    def __init__(self, processes: list[dict], runs: dict[int, dict]):
        self.by_pid = {item["pid"]: item for item in processes if isinstance(item, dict) and "pid" in item}
        self.children: dict[int, list[int]] = {}
        for item in self.by_pid.values():
            self.children.setdefault(item.get("ppid"), []).append(item["pid"])
        for kids in self.children.values():
            kids.sort()
        self.runs = runs

    def helpers_of(self, pid) -> list[dict]:
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return []
        agent = self.by_pid.get(pid)
        if agent is None:
            return []
        found = []
        for child in self._task_children(agent):
            if self.by_pid[child]["age_ms"] < HELPER_MIN_MS:
                continue
            found.extend(self._describe(child, 0))
        return _drop_pipe_filters(found)[:HELPER_WIDTH]

    def _task_children(self, agent: dict) -> list[int]:
        """Skip the servers an agent starts with itself, such as MCP servers."""
        kids = []
        for child in self.children.get(agent["pid"], []):
            proc = self.by_pid[child]
            args = proc.get("args") or ""
            if "shell-snapshots" in args or _is_ringer(args) or _agent_label(args):
                kids.append(child)
                continue
            if any(marker in args for marker in _INFRA_MARKERS):
                continue
            if agent.get("age_ms", 0) - proc.get("age_ms", 0) < 5_000:
                continue
            kids.append(child)
        return kids

    def _describe(self, pid: int, depth: int) -> list[dict]:
        proc = self.by_pid.get(pid)
        if proc is None:
            return []
        args = proc.get("args") or ""
        words = args.split()
        exe = _base(words[0]) if words else ""
        if exe in _SHELLS:
            inner = [node for child in self.children.get(pid, []) for node in self._describe(child, depth)]
            if inner:
                return _drop_pipe_filters(inner)
            text = _shell_text(args)
            return [_helper("command", _command_label(text.split()) or exe, age_ms=proc["age_ms"])]
        if _is_ringer(args):
            return [self._ringer(proc, depth)]
        agent = _agent_label(args)
        if agent:
            node = _helper("agent", agent, age_ms=proc["age_ms"])
            if depth < HELPER_DEPTH:
                below = [node for child in self._task_children(proc) for node in self._describe(child, depth + 1)]
                if below:
                    node["waiting_on"] = _drop_pipe_filters(below)[:HELPER_WIDTH]
            return [node]
        node = _helper("command", _command_label(words) or exe or "a command", age_ms=proc["age_ms"])
        if depth < HELPER_DEPTH:
            below = [item for child in self.children.get(pid, []) for item in self._describe(child, depth + 1)]
            below = [item for item in _drop_pipe_filters(below) if item["label"] != node["label"]]
            if below:
                node["waiting_on"] = below[:HELPER_WIDTH]
        return [node]

    def _ringer(self, proc: dict, depth: int) -> dict:
        run = self.runs.get(proc["pid"])
        if not run:
            node = _helper("ringer", "a Ringer run", age_ms=proc["age_ms"])
            below = [item for child in self.children.get(proc["pid"], []) for item in self._describe(child, depth + 1)] if depth < HELPER_DEPTH else []
            if below:
                node["waiting_on"] = below[:HELPER_WIDTH]
            return node
        tasks = [task for task in run.get("tasks") or [] if isinstance(task, dict)]
        passed = sum(1 for task in tasks if str(task.get("status") or "").lower() == "pass")
        failed = sum(1 for task in tasks if str(task.get("status") or "").lower() == "fail")
        active = [task for task in tasks if str(task.get("status") or "").lower() in {"running", "verifying", "retrying"}]
        queued = sum(1 for task in tasks if str(task.get("status") or "queued").lower() == "queued")
        counts = [f"{passed} of {len(tasks)} passed"]
        if failed:
            counts.append(f"{failed} failed")
        if queued:
            counts.append(f"{queued} queued")
        node = _helper("ringer", f"Ringer run {_clip(run.get('run_name') or run.get('run_id'), 60) or 'unnamed'}", detail=", ".join(counts), age_ms=proc["age_ms"])
        workers = []
        for task in active[:HELPER_WIDTH]:
            engine = _clip(task.get("engine"), 30) or "a worker"
            model = _clip(task.get("model"), 40)
            status = str(task.get("status") or "").lower()
            doing = {"verifying": "checking", "retrying": "retrying"}.get(status, "")
            label = f"{engine} ({model})" if model and model != engine else engine
            workers.append(_helper("agent", f"{label} on {_clip(task.get('key'), 60) or 'a task'}", detail=doing, age_ms=None))
        if workers:
            node["waiting_on"] = workers
        return node


def _helper(kind: str, label: str, *, detail: str = "", age_ms=None) -> dict:
    item = {"kind": kind, "label": label}
    if detail:
        item["detail"] = detail
    if isinstance(age_ms, (int, float)) and age_ms >= 0:
        item["age_ms"] = int(age_ms)
    return item


def _drop_pipe_filters(nodes: list[dict]) -> list[dict]:
    """`pytest | tail` is waiting on pytest, not tail."""
    if len(nodes) < 2:
        return nodes
    kept = [node for node in nodes if not (node["kind"] == "command" and node["label"].split()[0] in _PIPE_FILTERS)]
    return kept or nodes[:1]


def _is_ringer(args: str) -> bool:
    words = args.split()[:4]
    return any(_base(word) in {"ringer.py", "ringer"} for word in words) and " run " in f" {args} "


def _agent_label(args: str) -> str:
    words = args.split()
    if not words or any(marker in args for marker in _INFRA_MARKERS):
        return ""
    names = [_base(words[0])]
    if names[0] in {"node", "bun", "deno"} and len(words) > 1:
        names.append(_base(words[1]).removesuffix(".js").removesuffix(".mjs"))
    if names[0].startswith("python") and "hermes_cli" in args:
        names.append("hermes")
    for name in names:
        label = _AGENT_NAMES.get(name)
        if not label:
            continue
        rest = words[1:]
        if "exec" in rest[:2]:
            return f"{label} exec"
        if "-p" in rest or "--print" in rest:
            return f"{label} headless"
        return label
    return ""


def _shell_text(args: str) -> str:
    match = re.search(r"eval '(.*?)' < /dev/null", args)
    if match:
        return match.group(1)
    words = args.split()
    if "-c" in words:
        return " ".join(words[words.index("-c") + 1:])
    return ""


def _command_label(words: list[str]) -> str:
    """Program plus up to two plain words. Flags, values, and anything secret-shaped stay out."""
    words = [word for word in words if word]
    while words and (_base(words[0]) in {"env", "nice", "nohup", "time", "timeout", "exec"} or ("=" in words[0] and not words[0].startswith("-")) or (words[0][:1].isdigit() and words[0].rstrip("smhd").isdigit())):
        words = words[1:]
    if not words:
        return ""
    label = [_base(words[0])]
    rest = words[1:]
    if label[0].startswith("python") and rest[:1] == ["-m"] and len(rest) > 1:
        label, rest = [label[0], "-m", rest[1]], rest[2:]
    elif label[0].startswith(("python", "node", "bun", "ruby", "perl")) and rest and not rest[0].startswith("-"):
        label, rest = [_base(rest[0])], rest[1:]
    for word in rest:
        if len(label) >= 3:
            break
        if word.startswith("-") or "=" in word or not _SAFE_WORD.fullmatch(word):
            break
        if any(key in word.lower() for key in _SECRET_KEYS):
            break
        label.append(_base(word) if "/" in word else word)
    text = " ".join(label)
    return "" if any(key in text.lower() for key in _SECRET_KEYS) else text[:80]


def _base(word: str) -> str:
    return word.rstrip("/").rsplit("/", 1)[-1].strip("'\"")


def _claude_desktop_sessions(home: Path, now_ms: int) -> list[dict]:
    sessions = []
    for root in _desktop_roots(home):
        sessions.extend(_desktop_tree(root / "local-agent-mode-sessions", now_ms, cowork=True))
        sessions.extend(_desktop_tree(root / "claude-code-sessions", now_ms, cowork=False))
    return sessions


def _desktop_roots(home: Path):
    return (
        home / "Library/Application Support/Claude",
        home / "Library/Application Support/Claude-3p",
        home / ".config/Claude",
        home / ".config/Claude-3p",
    )


def _desktop_tree(root: Path, now_ms: int, *, cowork: bool) -> list[dict]:
    if not root.is_dir():
        return []
    sessions = []
    for path in _fresh_json(root, now_ms):
        payload = _read_json(path, limit=1_500_000)
        mode = _mode(payload) if isinstance(payload, dict) else ""
        title = _safe_title(payload) if isinstance(payload, dict) else ""
        if not title:
            title = "Cowork" if cowork else path.stem
        if cowork and ("chat" in mode) and ("cowork" not in mode):
            agent = "Claude chat"
        elif cowork or "cowork" in mode:
            agent = "Claude Cowork"
        else:
            agent = "Claude"
        folder = _folder(_first_path(payload) if isinstance(payload, dict) else "") or title
        sessions.append(
            _row(
                repo=folder,
                workspace_path=_first_path(payload) if isinstance(payload, dict) else "",
                display_name=_clip(title),
                path=str(path),
                agent_type=agent,
                surface="Claude Cowork" if agent == "Claude Cowork" else "Claude Chat" if agent == "Claude chat" else "Claude Desktop",
                instance_id=f"claude-desktop:{root.parent.name}:{path.relative_to(root)}",
                instance_name=f"{root.parent.name} · {path.stem}",
                agent_state="working",
                prompt="",
                tool_name="",
                last_output_at=int(path.stat().st_mtime * 1000),
                in_turn=True,
            )
        )
    return sessions


def _fresh_json(root: Path, now_ms: int):
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        if len(rel.parts) > 6:
            dirnames.clear()
            continue
        dirnames[:] = [name for name in dirnames if name not in {"uploads", "outputs", "memory", "Cache"}]
        for name in filenames:
            if not name.endswith(".json"):
                continue
            if name in {"scheduled-tasks.json", "cowork_account_settings.json", "manifest.json"}:
                continue
            if "local-agent-mode-sessions" in Path(dirpath).parts and not name.startswith("local_"):
                continue
            path = Path(dirpath) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_size > 2_000_000:
                continue
            if now_ms - int(stat.st_mtime * 1000) > FRESH_MS:
                continue
            found.append(path)
    return found


def _claude_transcript_ms(home: Path, session_id, cwd: str):
    if not session_id or not cwd:
        return None
    slug = "".join(char if char.isalnum() else "-" for char in str(cwd))
    path = home / ".claude" / "projects" / slug / f"{session_id}.jsonl"
    try:
        return int(path.stat().st_mtime * 1000)
    except OSError:
        return None


def surface_of(item: dict) -> str:
    if item.get("surface"):
        return str(item["surface"])
    agent = str(item.get("agent_type") or "").lower()
    if "codex desktop" in agent:
        return "Codex Desktop"
    if "cowork" in agent:
        return "Claude Cowork"
    if "hermes" in agent:
        return "Hermes"
    return "Terminal" if not agent else "CLI"


def _session_key(item: dict) -> tuple[str, str, str]:
    return (surface_of(item), str(item.get("instance_id") or item.get("path") or ""), _family(item.get("agent_type")))


def _family(agent_type) -> str:
    text = str(agent_type or "").lower()
    for name in ("cowork", "codex", "claude", "hermes", "grok"):
        if name in text:
            return "claude" if name == "cowork" else name
    return text.split()[0] if text else ""


def _row(**fields) -> dict:
    return {
        "repo": fields.get("repo") or "",
        "display_name": fields.get("display_name") or "",
        "path": fields.get("path") or "",
        "workspace_path": fields.get("workspace_path") or "",
        "surface": fields.get("surface") or "",
        "instance_id": fields.get("instance_id") or "",
        "instance_name": fields.get("instance_name") or "",
        "profile": fields.get("profile") or "",
        "status": fields.get("agent_state") or "",
        "archived": False,
        "live": True,
        "last_output_at": fields.get("last_output_at"),
        "agent_type": fields.get("agent_type") or "",
        "agent_state": fields.get("agent_state") or "",
        "prompt": fields.get("prompt") or "",
        "last_message": "",
        "tool_name": fields.get("tool_name") or "",
        "started_at": fields.get("last_output_at"),
        "in_turn": bool(fields.get("in_turn")),
        "waiting_on": fields.get("waiting_on") or [],
    }


def _connect(path: Path):
    if not path.is_file():
        return None
    uri = path.resolve().as_uri()
    for suffix in ("?mode=ro", "?mode=ro&immutable=1"):
        try:
            return sqlite3.connect(uri + suffix, uri=True)
        except sqlite3.Error:
            continue
    return None


def _columns(connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _recent_file(path: Path, now_ms: int) -> bool:
    try:
        modified = int(path.stat().st_mtime * 1000)
    except OSError:
        return False
    return now_ms - modified <= FRESH_MS + 120_000


def _read_json(path: Path, limit: int):
    try:
        if path.stat().st_size > limit:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None


def _safe_title(payload: dict) -> str:
    for key in _TITLE_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("session", "metadata", "task"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            found = _safe_title(nested)
            if found:
                return found
    return ""


def _mode(payload: dict) -> str:
    for key in ("mode", "kind", "surface", "type"):
        value = payload.get(key)
        if isinstance(value, str):
            return value.lower()
    return ""


def _first_path(payload: dict) -> str:
    for key in ("cwd", "workspace", "workspacePath", "folder", "path"):
        value = payload.get(key)
        if isinstance(value, str) and value.startswith(("/", "~")):
            return value
    return ""


def _pid_alive(pid) -> bool:
    try:
        number = int(pid)
    except (TypeError, ValueError):
        return False
    if number <= 0:
        return False
    if os.path.isdir("/proc"):
        return os.path.exists(f"/proc/{number}")
    try:
        os.kill(number, 0)
    except OSError:
        return False
    return True


def _ms(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value > 10_000_000_000:
        return int(value)
    if value <= 0:
        return None
    return int(value * 1000)


def _folder(path) -> str:
    text = str(path or "").rstrip("/")
    if not text:
        return ""
    return text.split("/")[-1]


def _clip(value, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    for key in _SECRET_KEYS:
        if key in text.lower():
            return ""
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
