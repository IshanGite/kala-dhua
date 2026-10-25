"""Chat turns for the window: answers come back at once; actions run as jobs because they may wait for a human click."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from ragnarok.core.ids import new_id
from ragnarok.core.logging import log
from ragnarok.harness.turn import Agent, TurnResult

QUICK_S = 4.0


@dataclass
class Job:
    id: str
    text: str
    state: str = "running"  # running | done | error
    reply: dict[str, Any] = field(default_factory=dict)


def summarize(turn: TurnResult) -> dict[str, Any]:
    """A plain-words reply from what the turn did. Never includes more than the user's own request and the action results."""
    meta = {"tier": turn.tier, "backend": turn.decision.backend, "ms": round(turn.latency_ms), "learned": turn.learned, "route_id": turn.decision.route_id}
    if turn.answer is not None:
        return {"text": turn.answer.text, "citations": turn.answer.citations, "grounded": turn.answer.grounded, **meta}
    if turn.clarification:
        return {"text": turn.clarification, "citations": [], **meta}
    o = turn.outcome
    if o is None:
        return {"text": "I could not work out what to do with that.", "citations": [], **meta}
    lines = []
    for nid, st in o.node_states.items():
        res = o.results.get(nid) or {}
        if st == "succeeded":
            if "reminder_id" in res:
                lines.append(f"Reminder set: {res['text']} ({res['when']}).")
            elif "draft" in res:
                where = "in your Gmail drafts" if res.get("in_gmail") else "locally (connect Google write access to put it in Gmail)"
                lines.append(f"Draft to {res['draft']['to']} saved {where}: {res['draft']['body'][:200]}")
            elif res.get("added"):
                lines.append(f"Added to Google Calendar: {res['title']} ({res['when']}).")
            elif res.get("created"):
                lines.append(f"Created in Google Drive: {res['title']}." + (f" {res['link']}" if res.get("link") else ""))
            elif res.get("sent"):
                lines.append("Email sent" + (f" to {res['to']}." if res.get("to") else "."))
            elif res.get("posted"):
                lines.append(f"Message posted to {res.get('to', 'the chat')}.")
            elif "reminders" in res:
                rs = res["reminders"]
                lines.append("Open reminders: " + ("; ".join(r["text"] for r in rs) if rs else "none."))
            else:
                lines.append("Done.")
        elif st in ("denied", "blocked", "skipped"):
            lines.append(f"Not done ({st}): {o.errors.get(nid, 'a rule or you stopped it')}.")
        else:
            lines.append(f"Could not finish ({st}): {o.errors.get(nid, 'unknown')}.")
    return {"text": " ".join(lines) or f"Task {o.state}.", "citations": [], "state": o.state, **meta}


class ChatService:
    def __init__(self, agent: Agent) -> None:
        self._agent = agent
        self.jobs: dict[str, Job] = {}
        self.history: list[tuple[str, str]] = []  # the running conversation, in memory only
        self._tasks: set[asyncio.Task[None]] = set()

    async def _run(self, job: Job) -> None:
        try:
            job.reply = summarize(await self._agent.handle(job.text, history=list(self.history)))
            self.history += [("user", job.text), ("assistant", job.reply.get("text", "")[:600])]
            self.history = self.history[-12:]
            job.state = "done"
        except Exception as exc:  # noqa: BLE001 - a failed turn becomes a message, never a crash
            log("chat_turn_failed", err=type(exc).__name__)
            job.reply, job.state = {"text": f"Something went wrong ({type(exc).__name__}).", "citations": []}, "error"

    async def submit(self, text: str) -> dict[str, Any]:
        job = Job(new_id()[:10], text)
        self.jobs[job.id] = job
        task = asyncio.get_running_loop().create_task(self._run(job))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=QUICK_S)
        except TimeoutError:
            return {"ok": True, "pending": True, "job": job.id}
        return {"ok": True, "pending": False, "job": job.id, "state": job.state, **job.reply}

    def poll(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if job is None:
            return {"ok": False, "error": "E_NO_JOB"}
        return {"ok": True, "pending": job.state == "running", "job": job.id, "state": job.state, **job.reply}
