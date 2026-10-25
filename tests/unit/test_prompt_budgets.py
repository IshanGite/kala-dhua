"""Every structured prompt must fit its model's context window with room for the answer: a prompt that silently overflows makes the agent stop understanding requests (this once happened when examples were added)."""

from __future__ import annotations

import pytest

from ragnarok.harness import actionparse, understand
from ragnarok.llm_gateway.client import NUM_CTX, approx_tokens


@pytest.mark.parametrize("name,prompt,answer_tokens", [("understand", understand.SYSTEM, 60), ("parse_action", actionparse.SYSTEM, 160), ("parse_steps", actionparse.STEPS_SYSTEM, 420)])
def test_the_prompt_leaves_room_for_a_long_request_and_the_answer(name, prompt, answer_tokens):
    request = 900 // 3 + 40  # the longest request the parsers accept, in estimated tokens, plus the timestamp line
    used = approx_tokens(prompt) + request + answer_tokens
    assert used <= NUM_CTX["arbiter"] * 0.9, f"{name}: {used} of {NUM_CTX['arbiter']} tokens"
