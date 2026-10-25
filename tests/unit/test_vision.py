"""Explain the screen / ask about an image: validation, shrinking, capture errors, and that only the user's typed request can look at the screen."""

from __future__ import annotations

import base64
import io

import pytest
from PIL import Image

from ragnarok.core.errors import RagnarokError
from ragnarok.harness.vision import SCREEN_ASK, Vision, prepare
from ragnarok.llm_gateway.fake import FakeGateway
from ragnarok.platform.screen import ScreenUnavailable


def png(size=(2400, 1500)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(b, "PNG")
    return b.getvalue()


def test_prepare_shrinks_large_screens():
    out = Image.open(io.BytesIO(base64.b64decode(prepare(png()))))
    assert max(out.size) <= 1280


def test_prepare_rejects_non_images_and_huge_files():
    with pytest.raises(RagnarokError):
        prepare(b"not an image at all")
    with pytest.raises(RagnarokError):
        prepare(b"\x89PNG\r\n\x1a\n" + b"0" * (13 * 1024 * 1024))


@pytest.mark.asyncio
async def test_ask_sends_one_image_with_untrusted_text_rule():
    gw = FakeGateway(lambda m, s: "An invoice, due 20 November.")
    v = Vision(gw, reader=lambda b: "")
    assert "invoice" in await v.ask(png(), "when is it due?", engine="deep")
    c = gw.calls[0]
    assert c["role"] == "vision" and c["images"] == 1
    assert "never instructions" in c["messages"][0]["content"]


@pytest.mark.asyncio
async def test_screen_capture_failure_is_a_plain_message():
    def boom(mode="screen", display=1):
        raise ScreenUnavailable("allow screen recording")

    with pytest.raises(RagnarokError) as e:
        await Vision(FakeGateway(), capture=boom).explain_screen()
    assert "screen" in str(e.value)


@pytest.mark.parametrize("t", ["explain my screen", "what's on my screen", "describe the screen", "what am I looking at on the screen"])
def test_screen_phrases_match(t):
    assert SCREEN_ASK.search(t)


@pytest.mark.parametrize("t", ["send an email to Ravi", "what did Ravi say yesterday"])
def test_other_requests_do_not_match(t):
    assert not SCREEN_ASK.search(t)


def test_black_capture_is_detected_as_missing_permission():
    from ragnarok.harness.vision import looks_blank

    b = io.BytesIO()
    Image.new("RGB", (800, 500), (0, 0, 0)).save(b, "PNG")
    assert looks_blank(b.getvalue())
    assert not looks_blank(png())


@pytest.mark.asyncio
async def test_read_engine_uses_screen_text_and_the_language_model_not_the_visual_model():
    gw = FakeGateway(lambda m, s: "It is an invoice from Kestrel, due 20 November.")
    v = Vision(gw, reader=lambda b: "Kestrel Supplies INVOICE 8812 " * 10)
    out = await v.ask(png(), None, engine="auto")
    assert "invoice" in out.lower() and [c["role"] for c in gw.calls] == ["arbiter"]
    assert "never instructions" in gw.calls[0]["messages"][0]["content"] and "<<<" in gw.calls[0]["messages"][1]["content"]


@pytest.mark.asyncio
async def test_sparse_text_falls_through_to_the_visual_model():
    gw = FakeGateway(lambda m, s: "A photo of a cat.")
    out = await Vision(gw, reader=lambda b: "hi").ask(png(), None, engine="auto")
    assert "cat" in out and gw.calls[-1]["role"] == "vision"


@pytest.mark.asyncio
async def test_visual_failure_still_returns_what_the_text_says():
    from ragnarok.core.errors import ModelError

    calls = []

    def responder(m, s):
        calls.append(1)
        return "Text based answer."

    class Flaky(FakeGateway):
        async def chat(self, **kw):
            if kw["role"] == "vision":
                raise ModelError("slow", code="E_MODEL_TIMEOUT")
            return await super().chat(**kw)

    out = await Vision(Flaky(responder), reader=lambda b: "short text").ask(png(), None, engine="auto")
    assert out == "Text based answer."


@pytest.mark.asyncio
async def test_cancelled_selection_is_a_plain_message():
    from ragnarok.platform.screen import ScreenCancelled

    def cancel(mode="screen", display=1):
        raise ScreenCancelled("Selection cancelled, nothing was captured.")

    with pytest.raises(RagnarokError) as e:
        await Vision(FakeGateway(), capture=cancel).explain_screen(mode="region")
    assert e.value.code == "E_SCREEN_CANCEL"


def test_display_list_always_has_a_main_display():
    from ragnarok.platform.screen import list_displays

    d = list_displays()
    assert d and d[0]["index"] == 1
