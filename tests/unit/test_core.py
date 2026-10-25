from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ragnarok.core import ids
from ragnarok.core.clock import DAY_MS, SimClock
from ragnarok.core.logging import ContentInLogError, log


def test_simclock_advances_only_when_told():
    c = SimClock(start_ms=1000)
    assert c.now_ms() == 1000
    c.advance(days=2)
    assert c.now_ms() == 1000 + 2 * DAY_MS


def test_seeded_ids_are_reproducible():
    ids.seed_ids(7)
    a = [ids.new_id() for _ in range(3)]
    ids.seed_ids(7)
    b = [ids.new_id() for _ in range(3)]
    ids.seed_ids(None)
    assert a == b and len(set(a)) == 3


def test_stable_key_is_stable_across_calls():
    assert ids.stable_key("task", "node") == ids.stable_key("task", "node")
    assert ids.stable_key("a", "bc") != ids.stable_key("ab", "c")


def test_log_accepts_codes_and_ids(capsys):
    log("task_started", task="abc123", n=3)
    assert "task_started" in capsys.readouterr().err


def test_log_refuses_content():
    with pytest.raises(ContentInLogError):
        log("oops", body="Dear Priya, please find the contract attached and let me know")
    with pytest.raises(ContentInLogError):
        log("oops", blob="x" * 200)


def test_failpoint_exits_abruptly_with_137():
    code = "from ragnarok.core.failpoints import failpoint; failpoint('p1'); print('survived')"
    src = str(Path(__file__).resolve().parents[2] / "src")
    env = {**os.environ, "RAGNAROK_FAILPOINT": "p1", "PYTHONPATH": src}
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert r.returncode == 137 and "survived" not in r.stdout
    r2 = subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": src}, capture_output=True, text=True)
    assert r2.returncode == 0 and "survived" in r2.stdout
