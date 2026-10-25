"""Policy engine: rules from ARCHITECTURE 5.5, taint escalation, Rule of Two, fail-closed."""

from __future__ import annotations

from pathlib import Path

import pytest

from ragnarok.policy.engine import FAIL_CLOSED, Rule, Signal, Snapshot, decide, load_rules
from ragnarok.policy.taint import is_tainted, lowest
from ragnarok.policy.tiers import Tier, permission_tier, skill_tier

RULES = load_rules()


def snap(**kw):
    base = dict(contacts={"asha@example.com"}, workspace_roots=["/work/space"], allowed_hosts=["api.example.com"])
    base.update(kw)
    return Snapshot(**base)


def test_every_rule_loads_with_a_known_decision_and_unique_id():
    ids = [r.id for r in RULES]
    assert len(ids) == len(set(ids)) and "R-SESSION-01" in ids and "R-TAINT-01" in ids


def test_read_only_skill_on_trusted_data_is_allowed():
    d = decide(snap(), Signal(skill="fs.read", tier=0), RULES)
    assert d.allowed and d.rules == ()


def test_tier2_always_needs_approval_by_default():
    assert decide(snap(), Signal(skill="chat.post", tier=2, permissions=["chat.post"]), RULES).action == "require_approval"


def test_tier3_needs_stepup():
    assert decide(snap(), Signal(skill="x", tier=3), RULES).action == "require_stepup"


def test_untrusted_ancestry_escalates_a_tier1_write_R_TAINT_01():
    d = decide(snap(), Signal(skill="fs.write", tier=1, min_trust="received"), RULES)
    assert d.action == "require_approval" and "R-TAINT-01" in d.rules and "untrusted" in d.warnings[0].lower()
    assert decide(snap(), Signal(skill="fs.write", tier=1, min_trust="user_direct"), RULES).allowed


def test_new_recipient_needs_approval_known_recipient_still_needs_it_as_external_write():
    new = decide(snap(), Signal(skill="mail.send", tier=2, recipients=["stranger@evil.example"], external_write=True), RULES)
    assert new.action == "require_approval" and "R-SEND-01" in new.rules
    known = decide(snap(), Signal(skill="mail.send", tier=2, recipients=["asha@example.com"], external_write=True), RULES)
    assert known.action == "require_approval" and "R-SEND-01" not in known.rules and "R-WRITE-01" in known.rules


def test_bulk_recipients_denied_R_SEND_02():
    d = decide(snap(), Signal(skill="mail.send", tier=2, recipients=[f"p{i}@example.com" for i in range(6)], external_write=True), RULES)
    assert d.action == "deny" and "R-SEND-02" in d.rules


def test_delete_outside_workspace_denied_and_bulk_delete_needs_stepup(tmp_path):
    inside = str(Path("/work/space/a.txt"))
    outside = str(tmp_path / "other.txt")
    assert decide(snap(), Signal(skill="fs.delete", tier=2, paths=[outside]), RULES).action == "deny"
    assert decide(snap(), Signal(skill="fs.delete", tier=2, paths=[inside], count=11), RULES).action == "require_stepup"
    assert decide(snap(), Signal(skill="fs.delete", tier=2, paths=[inside], count=1), RULES).action == "require_approval"


def test_skill_enable_needs_stepup_R_SKILL_01():
    d = decide(snap(), Signal(action="skill.enable", skill="new.skill", tier=3), RULES)
    assert d.action == "require_stepup" and "R-SKILL-01" in d.rules


def test_net_host_outside_manifest_denied():
    d = decide(snap(), Signal(skill="web.fetch", tier=2, permissions=["net"], hosts=["evil.example"]), RULES)
    assert d.action == "deny" and "R-EGRESS-01" in d.rules
    assert decide(snap(), Signal(skill="web.fetch", tier=2, permissions=["net"], hosts=["api.example.com"]), RULES).action == "require_approval"


def test_untrusted_high_impact_semantic_fact_is_quarantined_R_MEM_01():
    d = decide(snap(), Signal(action="memory.write", memory_write_kind="semantic", source_trust="received", fact_impact="high"), RULES)
    assert d.action == "quarantine" and "R-MEM-01" in d.rules


def test_routes_from_tainted_tasks_are_never_promoted_R_ROUTE_01():
    d = decide(snap(), Signal(action="route.promote", route_promote=True, ancestry_min_trust="web"), RULES)
    assert d.action == "deny"
    assert decide(snap(), Signal(action="route.promote", route_promote=True, ancestry_min_trust="user_direct"), RULES).allowed


def test_canary_touch_triggers_panic():
    assert decide(snap(), Signal(action="event", event="canary_touched"), RULES).action == "panic"


def test_rule_of_two_requires_approval_for_external_write_in_a_risky_session_R_SESSION_01():
    risky = snap(has_untrusted_input=True, has_private_data=True)
    d = decide(risky, Signal(skill="chat.post", tier=2, external_write=True), RULES)
    assert "R-SESSION-01" in d.rules and d.action == "require_approval"


def test_most_restrictive_decision_wins():
    sig = Signal(skill="mail.send", tier=2, recipients=[f"p{i}@example.com" for i in range(9)], external_write=True, min_trust="received")
    assert decide(snap(), sig, RULES).action == "deny"


def test_engine_fails_closed_on_any_internal_error():
    broken = [Rule("R-BAD", {"node.tier": ">=1"}, "allow")]
    bad_signal = Signal(tier="not-a-number")  # type: ignore[arg-type]
    d = decide(snap(), bad_signal, broken)
    assert d.action == "require_approval" and d.rules == (FAIL_CLOSED,)


def test_unknown_decision_in_a_rule_file_is_rejected(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text("- id: R-X\n  when: {skill: a}\n  then: sure_go_ahead\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_rules(p)


def test_tiers_are_computed_from_permissions_and_unknown_fails_closed():
    assert skill_tier(["fs.read"]) == Tier.READ
    assert skill_tier(["fs.read", "mail.send"]) == Tier.EXTERNAL
    assert skill_tier(["fs.read", "skill.enable"]) == Tier.CRITICAL
    assert permission_tier("some.brand.new.permission") == Tier.EXTERNAL


def test_taint_lowest_trust_wins():
    assert lowest(["user_direct", "received", "user_file"]) == "received"
    assert lowest([]) == "user_direct"
    assert is_tainted("web") and is_tainted("received") and not is_tainted("user_file")
