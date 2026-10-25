"""FR3.1: multi-part requests become execution graphs, by code, in microseconds; unknown shapes fall back to the planner."""

import pytest

from ragnarok.harness.decompose import clauses_of, decompose, is_compound


@pytest.mark.parametrize("req,n", [
    ("look up what Dev needs from me and remind me about it tomorrow at 9am", 2),
    ("check what is on my calendar tomorrow and draft an email to priya@acme.com listing it", 2),
    ("find the invoice email and remind me to pay it on 18 november at 10am", 2),
    ("remind me to call mom tomorrow at 8am and send an email to a@b.com saying I called", 2),
    ("remind me to buy milk", 1), ("what did Ravi and Asha say about the budget", 1), ("tell Ravi on telegram that bread and butter are ready", 1),
])
def test_clause_splitting(req, n):
    assert len(clauses_of(req)) == n and is_compound(req) == (n > 1)


def test_read_then_remind_passes_the_result_forward():
    d = decompose("look up what Dev needs from me and remind me about it tomorrow at 9am")
    n1, n2 = d.plan.nodes
    assert (n1.skill, n1.params["query"]) == ("memory.search", "what Dev needs from me")
    assert n2.skill == "reminder.add" and n2.depends_on == ["n1"] and n2.params == {"text": "{ref:n1.first}", "when": "tomorrow at 9am"}


def test_calendar_then_draft_uses_the_summary_as_the_body():
    d = decompose("check what is on my calendar tomorrow and draft an email to priya@acme.com listing it")
    n1, n2 = d.plan.nodes
    assert n1.skill == "calendar.agenda" and n2.skill == "mail.draft" and n2.params["to"] == "priya@acme.com" and n2.params["body"] == "{ref:n1.summary}" and n2.depends_on == ["n1"]


def test_independent_writes_have_no_dependencies():
    d = decompose("remind me to call mom tomorrow at 8am and send an email to a@b.com saying I called")
    assert [n.skill for n in d.plan.nodes] == ["reminder.add", "mail.send"] and all(not n.depends_on for n in d.plan.nodes)
    assert d.plan.nodes[1].params["body"] == "I called"


@pytest.mark.parametrize("req", ["look up the invoice and email it", "check my calendar and tell the team", "find the budget and frobnicate the widget"])
def test_unrecognised_shapes_return_no_plan_and_a_reason(req):
    d = decompose(req)
    assert d.plan is None and d.reason
