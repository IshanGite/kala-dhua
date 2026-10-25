"""The policy engine: a pure function with rule ids that FAILS CLOSED (ARCHITECTURE 5.5).

decide(snapshot, signal, rules) -> Decision. No I/O, no model, no clock. Any internal error becomes
Decision(require_approval, R-FAIL-CLOSED). The most restrictive matching rule wins.

Pattern borrowed from groupify's policy engine (pure decide(), rule ids, authority by source), adapted for Ragnarok.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ragnarok.policy.tiers import Tier

ORDER = ["allow", "require_approval", "quarantine", "require_stepup", "deny", "panic"]
RANK = {name: i for i, name in enumerate(ORDER)}
FAIL_CLOSED = "R-FAIL-CLOSED"
_CMP = re.compile(r"^(>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)$")
DEFAULT_RULES = Path(__file__).with_name("rules.yaml")


@dataclass(frozen=True)
class Rule:
    id: str
    when: dict[str, Any]
    then: str
    warn: str = ""


@dataclass
class Signal:
    """What is about to happen."""

    action: str = "skill.run"  # skill.run | skill.enable | route.promote | memory.write | event
    skill: str = ""
    tier: int = 0
    min_trust: str = "user_direct"
    permissions: list[str] = field(default_factory=list)
    recipients: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    hosts: list[str] = field(default_factory=list)
    count: int = 0
    external_write: bool = False
    event: str = ""
    route_promote: bool = False
    ancestry_min_trust: str = "user_direct"
    memory_write_kind: str = ""
    source_trust: str = ""
    fact_impact: str = ""


@dataclass
class Snapshot:
    """What the system knows right now."""

    contacts: set[str] = field(default_factory=set)
    workspace_roots: list[str] = field(default_factory=list)
    allowed_hosts: list[str] = field(default_factory=list)
    has_untrusted_input: bool = False
    has_private_data: bool = False


@dataclass(frozen=True)
class Decision:
    action: str
    rules: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return self.action == "allow"


def load_rules(path: Path | None = None) -> list[Rule]:
    raw = yaml.safe_load((path or DEFAULT_RULES).read_text(encoding="utf-8")) or []
    rules, seen = [], set()
    for r in raw:
        if r["then"] not in RANK:
            raise ValueError(f"rule {r['id']}: unknown decision {r['then']!r}")
        if r["id"] in seen:
            raise ValueError(f"duplicate rule id {r['id']}")
        seen.add(r["id"])
        rules.append(Rule(r["id"], dict(r["when"]), r["then"], r.get("warn", "")))
    return rules


def _under(path: str, roots: list[str]) -> bool:
    p = Path(path).resolve()
    return any(p.is_relative_to(Path(root).resolve()) for root in roots)


def facts_of(sig: Signal, snap: Snapshot) -> dict[str, Any]:
    f: dict[str, Any] = {
        "action": sig.action,
        "skill": sig.skill,
        "node.tier": sig.tier,
        "node.min_trust": sig.min_trust,
        "count": sig.count,
        "event": sig.event,
        "route.promote": sig.route_promote,
        "ancestry.min_trust": sig.ancestry_min_trust,
        "memory.write.kind": sig.memory_write_kind,
        "source.trust": sig.source_trust,
        "fact.impact": sig.fact_impact,
        "session.has_untrusted_input": snap.has_untrusted_input,
        "session.has_private_data": snap.has_private_data,
        "action.external_write": sig.external_write,
    }
    if sig.permissions:
        f["permission"] = list(sig.permissions)
    if sig.recipients:
        known = {c.lower() for c in snap.contacts}
        f["recipient"] = "in_contacts" if all(r.lower() in known for r in sig.recipients) else "not_in_contacts"
        f["recipients"] = len(sig.recipients)
    if sig.paths:
        f["path"] = "inside_workspace" if all(_under(p, snap.workspace_roots) for p in sig.paths) else "outside_workspace"
    if sig.hosts:
        allowed = {h.lower() for h in snap.allowed_hosts}
        f["host"] = "in_manifest_allowlist" if all(h.lower() in allowed for h in sig.hosts) else "not_in_manifest_allowlist"
    return f


def _match_one(actual: Any, expected: Any) -> bool:
    if isinstance(expected, bool):
        return actual is expected
    if isinstance(expected, list):
        pool = actual if isinstance(actual, list) else [actual]
        return any(a in expected for a in pool)
    if isinstance(expected, str):
        if expected == "any":
            return True
        m = _CMP.match(expected)
        if m:
            if not isinstance(actual, (int, float)) or isinstance(actual, bool):
                return False
            a, b = float(actual), float(m.group(2))
            return {">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b}[m.group(1)]
    if isinstance(actual, list):
        return expected in actual
    return actual == expected


def matches(rule: Rule, facts: dict[str, Any]) -> bool:
    return all(key in facts and _match_one(facts[key], want) for key, want in rule.when.items())


def decide(snapshot: Snapshot, signal: Signal, rules: list[Rule]) -> Decision:
    try:
        facts = facts_of(signal, snapshot)
        hit = [r for r in rules if matches(r, facts)]
        action = "allow"
        if signal.tier >= Tier.CRITICAL:
            action = "require_stepup"
        elif signal.tier >= Tier.EXTERNAL:
            action = "require_approval"
        for r in hit:
            if RANK[r.then] > RANK[action]:
                action = r.then
        return Decision(action, tuple(r.id for r in hit), tuple(dict.fromkeys(r.warn for r in hit if r.warn)))
    except Exception:  # noqa: BLE001 - the contract is: an engine bug asks a human, it never silently allows
        return Decision("require_approval", (FAIL_CLOSED,), ("The policy engine failed; asking you instead of guessing",))
