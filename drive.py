#!/usr/bin/env python3
"""Type continue into one usage or overload stall.

The web page only reads. A session that needs a decision stays on the page.
This file does not send Telegram, does not answer a dialog, and does not
post to a ledger.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

from classify import _board_state, _work_name

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
FLAG = STATE / "drive.enabled"
LOG_PATH = STATE / "drive-log.json"
SNAPSHOT = STATE / "snapshot.json"

COOLDOWN_MS = 45 * 60 * 1000
BOARD_MAX_AGE_MS = 2 * 60 * 1000
IDLE_TIMEOUT_MS = 2000
KEEP_LOG_MS = 7 * 24 * 60 * 60 * 1000
SELF_PATH = str(ROOT)
NUDGE = "continue"
SECRET_WORDS = (
    "token",
    "secret",
    "password",
    "credential",
    "authorization",
    "api_key",
    "apikey",
)
FAMILIES = ("grok", "claude", "codex")
CHOICE_RE = re.compile(r"^\s*(?:[❯>]\s*)?(\d+)\.\s+(.+)$")
USAGE_RE = re.compile(
    r"(usage limit|rate limit|overloaded|too many requests|quota exceeded|hit your limit|resource exhausted|\b529\b)",
    re.I,
)
NOISE_RE = re.compile(
    r"(idle check|cogitated|cooked|saut[eé]ed|worked for|ran \d+ shell|running scheduled)",
    re.I,
)
SECRET_RE = re.compile(
    r"(?i)(sk-[a-z0-9]{8,}|bearer\s+\S+|api[_-]?key\s*[:=]\s*\S+|password\s*[:=]\s*\S+|token\s*[:=]\s*\S+|eyJ[a-zA-Z0-9._-]{20,})"
)


def family_of(agent_type: str) -> str:
    text = (agent_type or "").lower()
    for name in FAMILIES:
        if name in text:
            return name
    return ""


def safe_title(session: dict) -> str:
    title = _work_name(session)
    lowered = title.lower()
    if any(word in lowered for word in SECRET_WORDS):
        return "session"
    return title or "session"


def session_key(session: dict) -> str:
    return f"{session.get('machine_id')}:{session.get('path')}"


def board_is_fresh(snapshot: dict, now_ms: int) -> bool:
    read_at = snapshot.get("read_at")
    return isinstance(read_at, (int, float)) and now_ms - int(read_at) <= BOARD_MAX_AGE_MS


def machine_readable(snapshot: dict, machine_id: str) -> bool:
    for machine in snapshot.get("machines") or []:
        if isinstance(machine, dict) and machine.get("id") == machine_id:
            return machine.get("read_ok") is not False
    return False


def classify_session(session: dict, snapshot: dict, now_ms: int) -> dict | None:
    """Return a hold or nudge for a stalled session. Return None otherwise."""
    if _board_state(session, now_ms) != "stalled":
        return None
    path = str(session.get("path") or "").rstrip("/")
    agent_state = (session.get("agent_state") or "").lower()
    agent_type = session.get("agent_type") or ""
    decision = {
        "key": session_key(session),
        "title": safe_title(session),
        "machine_id": session.get("machine_id") or "",
        "where": session.get("machine") or "that machine",
        "path": path,
        "sort_ms": int(session.get("last_output_at") or 0),
        "family": family_of(agent_type),
    }
    if path == SELF_PATH:
        return {**decision, "action": "hold", "reason": "This desk stays read-only."}
    if path.startswith("hermes:") or agent_type.lower().startswith("hermes"):
        return {**decision, "action": "hold", "reason": "Hermes owns that session."}
    note = f"{session.get('last_message') or ''} {session.get('prompt') or ''}"
    if agent_state in {"error", "failed"} and _is_usage(note):
        return {**decision, "action": "nudge", "reason": "It stalled from usage or overload."}
    if agent_state == "waiting":
        return {**decision, "action": "ask", "reason": "It needs a decision from you."}
    if agent_state in {"error", "failed"}:
        return {**decision, "action": "ask", "reason": "The session hit an error."}
    if session.get("machine_id") == "mac-mini":
        return {**decision, "action": "hold", "reason": "The Mac mini has no Orca terminal."}
    if not decision["family"]:
        return {**decision, "action": "hold", "reason": "No Orca agent is attached."}
    if not path.startswith("/"):
        return {**decision, "action": "hold", "reason": "There is no workspace path."}
    if not machine_readable(snapshot, decision["machine_id"]):
        return {**decision, "action": "hold", "reason": "That machine was not read."}
    return {**decision, "action": "nudge", "reason": "It stalled from usage or overload."}


def choose_terminal(terminals: list[dict], path: str, family: str) -> dict | None:
    matches = []
    for term in terminals:
        if term.get("worktreePath") != path:
            continue
        if not term.get("connected") or not term.get("writable"):
            continue
        if not term.get("handle"):
            continue
        identity = (term.get("agentIdentity") or "").lower()
        if family not in identity:
            continue
        matches.append(term)
    if not matches:
        return None
    matches.sort(key=lambda term: (bool(term.get("orphaned")), -int(term.get("lastOutputAt") or 0)))
    return matches[0]


def cooled(log: dict, key: str, now_ms: int, window: int = COOLDOWN_MS) -> bool:
    stamp = (log.get("at") or {}).get(key)
    return isinstance(stamp, (int, float)) and now_ms - int(stamp) < window


def scrub(text: str) -> str:
    cleaned = " ".join(str(text or "").split())
    return SECRET_RE.sub("[secret]", cleaned)


def public_need(text: str) -> str:
    cleaned = scrub(text)
    lowered = cleaned.lower()
    if any(word in lowered for word in SECRET_WORDS):
        return "It needs a decision from you."
    return cleaned[:280]


def _is_usage(text: str) -> bool:
    return bool(USAGE_RE.search(text or ""))


def parse_screen(lines: list[str]) -> dict:
    """Classify a terminal screen as a decision, a usage stall, or quiet."""
    clean = []
    for line in lines:
        text = scrub(line).strip(" ·")
        if not text:
            continue
        if set(text) <= set("▔─▁▂▃▄▅▆▇█▀ _-=."):
            continue
        clean.append(text)
    if any(_is_usage(text) for text in clean):
        return {"kind": "usage", "text": ""}
    choices = []
    first_choice = None
    for index, text in enumerate(clean):
        match = CHOICE_RE.match(text)
        if not match:
            continue
        if first_choice is None:
            first_choice = index
        choices.append(match.group(2).strip(" ·"))
    if choices:
        heading = _heading(clean, first_choice or 0)
        numbered = " ".join(f"{number}. {choice}" for number, choice in enumerate(choices, 1))
        text = f"{heading} {numbered}".strip()
        return {"kind": "ask", "text": public_need(text)}
    for text in reversed(clean):
        if NOISE_RE.search(text):
            continue
        lowered = text.lower()
        if text.endswith("?") or lowered.startswith("do you ") or "would you" in lowered:
            return {"kind": "ask", "text": public_need(text)}
    return {"kind": "quiet", "text": ""}


def _heading(lines: list[str], choice_at: int) -> str:
    kept = []
    for text in lines[max(0, choice_at - 4) : choice_at]:
        if NOISE_RE.search(text) or CHOICE_RE.match(text):
            continue
        if re.search(r"\d+,\d+,\d+", text):
            continue
        if "enter to confirm" in text.lower():
            continue
        kept.append(text.strip(" ·"))
    return " ".join(kept[:2])[:160]


def mark(log: dict, key: str, now_ms: int) -> None:
    log.setdefault("at", {})[key] = now_ms


def prune(log: dict, now_ms: int) -> None:
    kept = {}
    for key, stamp in (log.get("at") or {}).items():
        if isinstance(stamp, (int, float)) and now_ms - int(stamp) < KEEP_LOG_MS:
            kept[key] = int(stamp)
    log["at"] = kept


def line_for(decision: dict, lead: str) -> str:
    return f"{lead} {decision['title']} on {decision['where']}. {decision['reason']}"


def waiting_line(count: int, *, later: bool) -> str:
    noun = "session" if count == 1 else "sessions"
    verb = "waits" if count == 1 else "wait"
    tail = " for the next pass." if later else "."
    return f"{count} other quiet {noun} {verb}{tail}"


def look(decision: dict, gateway) -> dict:
    """Read the terminal screen and sort a decision from a usage stall."""
    if gateway is None or decision.get("action") not in {"ask", "nudge"}:
        return decision
    if decision.get("machine_id") not in DRIVE_MACHINES or not decision.get("family"):
        return decision
    terminal = choose_terminal(
        gateway.list(decision["machine_id"], decision["path"]),
        decision["path"],
        decision["family"],
    )
    if terminal is None:
        return {**decision, "terminal": None}
    parsed = parse_screen(gateway.screen(decision["machine_id"], terminal["handle"]))
    if parsed["kind"] == "ask" and parsed.get("text"):
        decision = {**decision, "action": "ask", "reason": parsed["text"]}
    elif parsed["kind"] == "usage":
        decision = {**decision, "action": "nudge", "reason": "It stalled from usage or overload."}
    return {**decision, "terminal": terminal}


def drive_once(
    snapshot: dict,
    now_ms: int,
    *,
    enabled: bool,
    log: dict,
    gateway,
) -> tuple[list[str], dict]:
    """Continue at most one usage or overload stall. Leave decisions on the page."""
    prune(log, now_ms)
    if not board_is_fresh(snapshot, now_ms):
        return ["The board is stale. No continue was sent."], log

    decisions = []
    for raw in snapshot.get("sessions") or []:
        if isinstance(raw, dict):
            item = classify_session(raw, snapshot, now_ms)
            if item:
                decisions.append(item)

    lines = []
    nudges = []
    for decision in decisions:
        if decision["action"] == "hold":
            key = "hold:" + decision["key"]
            if not cooled(log, key, now_ms):
                lines.append(line_for(decision, "Held"))
                mark(log, key, now_ms)
            continue
        if decision["action"] == "nudge" and cooled(log, "send:" + decision["key"], now_ms):
            continue
        seen = look(decision, gateway)
        if seen["action"] == "nudge":
            nudges.append(seen)

    nudges.sort(key=lambda item: item["sort_ms"])
    if not nudges:
        return lines, log
    if not enabled or gateway is None:
        target = nudges[0]
        key = "plan:" + target["key"]
        if not cooled(log, key, now_ms):
            lines.append(line_for(target, "Would continue"))
            if len(nudges) > 1:
                lines.append(waiting_line(len(nudges) - 1, later=False))
            mark(log, key, now_ms)
        return lines, log

    for index, target in enumerate(nudges):
        terminal = target.get("terminal")
        if terminal is None:
            held = {**target, "reason": "No matching Orca terminal is open."}
            lines.append(line_for(held, "Held"))
            mark(log, "hold:" + target["key"], now_ms)
            continue
        if not gateway.idle(target["machine_id"], terminal["handle"]):
            held = {**target, "reason": "The terminal is not idle."}
            lines.append(line_for(held, "Held"))
            continue
        if not gateway.send(target["machine_id"], terminal["handle"]):
            held = {**target, "reason": "The continue did not land."}
            lines.append(line_for(held, "Held"))
            continue
        sent = {**target, "reason": "It stalled from usage or overload."}
        lines.append(line_for(sent, "Sent continue to"))
        mark(log, "send:" + target["key"], now_ms)
        rest = len(nudges) - index - 1
        if rest:
            lines.append(waiting_line(rest, later=True))
        break
    return lines, log


class OrcaGateway:
    """Talk to Orca on this box, or over SSH on another machine. Drop terminal text."""

    def list(self, machine_id: str, path: str) -> list[dict]:
        payload = self._run(machine_id, ["terminal", "list", "--worktree", f"path:{path}", "--json"], 20)
        terminals = ((payload or {}).get("result") or {}).get("terminals") or []
        clean = []
        for term in terminals:
            if not isinstance(term, dict):
                continue
            last = term.get("lastOutputAt")
            clean.append(
                {
                    "handle": term.get("handle") or "",
                    "connected": bool(term.get("connected")),
                    "writable": bool(term.get("writable")),
                    "orphaned": bool(term.get("orphaned")),
                    "worktreePath": term.get("worktreePath") or "",
                    "agentIdentity": term.get("agentIdentity") or "",
                    "lastOutputAt": int(last) if isinstance(last, (int, float)) else 0,
                }
            )
        return clean

    def idle(self, machine_id: str, handle: str) -> bool:
        payload = self._run(
            machine_id,
            [
                "terminal",
                "wait",
                "--terminal",
                handle,
                "--for",
                "tui-idle",
                "--timeout-ms",
                str(IDLE_TIMEOUT_MS),
                "--json",
            ],
            15,
        )
        return bool(payload and payload.get("ok") is True)

    def screen(self, machine_id: str, handle: str) -> list[str]:
        payload = self._run(
            machine_id,
            ["terminal", "read", "--terminal", handle, "--screen", "--json"],
            20,
        )
        terminal = ((payload or {}).get("result") or {}).get("terminal") or {}
        tail = terminal.get("tail") or []
        return [str(line) for line in tail if isinstance(line, str)]

    def send(self, machine_id: str, handle: str) -> bool:
        payload = self._run(
            machine_id,
            ["terminal", "send", "--terminal", handle, "--text", NUDGE, "--enter", "--json"],
            20,
        )
        return bool(payload and payload.get("ok") is True)

    def _run(self, machine_id: str, args: list[str], timeout: int) -> dict | None:
        spec = DRIVE_MACHINES.get(machine_id)
        if not isinstance(spec, dict):
            return None
        command = spec.get("command")
        if not isinstance(command, list) or not command:
            command = ["orca-ide"]
        ssh = spec.get("ssh")
        if isinstance(ssh, list) and ssh:
            remote = " ".join(shlex.quote(part) for part in [*command, *args])
            command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", *ssh, remote]
        else:
            command = [*command, *args]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            return None
        raw = completed.stdout or ""
        start = raw.find("{")
        if start < 0:
            return None
        try:
            payload = json.loads(raw[start:])
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None


def load_json(path: Path, fallback: dict) -> dict:
    if not path.exists():
        return fallback
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    return payload if isinstance(payload, dict) else fallback


def _load_drive_machines() -> dict:
    """Read drivable machines from drive.json. No file or empty file means driving is off."""
    for path in (STATE / "drive.json", ROOT / "drive.json"):
        if not path.exists():
            continue
        payload = load_json(path, {})
        if payload:
            return payload
    return {}


DRIVE_MACHINES = _load_drive_machines()


def save_log(log: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "tick"
    if command not in {"plan", "tick"}:
        print("Use plan or tick.", file=sys.stderr)
        return 2
    snapshot = load_json(SNAPSHOT, {})
    if not snapshot.get("sessions") and not snapshot.get("read_at"):
        print("No board snapshot.")
        return 1
    now_ms = int(time.time() * 1000)
    enabled = command == "tick" and FLAG.is_file()
    log = load_json(LOG_PATH, {"at": {}})
    if not isinstance(log.get("at"), dict):
        log = {"at": {}}
    lines, log = drive_once(
        snapshot,
        now_ms,
        enabled=enabled,
        log=log,
        gateway=OrcaGateway(),
    )
    save_log(log)
    if lines:
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
