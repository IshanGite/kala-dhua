"""Action extraction (slot filling): from a request, the right skill with the right fields, and NOTHING for requests that are not actions. Requirements: FR2.2 (generate the parameters), FR3.1 (multi-step), NFR4.3 (nothing without intent)."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta

from evals.common import lat, make_world, prop

SUITE = "actions"
REQ = "FR2.2, FR3.1"


def at(spec: str, now: datetime) -> datetime:
    """'+1d 09:00', 'today 18:00', 'fri 17:00', '+30m'."""
    if spec.endswith("m") and spec.startswith("+") and " " not in spec:
        return (now + timedelta(minutes=int(spec[1:-1]))).replace(second=0, microsecond=0)
    if spec.endswith("h") and spec.startswith("+") and " " not in spec:
        return (now + timedelta(hours=int(spec[1:-1]))).replace(second=0, microsecond=0)
    day, hm = spec.split()
    h, m = (int(x) for x in hm.split(":"))
    if day.startswith("+"):
        base = (now + timedelta(days=int(day[1:-1]))).date()
    elif day == "today":
        base = now.date()
    else:
        names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        base = (now + timedelta(days=((names.index(day) - now.weekday()) % 7) or 7)).date()
    return datetime(base.year, base.month, base.day, h, m)


# (text, expected skills in order, checks) ; checks per step: dict of field -> expected substring (case-insensitive) or ("when", spec)
POS = [
    ("remind me to call mom tomorrow at 9am", ["reminder.add"], [{"text": "mom", "when": "+1d 09:00"}]),
    ("remind me to submit the report on friday at 4pm", ["reminder.add"], [{"text": "report", "when": "fri 16:00"}]),
    ("set a reminder for 6pm today to take the medicine", ["reminder.add"], [{"text": "medicine", "when": "today 18:00"}]),
    ("remind me in 30 minutes to check the oven", ["reminder.add"], [{"text": "oven", "when": "+30m"}]),
    ("remind me in an hour to stretch", ["reminder.add"], [{"text": "stretch", "when": "+1h"}]),
    ("ping me tomorrow 8am about the gym", ["reminder.add"], [{"text": "gym", "when": "+1d 08:00"}]),
    ("dont forget to buy milk tomorrow at 6pm", ["reminder.add"], [{"text": "milk", "when": "+1d 18:00"}]),
    ("remind me tmrw 7am to call dad", ["reminder.add"], [{"text": "dad", "when": "+1d 07:00"}]),
    ("recuerdame llamar al banco manana a las 5", ["reminder.add"], [{"text": "banco", "when": "+1d 17:00"}]),
    ("kal subah 9 baje gym yaad dilana", ["reminder.add"], [{"text": "gym", "when": "+1d 09:00"}]),
    ("put dentist on my calendar friday at 5pm", ["calendar.add"], [{"title": "dentist", "when": "fri 17:00"}]),
    ("schedule lunch with Priya on friday at 1pm", ["calendar.add"], [{"title": "lunch", "when": "fri 13:00"}]),
    ("add standup to my calendar tomorrow at 10am", ["calendar.add"], [{"title": "standup", "when": "+1d 10:00"}]),
    ("block tomorrow 2pm for deep work", ["calendar.add"], [{"title": "deep work", "when": "+1d 14:00"}]),
    ("book a meeting with the vendor on monday at 11am", ["calendar.add"], [{"title": "vendor", "when": "mon 11:00"}]),
    ("email sam@example.com that the report is ready", ["mail.send"], [{"to": "sam@example.com", "body": "report"}]),
    ("mail dev@example.com that I will send the contract tomorrow", ["mail.send"], [{"to": "dev@example.com", "body": "contract"}]),
    ("send an email to meera@example.com saying lunch on thursday works", ["mail.send"], [{"to": "meera@example.com", "body": "thursday"}]),
    ("shoot priya@example.com a note that the slides are attached", ["mail.send"], [{"to": "priya@example.com", "body": "slides"}]),
    ("email asha@example.com asking if she is free on friday", ["mail.send"], [{"to": "asha@example.com"}]),
    ("draft an email to hr@example.com about leave next week", ["mail.draft"], [{"to": "hr@example.com"}]),
    ("tell Ravi on telegram that I will be late", ["chat.reply"], [{"platform": "telegram", "target": "ravi", "text": "late"}]),
    ("text Tushar on tg that im omw", ["chat.reply"], [{"platform": "telegram", "target": "tushar", "text": "omw"}]),
    ("post in slack general that the build is green", ["chat.reply"], [{"platform": "slack", "target": "general", "text": "green"}]),
    ("message the discord general channel that standup moved to ten", ["chat.reply"], [{"platform": "discord", "target": "general", "text": "ten"}]),
    ("dm Priya on telegram: demo at five", ["chat.reply"], [{"platform": "telegram", "target": "priya", "text": "demo"}]),
    ("let Ravi know on slack that I am 5 minutes late", ["chat.reply"], [{"platform": "slack", "target": "ravi", "text": "late"}]),
    ("envoie un message a Ravi sur telegram: je suis en retard", ["chat.reply"], [{"platform": "telegram", "target": "ravi", "text": "retard"}]),
    ("create a doc called Ideas with three startup ideas", ["drive.create"], [{"title": "ideas"}]),
    ("make a document named Plan saying the key dates are in March", ["drive.create"], [{"title": "plan", "text": "march"}]),
    ("save a note in drive called groceries saying milk eggs bread", ["drive.create"], [{"title": "groceries", "text": "milk"}]),
    ("make a doc named papa with a poem about a father and mail it to tushar@example.com", ["drive.create", "mail.send"], [{"title": "papa"}, {"to": "tushar@example.com", "body": "{ref:"}]),
    ("write a poem in a doc called Rain and email it to sam@example.com", ["drive.create", "mail.send"], [{"title": "rain"}, {"to": "sam@example.com", "body": "{ref:"}]),
    ("write a haiku about rain and email it to sam@example.com", ["mail.send"], [{"to": "sam@example.com"}]),
    ("remind me tomorrow at 9am to call mom and tell Tushar on telegram I will be late", ["reminder.add", "chat.reply"], [{"text": "mom", "when": "+1d 09:00"}, {"platform": "telegram", "target": "tushar"}]),
    ("create a doc called Trip plan with a three day itinerary for Goa and send the link to Tushar on telegram", ["drive.create", "chat.reply"], [{"title": "trip"}, {"platform": "telegram", "target": "tushar", "text": "{ref:"}]),
    ("email dev@example.com the invoice summary and remind me tomorrow 9am to follow up", ["mail.send", "reminder.add"], [{"to": "dev@example.com"}, {"when": "+1d 09:00"}]),
]
NEG = [  # not actions: no plan may be produced
    "what did Ravi say", "who sent me the contract request", "summarize my emails", "catch me up", "what's on my calendar tomorrow", "when is the budget review", "how much is invoice 8812",
    "what is the weather in paris", "tell me a joke", "hi", "thanks", "explain recursion simply", "what is the capital of france", "how do I write a good email", "should I email Dev about the contract",
    "what happens if I tell Ravi on telegram that I quit", "can you remind me things?", "what can you do", "what time is it in tokyo", "what happened on slack today", "any messages from Tushar?",
    "what's the date tomorrow", "convert 5 km to miles", "is it ok to send an email without a subject", "do you remember what I said about the offsite", "how do reminders work", "read this page to me",
    "translate good morning to french", "give me tips to focus", "what do you know about me",
]


def _field_ok(params: dict, key: str, want: str, now: datetime) -> bool:
    from ragnarok.router.when import parse_when

    if key == "when":
        got = parse_when(str(params.get("when", "")), now)
        return got is not None and abs((got - at(want, now)).total_seconds()) <= 60
    have = str(params.get({"text": "text", "title": "title", "body": "body", "to": "to", "platform": "platform", "target": "target"}[key], "")).lower()
    if key in ("to", "platform"):
        return have == want.lower()
    if key == "target":
        return want.lower() in have
    return want.lower() in have


async def run_async() -> dict:
    from ragnarok.harness.actionparse import parse_steps

    w = await make_world(mirror=False)
    now = datetime.fromtimestamp(time.time())
    now = now.replace(second=0, microsecond=0)
    skill_ok = field_ok = n_fields = full_ok = 0
    times, fails = [], []
    for text, skills, checks in POS:
        t = time.perf_counter()
        steps = await parse_steps(w.gateway, text)
        plan = await w.agent._plan_from_steps(text, steps) if steps else None
        times.append((time.perf_counter() - t) * 1000)
        got = [n.skill for n in plan.nodes] if plan else []
        ok_skill = got == skills
        skill_ok += ok_skill
        row_ok = ok_skill
        if plan and ok_skill:
            for node, chk in zip(plan.nodes, checks, strict=True):
                for k, v in chk.items():
                    n_fields += 1
                    good = _field_ok(node.params, k, v, now)
                    field_ok += good
                    row_ok = row_ok and good
                    if not good:
                        fails.append({"text": text, "field": k, "want": v, "got": node.params.get({"text": "text"}.get(k, k))})
        else:
            fails.append({"text": text, "want_skills": skills, "got_skills": got})
        full_ok += row_ok
    false_actions = []
    for text in NEG:
        steps = await parse_steps(w.gateway, text)
        plan = await w.agent._plan_from_steps(text, steps) if steps else None
        # the agent also requires the understanding to agree: measure the parser alone (the worst case) and report what the live turn does in the tasks suite
        if plan:
            false_actions.append({"text": text, "plan": [n.skill for n in plan.nodes]})
    return {"suite": SUITE, "requirement": REQ, "n": len(POS) + len(NEG),
            "metrics": {"skills_correct": prop(skill_ok, len(POS)), "fields_correct": prop(field_ok, n_fields), "requests_fully_correct": prop(full_ok, len(POS)),
                        "parser_alone_false_action_rate": prop(len(false_actions), len(NEG)), "latency": lat(times)},
            "failures": fails[:20], "false_actions": false_actions[:10],
            "note": "times are checked against the system clock at run time; 'parser alone' is the worst case: in the live turn a second reading must also agree it is an action (see the tasks suite)"}


def run() -> dict:
    return asyncio.run(run_async())
