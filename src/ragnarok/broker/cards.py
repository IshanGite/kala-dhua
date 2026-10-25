"""Approval cards are rendered from SERVER-SIDE data only (stored parameters, policy warnings), never from text the agent supplies.

Untrusted content shown on a card (a mail body, a draft) is marked as content so the UI renders it as plain text.
"""

from __future__ import annotations

from typing import Any

MAX_FIELD = 4000
_CONTENT_FIELDS = {"body", "text", "message", "content", "subject", "title", "description"}


def _clip(v: Any) -> Any:
    if isinstance(v, str) and len(v) > MAX_FIELD:
        return v[:MAX_FIELD] + f"... [{len(v) - MAX_FIELD} more characters]"
    return v


def render_card(*, skill_id: str, params: dict[str, Any], tier: int, warnings: list[str], trust: str, dry_run: dict[str, Any] | None,
                snapshot_id: str | None, stepup: dict[str, Any] | None, task_goal: str = "") -> dict[str, Any]:
    fields = []
    for key, value in params.items():
        fields.append({"name": key, "value": _clip(value), "is_content": key in _CONTENT_FIELDS})
    headline = {
        "mail.send": "Send this email",
        "chat.post": "Post this message",
        "chat.reply": "Send this chat message",
        "calendar.add": "Add this event to your Google Calendar",
        "calendar.write": "Create or change this calendar event",
        "fs.delete": "Delete these files (moved to trash)",
        "python.run": "Run this code",
    }.get(skill_id, f"Run {skill_id}")
    return {
        "headline": headline,
        "skill": skill_id,
        "tier": tier,
        "fields": fields,
        "warnings": warnings,
        "trust": trust,
        "tainted": trust in ("received", "web"),
        "dry_run": dry_run,
        "snapshot_id": snapshot_id,
        "stepup": stepup,
        "task_goal": task_goal,
        "render_as": "plain_text",
    }
