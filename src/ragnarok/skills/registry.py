"""Skill registry (FR2.1, D-29, SEC-06, SEC-19, SEC-13).

* register(): validate manifest, static scan, compute tree hash, store DISABLED.
* enable(): a tier-3 action: needs a valid approval token for exactly this skill and hash (step-up), and a clean scan.
* verify_runnable(): at the moment of EVERY run the on-disk tree hash must equal the registered hash.
Adding a skill needs no core change: drop a folder, register it, approve it.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from ragnarok.broker.approvals import Broker
from ragnarok.core.errors import PolicyDenied
from ragnarok.memory.ledger import Ledger
from ragnarok.skills.install_scan import ScanReport, scan_skill
from ragnarok.skills.manifest import Manifest, load_manifest
from ragnarok.skills.treehash import tree_hash


class SkillNotRunnable(PolicyDenied):
    code = "E_SKILL_NOT_RUNNABLE"


@dataclass
class Registered:
    skill_id: str
    manifest: Manifest
    tree_hash: str
    report: ScanReport
    path: Path


class SkillRegistry:
    def __init__(self, ledger: Ledger, broker: Broker, skills_dir: Path) -> None:
        self._l, self._b, self.dir = ledger, broker, skills_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, skill_id: str) -> Path:
        return self.dir / skill_id

    def register(self, source: Path, *, builtin: bool = False) -> Registered:
        manifest = load_manifest(source)
        dest = self._path(manifest.name)
        if source.resolve() != dest.resolve():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(source, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        report = scan_skill(dest, manifest)
        h = tree_hash(dest)
        self._l.register_skill(manifest.name, manifest.version, h, report.as_dict())
        if builtin and not report.blocked:
            self._l.set_skill_enabled(manifest.name, True)  # first-party skills ship enabled; their hash is still checked on every run
        return Registered(manifest.name, manifest, h, report, dest)

    def enable_request_params(self, skill_id: str) -> dict:
        row = self._l.get_skill(skill_id)
        if row is None:
            raise SkillNotRunnable("unknown skill")
        return {"action": "skill.enable", "skill": skill_id, "tree_hash": row["tree_hash"], "count": 1}

    def enable(self, skill_id: str, approval_token: str) -> None:
        row = self._l.get_skill(skill_id)
        if row is None:
            raise SkillNotRunnable("unknown skill")
        import json

        if json.loads(row["scan_report"] or "{}").get("blocked"):
            raise SkillNotRunnable("the install scan found problems; fix the skill and register it again")
        if not self._b.verify_at_execution(approval_token, self.enable_request_params(skill_id)):
            raise SkillNotRunnable("enabling a skill needs a step-up approval for exactly this skill and version")
        self._l.set_skill_enabled(skill_id, True)

    def disable(self, skill_id: str) -> None:
        self._l.set_skill_enabled(skill_id, False)

    def verify_runnable(self, skill_id: str) -> Registered:
        row = self._l.get_skill(skill_id)
        if row is None:
            raise SkillNotRunnable("skill is not registered")
        if not row["enabled"]:
            raise SkillNotRunnable("skill is not enabled")
        path = self._path(skill_id)
        if not path.exists() or tree_hash(path) != row["tree_hash"]:
            self._l.record_incident("skill_tamper", [f"skill:{skill_id}"])
            raise SkillNotRunnable("skill files changed since registration (hash mismatch)", code="E_SKILL_TAMPERED")
        manifest = load_manifest(path)
        import json

        return Registered(skill_id, manifest, row["tree_hash"], ScanReport(), path) if not json.loads(row["scan_report"] or "{}").get("blocked") else _raise_blocked()


def _raise_blocked() -> Registered:
    raise SkillNotRunnable("skill failed its install scan")
