from __future__ import annotations

import numpy as np
import pytest

from ragnarok.core.clock import get_clock
from ragnarok.core.errors import ModelError
from ragnarok.llm_gateway.cassette import CassetteGateway
from ragnarok.llm_gateway.client import Priority
from ragnarok.llm_gateway.fake import FakeGateway, hash_embedding


def _deadline():
    return get_clock().monotonic() + 5


async def test_fake_gateway_structured_and_text():
    g = FakeGateway(lambda m, s: {"intent": "mail"} if s else "hello")
    r = await g.chat(role="arbiter", messages=[{"role": "user", "content": "x"}], schema={"type": "object"}, max_tokens=50, deadline=_deadline(), priority=Priority.INTERACTIVE)
    assert r.parsed == {"intent": "mail"}
    t = await g.chat(role="planner", messages=[], schema=None, max_tokens=5, deadline=_deadline(), priority=Priority.BACKGROUND)
    assert t.text == "hello"


async def test_fake_embeddings_are_deterministic_and_similarity_ordered():
    g = FakeGateway()
    a, b, c = await g.embed(["vendor contract renewal date", "contract renewal for the vendor", "pasta recipe"], deadline=_deadline(), priority=Priority.INTERACTIVE)
    assert np.allclose(a, hash_embedding("vendor contract renewal date"))
    assert float(a @ b) > float(a @ c)
    assert abs(float(np.linalg.norm(a)) - 1.0) < 1e-5


async def test_cassette_record_then_replay_without_inner(tmp_path):
    path = tmp_path / "c.jsonl"
    inner = FakeGateway(lambda m, s: {"ok": True})
    rec = CassetteGateway(path, record_with=inner)
    kw = dict(role="arbiter", messages=[{"role": "user", "content": "hi"}], schema={"type": "object"}, max_tokens=20, deadline=_deadline(), priority=Priority.INTERACTIVE)
    first = await rec.chat(**kw)
    vec = await rec.embed(["a b"], deadline=_deadline(), priority=Priority.INTERACTIVE)
    replay = CassetteGateway(path)
    again = await replay.chat(**kw)
    assert again.parsed == first.parsed == {"ok": True}
    assert np.allclose(await replay.embed(["a b"], deadline=_deadline(), priority=Priority.INTERACTIVE), vec, atol=1e-5)
    assert path.read_bytes().count(b"\r\n") == 0, "cassettes must use LF line endings"


async def test_cassette_miss_is_an_error_not_a_guess(tmp_path):
    replay = CassetteGateway(tmp_path / "empty.jsonl")
    with pytest.raises(ModelError) as e:
        await replay.chat(role="arbiter", messages=[{"role": "user", "content": "unseen"}], schema=None, max_tokens=5, deadline=_deadline(), priority=Priority.INTERACTIVE)
    assert e.value.code == "E_CASSETTE_MISS"
