"""Static scan of a skill BEFORE it can be enabled (SEC-13). Answers the OpenClaw/ClawHub class of attacks: hidden network calls,
hidden command execution, obfuscated payloads, prompt injection in descriptions, undeclared capabilities.

A finding is 'high' when the code does something its manifest permissions do not declare. High findings block enabling.
This is a first filter, never the only barrier: the OS sandbox still denies network and files the manifest does not grant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ragnarok.gateway_scan.scan import scan
from ragnarok.skills.manifest import Manifest

_NET = re.compile(r"\b(?:import|from)\s+(?:socket|urllib|requests|httpx|http\.client|aiohttp|ftplib|smtplib|telnetlib|websockets|xmlrpc)\b|\bsocket\s*\.")
_EXEC = re.compile(r"\b(?:subprocess|os\.system|os\.popen|os\.exec\w*|os\.spawn\w*|pty\.spawn|multiprocessing)\b")
_DYN = re.compile(r"\b(?:eval|exec|compile)\s*\(|__import__\s*\(|\bimportlib\b|\bctypes\b|\bcffi\b")
_B64_BLOB = re.compile(r"[A-Za-z0-9+/=]{300,}")
_SENSITIVE_PATH = re.compile(r"\.ssh|\.aws|\.gnupg|Keychains|id_rsa|\.env\b|AppData\\\\Roaming|Login Data|Cookies|\.config/gcloud", re.I)
_ENV_DUMP = re.compile(r"os\.environ(?!\.get\(\s*['\"]RAGNAROK_)")


@dataclass
class ScanFinding:
    kind: str
    file: str
    line: int
    severity: str  # info | warn | high


@dataclass
class ScanReport:
    findings: list[ScanFinding] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(f.severity == "high" for f in self.findings)

    def as_dict(self) -> dict:
        return {"blocked": self.blocked, "findings": [f.__dict__ for f in self.findings]}


def scan_skill(root: Path, manifest: Manifest) -> ScanReport:
    report = ScanReport()
    perms = set(manifest.permission_names)
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if _NET.search(line):
                report.findings.append(ScanFinding("network_code", rel, i, "info" if "net" in perms else "high"))
            if _EXEC.search(line):
                report.findings.append(ScanFinding("process_execution", rel, i, "info" if "exec" in perms else "high"))
            if _DYN.search(line):
                report.findings.append(ScanFinding("dynamic_code", rel, i, "info" if "exec" in perms else "high"))
            if _B64_BLOB.search(line):
                report.findings.append(ScanFinding("obfuscated_blob", rel, i, "high"))
            if _SENSITIVE_PATH.search(line):
                report.findings.append(ScanFinding("sensitive_path_reference", rel, i, "high"))
            if _ENV_DUMP.search(line):
                report.findings.append(ScanFinding("environment_access", rel, i, "warn"))
    texts = [("manifest.description", manifest.description), *[(f"example:{i}", str(e.params) + e.note) for i, e in enumerate(manifest.examples)]]
    for md in sorted(root.rglob("*.md")):
        texts.append((md.relative_to(root).as_posix(), md.read_text(encoding="utf-8", errors="replace")))
    for where, text in texts:
        res = scan(text, "ingest")
        if res.injection_suspected:
            report.findings.append(ScanFinding("prompt_injection_text", where, 0, "high"))
        if "hidden_text" in res.kinds():
            report.findings.append(ScanFinding("hidden_text", where, 0, "high"))
        if any(k in res.kinds() for k in ("aws_key", "github_token", "private_key", "credential", "provider_key", "slack_token")):
            report.findings.append(ScanFinding("embedded_secret", where, 0, "high"))
    for u in manifest.unknown_permissions:
        report.findings.append(ScanFinding(f"unknown_permission:{u}", "manifest.json", 0, "warn"))
    if manifest.entrypoint.type == "python" and not (root / manifest.entrypoint.file).exists():
        report.findings.append(ScanFinding("missing_entrypoint", "manifest.json", 0, "high"))
    return report
