"""Turning a free-form request, in any language and with typos, into the parameters of one action. A small local model reads the sentence; CODE then checks every field
(an email address must be valid, a time must parse, a chat must exist) before anything becomes a plan, and every write still waits for a click.

The model never decides whether something is allowed. It only fills in who, what and when from the user's own words (the message text stays in the user's own words and language)."""

from __future__ import annotations

from dataclasses import dataclass, field

from ragnarok.core.clock import get_clock
from ragnarok.core.errors import ModelError
from ragnarok.llm_gateway.client import Gateway, Priority

SKILLS = ("reminder.add", "calendar.add", "mail.send", "mail.draft", "mail.reply", "chat.reply", "drive.create", "python.run", "reminder.list", "none")
SCHEMA = {"type": "object", "properties": {
    "skill": {"type": "string", "enum": list(SKILLS)}, "platform": {"type": "string"}, "target": {"type": "string"}, "to": {"type": "string"}, "subject": {"type": "string"},
    "body": {"type": "string"}, "when": {"type": "string"}, "title": {"type": "string"}}, "required": ["skill"]}
SYSTEM = """You read one request to a personal assistant (any language, casual wording, typos) and fill in the action it asks for. Copy the user's own words for message text; do not translate or improve them.
skill:
reminder.add = remind the user about something (body = what to remember, when = the time in plain English like "tomorrow at 9 pm")
calendar.add = put an event on the calendar (title, when)
mail.send = send an email (to = address, subject, body). mail.draft = write a draft only. mail.reply = reply to an email someone sent (target = the sender's name, body)
chat.reply = send a message on telegram, discord or slack (platform, target = the person or channel, body = the message)
drive.create = create a document (title, body)   python.run = run code   reminder.list = list reminders
none = it is not asking for an action. Questions (who, what, when, where, did, is, how much) and requests to summarize or show things are NEVER actions: always none.
Leave a field empty when the request does not say it. Never invent an address, a name or a time.
Examples:
"send a message on discord on broseph aying valo cancel?" -> chat.reply, platform discord, target broseph, body "valo cancel?"
"ping ravi on tg: running 10 min late" -> chat.reply, platform telegram, target ravi, body "running 10 min late"
"set a reminder on my calendar for tomorrow at 9 pm for final eval round" -> reminder.add, body "final eval round", when "tomorrow at 9 pm"
"mail sam@x.com that the report is ready" -> mail.send, to sam@x.com, body "the report is ready"
"reply to dev saying thursday works" -> mail.reply, target dev, body "thursday works"
"recuerdame llamar al banco manana a las 5" -> reminder.add, body "llamar al banco", when "tomorrow at 5 pm"
"envoie un message a Ravi sur telegram: je suis en retard" -> chat.reply, platform telegram, target Ravi, body "je suis en retard"
"can u text Tushar on tg that im omw" -> chat.reply, platform telegram, target Tushar, body "im omw"
"shoot sam@example.com a note that the report is done" -> mail.send, to sam@example.com, body "the report is done"
"yo remind me in half an hour to stretch" -> reminder.add, body "stretch", when "in 30 minutes"
"remind me tmrw 8am to call mom" -> reminder.add, body "call mom", when "tomorrow at 8 am"
"message priya on slack general: demo at 5, be there" -> chat.reply, platform slack, target general, body "demo at 5, be there"
"kal subah 9 baje mujhe gym ki yaad dilana" -> reminder.add, body "gym", when "tomorrow at 9 am"
"कल सुबह 9 बजे मुझे जिम जाने की याद दिलाओ" -> reminder.add, body "जिम जाने", when "tomorrow at 9 am"
"who sent me the contract request" -> none
"what was said on discord" -> none
"what happened on slack today" -> none
"summarize my emails" -> none
"set a timer for 10 minutes" -> reminder.add, body "timer", when "in 10 minutes"
"how are you" -> none
"tell me a joke" -> none
"what is on my calendar tomorrow" -> none
Reply with JSON only."""


@dataclass
class Parsed:
    skill: str
    fields: dict[str, str] = field(default_factory=dict)


async def parse_action(gateway: Gateway, text: str) -> Parsed | None:
    """None when the model is down, answers badly, or says the message is not an action."""
    now = get_clock().now_ms()
    from datetime import datetime

    stamp = datetime.fromtimestamp(now / 1000).astimezone().strftime("%A %Y-%m-%d %H:%M")
    try:
        out = await gateway.chat(role="arbiter", messages=[{"role": "system", "content": SYSTEM + f"\nNow: {stamp}."}, {"role": "user", "content": text[:800]}], schema=SCHEMA, max_tokens=160,
                                 deadline=get_clock().monotonic() + 15, priority=Priority.INTERACTIVE, seed=0, temperature=0.0)
    except ModelError:
        return None
    d = out.parsed or {}
    skill = d.get("skill")
    if skill not in SKILLS or skill == "none":
        return None
    return Parsed(skill, {k: str(v).strip()[:1500] for k, v in d.items() if k != "skill" and isinstance(v, str) and v.strip()})
