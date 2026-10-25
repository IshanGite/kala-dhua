"""NFR4.3 / SEC-02 / SEC-20: every tier-2 case: approve, deny, timeout, edited params, replayed token, token for different params."""

from __future__ import annotations

import pytest

from ragnarok.broker.approvals import (
    APPROVAL_TTL_MS,
    STEPUP_DELAY_MS,
    ApprovalError,
    Broker,
    UiSession,
    params_hash,
)
from ragnarok.broker.autonomy import commit_requires_click, mode_allows
from ragnarok.core.clock import RealClock, SimClock, set_clock
from ragnarok.memory.ledger import Ledger
from ragnarok.memory.service import MemoryService

HUMAN = UiSession(human=True, csrf_ok=True)
MAIL = {"to": ["asha@example.com"], "subject": "Hello", "body": "See you at 4pm."}


@pytest.fixture
def world(tmp_path, keys):
    clock = SimClock(1_760_000_000_000)
    set_clock(clock)
    mem = MemoryService(tmp_path / "db" / "b.db", keys)
    ledger = Ledger(mem)
    tid = ledger.create_task("work", "send a mail", "user", "trace1")
    nid = ledger.add_node(tid, 0, "mail.send", MAIL, tier=2)
    yield Broker(ledger, keys), nid, clock, ledger
    mem.close()
    set_clock(RealClock())


def req(broker, nid, params=MAIL, tier=2, trust="user_direct"):
    return broker.create_request(nid, "mail.send", params, tier=tier, warnings=["First message to this address"], trust=trust)


def test_NFR4_3_approve_then_execute_once(world):
    b, nid, _, _ = world
    aid = req(b, nid)
    token = b.approve(aid, HUMAN)
    assert b.verify_at_execution(token, MAIL) is True
    assert b.verify_at_execution(token, MAIL) is False, "a token is single use (replay refused)"


def test_NFR4_3_deny_blocks_everything(world):
    b, nid, _, _ = world
    aid = req(b, nid)
    b.deny(aid, HUMAN)
    with pytest.raises(ApprovalError):
        b.approve(aid, HUMAN)


def test_NFR4_3_unanswered_request_expires_and_counts_as_denied(world):
    b, nid, clock, _ = world
    aid = req(b, nid)
    clock.advance(ms=APPROVAL_TTL_MS + 1)
    with pytest.raises(ApprovalError) as e:
        b.approve(aid, HUMAN)
    assert e.value.code == "E_APPROVAL_EXPIRED"


def test_NFR4_3_token_expires_before_execution(world):
    b, nid, clock, _ = world
    token = b.approve(req(b, nid), HUMAN)
    clock.advance(ms=APPROVAL_TTL_MS + 1)
    assert b.verify_at_execution(token, MAIL) is False


def test_NFR4_3_params_changed_after_approval_is_refused(world):
    b, nid, _, _ = world
    token = b.approve(req(b, nid), HUMAN)
    swapped = {**MAIL, "to": ["attacker@evil.example"]}
    assert b.verify_at_execution(token, swapped) is False
    assert b.verify_at_execution(token, MAIL) is True, "the refusal does not burn a legitimate approval"


def test_NFR4_3_edit_then_approve_binds_to_the_edited_content(world):
    b, nid, _, _ = world
    edited = {**MAIL, "body": "Actually, 5pm."}
    token = b.approve(req(b, nid), HUMAN, edit=edited)
    assert b.verify_at_execution(token, MAIL) is False
    assert b.verify_at_execution(token, edited) is True
    assert b.card(token.split(".")[0])["edited"] is True


def test_NFR4_3_token_for_a_different_approval_is_worthless(world):
    b, nid, _, ledger = world
    a1 = b.approve(req(b, nid), HUMAN)
    other = {**MAIL, "subject": "Other"}
    a2 = req(b, nid, other)
    forged = f"{a2}.{a1.split('.')[1]}.{a1.split('.')[2]}"
    assert b.verify_at_execution(forged, other) is False
    assert b.verify_at_execution("garbage", MAIL) is False
    assert b.verify_at_execution("a.b.c", MAIL) is False


def test_SEC_20_only_a_human_ui_session_can_approve(world):
    b, nid, _, _ = world
    aid = req(b, nid)
    for ui in (UiSession(), UiSession(human=True, csrf_ok=False), UiSession(human=False, csrf_ok=True)):
        with pytest.raises(ApprovalError) as e:
            b.approve(aid, ui)
        assert e.value.code == "E_APPROVAL_NOT_HUMAN"
    with pytest.raises(ApprovalError):
        b.deny(aid, UiSession())


def test_card_is_rendered_from_server_data_and_marks_content(world):
    b, nid, _, _ = world
    aid = req(b, nid, trust="received")
    card = b.card(aid)
    assert card["headline"] == "Send this email" and card["tainted"] and card["render_as"] == "plain_text"
    body = next(f for f in card["fields"] if f["name"] == "body")
    assert body["is_content"] is True and body["value"] == MAIL["body"]
    assert "First message to this address" in card["warnings"]


def test_tier3_stepup_needs_typed_phrase_and_a_delay(world):
    b, nid, clock, _ = world
    aid = b.create_request(nid, "fs.delete", {"count": 12, "paths": ["a"]}, tier=3, warnings=["Bulk delete"], trust="user_direct")
    with pytest.raises(ApprovalError):
        b.approve(aid, HUMAN, typed_phrase="CONFIRM 12")  # too fast
    clock.advance(ms=STEPUP_DELAY_MS + 1)
    with pytest.raises(ApprovalError):
        b.approve(aid, HUMAN, typed_phrase="yes")  # wrong phrase
    token = b.approve(aid, HUMAN, typed_phrase="CONFIRM 12")
    assert b.verify_at_execution(token, {"count": 12, "paths": ["a"]})


def test_autonomy_modes():
    assert mode_allows("observe", 0) and not mode_allows("observe", 1)
    assert mode_allows("draft", 1) and not mode_allows("draft", 2) and mode_allows("draft", 2, preparing=True)
    assert commit_requires_click(2) and commit_requires_click(3) and not commit_requires_click(1)


def test_params_hash_is_canonical():
    assert params_hash({"a": 1, "b": [1, 2]}) == params_hash({"b": [1, 2], "a": 1})
    assert params_hash({"a": 1}) != params_hash({"a": 2})
