"""Read live sessions on this machine and on any machines in machines.json.

The local machine is always read. Extra machines are read over SSH with the
collectors in apps.py and projects.py. Tailscale status shows which machines
are online. Quiet workspaces are not stored. This module does not send
terminal input.
"""

from __future__ import annotations

import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from classify import LOCAL_ID, keep_session
from projects import IDENTITY_FIELDS, apply_project_labels

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / "state"
SNAPSHOT_PATH = STATE_DIR / "snapshot.json"
COMMAND_TIMEOUT_S = 28


def _load_project_labels() -> dict:
    """Read optional product labels. A file in state/ wins over the repo root."""
    for path in (STATE_DIR / "project-labels.json", ROOT / "project-labels.json"):
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return {}


PROJECT_LABELS = _load_project_labels()


def _load_machines() -> tuple[dict, ...]:
    """Read extra machines from machines.json. No file means local only."""
    path = ROOT / "machines.json"
    if not path.exists():
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    if not isinstance(payload, list):
        return ()
    machines = []
    for entry in payload:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        ssh = entry.get("ssh")
        if isinstance(ssh, str):
            ssh = ssh.split()
        if not isinstance(ssh, list) or not ssh:
            ssh = None
        machines.append(
            {
                "id": str(entry["id"]),
                "label": str(entry.get("label") or entry["id"]),
                "ssh": ssh,
                "host": str(entry.get("host") or entry["id"]).lower(),
            }
        )
    return tuple(machines)


LOCAL_MACHINE = {"id": LOCAL_ID, "label": "This box", "ssh": None}
MACHINES = (LOCAL_MACHINE, *_load_machines())

SESSION_FIELDS = (
    *IDENTITY_FIELDS,
    "surface",
    "instance_id",
    "instance_name",
    "profile",
    "repo",
    "display_name",
    "path",
    "agent_type",
    "agent_state",
    "prompt",
    "last_message",
    "tool_name",
    "last_output_at",
    "started_at",
    "in_turn",
    "live",
    "archived",
    "status",
    "waiting_on",
)


def collect(previous: dict | None = None, *, now_ms: int) -> dict:
    previous = previous or {}
    prev_sources = previous.get("sources") or {}
    tailscale = _read_tailscale(prev_sources.get("tailscale"), now_ms)
    online = peer_online(tailscale.get("payload") or {}) if tailscale.get("ok") else {}

    snapshot = {
        "read_at": now_ms,
        "sources": {"tailscale": _source_record(tailscale, now_ms)},
        "machines": [],
        "sessions": [],
    }

    def read_machine(spec: dict) -> dict:
        return _read_machine(spec, online.get(spec["id"]), prev_sources.get(spec["id"]), now_ms)

    with ThreadPoolExecutor(max_workers=len(MACHINES)) as pool:
        results = list(pool.map(read_machine, MACHINES))

    for machine, sessions in results:
        snapshot["machines"].append(machine)
        snapshot["sources"][machine["id"]] = {
            "ok": machine.get("read_ok") is not False,
            "error": machine.get("error"),
            "read_at": now_ms,
            "last_good_at": machine.get("last_good_at"),
        }
        snapshot["sessions"].extend(sessions)
    return snapshot


def peer_online(status: dict, machines=MACHINES) -> dict[str, bool]:
    """Map configured machines to Tailscale online flags."""
    found: dict[str, bool] = {}
    peers = status.get("Peer") or {}
    if isinstance(peers, dict):
        values = peers.values()
    elif isinstance(peers, list):
        values = peers
    else:
        values = []
    for peer in values:
        if not isinstance(peer, dict):
            continue
        machine_id = _machine_id(peer.get("HostName") or "", peer.get("DNSName") or "", machines)
        if machine_id is None or machine_id in found:
            continue
        found[machine_id] = bool(peer.get("Online"))
    return found


def apply_turns(worktrees: list[dict], turns: list[dict]) -> list[dict]:
    """Attach live turns to workspaces. A turn with no workspace becomes a session.

    A turn that names its Orca pane attaches to that pane only. Its lock means the
    agent is mid-turn, so it outranks a stale done from the pane's hook state.
    """
    worktrees = [dict(item) for item in worktrees if isinstance(item, dict)]
    remaining = []
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        pane = turn.get("pane_key") or ""
        target = next((item for item in worktrees if pane and str(item.get("instance_id") or "").endswith(":" + pane) and item.get("path") == turn.get("path")), None)
        if target is None:
            remaining.append(turn)
            continue
        target["in_turn"] = True
        if not target.get("agent_type"):
            target["agent_type"] = turn.get("who") or ""
        if (target.get("agent_state") or "") in {"", "done", "idle"}:
            target["agent_state"] = "working"
            target["last_message"] = ""
            target["started_at"] = turn.get("started_at")
        if turn.get("waiting_on"):
            target["waiting_on"] = turn["waiting_on"]
    turns = remaining

    by_path: dict[str, list[str]] = {}
    identity_by_path = {}
    waits_by_path: dict[str, list[dict]] = {}
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        if turn.get("waiting_on") and turn.get("path"):
            waits_by_path.setdefault(turn["path"], []).extend(turn["waiting_on"])
        path = turn.get("path") or ""
        who = (turn.get("who") or "").lower()
        if not path or not who:
            continue
        names = by_path.setdefault(path, [])
        if who not in names:
            names.append(who)
        identity_by_path[path] = {field: turn[field] for field in IDENTITY_FIELDS if turn.get(field)}

    used = set()
    sessions = []
    for item in worktrees:
        if not isinstance(item, dict):
            continue
        session = dict(item)
        path = session.get("path") or ""
        whos = by_path.get(path) or []
        matching = [who for who in whos if not session.get("agent_type") or who in str(session.get("agent_type")).lower()]
        if matching:
            used.update((path, who) for who in matching)
            session["in_turn"] = True
            if waits_by_path.get(path) and not session.get("waiting_on"):
                session["waiting_on"] = waits_by_path[path]
            session.update(identity_by_path.get(path) or {})
            if not session.get("agent_type"):
                session["agent_type"] = " and ".join(whos)
                if not session.get("agent_state"):
                    session["agent_state"] = "working"
        sessions.append(session)

    for path, whos in by_path.items():
        whos = [who for who in whos if (path, who) not in used]
        if not whos:
            continue
        name = path.rstrip("/").split("/")[-1] or path
        candidates = [turn for turn in turns if isinstance(turn, dict) and turn.get("path") == path and turn.get("who", "").lower() in whos and turn.get("instance_id")]
        instances = candidates or [{"who": " and ".join(whos)}]
        for instance in instances:
            sessions.append(
                {
                    **(identity_by_path.get(path) or {}),
                    **{field: instance[field] for field in ("surface", "instance_id", "instance_name") if instance.get(field)},
                    "repo": name,
                    "display_name": name,
                    "path": path,
                    "agent_type": instance["who"],
                    "agent_state": "working",
                    "in_turn": True,
                    "live": True,
                    "last_output_at": None,
                    "prompt": "",
                    "last_message": "",
                    "tool_name": "",
                    "archived": False,
                    "status": "working",
                    "waiting_on": instance.get("waiting_on") or [],
                }
            )
    return sessions


def write_snapshot(snapshot: dict, path: Path = SNAPSHOT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")


def read_snapshot(path: Path = SNAPSHOT_PATH) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_machine(spec: dict, online: bool | None, previous: dict | None, now_ms: int) -> tuple[dict, list[dict]]:
    last_good = None
    if previous and previous.get("ok"):
        last_good = previous.get("last_good_at") or previous.get("read_at")
    machine = {
        "id": spec["id"],
        "label": spec["label"],
        "online": True if spec["ssh"] is None else online,
        "read_ok": True,
        "error": None,
        "last_good_at": last_good,
    }
    if spec["ssh"] is not None and online is False:
        machine["read_ok"] = True
        machine["last_good_at"] = now_ms
        return machine, []

    try:
        payload = _run_probe(spec["ssh"])
    except Exception as exc:
        machine["read_ok"] = False
        machine["error"] = str(exc)[:300]
        return machine, []

    if payload.get("ok") is False and not payload.get("worktrees") and not payload.get("turns"):
        machine["read_ok"] = False
        machine["error"] = str(payload.get("error") or "the read failed")[:300]
        return machine, []

    if spec["ssh"] is None:
        machine["online"] = True
    elif online is None:
        machine["online"] = True
    merged = apply_turns(payload.get("worktrees") or [], payload.get("turns") or [])
    sessions = []
    for item in merged:
        public = _public_session(item, machine)
        if keep_session(public, now_ms):
            sessions.append(public)
    if payload.get("error") and not sessions:
        machine["read_ok"] = False
        machine["error"] = str(payload.get("error"))[:300]
        return machine, []
    machine["read_ok"] = True
    machine["error"] = payload.get("error")
    machine["last_good_at"] = now_ms
    return machine, sessions


def _public_session(item: dict, machine: dict) -> dict:
    session = {key: item.get(key) for key in SESSION_FIELDS}
    session["machine_id"] = machine["id"]
    session["machine"] = machine["label"]
    if session.get("in_turn") is None:
        session["in_turn"] = False
    session["live"] = bool(session.get("live"))
    session["archived"] = bool(session.get("archived"))
    return apply_project_labels(session, PROJECT_LABELS)


def _run_probe(ssh_args: list[str] | None) -> dict:
    if ssh_args is None:
        command = ["python3", "-"]
    else:
        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            "-o",
            "StrictHostKeyChecking=accept-new",
            *ssh_args,
            "python3",
            "-",
        ]
    try:
        completed = subprocess.run(
            command,
            input=REMOTE_PROBE,
            check=False,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("session read timed out") from exc
    if completed.returncode != 0 and "{" not in (completed.stdout or ""):
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(detail[:300] or f"session read exited {completed.returncode}")
    return _extract_json(completed.stdout or "")


def _read_tailscale(previous: dict | None, now_ms: int) -> dict:
    try:
        completed = subprocess.run(
            ["tailscale", "status", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)[:300], "payload": None, "previous": previous, "read_at": now_ms}
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return {"ok": False, "error": detail[:300] or "tailscale status failed", "payload": None, "previous": previous, "read_at": now_ms}
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": "tailscale status was not JSON", "payload": None, "previous": previous, "read_at": now_ms}
    return {"ok": True, "error": None, "payload": payload, "previous": previous, "read_at": now_ms}


def _source_record(result: dict, now_ms: int) -> dict:
    previous = result.get("previous") or {}
    last_good = now_ms if result.get("ok") else None
    if not result.get("ok") and previous.get("ok"):
        last_good = previous.get("last_good_at") or previous.get("read_at")
    return {
        "ok": bool(result.get("ok")),
        "error": result.get("error"),
        "read_at": now_ms,
        "last_good_at": last_good,
    }


def _peer_tokens(machine: dict) -> set[str]:
    label = str(machine.get("label") or "").lower()
    tokens = {
        str(machine.get("id") or "").lower(),
        str(machine.get("host") or "").lower(),
        label,
        label.replace(" ", "-"),
        label.replace(" ", ""),
    }
    tokens.discard("")
    return tokens


def _machine_id(host: str, dns: str, machines) -> str | None:
    """Match a Tailscale peer to a configured machine by exact name or label words."""
    short = dns.rstrip(".").split(".")[0].lower()
    host_name = host.lower().replace("’", "'").replace("‘", "'")
    for machine in machines:
        if machine.get("id") == LOCAL_ID:
            continue
        tokens = _peer_tokens(machine)
        if short in tokens or host_name in tokens:
            return str(machine["id"])
        words = [word for word in re.split(r"[\s-]+", str(machine.get("label") or "")) if len(word) >= 3]
        if words and all(word in host_name for word in words):
            return str(machine["id"])
    return None


def _extract_json(stdout: str) -> dict:
    start = stdout.find("{")
    if start < 0:
        raise RuntimeError("session read returned no JSON")
    try:
        payload = json.loads(stdout[start:])
    except json.JSONDecodeError as exc:
        raise RuntimeError("session read returned unreadable JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("session read returned the wrong shape")
    return payload


def _remote_probe() -> str:
    apps = (ROOT / "apps.py").read_text(encoding="utf-8")
    projects = (ROOT / "projects.py").read_text(encoding="utf-8")
    return apps + "\n\n" + projects + "\n\n" + _PROBE_MAIN


_PROBE_MAIN = r"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

FRESH_FIELDS = (
    "repo",
    "displayName",
    "path",
    "status",
    "isArchived",
    "liveTerminalCount",
    "hasAttachedPty",
    "lastOutputAt",
)

def run(command, timeout):
    return subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)

def clip(value, limit=400):
    if not value:
        return ""
    text = " ".join(str(value).split())
    return text[:limit]

def extract_json(stdout):
    start = stdout.find("{")
    if start < 0:
        raise RuntimeError("orca-ide returned no JSON")
    payload = json.loads(stdout[start:])
    if isinstance(payload, dict) and payload.get("ok") is False:
        raise RuntimeError("orca-ide reported ok=false")
    return payload

def slim_worktree(item, lead=None, terminal=None):
    lead = lead or {}
    terminal = terminal or {}
    state = lead.get("state") or ""
    pane = lead.get("paneKey") or (str(terminal.get("tabId") or "") + ":" + str(terminal.get("leafId") or ""))
    return {
        "surface": "Orca",
        "instance_id": "orca:" + str(item.get("worktreeInstanceId") or item.get("path")) + ":" + pane,
        "instance_name": "Terminal " + (pane.split(":")[-1][:8] or str(item.get("displayName") or "workspace")),
        "repo": item.get("repo") or "",
        "display_name": item.get("displayName") or item.get("repo") or "",
        "path": item.get("path") or "",
        "status": item.get("status") or "",
        "archived": bool(item.get("isArchived")),
        "live": bool(item.get("liveTerminalCount") or item.get("hasAttachedPty")),
        "last_output_at": terminal.get("lastOutputAt") or lead.get("updatedAt") or item.get("lastOutputAt"),
        "agent_type": lead.get("agentType") or terminal.get("agentIdentity") or "",
        "agent_state": state,
        "prompt": clip(lead.get("prompt")),
        "last_message": clip(lead.get("lastAssistantMessage")),
        "tool_name": lead.get("toolName") or "",
        "started_at": lead.get("stateStartedAt"),
        "in_turn": state == "working",
    }

def read_orca():
    if not shutil.which("orca-ide"):
        return [], None
    completed = run(["orca-ide", "worktree", "ps", "--limit", "1000", "--json"], 18)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(detail[:300] or "orca-ide failed")
    payload = extract_json(completed.stdout or "")
    worktrees = (payload.get("result") or {}).get("worktrees") or []
    terminals = []
    warning = None
    try:
        result = run(["orca-ide", "terminal", "list", "--limit", "1000", "--json"], 6)
        if result.returncode != 0:
            raise RuntimeError("terminal list failed")
        terminals = (extract_json(result.stdout).get("result") or {}).get("terminals") or []
    except Exception:
        warning = "Orca terminal instances could not be read; agent-state instances are shown."
    sessions = []
    for item in worktrees:
        if not isinstance(item, dict):
            continue
        attached = [t for t in terminals if t.get("worktreePath") == item.get("path") and t.get("connected")]
        by_pane = {str(t.get("tabId")) + ":" + str(t.get("leafId")): t for t in attached}
        used = set()
        for agent in item.get("agents") or []:
            pane = agent.get("paneKey") or ""
            used.add(pane)
            sessions.append(slim_worktree(item, agent, by_pane.get(pane)))
        for pane, terminal in by_pane.items():
            if pane not in used:
                sessions.append(slim_worktree(item, terminal=terminal))
        if not attached and not item.get("agents"):
            sessions.append(slim_worktree(item))
    return sessions, warning

def useful_cwd(path):
    if not path or path in {"/", "/root"}:
        return False
    home = os.path.expanduser("~")
    if path == home:
        return False
    blocked = {".hermes", ".cursor", ".cursor-proxy", ".codex", "node_modules", ".config"}
    return not any(part in blocked for part in path.split("/"))

def arg_value(parts, flag):
    for index, part in enumerate(parts):
        if part == flag and index + 1 < len(parts):
            return parts[index + 1]
        if part.startswith(flag + "="):
            return part.split("=", 1)[1]
    return ""

def agent_env(pid):
    # Only the two keys that link a turn to its pane and Codex home are read.
    found = {}
    try:
        raw = open(os.path.join("/proc", str(pid), "environ"), "rb").read().split(b"\0")
    except OSError:
        return found
    for piece in raw:
        key, _, value = piece.partition(b"=")
        if key in (b"ORCA_PANE_KEY", b"CODEX_HOME"):
            found[key.decode()] = value.decode("utf-8", "replace")[:300]
    return found

def read_turns(processes=None, now_ms=None):
    turns = []
    if not os.path.isdir("/proc"):
        return turns
    processes = processes if processes is not None else read_processes()
    now_ms = now_ms or int(time.time() * 1000)
    parents = {item["pid"]: item.get("ppid") for item in processes}
    ages = {item["pid"]: item.get("age_ms") for item in processes}
    tree = _ProcessTree(processes, _ringer_runs(Path.home(), now_ms))
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        cmdline_path = os.path.join("/proc", name, "cmdline")
        cwd_link = os.path.join("/proc", name, "cwd")
        try:
            raw = open(cmdline_path, "rb").read().split(b"\0")
        except OSError:
            continue
        parts = [piece.decode("utf-8", "replace") for piece in raw if piece]
        if "systemd-inhibit" not in " ".join(parts):
            continue
        who = arg_value(parts, "--who").lower()
        why = arg_value(parts, "--why").lower()
        if who not in {"grok", "codex", "claude"}:
            continue
        if "turn" not in why and "running" not in why:
            continue
        try:
            cwd = os.path.realpath(cwd_link)
        except OSError:
            continue
        if not useful_cwd(cwd):
            continue
        turn = {"path": cwd, "who": who, "surface": "CLI turn", "instance_id": "turn:" + name, "instance_name": "Process " + name}
        if isinstance(ages.get(int(name)), int):
            turn["started_at"] = now_ms - ages[int(name)]
        agent = parents.get(int(name))
        if agent:
            env = agent_env(agent)
            if env.get("ORCA_PANE_KEY"):
                turn["pane_key"] = env["ORCA_PANE_KEY"]
            waits = tree.helpers_of(agent)
            if who == "codex":
                home = Path(env.get("CODEX_HOME") or Path.home() / ".codex")
                waits = codex_turn_waits(home, cwd, now_ms) + waits
            if waits:
                turn["waiting_on"] = waits[:HELPER_WIDTH]
        turns.append(turn)
    return turns

def main():
    error = None
    worktrees = []
    try:
        worktrees, error = read_orca()
    except Exception as exc:
        error = str(exc)[:300]
        worktrees = []
    now_ms = int(time.time() * 1000)
    processes = read_processes()
    try:
        apps = read_app_sessions(Path.home(), now_ms, processes)
    except Exception:
        apps = []
        error = (error + " " if error else "") + "App session sources could not be read."
    worktrees = merge_app_sessions(worktrees, apps)
    turns = []
    try:
        turns = read_turns(processes, now_ms)
    except Exception:
        turns = []
    enrich_projects(worktrees + turns)
    ok = not (error and not worktrees and not turns)
    json.dump({"ok": ok, "error": error, "worktrees": worktrees, "turns": turns}, sys.stdout)

main()
"""

REMOTE_PROBE = _remote_probe()


def main() -> None:
    import time

    previous = read_snapshot()
    snapshot = collect(previous, now_ms=int(time.time() * 1000))
    write_snapshot(snapshot)
    counts = []
    for machine in snapshot["machines"]:
        count = sum(1 for session in snapshot["sessions"] if session.get("machine_id") == machine["id"])
        counts.append(f"{machine['id']}={count}")
    print(f"wrote {SNAPSHOT_PATH} " + " ".join(counts))


if __name__ == "__main__":
    main()
