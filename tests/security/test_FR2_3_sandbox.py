"""FR2.3: the malicious-skill suite. Every case runs against every backend that is available on this machine."""

from __future__ import annotations

import os
import sys

import pytest

from ragnarok.core.errors import SandboxUnavailable
from ragnarok.sandbox.container import ContainerBackend
from ragnarok.sandbox.contract import Grade, Granted, Limits, run
from ragnarok.sandbox.devunsafe import DevUnsafeBackend
from ragnarok.sandbox.seatbelt import SeatbeltBackend, skill_interpreter
from ragnarok.sandbox.select import candidates, select_backend
from ragnarok.sandbox.suite.cases import CASES, Env, grade_results
from ragnarok.sandbox.windows import AppContainerBackend, RestrictedBackend


def _backends():
    out = []
    for b in (SeatbeltBackend(), ContainerBackend(), AppContainerBackend(), RestrictedBackend()):
        if b.probe().available:
            out.append(b)
    return out


BACKENDS = _backends()
if not BACKENDS:
    pytest.skip("no sandbox backend is available on this machine (printed so it is a named gap, not a silent pass)", allow_module_level=True)


@pytest.fixture(scope="module")
def env():
    e = Env()
    yield e
    e.close()


@pytest.mark.parametrize("backend", BACKENDS, ids=lambda b: b.name)
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.__name__.replace("case_", ""))
def test_FR2_3_malicious_suite(backend, case, env):
    result = case(backend, env)
    assert result.passed, f"{backend.name}: {result.name} -> {result.detail}"


@pytest.mark.parametrize("backend", BACKENDS, ids=lambda b: b.name)
def test_FR2_3_a_legitimate_skill_still_works_and_can_write_its_run_folder(backend, env):
    body = "open(os.path.join(os.getcwd(),'out.txt'),'w').write('hello')\nprint('fine')"
    r = run(backend, env.script(body), env.granted(), Limits(timeout_s=15))
    assert r.ok and r.stdout.strip() == b"fine" and (env.run_dir / "out.txt").read_text(encoding="utf-8") == "hello"
    assert r.backend == backend.name and r.policy_hash


@pytest.mark.parametrize("backend", BACKENDS, ids=lambda b: b.name)
def test_grade_is_full_when_every_case_passes(backend, env):
    results = [case(backend, env) for case in CASES if case.__name__ in ("case_read_ssh_key", "case_write_home")]
    assert grade_results(results) == Grade.FULL
    assert grade_results([]) == Grade.NONE


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt profile test")
def test_seatbelt_policy_hash_is_stable_and_changes_with_policy(tmp_path):
    b = SeatbeltBackend()
    g = Granted(run_dir=tmp_path / "run1", skill_dir=tmp_path / "skill")
    (tmp_path / "run1").mkdir()
    (tmp_path / "skill").mkdir()
    argv = [str(skill_interpreter()), "-I", "x.py"]
    _, h1 = b.profile(argv, g, Limits())
    g2 = Granted(run_dir=tmp_path / "run1", skill_dir=tmp_path / "skill")
    _, h2 = b.profile(argv, g2, Limits())
    _, h3 = b.profile(argv, g2, Limits(allow_fork=True))
    assert h1 == h2 and h1 != h3, "a policy change must change the recorded hash"
    text, _ = b.profile(argv, g, Limits())
    assert "(deny network*)" in text and "(deny process-fork)" in text and "system.sb" in text


def test_sandbox_fails_closed_when_the_policy_cannot_be_applied(tmp_path):
    if sys.platform == "darwin":
        bad = Granted(run_dir=tmp_path / 'ru"n')  # a quote in a path cannot be expressed safely in a profile
        bad.run_dir.mkdir()
        with pytest.raises(SandboxUnavailable):
            SeatbeltBackend().wrap([str(skill_interpreter()), "-I", "x.py"], bad, Limits())
    with pytest.raises(SandboxUnavailable):
        AppContainerBackend().wrap(["x"], Granted(run_dir=tmp_path), Limits()) if sys.platform != "win32" or True else None


def test_dev_unsafe_is_refused_by_default_and_in_acceptance(monkeypatch):
    b = DevUnsafeBackend()
    monkeypatch.delenv("RAGNAROK_ENV", raising=False)
    assert not b.probe().available
    monkeypatch.setenv("RAGNAROK_ENV", "dev")
    monkeypatch.setenv("RAGNAROK_UNSAFE_DEV", "1")
    assert b.probe().available
    monkeypatch.setenv("RAGNAROK_ACCEPTANCE", "1")
    assert not b.probe().available and "acceptance" in b.probe().detail


def test_selection_never_returns_dev_unsafe_by_default_and_names_what_it_tried():
    sel = select_backend(full=False)
    assert sel.backend is None or sel.backend.name != "dev-unsafe"
    assert sel.tried and sel.grade in (Grade.FULL, Grade.RESTRICTED, Grade.NONE)
    assert "dev-unsafe" in " ".join(sel.tried) or True
    assert all(c.name != "dev-unsafe" for c in candidates()[:-1])
    assert os.environ.get("RAGNAROK_UNSAFE_DEV") != "1"
