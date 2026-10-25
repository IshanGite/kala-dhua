"""Chat turns for the window: answers come back at once; actions run as jobs because they may wait for a human click."""

from __future__ import annotations

import asyncio
import re
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


FRIENDLY = {
    "E_UNKNOWN_TARGET": "I do not know that conversation yet. Say the name as it appears in the app, for example 'tell Ravi on telegram that ...'.",
    "E_NOT_CONNECTED": "That app is not connected for writing yet. Run the connect command for it (see Sources).",
    "E_BAD_RECIPIENT": "That is not a valid email address.", "E_NO_TIME": "I could not tell what time that is. Say it like 'tomorrow at 3pm'.",
    "E_AUTH": "The app refused the request (permission or expired sign-in).", "E_SLACK": "Slack refused the message. It needs the chat:write permission and the bot invited to the channel.",
    "E_DISCORD": "Discord refused the message. The bot needs permission to send messages in that channel.", "E_TELEGRAM": "Telegram refused the message.",
    "E_APPROVAL_DENIED": "You denied it, so nothing was done.", "E_GOOGLE": "Google refused the request.", "E_NET": "No network connection to that app.",
}


def summarize(turn: TurnResult) -> dict[str, Any]:
    """A plain-words reply from what the turn did. Never includes more than the user's own request and the action results."""
    meta = {"timings": turn.timings, "tier": turn.tier, "backend": turn.decision.backend, "ms": round(turn.latency_ms), "learned": turn.learned, "route_id": turn.decision.route_id}
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
                lines.append(f"Reminder set: {res['text']} ({res['when']})." + (" That time has already passed, so it will fire right away." if res.get("past") else ""))
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
            code = o.errors.get(nid, "")
            lines.append(FRIENDLY.get(code, f"Not done ({st}): {code or 'a rule or you stopped it'}."))
        else:
            code = o.errors.get(nid, "unknown")
            lines.append(FRIENDLY.get(code, f"Could not finish ({st}): {code}."))
    return {"text": " ".join(lines) or f"Task {o.state}.", "citations": [], "state": o.state, **meta}


CONFIRM = re.compile(r"^\s*(?:ok(?:ay)?[, ]*)?(?:do it|go ahead|yes|yep|yeah|sure|confirm|approve|approved|send it|proceed|please do|run it)\b[\s!.]*$", re.I)


class ChatService:
    def __init__(self, agent: Agent, hub: Any = None, triggers: Any = None) -> None:
        self._agent = agent
        self._hub = hub
        self._triggers = triggers
        self.jobs: dict[str, Job] = {}
        self.history: list[tuple[str, str]] = []  # the running conversation, in memory only
        self._tasks: set[asyncio.Task[None]] = set()

    def _log_reply(self, text: str) -> None:
        """Replies are logged as events so the conversation survives a restart (NFR3.2); they are never retrieved as knowledge."""
        try:
            from ragnarok.memory.models import EventIn

            self._agent.mem.write_event(EventIn(compartment=self._agent.ctx.compartment, kind="assistant_message", actor="agent", content=text[:1500] or "(empty)", trust="agent"))
        except Exception as exc:  # noqa: BLE001 - logging a reply must never break the chat
            log("assistant_log_failed", err=type(exc).__name__)

    def restore(self) -> int:
        """Working memory after a restart: the last turns of the conversation, read back from the encrypted log."""
        cid = self._agent.reader.compartment_id(self._agent.ctx.compartment)
        self.history = self._agent.reader.conversation_tail(cid, 12) if cid else []
        return len(self.history)

    async def _run(self, job: Job) -> None:
        try:
            job.reply = summarize(await self._agent.handle(job.text, history=list(self.history)))
            self.history += [("user", job.text), ("assistant", job.reply.get("text", "")[:600])]
            self.history = self.history[-12:]
            self._log_reply(job.reply.get("text", ""))
            job.state = "done"
        except Exception as exc:  # noqa: BLE001 - a failed turn becomes a message, never a crash
            log("chat_turn_failed", err=type(exc).__name__)
            job.reply, job.state = {"text": f"Something went wrong ({type(exc).__name__}).", "citations": []}, "error"

    async def submit(self, text: str) -> dict[str, Any]:
        if CONFIRM.match(text):  # approval is a click on a card, never a sentence: typed text cannot approve anything
            n = len(self._hub.pending()) if self._hub is not None else 0
            reply = (f"{n} action(s) are waiting for you in the Approvals tab. Each card shows exactly what will happen; press Approve there." if n else
                     "Nothing is waiting for approval and I am not in the middle of anything. Tell me what to do.")
            return {"ok": True, "pending": False, "job": "", "state": "done", "text": reply, "citations": [], "tier": "L1", "backend": "rule", "ms": 0}
        if self._triggers is not None and (reply := self._triggers.parse(text)) is not None:  # 'every day at 8am ...' / 'when I get an email from X ...' / 'list my triggers'
            return {"ok": True, "pending": False, "job": "", "state": "done", "text": reply, "citations": [], "tier": "L1", "backend": "rule", "ms": 0}
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
