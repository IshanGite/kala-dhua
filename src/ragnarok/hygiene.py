"""Repository hygiene for two operating systems: reserved names, case collisions, path length, line endings.

Used by `ragnarok doctor` and CI. Windows-reserved names break checkouts on Windows even if they work on macOS.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
MAX_REL_PATH = 110
TEXT_SUFFIXES = {".py", ".md", ".toml", ".yaml", ".yml", ".json", ".sql", ".txt", ".js", ".ts", ".tsx", ".css", ".html"}


@dataclass(frozen=True)
class Finding:
    path: str
    problem: str


def tracked_files(root: Path) -> list[str]:
    try:
        out = subprocess.run(  # noqa: S603
            ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True, encoding="utf-8"
        ).stdout
        return [line for line in out.splitlines() if line]
    except (OSError, subprocess.CalledProcessError):
        return [str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file() and ".git" not in p.parts and ".venv" not in p.parts]


def scan(root: Path, files: list[str] | None = None) -> list[Finding]:
    files = files if files is not None else tracked_files(root)
    findings: list[Finding] = []
    seen: dict[str, str] = {}
    for rel in files:
        if len(rel) > MAX_REL_PATH:
            findings.append(Finding(rel, f"path longer than {MAX_REL_PATH} characters"))
        for part in rel.split("/"):
            stem = part.split(".")[0].upper()
            if stem in RESERVED:
                findings.append(Finding(rel, f"reserved Windows name {stem}"))
            if BAD_CHARS.search(part) or part.endswith((".", " ")):
                findings.append(Finding(rel, "character not allowed in Windows file names"))
        lower = rel.lower()
        if lower in seen and seen[lower] != rel:
            findings.append(Finding(rel, f"differs only by case from {seen[lower]}"))
        seen[lower] = rel
        p = root / rel
        if p.suffix.lower() in TEXT_SUFFIXES and p.is_file():
            try:
                if b"\r\n" in p.read_bytes():
                    findings.append(Finding(rel, "contains CRLF line endings (repo uses LF)"))
            except OSError:
                pass
    return findings
