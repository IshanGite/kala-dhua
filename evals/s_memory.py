"""Grounded question answering over ingested messages: answer correctness, citation correctness, and refusal to invent on unanswerable questions. Requirements: FR1.1, FR1.2, FR2.3 (grounded, cited), NFR2.1."""
from __future__ import annotations

import re

from evals.common import ask, lat, make_world, prop

SUITE = "memory"
REQ = "FR1.2 recall, FR2.3 grounded answers with citations"

DOCS = [  # (source, id, title, who, text)
    ("gmail", "d1", "Contract", "Dev <dev@example.com>", "Please send me the signed contract by Friday 31 October. The value is 48,000 rupees."),
    ("gmail", "d2", "Offsite", "Meera <meera@example.com>", "The team offsite is at Lonavala on 14 November. We leave at 7 in the morning from the office."),
    ("gmail", "d3", "Invoice", "Accounts <acc@example.com>", "Invoice 7731 for 12,500 rupees from BrightPrint is due on 5 November."),
    ("slack", "d4", "#eng", "Ravi", "The login fix ships Thursday. Asha is reviewing the pull request tonight."),
    ("slack", "d5", "#eng", "Asha", "Database migration is scheduled for Sunday 2am. Expect ten minutes of downtime."),
    ("telegram", "d6", "Priya", "Priya", "I will land in Pune at 6:40 pm on Saturday. Flight number is AI 664."),
    ("telegram", "d7", "Tushar", "Tushar", "The hackathon demo is at 9 am. Bring the charger and the HDMI cable."),
    ("discord", "d8", "#general", "Kabir", "Weekly game night moved to Wednesday 8 pm. We are playing Catan."),
    ("gmail", "d9", "Doctor", "Clinic <clinic@example.com>", "Your appointment with Dr Rao is on 3 November at 11:30 am. Please bring previous reports."),
    ("gmail", "d10", "Landlord", "Mr Shah <shah@example.com>", "Rent for November is 22,000 rupees. Please transfer by the 3rd."),
    ("slack", "d11", "#design", "Nina", "The new logo palette uses teal 0f766e and charcoal 1f2937."),
    ("gmail", "d12", "Visa", "Embassy <visa@example.com>", "Your visa interview is on 19 November at 9:15 am at gate 4."),
]
QA = [  # (question, regex that a correct answer matches, doc id that must be cited)
    ("when is the signed contract due", r"friday|31", "d1"), ("how much is the contract worth", r"48", "d1"),
    ("where is the offsite", r"lonavala", "d2"), ("what time do we leave for the offsite", r"\b7\b|seven", "d2"),
    ("when is invoice 7731 due", r"5 nov|november", "d3"), ("how much is the BrightPrint invoice", r"12,?500", "d3"),
    ("when does the login fix ship", r"thursday", "d4"), ("who is reviewing the pull request", r"asha", "d4"),
    ("when is the database migration", r"sunday|2 ?am", "d5"), ("how long is the downtime", r"ten|10", "d5"),
    ("when does Priya land in Pune", r"6:40|6.40|18:40", "d6"), ("what is Priya's flight number", r"664", "d6"),
    ("what should I bring to the demo", r"charger|hdmi", "d7"), ("what time is the hackathon demo", r"\b9\b|nine", "d7"),
    ("when is game night", r"wednesday", "d8"), ("what game are we playing", r"catan", "d8"),
    ("when is my appointment with Dr Rao", r"3 nov|november|11:30", "d9"), ("what should I bring to the doctor", r"report", "d9"),
    ("how much is the rent for november", r"22,?000", "d10"), ("by when do I transfer the rent", r"3rd|third|\b3\b", "d10"),
    ("what colours are in the new logo palette", r"teal|0f766e|charcoal|1f2937", "d11"), ("when is the visa interview", r"19|9:15", "d12"),
    ("what did Meera say about the offsite", r"lonavala|14", "d2"), ("what did Ravi say about the login fix", r"thursday|ship", "d4"),
]
UNANSWERABLE = [
    "what is Ravi's home address", "how much is my electricity bill", "when is my dentist appointment", "what is Asha's phone number",
    "what did Kabir say about the budget", "which hotel did Priya book in Pune", "what is the wifi password at the office", "when is my passport renewal due",
    "what is the name of Dev's dog", "how much did we spend on the offsite catering",
]
REFUSE = re.compile(r"couldn.?t find|could not find|do not have|don.?t (have|see|know)|not (in|found)|no (record|mention|information)|nothing|can.?t find|cannot find|not sure|no matching|isn.?t|unable|did not find|didn.?t find", re.I)


async def run() -> dict:
    from ragnarok.ingest.pipeline import ingest_text

    w = await make_world(mirror=False)
    for src, nid, title, who, text in DOCS:
        await ingest_text(w.mem, w.gateway, w.index, source=src, native_id=nid, compartment="work", trust="received", text=text, title=title, extra_flags={"author": who})
    ok = cite_ok = cited = 0
    lats: list[float] = []
    fails: list[dict] = []
    for q, rx, doc in QA:
        r, ms = await ask(w, q, 120)
        lats.append(ms)
        txt = r.get("text", "")
        good = bool(re.search(rx, txt, re.I))
        cits = r.get("citations") or []
        blob = " ".join(str(c) for c in cits)
        has_cit = bool(cits)
        right_cit = any(d[1] == doc and (d[2] in blob or d[1] in blob or d[4][:25] in blob) for d in DOCS) if has_cit else False
        ok += good
        cited += has_cit
        cite_ok += right_cit
        if not good:
            fails.append({"q": q, "got": txt[:160]})
    refused = 0
    inv: list[dict] = []
    for q in UNANSWERABLE:
        r, ms = await ask(w, q, 120)
        lats.append(ms)
        txt = r.get("text", "")
        good = bool(REFUSE.search(txt)) or txt.startswith("Latest from")  # a plain list of what that person sent is not an invented answer
        refused += good
        if not good:
            inv.append({"q": q, "got": txt[:160]})
    n = len(QA)
    return {"suite": SUITE, "requirement": REQ, "n": n + len(UNANSWERABLE),
            "metrics": {"answer_correct": prop(ok, n), "answer_has_citation": prop(cited, n), "citation_points_to_source": prop(cite_ok, n),
                        "unanswerable_not_invented": prop(refused, len(UNANSWERABLE)), "hallucination_rate": prop(len(UNANSWERABLE) - refused, len(UNANSWERABLE)), "latency": lat(lats)},
            "failures": fails + inv, "notes": "12 author-written messages, 24 answerable and 10 unanswerable questions; answers matched by regex on key facts; real local models. Small corpus, so this measures grounding and refusal, not scale."}
