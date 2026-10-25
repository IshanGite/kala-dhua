"""What does the user want? A small local model decides, not a list of keywords. It returns an intent plus the person, app and topic the message names.

The intent only chooses WHICH PATH handles the message. It never grants anything: actions still go through the router, the policy engine and a click on a card, and a message the model calls
'chat', 'recall', 'catchup', 'agenda' or 'screen' can never reach a tool that changes something. If the model is down or answers badly, the caller falls back to the old keyword paths.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ragnarok.core.clock import get_clock
from ragnarok.core.errors import ModelError
from ragnarok.core.logging import log
from ragnarok.llm_gateway.client import Gateway, Priority

INTENTS = ("chat", "recall", "catchup", "agenda", "screen", "action", "unclear")
SCHEMA = {"type": "object", "properties": {"intent": {"type": "string", "enum": list(INTENTS)}, "person": {"type": "string"}, "source": {"type": "string"}, "topic": {"type": "string"}}, "required": ["intent"]}
SYSTEM = """Classify the user's message to a personal assistant that can read their email, chats and calendar, and can send messages, set reminders and add events.
intent:
chat = greeting, thanks, feelings, jokes, general knowledge, advice, explanations, questions about the assistant itself. Needs none of the user's data and changes nothing.
recall = asks what someone said or sent, when or where something is, or what their data says about a thing (a person, a topic, an invoice, a meeting, a deadline).
catchup = wants a summary, overview, briefing or "what's new" across their messages or one app.
agenda = asks what is on the calendar or schedule for a day.
screen = wants the assistant to look at, explain or read their screen, window or a picture.
action = asks the assistant to DO something: send, email, reply, post, message, remind, schedule, create, draft, run code, delete.
unclear = gibberish, or too vague to tell what is wanted.
person = the person named in a recall (else empty). source = gmail|slack|discord|telegram|calendar|drive if an app is named, else empty. topic = the main subject words if any, else empty.
Examples:
"hi" -> chat
"tell me a joke" -> chat
"explain what an API is" -> chat
"I am stressed about the demo" -> chat
"what did Ravi say" -> recall, person Ravi
"anything from Mugdha" -> recall, person Mugdha
"when is the budget review" -> recall, topic budget review
"is the contract urgent" -> recall, topic contract
"how much is invoice 8812" -> recall, topic invoice 8812
"summarize everything we ingested" -> catchup
"give me a summary of all contexts" -> catchup
"what needs my attention" -> catchup, topic attention
"anything urgent?" -> catchup, topic urgent
"what do I need to reply to" -> catchup, topic attention
"what happened on slack today" -> catchup, source slack
"what do I have tomorrow" -> agenda
"what is my day like" -> agenda
"what is on my screen" -> screen
"tell Ravi on telegram that I am late" -> action
"email bob@x.com asking for leave" -> action
"remind me to call mom at 5" -> action
"help me write an apology to a client" -> chat
"who made you" -> chat
"who won the match yesterday" -> chat
"what was the plan for friday" -> recall
"where is the pitch deck" -> recall, topic pitch deck
"am I free on friday" -> agenda
"kal subah 9 baje gym yaad dilana" -> action
"do something" -> unclear
"berlin time" -> chat
"what is the weather in paris" -> chat
"what can you do" -> chat
"what is the capital of France" -> chat
"asdfgh" -> unclear
"do something with my files" -> unclear
Reply with JSON only."""


FOLLOWUP = re.compile(r"^\s*(?:and|also|what about|how about|then|so|why|who|when|where|it|that|this|they|them)\b", re.I)


@dataclass(frozen=True)
class Understanding:
    intent: str
    person: str = ""
    source: str = ""
    topic: str = ""


async def understand(gateway: Gateway, text: str, history: list[tuple[str, str]] | None = None) -> Understanding | None:
    """None when the model is unavailable or answers badly: the caller then uses its fallback paths."""
    ctx = ""
    if history and FOLLOWUP.match(text):  # only a follow-up ('and Ravi?', 'what about it') is read together with the last question; anything else stands alone
        last = next((c for r, c in reversed(history) if r == "user"), "")
        if last:
            ctx = f'(the previous user message was: "{last[:120]}")\n'
    out = None
    for attempt in range(2):
        try:
            out = await gateway.chat(role="arbiter", messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": ctx + text[:600]}], schema=SCHEMA, max_tokens=60,
                                     deadline=get_clock().monotonic() + (12 if attempt == 0 else 25), priority=Priority.INTERACTIVE, seed=0, temperature=0.0)
            break
        except ModelError as exc:
            log("understand_failed", err=getattr(exc, "code", "?"), attempt=attempt)
            if attempt:
                return None
    if out is None:
        return None
    d = out.parsed or {}
    intent = d.get("intent")
    if intent not in INTENTS:
        return None
    clean = lambda k: str(d.get(k) or "").strip()[:60]  # noqa: E731
    src = clean("source").lower()
    person = clean("person")
    if person and person.lower() not in text.lower():
        person = ""  # a name the user never wrote is the model carrying over the last question, not a person to look up
    return Understanding(intent, person, src if src in ("gmail", "slack", "discord", "telegram", "calendar", "drive") else "", clean("topic"))


_CUES = {
    "screen": r"screen|display|monitor|window|page|tab\b|desktop|picture|image|photo|looking at|showing|see\b|visible|open right now|this app",
    "catchup": r"catch|summar|overview|recap|brief|miss|new\b|happen|urgent|attention|reply|respond|unread|inbox|e-?mails?|mails?|messages?|slack|discord|telegram|gmail|today|yesterday|week|anything|everything|digest|resum|correos|nachrichten|important|going on|update|pending|unanswered|waiting",
    "agenda": r"calendar|schedule|agenda|meeting|appointment|event|today|tomorrow|tonight|week|free|busy|day\b|monday|tuesday|wednesday|thursday|friday|saturday|sunday|next|plans?\b|available|booked|calendario|reunion|reuni",
}


def plausible(intent: str, request: str) -> bool:
    """A cheap check that the words support what the model decided. Only the intents that would show something large or look at the screen need one."""
    pat = _CUES.get(intent)
    return True if pat is None else bool(re.search(pat, request, re.I))
