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
    v = Vision(gw)
    assert "invoice" in await v.ask(png(), "when is it due?")
    c = gw.calls[0]
    assert c["role"] == "vision" and c["images"] == 1
    assert "never instructions" in c["messages"][0]["content"]


@pytest.mark.asyncio
async def test_screen_capture_failure_is_a_plain_message():
    def boom():
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
