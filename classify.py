"""Turn a collected snapshot into a session list.

A session stays on the list while an agent is in a turn, while it is waiting
on a helper or a long command, or while output arrived in the last 15
minutes. Quiet workspaces and machines with nothing running are left off the
list. This module does not start agents.
"""

from __future__ import annotations

from datetime import datetime
from apps import surface_of

FRESH_MS = 15 * 60 * 1000
WAITING_MS = 6 * 60 * 60 * 1000
LOCAL_ID = "local"
RANKS = {"attention": 0, "stalled": 1, "working": 2, "helpers": 3, "blocked": 4, "finished": 5}

TOOL_PHRASES = {
    "run_terminal_command": "running a command",
    "read_file": "reading a file",
    "grep": "searching the project",
    "search_replace": "editing a file",
    "write": "writing a file",
    "web_search": "searching the web",
    "open_page": "opening a page",
}


def classify(snapshot: dict, *, now_ms: int) -> dict:
    machines = _machines(snapshot)
    sessions = []
    for raw in snapshot.get("sessions") or []:
        if not isinstance(raw, dict):
            continue
        if not _machine_readable(machines, raw.get("machine_id")):
            continue
        state = _board_state(raw, now_ms)
        if state is None:
            continue
        sessions.append(_session_row(raw, state, now_ms))

    sessions.extend(_attention_rows(machines))
    sessions.sort(key=lambda row: (row["rank"], -row["sort_ms"]))

    read_at = int(snapshot.get("read_at") or 0)
    view = {
        "stale": bool(read_at) and now_ms - read_at > FRESH_MS,
        "read_at_label": fmt_time(read_at) if read_at else "unknown",
        "overview": _overview(sessions, machines),
        "sessions": sessions,
        "locations": [clean(machine.get("label") or machine.get("id"), 60) for machine in machines],
        "machines": [
            {"label": clean(machine.get("label") or machine.get("id"), 60),
             "online": machine.get("online"), "read_ok": machine.get("read_ok")}
            for machine in machines
        ],
    }
    partial = [f"{clean(machine.get('label') or machine.get('id'), 60)}: {clean(machine['error'], 220)}" for machine in machines if machine.get("error") and machine.get("read_ok") is not False]
    if partial:
        view["warning"] = "Some session sources could not be fully read. " + " ".join(partial)
    return view


def keep_session(session: dict, now_ms: int) -> bool:
    return _board_state(session, now_ms) is not None


def clean(text, limit: int = 180) -> str:
    if text is None:
        return ""
    squashed = " ".join(str(text).split())
    for token in ("**", "__", "`"):
        squashed = squashed.replace(token, "")
    return squashed[:limit]


def fmt_time(ms: int) -> str:
    if not ms:
        return "unknown"
    when = datetime.fromtimestamp(ms / 1000).astimezone()
    return when.strftime("%Y-%m-%d %H:%M")


def format_age(delta_ms: int) -> str:
    if delta_ms < 45_000:
        return "just now"
    minutes = delta_ms // 60_000
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} h"
    days = hours // 24
    return f"{days} d"


def _machines(snapshot: dict) -> list[dict]:
    machines = snapshot.get("machines")
    if isinstance(machines, list) and machines:
        return [item for item in machines if isinstance(item, dict)]
    return [
        {"id": LOCAL_ID, "label": "This box", "online": True, "read_ok": True, "error": None},
    ]


def _machine_readable(machines: list[dict], machine_id) -> bool:
    if not machine_id:
        return True
    for machine in machines:
        if machine.get("id") == machine_id:
            return machine.get("read_ok") is not False
    return True


def _board_state(session: dict, now_ms: int) -> str | None:
    if session.get("archived"):
        return None
    state = (session.get("agent_state") or "").lower()
    fresh = _is_fresh(session.get("last_output_at"), now_ms)
    in_turn = bool(session.get("in_turn"))
    if state == "waiting":
        last = session.get("last_output_at")
        if isinstance(last, (int, float)) and now_ms - int(last) <= WAITING_MS:
            return "stalled"
        return None
    if state in {"error", "failed"}:
        return "stalled"
    waits = _waits(session)
    if waits:
        return "helpers" if _has_helper(waits) else "blocked"
    if state == "working" or (in_turn and state not in {"done", "idle"}):
        if fresh or not isinstance(session.get("last_output_at"), (int, float)):
            return "working"
        return "stalled"
    if state in {"done", "idle"} and fresh:
        return "finished"
    if fresh and session.get("live") and not state:
        return "working"
    return None


def _session_row(session: dict, state: str, now_ms: int) -> dict:
    session_name = _work_name(session)
    product = clean(session.get("product"), 100)
    project = clean(session.get("project_name"), 100)
    title = product or project or session_name
    age = _age_label(session.get("last_output_at"), now_ms)
    machine = clean(session.get("machine") or "this box", 60) or "this box"
    line = _wait_line(session, title) if state in {"helpers", "blocked"} else _line(session, title)
    surface = clean(surface_of(session), 80)
    instance_id = clean(session.get("instance_id"), 400)
    instance_name = clean(session.get("instance_name"), 100) or (instance_id.rsplit(":", 1)[-1][-12:] if instance_id else "")
    return {
        "state": state,
        "rank": RANKS[state],
        "title": title,
        "key": f"{session.get('machine_id') or machine}:{surface}:{instance_id}" if instance_id else f"{session.get('machine_id') or machine}:{session.get('path') or session_name}:{session.get('agent_type') or ''}",
        "surface": surface,
        "instance_id": instance_id,
        "instance_name": instance_name,
        "profile": clean(session.get("profile"), 80),
        "product": product,
        "project_name": project,
        "repo": clean(session.get("repo"), 120),
        "github_repo": clean(session.get("github_repo"), 160),
        "session_name": session_name,
        "line": line,
        "where": machine,
        "agent": _agent_label(session.get("agent_type")) if session.get("agent_type") else "Terminal",
        "filter_agents": _filter_agents(session.get("agent_type")),
        "age": age,
        "stands": _stands(session, machine, line, age, now_ms),
        "sort_ms": int(session.get("last_output_at") or session.get("started_at") or 0),
    }


def _line(session: dict, title: str) -> str:
    state = (session.get("agent_state") or "").lower()
    agent = _agent_label(session.get("agent_type"))
    prompt = _task_text(session.get("prompt"))
    message = _task_text(session.get("last_message"))
    if state == "waiting":
        return f"{agent} is waiting on a dialog in {title}."
    if state in {"done", "idle"} and not message:
        return f"{agent} just finished {title}."
    if state in {"done", "idle"} and message:
        return _first_sentence(message, 180)
    if state in {"error", "failed"}:
        if message:
            return _first_sentence(message, 180)
        return f"{agent} hit an error in {title}."
    if prompt and state == "working":
        return _clip(prompt, 180)
    if (state == "working" or session.get("in_turn")) and not prompt and not message:
        return f"{agent} is in a turn in {title}."
    if not session.get("agent_type"):
        return f"A terminal is live in {title}."
    if message:
        return _first_sentence(message, 180)
    return f"{agent} is {state or 'open'} in {title}."


def _stands(session: dict, machine: str, line: str, age: str, now_ms: int) -> list[str]:
    state = (session.get("agent_state") or "").lower()
    agent = _agent_label(session.get("agent_type"))
    prompt = _task_text(session.get("prompt"))
    message = _task_text(session.get("last_message"))
    stands = []
    if state:
        stands.append(f"{agent} is {state} on {machine}.")
    elif session.get("in_turn"):
        stands.append(f"{agent} is in a turn on {machine}.")
    else:
        stands.append(f"A terminal is live on {machine}. No agent is attached.")
    if prompt and prompt not in line:
        stands.append(f"The ask: {_clip(prompt, 220)}.")
    if message and message not in line:
        stands.append(f"Latest note: {_first_sentence(message, 220)}.")
    if state == "working":
        phrase = _tool_phrase(session.get("tool_name"))
        if phrase:
            stands.append(f"Right now it is {phrase}.")
        started = session.get("started_at")
        if isinstance(started, (int, float)):
            start_age = format_age(max(0, now_ms - int(started)))
            if start_age != "just now":
                stands.append(f"This turn started {start_age} ago.")
    for node in _waits(session):
        stands.append(f"Waiting on {_chain(node, now_ms, 0)}.")
    stands.append(_output_sentence(age))
    return stands


def _waits(session: dict) -> list[dict]:
    waits = session.get("waiting_on")
    if not isinstance(waits, list):
        return []
    return [node for node in waits if isinstance(node, dict) and node.get("label")]


def _has_helper(nodes: list[dict]) -> bool:
    for node in nodes:
        if node.get("kind") != "command" or _has_helper(_waits(node)):
            return True
    return False


def _wait_line(session: dict, title: str) -> str:
    agent = _agent_label(session.get("agent_type"))
    waits = _waits(session)
    if len(waits) > 1 and all(node.get("kind") == "subagent" for node in waits):
        what = f"{len(waits)} subagents"
    else:
        what = _phrase(waits[0])
        if len(waits) > 1:
            what += f" and {len(waits) - 1} more"
    return _clip(f"{agent} is waiting on {what} in {title}.", 180)


def _phrase(node: dict) -> str:
    kind = node.get("kind")
    label = clean(node.get("label"), 100)
    detail = clean(node.get("detail"), 100)
    if kind == "subagent":
        text = f"subagent {label}"
    elif kind == "command":
        text = f"the command “{label}”"
    else:
        text = label
    return f"{text} ({detail})" if detail else text


def _chain(node: dict, now_ms: int, depth: int) -> str:
    text = _phrase(node)
    age = node.get("age_ms")
    if isinstance(age, (int, float)) and format_age(int(age)) != "just now":
        text += f" for {format_age(int(age))}"
    below = _waits(node)
    if below and depth < 3:
        parts = [_chain(item, now_ms, depth + 1) for item in below[:3]]
        if len(below) > 3:
            parts.append(f"{len(below) - 3} more")
        text += ", which is waiting on " + _and(parts)
    return text


def _attention_rows(machines: list[dict]) -> list[dict]:
    rows = []
    for machine in machines:
        if machine.get("read_ok") is not False:
            continue
        label = clean(machine.get("label") or machine.get("id") or "machine", 60)
        error = clean(machine.get("error") or "the read failed", 220)
        if machine.get("online") is True:
            line = f"{label} is on Tailscale. The session read failed."
        else:
            line = f"{label} did not answer."
        rows.append(
            {
                "state": "attention",
                "rank": 0,
                "title": label,
                "line": line,
                "where": label,
                "filter_agents": [],
                "age": "unread",
                "stands": [
                    error[:1].upper() + error[1:] + ("" if error.endswith(".") else "."),
                    "Reload this page after the read works.",
                ],
                "sort_ms": 0,
            }
        )
    return rows


def _overview(sessions: list[dict], machines: list[dict]) -> str:
    sentences = []
    by_machine: dict[str, list[dict]] = {}
    for row in sessions:
        if row["state"] == "attention":
            continue
        by_machine.setdefault(row["where"], []).append(row)
    for machine in machines:
        if machine.get("read_ok") is False:
            continue
        label = _sentence_name(machine)
        rows = []
        for row in sessions:
            if row["state"] == "attention":
                continue
            if row["where"] == (machine.get("label") or ""):
                rows.append(row)
        sentence = _machine_sentence(label, rows)
        if sentence:
            sentences.append(sentence)
        elif machine.get("id") != LOCAL_ID:
            if machine.get("online") is False:
                sentences.append(f"{machine.get('label')} is off Tailscale.")
            elif machine.get("online") is True:
                sentences.append(f"{machine.get('label')} is connected. No session is running there.")
    for row in sessions:
        if row["state"] != "attention":
            continue
        sentences.append(row["line"])
    if not sentences:
        return "No session is running."
    return " ".join(sentences)


def _machine_sentence(label: str, rows: list[dict]) -> str | None:
    working = sum(1 for row in rows if row["state"] == "working")
    helpers = sum(1 for row in rows if row["state"] == "helpers")
    blocked = sum(1 for row in rows if row["state"] == "blocked")
    stalled = sum(1 for row in rows if row["state"] == "stalled")
    finished = sum(1 for row in rows if row["state"] == "finished")
    parts = []
    if working:
        parts.append(f"{_noun(working, 'session')} {'is' if working == 1 else 'are'} working")
    if helpers:
        parts.append(f"{_noun(helpers, 'session')} {'is' if helpers == 1 else 'are'} waiting on helpers")
    if blocked:
        parts.append(f"{_noun(blocked, 'session')} {'is' if blocked == 1 else 'are'} waiting on other work")
    if stalled:
        parts.append(f"{_noun(stalled, 'session')} {'is' if stalled == 1 else 'are'} stalled")
    if finished:
        parts.append(f"{_noun(finished, 'session')} just finished")
    if not parts:
        return None
    return f"On {label}, {_and(parts)}."


def _sentence_name(machine: dict) -> str:
    if machine.get("id") == LOCAL_ID:
        return "this box"
    return clean(machine.get("label") or "that machine", 60)


def _noun(count: int, word: str) -> str:
    if count == 1:
        return f"1 {word}"
    return f"{count} {word}s"


def _and(parts: list[str]) -> str:
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _work_name(session: dict) -> str:
    repo = clean(session.get("repo") or "", 80)
    display = clean(session.get("display_name") or "", 80)
    generic = {"", "main", "master", "develop", "dev", "trunk", "head"}
    if display.lower() not in generic and display != repo:
        return display
    return repo or display or "session"


def _agent_label(agent_type) -> str:
    raw = clean(agent_type or "agent", 80)
    parts = [part for part in raw.split(" and ") if part]
    labels = []
    for part in parts:
        labels.append(part[:1].upper() + part[1:] if part else "Agent")
    return " and ".join(labels) if labels else "Agent"


def _filter_agents(agent_type) -> list[str]:
    if not agent_type:
        return ["Terminal"]
    agents = []
    for part in clean(agent_type, 120).split(" and "):
        text = part.lower()
        if "codex" in text:
            label = "Codex"
        elif "claude" in text or "cowork" in text:
            label = "Claude"
        elif "hermes" in text:
            label = "Hermes"
        elif "grok" in text:
            label = "Grok"
        else:
            label = _agent_label(part)
        if label not in agents:
            agents.append(label)
    return agents


def _task_text(text) -> str:
    cleaned = clean(text, 400)
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        pieces = cleaned.split(None, 1)
        cleaned = pieces[1] if len(pieces) > 1 else cleaned
    return cleaned


def _first_sentence(text: str, limit: int) -> str:
    clip = _clip(text, limit)
    if ". " in clip and len(clip.split(". ", 1)[0]) >= 40:
        return clip.split(". ", 1)[0] + "."
    return clip


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _tool_phrase(name) -> str:
    if not name:
        return ""
    if name in TOOL_PHRASES:
        return TOOL_PHRASES[name]
    return str(name).replace("_", " ")


def _output_sentence(age: str) -> str:
    if age == "an unknown time":
        return "No output is recorded."
    if age == "just now":
        return "Output arrived just now."
    return f"Last output was {age} ago."


def _is_fresh(last_output, now_ms: int) -> bool:
    if not isinstance(last_output, (int, float)):
        return False
    return now_ms - int(last_output) <= FRESH_MS


def _age_label(last_output, now_ms: int) -> str:
    if not isinstance(last_output, (int, float)):
        return "an unknown time"
    return format_age(max(0, now_ms - int(last_output)))
