"""Memory by command (FR1.1, FR1.2): 'remember that ...', 'what do you know about me', 'what is my ...', 'what did I ask you yesterday'.

Everything here is deterministic and local. A remembered statement is stored as a fact of the user's own trust level with the chat message as its
provenance; a changed value closes the old one (bitemporal), so 'what did I believe last week' stays answerable. Nothing is ever executed.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from ragnarok.core.clock import get_clock
from ragnarok.memory.models import FactIn, ProvenanceIn
from ragnarok.memory.reader import MemoryReader
from ragnarok.memory.service import MemoryService

REMEMBER = re.compile(r"^\s*(?:please\s+)?(?:remember|keep in mind|make a note|note|don'?t forget)\s*(?:that\s+|:\s*|,\s*)(.+?)\s*$", re.I | re.S)
REMEMBER_SOFT = re.compile(r"^\s*(?:please\s+)?remember\s+((?:my|i|we|our)\b.+?)\s*$", re.I | re.S)
ABOUT_ME = re.compile(r"\bwhat\s+(?:do|did)\s+you\s+(?:know|remember)\s+about\s+me\b|\bwhat\s+(?:have\s+)?i\s+(?:told|asked)\s+you\s+to\s+remember\b|\bmy\s+(?:saved\s+)?preferences\b|\bwhat\s+do\s+i\s+(?:prefer|like)\b", re.I)
MY_THING = re.compile(r"^\s*what(?:'s|\s+is|\s+are|\s+was)\s+my\s+(.+?)[\s?.!]*$", re.I)
EPISODIC = re.compile(r"\b(?:what\s+(?:did|have)\s+(?:i|we)\s+(?:ask(?:ed)?|say|said|tell|told|do|done|work(?:ed)?)|what\s+(?:did|have)\s+you\s+(?:do|done|complete[d]?|finish(?:ed)?|send|sent)|what\s+(?:tasks|things)\s+(?:did|have)\s+you|"
                      r"what\s+happened\s+(?:today|yesterday|earlier)|recap\s+(?:today|yesterday|our)|our\s+(?:history|conversation\s+log)|what\s+have\s+we\s+done)\b", re.I)
PREF_VERBS = r"prefer|like|love|hate|dislike|enjoy|always|never|usually|want|need|avoid"


def _day_range(text: str, now: datetime) -> tuple[datetime, datetime, str]:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    low = text.lower()
    if "yesterday" in low:
        return start - timedelta(days=1), start, "yesterday"
    if "this week" in low or "past week" in low or "last week" in low:
        return start - timedelta(days=7), start + timedelta(days=1), "the past week"
    return start, start + timedelta(days=1), "today"


class MemoryCommands:
    def __init__(self, mem: MemoryService, reader: MemoryReader, compartment: str = "work") -> None:
        self._m, self._r, self._comp = mem, reader, compartment

    def remember(self, request: str, event_id: str | None) -> str | None:
        m = REMEMBER.match(request) or REMEMBER_SOFT.match(request)
        if not m or re.match(r"\s*(?:to|about)\b", m.group(1), re.I) or event_id is None:
            return None  # 'remember to call mom' is a reminder, handled elsewhere
        s = m.group(1).strip().rstrip(".")
        if len(s) < 4:
            return None
        mine = re.match(r"my\s+(.+?)\s+(?:is|are|was|will be)\s+(.+)", s, re.I)
        pref = re.match(rf"i\s+(?:really\s+|strongly\s+)?({PREF_VERBS})\s+(.+)", s, re.I)
        if mine:
            kind, pred, val = "attribute", mine.group(1).strip().lower(), mine.group(2).strip()
        elif pref:
            kind, pred, val = "preference", pref.group(1).lower(), pref.group(2).strip()
        else:
            kind, pred, val = "knowledge", "note", s
        res = self._m.write_fact(FactIn(compartment=self._comp, kind=kind, subject="user", predicate=pred, value=val, confidence=1.0, importance=0.8, trust="user_direct"),  # type: ignore[arg-type]
                                 [ProvenanceIn(parent_type="event", parent_id=event_id, quote=s)])
        note = {"duplicate": "I already had that.", "superseded_old": "That replaces what I knew before (the old value is kept in history)."}.get(res.action, "")
        return f"Remembered: {s}. {note} You can see, edit or delete it in the Memory tab.".replace("  ", " ")

    def recall(self, request: str) -> str | None:
        cid = self._r.compartment_id(self._comp)
        if cid is None:
            return None
        if ABOUT_ME.search(request):
            facts = [f for f in self._r.facts(cid) if f["trust"] in ("user_direct", "user_file")][:12]
            if not facts:
                return "I have not been told anything to remember yet. Try: remember that I prefer morning meetings."
            return "What I remember about you:\n" + "\n".join(f"- {self._line(f)}" for f in facts)
        m = MY_THING.match(request)
        if m:
            key = m.group(1).lower().strip()
            hits = [f for f in self._r.facts(cid) if f["subject"].lower() == "user" and (key in f["predicate"].lower() or f["predicate"].lower() in key or key in f["value"].lower())]
            if hits:
                return f"Your {key}: {hits[0]['value']} (you told me on {datetime.fromtimestamp(hits[0]['learned_at'] / 1000).strftime('%d %b')})."
            return None  # not something we were told: the normal paths (memory search) may still know
        return None

    @staticmethod
    def _line(f: dict) -> str:
        when = datetime.fromtimestamp(f["learned_at"] / 1000).strftime("%d %b")
        if f["kind"] == "preference":
            return f"you {f['predicate']} {f['value']} (since {when})"
        if f["kind"] == "attribute":
            return f"your {f['predicate']} is {f['value']} (since {when})"
        return f"{f['value']} (since {when})"

    def history(self, request: str) -> str | None:
        if not EPISODIC.search(request):
            return None
        cid = self._r.compartment_id(self._comp)
        if cid is None:
            return "Nothing has happened yet."
        a, b, label = _day_range(request, datetime.fromtimestamp(get_clock().now_ms() / 1000))
        evs = [e for e in self._r.events_in_range(cid, int(a.timestamp() * 1000), int(b.timestamp() * 1000), limit=80) if e["kind"] in ("user_message", "task_result")]
        if not evs:
            return f"Nothing logged for {label}."
        lines = []
        for e in reversed(evs[:25]):
            t = datetime.fromtimestamp(e["ts"] / 1000).strftime("%H:%M")
            lines.append(f"{t}  you asked: {e['text'][:90]}" if e["kind"] == "user_message" else f"{t}  result: {e['text'][:110]}")
        return f"From {label}:\n" + "\n".join(lines)
