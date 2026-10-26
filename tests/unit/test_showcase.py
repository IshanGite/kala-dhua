"""Showcase buttons: every feature runs directly on real data, results come back as chat replies, sends wait for a click, the attack is a labelled simulation."""

from __future__ import annotations

import asyncio

import pytest

from ragnarok.core.clock import RealClock, set_clock
from ragnarok.daemon.chat import summarize
from ragnarok.daemon.wiring import build_world, warm_up
from ragnarok.ingest.pipeline import ingest_text
from ragnarok.llm_gateway.fake import FakeGateway
from ragnarok.platform.secrets import MemoryStore

SENT: list = []


async def fake_post(chat_id, text):
    SENT.append(("post", chat_id, text))
    return "m1"


async def fake_mail(p):
    SENT.append(("mail", p["to"], p["subject"], p["body"]))
    return "gm1"


async def fake_drive(p):
    SENT.append(("drive", p["title"], p["text"]))
    return "https://docs.example/d/1"


async def fake_cal(p, due):
    SENT.append(("calendar", p["title"]))
    return "https://calendar.example/e"


@pytest.fixture
def world(tmp_path, monkeypatch):
    set_clock(RealClock())
    monkeypatch.setenv("RAGNAROK_HOME", str(tmp_path / "home"))
    SENT.clear()
    w = build_world(vault=MemoryStore(), gateway=FakeGateway(lambda m, s: "A short poem about privacy." if s is None else {}), sandbox=False, use_laya=False, embed_rungs=False,
                    senders={"telegram": fake_post, "mail": fake_mail, "drive": fake_drive, "calendar": fake_cal}, max_cpu=101)
    yield w
    w.mem.close()


async def start(w):
    await warm_up(w)
    await ingest_text(w.mem, w.gateway, w.index, source="telegram", native_id="55:1", compartment="work", trust="received", text="Can you send me the contract by Friday?", title="Ravi", extra_flags={"author": "Ravi"})


async def clicks(w, approve=True, n=1):
    done = 0
    while done < n:
        for c in w.services.hub.pending():
            w.services.hub.decide(c["id"], approve)
            done += 1
        await asyncio.sleep(0.01)


async def test_every_item_is_listed_with_a_label_and_help(world):
    ids = {i["id"] for i in world.services.showcase.items()}
    assert {"catchup", "attention", "agenda", "screen", "alert", "reminder", "attack", "email", "telegram", "discord", "slack", "calendar", "docmail"} <= ids
    assert all(i["label"] and i["help"] for i in world.services.showcase.items())


async def test_catchup_and_attention_read_real_messages(world):
    await start(world)
    assert "Ravi" in summarize(await world.services.showcase.run("catchup"))["text"]
    assert "Ravi" in summarize(await world.services.showcase.run("attention"))["text"]


async def test_alert_button_raises_a_real_inbox_alert(world):
    await start(world)
    out = summarize(await world.services.showcase.run("alert"))
    assert "Alert raised" in out["text"] and any("Ravi" in i["title"] or "Ravi" in i["body"] for i in world.services.proactive.inbox.items())


async def test_attack_is_a_labelled_simulation_that_sends_nothing(world):
    await start(world)
    out = summarize(await world.services.showcase.run("attack"))
    assert out["text"].startswith("SIMULATION") and "withheld" in out["text"] and SENT == []


async def test_telegram_button_goes_through_a_click_and_posts_to_the_real_chat(world):
    await start(world)
    task = asyncio.create_task(world.services.showcase.run("telegram"))
    await asyncio.wait_for(clicks(world), 5)
    out = summarize(await asyncio.wait_for(task, 5))
    assert SENT[0][:2] == ("post", "55") and "posted" in out["text"].lower()


async def test_denying_sends_nothing(world):
    await start(world)
    task = asyncio.create_task(world.services.showcase.run("email"))
    await asyncio.wait_for(clicks(world, False), 5)
    await asyncio.wait_for(task, 5)
    assert SENT == []


async def test_doc_and_mail_uses_the_documents_real_link(world):
    await start(world)
    task = asyncio.create_task(world.services.showcase.run("docmail"))
    await asyncio.wait_for(clicks(world, True, 2), 8)
    await asyncio.wait_for(task, 8)
    kinds = [s[0] for s in SENT]
    assert kinds == ["drive", "mail"] and "https://docs.example/d/1" in SENT[1][3] and "privacy" in SENT[1][3]


async def test_email_button_goes_to_the_demo_recipient(world):
    from ragnarok.harness.showcase import DEFAULT_TO

    await start(world)
    task = asyncio.create_task(world.services.showcase.run("email"))
    await asyncio.wait_for(clicks(world), 5)
    await asyncio.wait_for(task, 5)
    assert SENT[0][0] == "mail" and SENT[0][1] == DEFAULT_TO == "tushar.singh.software@gmail.com"


async def test_full_flow_writes_a_doc_mails_the_link_adds_an_event_and_tells_the_chat(world):
    await start(world)
    task = asyncio.create_task(world.services.showcase.run("flow"))
    await asyncio.wait_for(clicks(world, True, 4), 20)
    await asyncio.wait_for(task, 20)
    kinds = [s[0] for s in SENT]
    assert kinds[0] == "drive" and set(kinds) == {"drive", "mail", "calendar", "post"}
    mail = next(s for s in SENT if s[0] == "mail")
    post = next(s for s in SENT if s[0] == "post")
    assert mail[1] == "tushar.singh.software@gmail.com" and "https://docs.example/d/1" in mail[3] and "https://docs.example/d/1" in post[2]


async def test_composer_sends_what_was_typed_where_it_was_typed(world):
    await start(world)
    task = asyncio.create_task(world.services.showcase.run("send", {"app": "mail", "to": "someone@example.com", "text": "Lunch is at one\nSee you there"}))
    await asyncio.wait_for(clicks(world), 5)
    await asyncio.wait_for(task, 5)
    assert SENT[0][:3] == ("mail", "someone@example.com", "Lunch is at one") and "See you there" in SENT[0][3]


async def test_composer_refuses_a_bad_address_and_sends_nothing(world):
    await start(world)
    out = summarize(await world.services.showcase.run("send", {"app": "mail", "to": "not an address", "text": "hi"}))
    assert "not an email address" in out["text"] and SENT == [] and not world.services.hub.pending()


async def test_composer_posts_to_a_chat_and_apps_say_what_is_ready(world):
    await start(world)
    apps = {a["id"]: a for a in world.services.showcase.apps()}
    assert apps["telegram"]["ready"] and apps["telegram"]["target"] == "Ravi" and not apps["slack"]["ready"] and apps["mail"]["target"] == "tushar.singh.software@gmail.com"
    task = asyncio.create_task(world.services.showcase.run("send", {"app": "telegram", "to": "", "text": "Demo is at nine"}))
    await asyncio.wait_for(clicks(world), 5)
    await asyncio.wait_for(task, 5)
    assert SENT[0] == ("post", "55", "Demo is at nine")
