"""Accuracy of the intent reading on a labelled set of everyday messages (author-written, one labeller: a regression guard, not a benchmark).
Run: uv run python scripts/eval_understand.py"""
import asyncio, json, sys, time
sys.path.insert(0, "src")
from pathlib import Path
from ragnarok.harness.understand import understand
from ragnarok.llm_gateway.client import OllamaGateway
from ragnarok.llm_gateway.models import ModelLock

SET = [
 # chat: conversation, general knowledge, advice, the assistant itself, live facts it cannot know
 ("hi", "chat"), ("hey there", "chat"), ("good morning", "chat"), ("thanks a lot", "chat"), ("lol ok", "chat"), ("who made you", "chat"), ("are you online", "chat"), ("what can you do for me", "chat"),
 ("tell me a joke", "chat"), ("i am bored", "chat"), ("i'm nervous about the demo", "chat"), ("give me a pep talk", "chat"), ("what is the capital of japan", "chat"), ("explain blockchain simply", "chat"),
 ("how do I boil an egg", "chat"), ("what is 15 percent of 80", "chat"), ("berlin time", "chat"), ("what is the weather in paris", "chat"), ("who won the match yesterday", "chat"), ("translate hello to spanish", "chat"),
 ("write a short poem about autumn", "chat"), ("suggest a name for my dog", "chat"), ("is it ok to skip breakfast", "chat"), ("how are you doing today", "chat"), ("good night", "chat"), ("what is the time in tokyo", "chat"),
 ("hola como estas", "chat"), ("bonjour ca va", "chat"), ("explain what an API is", "chat"), ("what should i cook tonight", "chat"),
 # recall: about the user's own data
 ("what did Ravi say", "recall"), ("anything from Mugdha", "recall"), ("did Dev reply", "recall"), ("when is the budget review", "recall"), ("how much is invoice 8812", "recall"), ("who sent me the contract", "recall"),
 ("what did I tell Priya about the offsite", "recall"), ("is there a mail from the bank", "recall"), ("what was the plan for friday", "recall"), ("find the email about the visa", "recall"), ("where is the pitch deck", "recall"),
 ("what did Tushar message me", "recall"), ("did anyone mention the budget", "recall"), ("show me emails from Meera", "recall"), ("what is in my notes about orion", "recall"), ("what did mom say", "recall"),
 # catchup: summaries of many things
 ("catch me up", "catchup"), ("what did I miss", "catchup"), ("summarize my emails", "catchup"), ("give me an overview of today", "catchup"), ("what is new on slack", "catchup"), ("brief me", "catchup"),
 ("anything new", "catchup"), ("summary of everything", "catchup"), ("what happened while I was away", "catchup"), ("what needs my attention", "catchup"), ("anything urgent", "catchup"), ("what do I need to reply to", "catchup"),
 ("resume mis correos", "catchup"), ("recap discord", "catchup"),
 # agenda
 ("what's on my calendar tomorrow", "agenda"), ("what do I have today", "agenda"), ("am I free on friday", "agenda"), ("what is my day like", "agenda"), ("show my schedule for next week", "agenda"),
 ("any meetings tomorrow", "agenda"), ("when is my next meeting", "agenda"), ("what's my agenda", "agenda"),
 # screen
 ("explain my screen", "screen"), ("what am I looking at", "screen"), ("read what is on screen", "screen"), ("what is this page", "screen"), ("describe my screen", "screen"), ("what's on my display", "screen"),
 # action
 ("remind me to call mom at 5", "action"), ("set a reminder for tomorrow 9am gym", "action"), ("tell Ravi on telegram I'm late", "action"), ("email sam@x.com that the report is done", "action"),
 ("put dentist friday 5pm on my calendar", "action"), ("create a doc called plan with the key dates", "action"), ("post in slack general that the build is green", "action"), ("send tushar a message on discord saying hi", "action"),
 ("reply to dev saying thursday works", "action"), ("make a doc called papa with a poem and mail it to a@b.com", "action"), ("draft an email to hr@x.com about leave", "action"), ("schedule lunch with priya at 1", "action"),
 ("can u text tushar on tg im omw", "action"), ("kal subah 9 baje gym yaad dilana", "action"), ("recuerdame llamar al banco manana a las 5", "action"), ("run python to print 1 to 10", "action"), ("wake me up at 6", "action"),
 ("remind me in an hour", "action"), ("add a meeting with the team on monday 11am", "action"), ("message mom on telegram happy birthday", "action"),
 # unclear
 ("asdf", "unclear"), ("hmm", "unclear"), ("do something", "unclear"), ("that one", "unclear"), ("???", "unclear"),
]


async def main():
    gw = OllamaGateway(ModelLock.load(Path("models.lock")))
    ok, rows, t0 = 0, [], time.time()
    for text, exp in SET:
        u = await understand(gw, text, None)
        got = u.intent if u else None
        rows.append((text, exp, got))
        ok += got == exp
    print(f"{ok}/{len(SET)} correct; {(time.time() - t0) / len(SET):.2f} s each")
    for t, e, g in rows:
        if e != g:
            print(f"MISS {t!r}: expected {e}, got {g}")
    Path("docs/evidence").mkdir(exist_ok=True)
    Path("docs/evidence/understand_eval.json").write_text(json.dumps({"n": len(SET), "correct": ok, "note": "author-written labelled messages, one labeller; a regression guard, not a benchmark", "misses": [{"text": t, "expected": e, "got": g} for t, e, g in rows if e != g]}, indent=1), encoding="utf-8")


asyncio.run(main())
