"""Render the session tracker as a list or a kanban board."""

from __future__ import annotations

from html import escape
from pathlib import Path
import json
import re

BADGES = {
    "attention": "Unread",
    "stalled": "Stalled",
    "working": "Working",
    "helpers": "On helpers",
    "blocked": "Waiting",
    "finished": "Finished",
}


COLUMNS = (
    ("working", "Working"),
    ("helpers", "Waiting on helpers"),
    ("blocked", "Waiting on other work"),
    ("stalled", "Stalled"),
    ("finished", "Finished"),
    ("attention", "Unread"),
)


def render_page(view: dict) -> str:
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="referrer" content="no-referrer">',
        '<script>try { document.documentElement.dataset.theme = localStorage.getItem("cc-theme") === "standard" ? "standard" : "arcade"; } catch {}</script>',
        "<title>Central Command</title>",
        '<link rel="preconnect" href="https://fonts.googleapis.com">',
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>',
        '<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;650;700;750;800&family=IBM+Plex+Mono:wght@400;500&family=Press+Start+2P&family=VT323&display=swap" rel="stylesheet">',
        f"<style>{_CSS}</style>",
        "</head>",
        '<body data-theme="arcade">',
        '<aside class="rail" aria-label="Workspace navigation">',
        '<a class="brand" href="/" aria-label="Central Command home"><span class="brand-mark" aria-hidden="true">c<span>c</span></span><span>Central<br>Command<span class="brand-caption">PERSONAL WORKSPACE</span></span></a>',
        '<p class="rail-label">WORKSPACE</p>',
        '<nav aria-label="Views"><button type="button" class="nav-link" data-view="list" aria-pressed="true"><span aria-hidden="true">☰</span> Sessions <span class="nav-total"></span></button><button type="button" class="nav-link" data-view="board" aria-pressed="false"><span aria-hidden="true">▥</span> Kanban</button></nav>',
        '<div class="rail-bottom"><span class="rail-status"><i class="dot"></i> Automatic updates</span><p>Your workspace, in view.<br>Across connected machines.</p><span class="privacy">◈ &nbsp; Private · Read-only</span></div></aside>',
        '<main class="wrap">',
        '<header class="appbar"><span class="breadcrumb">Workspace <span>/</span> Command Center</span><button type="button" class="theme-toggle" aria-label="Switch to standard theme">☀</button></header>',
        '<div class="top">',
        "<div>",
        '<p class="kicker">YOUR WORKSPACE, AT A GLANCE</p>',
        "<h1>Sessions</h1>",
        '<p class="subtitle">A little clarity for everything in motion.</p>',
        "</div>",
        '<span class="refresh-label"><i class="dot"></i> Auto-refresh</span>',
        "</div>",
        f'<div id="board">{render_board(view)}</div>',
        "</main>",
        f"<script>{_SCRIPT}</script>",
        "</body></html>",
    ]
    return "\n".join(parts)


def render_board(view: dict) -> str:
    sessions = view.get("sessions") or []
    parts = [
        f'<div class="live" data-stamp="{escape(_stamp(view), quote=True)}">',
        _filters(view),
        _machines(view),
        '<div class="alerts">',
    ]
    if view.get("stale"):
        parts.append(
            '<p class="banner">This snapshot is older than 15 minutes. The next read replaces it.</p>'
        )
    if view.get("warning"):
        parts.append(f'<p class="banner">{escape(view["warning"])}</p>')
    parts.append('</div><section class="session-panel" aria-label="Sessions">')
    parts.append('<div class="panel-head"><div class="state-tabs" role="group" aria-label="Session status">')
    for state, label in (("", "All sessions"), *COLUMNS):
        count = sum(1 for row in sessions if not state or row.get("state") == state)
        parts.append(f'<button type="button" data-state="{state}" aria-pressed="{"false" if state else "true"}">{label}<span>{count}</span></button>')
    parts.append('</div><div class="switch" role="group" aria-label="Layout"><button type="button" data-view="list" aria-pressed="true">List</button><button type="button" data-view="board" aria-pressed="false">Kanban</button></div></div>')
    parts.append('<div class="results-head"><p class="filter-status" aria-live="polite"></p><span class="results-hint">Click a session to see more <span aria-hidden="true">↗</span></span></div>')
    parts.append('<p class="empty filter-empty" hidden>No sessions match these filters.</p>')
    parts.append('<div class="list">')
    for session in sessions:
        parts.append(_session(session))
    parts.append(f'<p class="empty all-empty"{" hidden" if sessions else ""}>Nothing is running.</p></div>')
    parts.append('<div class="kanban">')
    for key, title in COLUMNS:
        rows = [session for session in sessions if session.get("state") == key]
        parts.append(f'<section class="column" aria-labelledby="col-{key}">')
        parts.append(
            f'<div class="column-head"><h2 id="col-{key}">{title}</h2>'
            f'<span class="count">{len(rows)}</span></div>'
        )
        if rows:
            for session in rows:
                parts.append(_session(session))
        parts.append(f'<p class="empty column-empty"{" hidden" if rows else ""}>None.</p>')
        parts.append("</section>")
    parts.append('</div></section><footer class="board-footer">')
    parts.append(f'<span class="read">Updated {escape(view.get("read_at_label") or "unknown")}</span>')
    parts.append('<details class="about"><summary>About this view</summary><div>')
    parts.append(f'<p class="overview">{escape(view.get("overview") or "No session is running.")}</p>')
    parts.append('<p>Sessions appear while an agent is working, waiting on a dialog, waiting on subagents, a Ringer run, or a long command, or has produced output in the last 15 minutes. This page only reads.</p></div></details></footer></div>')
    return "\n".join(parts)


def _filters(view: dict) -> str:
    sessions = view.get("sessions") or []
    locations = list(dict.fromkeys([*(view.get("locations") or []), *(row.get("where") for row in sessions if row.get("where"))]))
    agents = sorted({agent for row in sessions for agent in row.get("filter_agents") or []})
    surfaces = sorted({row["surface"] for row in sessions if row.get("surface")})
    instances = sorted([(row["key"], " · ".join([row.get("title") or "", row.get("where") or "", row.get("surface") or "", row.get("instance_name") or row["instance_id"]])) for row in sessions if row.get("instance_id")], key=lambda item: item[1])
    parts = ['<div class="toolbar"><label class="search-field"><span aria-hidden="true">⌕</span><input id="session-search" type="search" placeholder="Find a project, repo, or session…" aria-label="Search sessions" autocomplete="off"><kbd aria-hidden="true">/</kbd></label><div class="filters" role="group" aria-label="Session filters">']
    for name, choices, all_label in (("location", [(v, v) for v in locations], "All locations"), ("agent", [(v, v) for v in agents], "All agents"), ("surface", [(v, v) for v in surfaces], "All surfaces"), ("instance", instances, "All instances")):
        parts.append(f'<label for="filter-{name}">{name.title()}<select id="filter-{name}" data-filter="{name}">')
        parts.append(f'<option value="">{all_label}</option>')
        parts.extend(f'<option value="{escape(value, quote=True)}">{escape(label)}</option>' for value, label in choices)
        parts.append('</select></label>')
    parts.append('<button type="button" class="clear-filters" aria-label="Clear filters" disabled>Reset</button></div></div>')
    return "".join(parts)


def _machines(view: dict) -> str:
    machines = view.get("machines") or [{"label": label} for label in view.get("locations") or []]
    parts = [f'<div class="machines" aria-label="Locations" data-render="{escape(json.dumps(machines, sort_keys=True), quote=True)}">']
    for machine in machines:
        label = machine.get("label") or "Machine"
        state = "unread" if machine.get("read_ok") is False else "online" if machine.get("online") is True else "offline" if machine.get("online") is False else "unknown"
        word = {"unread": "Read failed", "online": "Connected", "offline": "Offline", "unknown": "Unknown"}[state]
        parts.append(f'<button type="button" class="machine {state}" data-location-choice="{escape(label, quote=True)}" aria-pressed="false"><span class="machine-icon" aria-hidden="true">▣</span><span class="machine-name">{escape(label)}</span><span class="machine-state"><i class="dot"></i>{word}</span><span class="machine-count"></span></button>')
    parts.append('</div>')
    return "".join(parts)


def _stamp(view: dict) -> str:
    parts = [
        view.get("read_at_label") or "",
        view.get("overview") or "",
        view.get("warning") or "",
        "stale" if view.get("stale") else "fresh",
        json.dumps(view.get("locations") or []),
        json.dumps(view.get("machines") or [], sort_keys=True),
    ]
    for session in view.get("sessions") or []:
        parts.append(
            " ".join(
                [
                    session.get("state") or "",
                    session.get("title") or "",
                    session.get("line") or "",
                    session.get("age") or "",
                    session.get("key") or "",
                    session.get("product") or "",
                    session.get("project_name") or "",
                    session.get("repo") or "",
                    session.get("github_repo") or "",
                    session.get("session_name") or "",
                    session.get("where") or "",
                    session.get("agent") or "",
                    session.get("surface") or "",
                    session.get("instance_id") or "",
                    session.get("instance_name") or "",
                    session.get("profile") or "",
                    json.dumps(session.get("stands") or []),
                    *(session.get("filter_agents") or []),
                ]
            )
        )
    return str(hash(tuple(parts)))


def _session(session: dict) -> str:
    state = session.get("state") or "working"
    badge = BADGES.get(state, state)
    open_attr = " open" if state == "attention" else ""
    stands = "".join(f"<li>{escape(item)}</li>" for item in session.get("stands") or [])
    age = session.get("age") or ""
    where = session.get("where") or ""
    meta = " · ".join(part for part in (where, age) if part)
    key = escape(session.get("key") or f"{state}:{session.get('title') or ''}", quote=True)
    identity = _identity(session)
    location = escape(where, quote=True)
    agents = escape(json.dumps(session.get("filter_agents") or []), quote=True)
    title = session.get("title") or "Session"
    initials = "".join(word[0] for word in title.split()[:2]).upper()
    color = sum(ord(char) for char in title) % 5
    github = session.get("github_repo") or ""
    repo = github or session.get("repo") or session.get("project_name") or ""
    repo_text = escape(repo)
    if github and re.fullmatch(r"[\w.-]+/[\w.-]+", github, flags=re.ASCII):
        repo_text = f'<a href="https://github.com/{escape(github, quote=True)}" target="_blank" rel="noopener noreferrer">{repo_text}</a>'
    search = escape(" ".join(str(session.get(field) or "") for field in ("title", "project_name", "repo", "github_repo", "session_name", "line", "agent", "where", "surface", "instance_id", "instance_name", "profile")).lower(), quote=True)
    surface = escape(session.get("surface") or "", quote=True)
    instance = escape(session.get("instance_name") or "")
    execution = f'<span class="execution-label"><span class="surface-name">{surface}</span><span class="instance-name">{instance}</span></span>' if surface or instance else ''
    return (
        f'<details class="session {escape(state)}" data-key="{key}" data-state="{escape(state)}" data-location="{location}" data-agents="{agents}" data-surface="{surface}" data-instance="{key}" data-search="{search}"{open_attr}>'
        "<summary>"
        f'<span class="project-symbol color-{color}" aria-hidden="true">{escape(initials)}</span>'
        '<span class="session-main">'
        f'<span class="title">{escape(title)}</span>'
        f'<span class="repo-label">{repo_text}</span>'
        f'{execution}'
        f'<span class="line">{escape(session.get("line") or "")}</span>'
        '</span>'
        f'<span class="session-context"><span class="agent-name">{escape(session.get("agent") or "")}</span><span class="location-name">{escape(where)}</span></span>'
        f'<span class="session-status"><span class="badge"><i class="dot"></i>{escape(badge)}</span><span class="session-age">{escape(age)}</span></span>'
        '<span class="chevron" aria-hidden="true">›</span>'
        "</summary>"
        '<div class="body">'
        f'<p class="meta">{escape(meta)}</p>'
        f'{identity}'
        "<h2>Where it stands</h2>"
        f"<ul>{stands}</ul>"
        "</div>"
        "</details>"
    )


def _identity(session: dict) -> str:
    labels = []
    if session.get("where"):
        labels.append(f'<span>Location: {escape(session["where"])}</span>')
    if session.get("agent"):
        labels.append(f'<span>Agent: {escape(session["agent"])}</span>')
    if session.get("surface"):
        labels.append(f'<span>Surface: {escape(session["surface"])}</span>')
    if session.get("instance_id"):
        labels.append(f'<span>Instance: {escape(session.get("instance_name") or session["instance_id"])}</span>')
        labels.append(f'<span class="instance-id">ID: {escape(session["instance_id"])}</span>')
    if session.get("profile"):
        labels.append(f'<span>Profile: {escape(session["profile"])}</span>')
    project = session.get("project_name") or ""
    if project and project != session.get("title"):
        labels.append(f'<span>Project: {escape(project)}</span>')
    github = session.get("github_repo") or ""
    repo = session.get("repo") or ""
    if github and re.fullmatch(r"[\w.-]+/[\w.-]+", github, flags=re.ASCII):
        labels.append(
            f'<span>GitHub: <a href="https://github.com/{escape(github, quote=True)}" '
            f'target="_blank" rel="noopener noreferrer">{escape(github)}</a></span>'
        )
    elif repo:
        kind = "Profile" if re.search(r":(?:hermes|hermes-task):", session.get("key") or "") else "Workspace"
        labels.append(f'<span>{kind}: {escape(repo)}</span>')
    name = session.get("session_name") or ""
    if name and name not in {session.get("title"), project, repo, github.rsplit("/", 1)[-1]}:
        labels.append(f'<span>Session: {escape(name)}</span>')
    return '<span class="identity">' + "".join(labels) + '</span>' if labels else ""


_CSS = Path(__file__).with_name("ui.css").read_text(encoding="utf-8")
_SCRIPT = Path(__file__).with_name("ui.js").read_text(encoding="utf-8")
