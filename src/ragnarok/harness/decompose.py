"""FR3.1: split a multi-part request into an execution graph, by code, in about a millisecond.

A 4B planner needs 20-30 s per plan and sometimes invents skills, so the common shapes are decomposed deterministically and the LLM planner is only the fallback.
Shape: read clauses (look up, find, check the calendar) followed by write clauses (remind, draft, send, tell, create), or several independent write clauses.
Results flow forward through {ref:node.field}; the executor taints anything derived from read results, so a write that depends on outside content still asks for a click.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ragnarok.harness.plan import Plan, PlanNode
from ragnarok.router.when import split_chat, split_doc, split_event, split_mail, split_reminder

VERBS = (r"look up|look for|find|search(?: for)?|check|show|list|get|read|summari[sz]e|remind|set (?:a )?reminder|send|email|mail|draft|write|compose|tell|message|text|post|reply|respond|"
         r"put|add|schedule|book|create|save|note|forward")
SPLIT = re.compile(rf"\s*(?:,\s*and\s+then\s+|\s+and\s+then\s+|,\s*then\s+|\s+then\s+|;\s*|\s+after\s+that\s+|\s+and\s+also\s+|,\s+and\s+(?=(?:{VERBS})\b)|\s+and\s+(?=(?:{VERBS})\b))", re.I)
READ = re.compile(r"^\s*(?:please\s+)?(?:look up|look for|find|search(?: for)?|check|show|list|get|read|summari[sz]e|what)\b", re.I)
CAL = re.compile(r"\b(calendar|agenda|schedule|meetings?|events?)\b", re.I)
PRON = re.compile(r"\b(it|that|them|those|this|the result|the results|the summary)\b|\blisting\b|\bwith (?:it|that|them)\b|\babout (?:it|that|them)\b", re.I)
LEAD_READ = re.compile(r"^\s*(?:please\s+)?(?:look up|look for|find|search(?: for)?|check(?: on)?|show(?: me)?|list|get|read|summari[sz]e)\s+(?:for\s+)?(?:me\s+)?", re.I)


@dataclass
class Decomposed:
    plan: Plan | None
    clauses: list[str]
    reason: str = ""


def clauses_of(request: str) -> list[str]:
    parts = [p.strip(" ,.;") for p in SPLIT.split(request.strip()) if p and p.strip(" ,.;")]
    return parts if len(parts) >= 2 else [request.strip()]


def is_compound(request: str) -> bool:
    return len(clauses_of(request)) >= 2


def decompose(request: str) -> Decomposed:
    clauses = clauses_of(request)
    if len(clauses) < 2:
        return Decomposed(None, clauses, "single clause")
    nodes: list[PlanNode] = []
    last_read: str | None = None
    for i, c in enumerate(clauses, 1):
        nid = f"n{i}"
        low = c.lower()
        if READ.match(c) and not re.match(r"^\s*(?:remind|send|email|draft|tell|message|post|put|add|create|schedule|book)\b", c, re.I):
            if CAL.search(low):
                nodes.append(PlanNode(id=nid, goal=c[:200], skill="calendar.agenda", params={"when": c}, criteria=[{"kind": "ok"}]))
            else:
                q = LEAD_READ.sub("", c).strip(" ?.") or c
                nodes.append(PlanNode(id=nid, goal=c[:200], skill="memory.search", params={"query": q[:200]}, criteria=[{"kind": "ok"}]))
            last_read = nid
            continue
        dep = [last_read] if last_read else []
        uses_prev = bool(last_read and PRON.search(low))
        if re.match(r"^\s*(?:please\s+)?(?:remind me|set (?:a )?reminder|add (?:a )?(?:to-?do|reminder))\b", c, re.I):
            text, when = split_reminder(c)
            if uses_prev and (not text or PRON.fullmatch(text.strip()) or re.fullmatch(r"(?:about\s+)?(?:it|that|them|this)", text.strip(), re.I) or text.strip().lower() in ("it", "that", "them", "this")):
                text = f"{{ref:{last_read}.first}}"
            nodes.append(PlanNode(id=nid, goal=c[:200], skill="reminder.add", params={"text": text, "when": when}, depends_on=dep, criteria=[{"kind": "field_present", "path": "reminder_id"}]))
        elif re.match(r"^\s*(?:please\s+)?(?:draft|write|compose|prepare)\s+(?:an?\s+)?(?:email|mail|message)\b", c, re.I) or re.match(r"^\s*(?:please\s+)?(?:send|email|mail)\b", c, re.I):
            to, subject, body = split_mail(c)
            if not to:
                return Decomposed(None, clauses, f"clause {i} has no recipient address")
            if (uses_prev or not body) and last_read:
                body = f"{{ref:{last_read}.summary}}"
                subject = subject if subject and not PRON.fullmatch(subject.strip()) else "Summary"
            if not body:
                return Decomposed(None, clauses, f"clause {i} has no body")
            send = bool(re.match(r"^\s*(?:please\s+)?(?:send|email|mail)\b", c, re.I))
            nodes.append(PlanNode(id=nid, goal=c[:200], skill="mail.send" if send else "mail.draft", params={"to": to, "subject": (subject or "Message")[:150], "body": body}, depends_on=dep,
                                  criteria=[{"kind": "equals", "path": "sent", "value": True}] if send else [{"kind": "field_present", "path": "draft"}]))
        elif re.search(r"\b(?:telegram|discord|slack)\b", low) and re.match(r"^\s*(?:please\s+)?(?:tell|message|text|post|send|say|reply|write)\b", c, re.I):
            platform, target, body = split_chat(c)
            if (uses_prev or not body) and last_read:
                body = f"{{ref:{last_read}.summary}}"
            if not (platform and target and body):
                return Decomposed(None, clauses, f"clause {i}: I need the app, the person and the text")
            nodes.append(PlanNode(id=nid, goal=c[:200], skill="chat.reply", params={"platform": platform, "target": target, "text": body}, depends_on=dep, criteria=[{"kind": "equals", "path": "posted", "value": True}]))
        elif re.search(r"\bcalendar\b", low) and re.match(r"^\s*(?:please\s+)?(?:put|add|schedule|book|block|create)\b", c, re.I):
            title, when = split_event(c)
            if not title or not when:
                return Decomposed(None, clauses, f"clause {i}: I need a title and a time")
            nodes.append(PlanNode(id=nid, goal=c[:200], skill="calendar.add", params={"title": title, "when": when}, depends_on=dep, criteria=[{"kind": "equals", "path": "added", "value": True}]))
        elif re.match(r"^\s*(?:please\s+)?(?:create|write|save|make)\b.*\bdrive\b", c, re.I):
            title, text = split_doc(c)
            if (uses_prev or not text) and last_read:
                text = f"{{ref:{last_read}.summary}}"
            if not title or not text:
                return Decomposed(None, clauses, f"clause {i}: I need a title and the text")
            nodes.append(PlanNode(id=nid, goal=c[:200], skill="drive.create", params={"title": title, "text": text}, depends_on=dep, criteria=[{"kind": "equals", "path": "created", "value": True}]))
        else:
            return Decomposed(None, clauses, f"I did not understand clause {i}: '{c[:60]}'")
    return Decomposed(Plan(nodes=nodes), clauses)
