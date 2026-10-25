"""FR3.1 plan validation (pure) and FR3.3 code-based success criteria."""

from __future__ import annotations

import pytest

from ragnarok.harness.plan import MAX_NODES, Plan, PlanNode, validate_plan
from ragnarok.harness.verify import all_passed, dig, evaluate
from ragnarok.skills.manifest import Manifest

READ = Manifest.model_validate({"name": "fs.read", "description": "Use when reading.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                                "permissions": [{"permission": "fs.read"}], "entrypoint": {"type": "python"}})
SEND = Manifest.model_validate({"name": "mail.send", "description": "Use when sending.", "parameters": {"type": "object", "properties": {"to": {"type": "array", "items": {"type": "string"}}, "body": {"type": "string"}}, "required": ["to", "body"]},
                                "permissions": [{"permission": "mail.send"}], "side_effects": "irreversible", "dry_run": True, "entrypoint": {"type": "native", "action": "mail.send"}})
SKILLS = {"fs.read": READ, "mail.send": SEND}
OK = [{"kind": "ok"}]


def node(i, skill="fs.read", params=None, deps=(), crit=OK):
    return PlanNode(id=i, goal=f"step {i}", skill=skill, params=params or {"path": "a.md"}, depends_on=list(deps), criteria=list(crit))


def check(*nodes):
    return validate_plan(Plan(nodes=list(nodes)), SKILLS.get)


def test_valid_plan_gets_a_topological_order_and_an_approval_preview():
    v = check(node("n1"), node("n2", "mail.send", {"to": ["a@b.co"], "body": "see {ref:n1.result.text}"}, deps=["n1"]))
    assert v.ok and v.order == ["n1", "n2"]
    assert any("n2" in a and "mail.send" in a for a in v.approvals_needed), "the user sees every click the plan will need before it runs"


def test_cycle_unknown_skill_missing_criteria_and_bad_refs_are_rejected():
    assert any("cycle" in e for e in check(node("a", deps=["b"]), node("b", deps=["a"])).errors)
    assert any("unknown skill" in e for e in check(node("a", "no.such.skill")).errors)
    assert any("success criterion" in e for e in check(node("a", crit=[])).errors)
    assert any("unknown step" in e for e in check(node("a", deps=["ghost"])).errors)
    assert any("without depending" in e for e in check(node("a"), node("b", "mail.send", {"to": ["x@y.co"], "body": "{ref:a.result.text}"})).errors)


def test_parameters_are_checked_against_the_skill_schema_but_references_are_allowed():
    assert any("do not match" in e for e in check(node("a", params={"nope": 1})).errors)
    assert check(node("a"), node("b", "fs.read", {"path": "{ref:a.result.text}"}, deps=["a"])).ok


def test_node_cap_and_duplicate_ids():
    assert any("more than" in e for e in check(*[node(f"n{i}") for i in range(MAX_NODES + 1)]).errors)
    assert any("duplicate" in e for e in check(node("a"), node("a")).errors)


def test_empty_plan_is_invalid():
    assert not check().ok


def test_dig_walks_nested_results_and_lists():
    r = {"matches": [{"path": "a.md"}, {"path": "b.md"}], "count": 2}
    assert dig(r, "matches[1].path") == "b.md" and dig(r, "count") == 2 and dig(r, "missing.x") is None and dig(r, "matches[5].path") is None


def test_criteria_are_checked_by_code(tmp_path):
    (tmp_path / "out.md").write_text("x", encoding="utf-8")
    result = {"text": "renewal on 1 March", "matches": [1, 2, 3], "sent": True}
    res = evaluate([{"kind": "field_present", "path": "text"}, {"kind": "contains", "path": "text", "value": "renewal"},
                    {"kind": "min_count", "path": "matches", "n": 3}, {"kind": "equals", "path": "sent", "value": True}, {"kind": "file_exists", "path": "out.md"}],
                   result, workspace=tmp_path)
    assert all_passed(res)
    bad = evaluate([{"kind": "min_count", "path": "matches", "n": 9}], result)
    assert not all_passed(bad)
    assert not all_passed(evaluate([{"kind": "judge"}], result)), "a model-judged criterion never passes by default"
    assert not all_passed(evaluate([{"kind": "weird"}], result))
    assert not all_passed([]), "no criteria is never success"


@pytest.mark.parametrize("path,expected", [("a.b", 3), ("a.c[0]", 7), ("a.z", None)])
def test_dig_parametrized(path, expected):
    assert dig({"a": {"b": 3, "c": [7]}}, path) == expected
