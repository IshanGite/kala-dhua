"""Success criteria checked by CODE first (FR3.3). A model judges only what code cannot (kind "judge", used sparingly)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_TOKEN = re.compile(r"[^.\[\]]+|\[\d+\]")


def dig(obj: Any, path: str) -> Any:
    """result.matches[0].path style lookup. Returns None when anything along the way is missing."""
    cur = obj
    for tok in _TOKEN.findall(path):
        if tok.startswith("["):
            idx = int(tok[1:-1])
            cur = cur[idx] if isinstance(cur, list) and idx < len(cur) else None
        else:
            cur = cur.get(tok) if isinstance(cur, dict) else None
        if cur is None:
            return None
    return cur


@dataclass(frozen=True)
class CriterionResult:
    criterion: dict[str, Any]
    passed: bool
    detail: str = ""
    deterministic: bool = True


def evaluate(criteria: list[dict[str, Any]], result: Any, *, workspace: Path | None = None, readback: Any = None) -> list[CriterionResult]:
    out: list[CriterionResult] = []
    for c in criteria:
        kind = c.get("kind", "ok")
        if kind == "ok":
            out.append(CriterionResult(c, result is not None, "skill returned a result"))
        elif kind == "field_present":
            out.append(CriterionResult(c, dig(result, c["path"]) not in (None, "", [], {}), f"{c['path']} present"))
        elif kind == "equals":
            out.append(CriterionResult(c, dig(result, c["path"]) == c["value"], f"{c['path']} equals expected"))
        elif kind == "contains":
            v = dig(result, c["path"])
            out.append(CriterionResult(c, isinstance(v, str) and str(c["value"]).lower() in v.lower(), f"{c['path']} contains expected"))
        elif kind == "min_count":
            v = dig(result, c["path"])
            out.append(CriterionResult(c, isinstance(v, list) and len(v) >= int(c["n"]), f"{c['path']} has at least {c['n']}"))
        elif kind == "file_exists":
            ok = bool(workspace) and (workspace / c["path"]).is_file()
            out.append(CriterionResult(c, ok, f"file {c['path']} exists"))
        elif kind == "readback":  # a side effect is confirmed by reading it back from the system it changed
            out.append(CriterionResult(c, bool(readback), "read-back confirmed the change"))
        elif kind == "judge":
            out.append(CriterionResult(c, False, "needs a judge", deterministic=False))
        else:
            out.append(CriterionResult(c, False, f"unknown criterion kind {kind!r}"))
    return out


def all_passed(results: list[CriterionResult]) -> bool:
    return bool(results) and all(r.passed for r in results)


def all_deterministic(results: list[CriterionResult]) -> bool:
    return all(r.deterministic for r in results)
