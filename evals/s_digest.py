"""The digest: do messages about the same thing land in one section, are urgent items flagged, is nothing invented. Requirements: FR2.2 digest and grouping, FR2.3."""
from __future__ import annotations

import itertools

from evals.common import make_world, prop

SUITE = "digest"
REQ = "FR2.2 context-grouped digest with needs-action flags"

TOPICS = {  # topic -> [(source, author, title, text)]
    "contract": [("gmail", "Dev", "Contract", "Please send me the signed contract by Friday, it is urgent."), ("slack", "Ravi", "#legal", "Dev is waiting on the signed contract, can someone send it today?"),
                 ("telegram", "Dev", "Dev", "Did you get my mail about the signed contract?")],
    "offsite": [("gmail", "Meera", "Offsite", "The team offsite is in Lonavala on 14 November, we leave at 7."), ("discord", "Kabir", "#general", "Who is driving to the Lonavala offsite? I can take three people."),
                ("slack", "Asha", "#team", "Offsite packing list: sunscreen, charger, board games.")],
    "migration": [("slack", "Asha", "#eng", "Database migration is Sunday 2am, expect ten minutes of downtime."), ("gmail", "Ops", "Maintenance", "Scheduled maintenance: database migration Sunday, services restart briefly."),
                  ("slack", "Ravi", "#eng", "Migration rollback plan is in the wiki, please review before Sunday.")],
    "invoice": [("gmail", "Accounts", "Invoice 7731", "Invoice 7731 for 12,500 rupees from BrightPrint is due on 5 November."), ("gmail", "Accounts", "Invoice 7731 reminder", "Reminder: BrightPrint invoice 7731 is still unpaid."),
                ("telegram", "Priya", "Priya", "Did you pay the BrightPrint invoice? Accounts keeps asking.")],
    "hackathon": [("telegram", "Tushar", "Tushar", "The hackathon demo is at 9 am, bring the charger and the HDMI cable."), ("discord", "Kabir", "#hack", "Hackathon demo slides are in the drive, please check the last slide."),
                  ("slack", "Nina", "#design", "Hackathon logo is final: teal and charcoal.")],
}
NOISE = [("slack", "Bot", "#random", "Lunch menu: dal, rice, salad."), ("discord", "Zed", "#memes", "this cat video is great"), ("gmail", "Shop", "Sale", "Weekend sale on headphones, 20 percent off.")]
URGENT = {"contract", "migration", "invoice"}  # a person would flag these as needing action


async def run() -> dict:
    from ragnarok.ingest.pipeline import ingest_text
    w = await make_world(mirror=False)
    owner: dict[str, str] = {}
    n = 0
    for topic, msgs in TOPICS.items():
        for src, who, title, text in msgs:
            n += 1
            nid = f"m{n}"
            await ingest_text(w.mem, w.gateway, w.index, source=src, native_id=nid, compartment="work", trust="received", text=text, title=title, extra_flags={"author": who})
            owner[nid] = topic
    for src, who, title, text in NOISE:
        n += 1
        nid = f"m{n}"
        await ingest_text(w.mem, w.gateway, w.index, source=src, native_id=nid, compartment="work", trust="received", text=text, title=title, extra_flags={"author": who})
        owner[nid] = "noise" + nid
    d = w.services.digest.build(24)
    # map each item back to its topic via text
    text_topic = {}
    for topic, msgs in TOPICS.items():
        for _s, _w, _t, text in msgs:
            text_topic[text[:40]] = topic
    sec_of: dict[str, int] = {}
    for si, s in enumerate(d["sections"]):
        for it in s["items"]:
            for key, tp in text_topic.items():
                if key[:30].lower() in (it["snippet"] or "").lower():
                    sec_of[key] = si
    pairs = list(itertools.combinations(text_topic.items(), 2))
    same_t = [(a, b) for a, b in pairs if a[1] == b[1]]
    diff_t = [(a, b) for a, b in pairs if a[1] != b[1]]
    tp = sum(1 for a, b in same_t if a[0] in sec_of and b[0] in sec_of and sec_of[a[0]] == sec_of[b[0]])
    fp = sum(1 for a, b in diff_t if a[0] in sec_of and b[0] in sec_of and sec_of[a[0]] == sec_of[b[0]])
    # needs-action: a section is flagged; ground truth: its majority topic is in URGENT
    flagged_ok = flagged_n = miss = 0
    for s in d["sections"]:
        tops = [tp_ for key, tp_ in text_topic.items() if sec_of.get(key) is not None and d["sections"][sec_of[key]] is s]
        if not tops:
            continue
        maj = max(set(tops), key=tops.count)
        truth = maj in URGENT
        if s["needs_action"]:
            flagged_n += 1
            flagged_ok += truth
        elif truth:
            miss += 1
    # faithfulness: every number in a summary appears in the section's items
    import re
    unfaithful = 0
    for s in d["sections"]:
        blob = " ".join(it["snippet"] for it in s["items"]).replace(",", "")
        nums = re.findall(r"\d[\d.]*\d|\d", re.sub(r"^\d+ (item|message|email|notification)\(s\)", "", s["summary"]).replace(",", ""))
        unfaithful += any(x not in blob for x in nums)
    tl = d["timeline"]
    dump = [{'t': x['title'], 'n': x['count'], 'sum': x['summary'][:200], 'na': x['needs_action']} for x in d['sections']]
    return {"suite": SUITE, "requirement": REQ, "n": len(text_topic) + len(NOISE),
            "metrics": {"same_topic_pairs_grouped_together": prop(tp, len(same_t)), "different_topic_pairs_wrongly_merged": prop(fp, len(diff_t)),
                        "flagged_sections_correct": prop(flagged_ok, flagged_n), "urgent_sections_missed": miss, "sections_with_unfaithful_numbers": prop(unfaithful, len(d["sections"])),
                        "sections": len(d["sections"]), "timeline_entries": len(tl) if isinstance(tl, list) else None},
            "failures": [], "sections_dump": dump, "notes": "15 author-written messages across 5 topics and 3 apps plus 3 noise items; pairwise grouping precision and recall against the author's topic labels; 'urgent' is the author's judgement. Small corpus."}
