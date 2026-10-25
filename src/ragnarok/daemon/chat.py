"""Chat turns for the window: answers come back at once; actions run as jobs because they may wait for a human click."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

from ragnarok.core.ids import new_id
from ragnarok.core.logging import log
from ragnarok.harness.turn import PROGRESS, Agent, TurnResult

QUICK_S = 4.0


@dataclass
class Job:
    id: str
    text: str
    state: str = "running"  # running | done | error
    reply: dict[str, Any] = field(default_factory=dict)
    task_id: str | None = None
    task: Any = None  # the asyncio task, so the user can stop it


FRIENDLY = {
    "E_UNKNOWN_TARGET": "I do not know that conversation yet. Say the name as it appears in the app, for example 'tell Ravi on telegram that ...'.",
    "E_NOT_CONNECTED": "That app is not connected for writing yet. Run the connect command for it (see Sources).",
    "E_BAD_RECIPIENT": "That is not a valid email address.", "E_NO_TIME": "I could not tell what time that is. Say it like 'tomorrow at 3pm'.",
    "E_AUTH": "The app refused the request (permission or expired sign-in).", "E_SLACK": "Slack refused the message. It needs the chat:write permission and the bot invited to the channel.",
    "E_DISCORD": "Discord refused the message. The bot needs permission to send messages in that channel.", "E_TELEGRAM": "Telegram refused the message.",
    "E_APPROVAL_DENIED": "You denied it, so nothing was done.", "E_CODE_NAMEERROR": "The code had an error (a name was not defined).", "E_CODE_SYNTAXERROR": "The code had a syntax error.", "E_SANDBOX_UNAVAILABLE": "No sandbox is available on this machine, so I will not run code.", "E_GOOGLE": "Google refused the request.", "E_NET": "No network connection to that app.",
}


def node_line(st: str, res: dict[str, Any], err: str) -> str:
    """One plain sentence for one finished (or waiting) step."""
    if st == "succeeded":
        if "reminder_id" in res:
            return f"Reminder set: {res['text']} ({res['when']})." + (" That time has already passed, so it will fire right away." if res.get("past") else "")
        if "draft" in res:
            where = "in your Gmail drafts" if res.get("in_gmail") else "locally (connect Google write access to put it in Gmail)"
            return f"Draft to {res['draft']['to']} saved {where}: {res['draft']['body'][:200]}"
        if res.get("added"):
            return f"Added to Google Calendar: {res['title']} ({res['when']})."
        if res.get("created"):
            return f"Created in Google Drive: {res['title']}." + (f" {res['link']}" if res.get("link") else "")
        if res.get("sent"):
            return "Email sent" + (f" to {res['to']}." if res.get("to") else ".")
        if res.get("posted"):
            return f"Message posted to {res.get('to', 'the chat')}."
        if "stdout" in res:
            out = str(res["stdout"]).strip()
            return ("Ran in the sandbox. Output:\n" + out[:1500]) if out else "Ran in the sandbox. It printed nothing."
        if "events" in res and "summary" in res:
            return str(res["summary"])
        if "hits" in res:
            return f"Found {len(res['hits'])} match(es) in memory."
        if "reminders" in res:
            rs = res["reminders"]
            return "Open reminders: " + ("; ".join(r["text"] for r in rs) if rs else "none.")
        return "Done."
    if st == "awaiting_approval":
        return "Waiting for your OK in the Approvals tab."
    if st in ("pending", "running"):
        return "Working on the next step..."
    if st in ("denied", "blocked", "skipped"):
        return FRIENDLY.get(err, f"Not done ({st}): {err or 'a rule or you stopped it'}.")
    return FRIENDLY.get(err or "unknown", f"Could not finish ({st}): {err or 'unknown'}.")


def summarize(turn: TurnResult) -> dict[str, Any]:
    """A plain-words reply from what the turn did. Never includes more than the user's own request and the action results."""
    meta = {"why": turn.decision.trace + [f"Time: {turn.latency_ms:.0f} ms" + (f" ({', '.join(f'{k} {v}' for k, v in turn.timings.items())})" if turn.timings else "")], "timings": turn.timings, "tier": turn.tier, "backend": turn.decision.backend, "ms": round(turn.latency_ms), "learned": turn.learned, "route_id": turn.decision.route_id}
    if turn.answer is not None:
        return {"text": turn.answer.text, "citations": turn.answer.citations, "grounded": turn.answer.grounded, **meta}
    if turn.clarification:
        return {"text": turn.clarification, "citations": [], **meta}
    o = turn.outcome
    if o is None:
        return {"text": "I could not work out what to do with that.", "citations": [], **meta}
    lines = [node_line(st, o.results.get(nid) or {}, o.errors.get(nid, "")) for nid, st in o.node_states.items()]
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

    def recent_trace(self, limit: int = 20) -> list[dict[str, Any]]:
        """The last decisions with the reasons the agent gave itself, newest first (NFR4.1)."""
        return [{"job": j.id, "text": j.text[:120], "state": j.state, "why": j.reply.get("why", []), "tier": j.reply.get("tier"), "backend": j.reply.get("backend")} for j in list(self.jobs.values())[-max(1, min(limit, 100)):][::-1]]

    def interim(self, job: Job) -> dict[str, Any]:
        """While a task waits for a click, say what is already done and what is waiting: the user should never stare at 'working'."""
        if not job.task_id:
            return {}
        import json as _json

        lines = []
        for n in self._agent.ledger.nodes_of(job.task_id):
            res = _json.loads(n["result_json"]) if n["result_json"] else {}
            lines.append(node_line(n["state"], res, n["error_code"] or ""))
        return {"text": " ".join(lines), "citations": []}

    async def _run(self, job: Job) -> None:
        PROGRESS.set(lambda tid: setattr(job, "task_id", tid))
        try:
            job.reply = summarize(await self._agent.handle(job.text, history=list(self.history)))
            self.history += [("user", job.text), ("assistant", job.reply.get("text", "")[:600])]
            self.history = self.history[-12:]
            self._log_reply(job.reply.get("text", ""))
            job.state = "done"
        except asyncio.CancelledError:
            self._mark_cancelled(job)
            job.reply, job.state = {"text": "Stopped. Nothing further was done.", "citations": [], "cancelled": True}, "cancelled"
        except Exception as exc:  # noqa: BLE001 - a failed turn becomes a message, never a crash
            log("chat_turn_failed", err=type(exc).__name__)
            job.reply, job.state = {"text": f"Something went wrong ({type(exc).__name__}).", "citations": []}, "error"

    def _mark_cancelled(self, job: Job) -> None:
        """A stopped task must not come back after a restart: its unfinished steps are skipped and the task is closed."""
        if not job.task_id:
            return
        try:
            for n in self._agent.ledger.nodes_of(job.task_id):
                if n["state"] not in ("succeeded", "failed", "skipped", "blocked"):
                    self._agent.ledger.set_node_state(n["id"], "skipped", error_code="E_CANCELLED")
            self._agent.ledger.set_task_state(job.task_id, "cancelled")
        except Exception as exc:  # noqa: BLE001
            log("cancel_mark_failed", err=type(exc).__name__)

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if job is None or job.task is None:
            return {"ok": False, "error": "E_NO_JOB"}
        if job.state == "running":
            job.task.cancel()
        return {"ok": True}

    async def submit_vision(self, question: str | None, image: bytes | None, delay_s: float = 0.0, *, mode: str = "screen", display: int = 1, engine: str = "auto") -> dict[str, Any]:
        """Explain the screen (no image) or answer about a picture. A job like any other, so it shows progress and can be stopped."""
        vision = getattr(self._agent, "vision", None)
        job = Job(new_id()[:10], question or ("a picture" if image else "my screen"))
        self.jobs[job.id] = job

        async def run() -> None:
            try:
                if vision is None:
                    job.reply, job.state = {"text": "Vision is not set up on this machine.", "citations": []}, "error"
                    return
                if image is not None:
                    text = await vision.ask(image, question, engine=engine)
                else:
                    if delay_s and mode == "screen":
                        await asyncio.sleep(delay_s)  # time to switch to the window that should be explained
                    text = await vision.explain_screen(question, mode=mode, display=display, engine=engine)
                job.reply, job.state = {"text": text, "citations": [], "tier": "L1", "backend": "vision", "why": ["You asked about " + ("a picture" if image else {"screen": "your screen", "region": "a part of your screen", "window": "a window"}.get(mode, "your screen")) + f" (engine: {engine}): read and explained on this computer, nothing stored"]}, "done"
            except asyncio.CancelledError:
                job.reply, job.state = {"text": "Stopped.", "citations": [], "cancelled": True}, "cancelled"
            except Exception as exc:  # noqa: BLE001
                job.reply, job.state = {"text": str(exc) if getattr(exc, "code", "").startswith("E_") else "I could not do that.", "citations": [], "error": getattr(exc, "code", "E_VISION")}, "error"

        return await self._start(job, run(), 0.4)

    async def _start(self, job: Job, coro: Any, quick_s: float | None = None) -> dict[str, Any]:
        task = asyncio.get_running_loop().create_task(coro)
        job.task = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=QUICK_S if quick_s is None else quick_s)
        except TimeoutError:
            return {"ok": True, "pending": True, "job": job.id, **self.interim(job)}
        except asyncio.CancelledError:
            pass
        return {"ok": True, "pending": False, "job": job.id, "state": job.state, **job.reply}

    async def submit(self, text: str, quick_s: float | None = None) -> dict[str, Any]:
        text = text.strip()
        if not text:
            return {"ok": True, "pending": False, "job": "", "state": "done", "text": "Say something and I will answer. Try 'catch me up' or 'what did Ravi say'.", "citations": [], "tier": "L1", "backend": "rule", "ms": 0}
        if CONFIRM.match(text):  # approval is a click on a card, never a sentence: typed text cannot approve anything
            n = len(self._hub.pending()) if self._hub is not None else 0
            reply = (f"{n} action(s) are waiting for you in the Approvals tab. Each card shows exactly what will happen; press Approve there." if n else
                     "Nothing is waiting for approval and I am not in the middle of anything. Tell me what to do.")
            return {"ok": True, "pending": False, "job": "", "state": "done", "text": reply, "citations": [], "tier": "L1", "backend": "rule", "ms": 0}
        if self._triggers is not None and (reply := self._triggers.parse(text)) is not None:  # 'every day at 8am ...' / 'when I get an email from X ...' / 'list my triggers'
            return {"ok": True, "pending": False, "job": "", "state": "done", "text": reply, "citations": [], "tier": "L1", "backend": "rule", "ms": 0}
        job = Job(new_id()[:10], text)
        self.jobs[job.id] = job
        return await self._start(job, self._run(job), quick_s)

    def poll(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if job is None:
            return {"ok": False, "error": "E_NO_JOB"}
        return {"ok": True, "pending": job.state == "running", "job": job.id, "state": job.state, **(job.reply if job.state != "running" else self.interim(job))}
