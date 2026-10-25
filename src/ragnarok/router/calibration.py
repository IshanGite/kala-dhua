"""Calibration (ARCHITECTURE 4.3, 4.6). Laya ships over-confident: confidence orders decisions but is not a probability of being right.
So per decision type we fit (a) a temperature that rescales the distribution and (b) a threshold chosen so that ACCEPTED decisions
reach a target precision on held-out examples. Below the threshold the decision abstains and moves up a rung.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from ragnarok.router.backends.base import temper
from ragnarok.router.types import Answer

DEFAULT_THRESHOLD = 0.85
GRID = [0.5 + 0.1 * i for i in range(0, 36)]  # temperatures 0.5 .. 4.0


@dataclass(frozen=True)
class Calib:
    temperature: float = 1.0
    threshold: float = DEFAULT_THRESHOLD


def fit_temperature(samples: list[tuple[dict[str, float], str]]) -> float:
    """samples: (predicted distribution over labels, true label). Grid search of the temperature minimising negative log-likelihood."""
    if not samples:
        return 1.0
    best_t, best_nll = 1.0, math.inf
    for t in GRID:
        nll = 0.0
        for dist, truth in samples:
            nll -= math.log(max(temper(dist, t).get(truth, 1e-9), 1e-9))
        if nll < best_nll:
            best_t, best_nll = t, nll
    return round(best_t, 2)


def fit_threshold(samples: list[tuple[float, bool]], target_precision: float = 0.95, min_n: int = 5) -> float:
    """samples: (calibrated confidence, was the decision correct). The lowest threshold whose accepted decisions reach the target precision.
    If none does, return 1.01: the decision always abstains (escalates), which is the safe direction."""
    best = 1.01
    for c in sorted({s[0] for s in samples}, reverse=True):  # judge each candidate threshold over EVERYTHING it would accept (ties included)
        accepted = [ok for conf, ok in samples if conf >= c]
        if len(accepted) >= min_n and sum(accepted) / len(accepted) >= target_precision:
            best = c
    return best


class Calibrator:
    def __init__(self, table: dict[tuple[str, str], Calib] | None = None, default: Calib | None = None) -> None:
        self.table = table or {}
        self.default = default or Calib()

    def _get(self, decision: str, backend: str) -> Calib:
        return self.table.get((decision, backend), self.default)

    def adjust(self, decision: str, backend: str, a: Answer) -> Answer:
        c = self._get(decision, backend)
        if c.temperature == 1.0 or not a.distribution:
            return a
        d = temper(a.distribution, c.temperature)
        key = str(a.value) if str(a.value) in d else max(d, key=lambda k: d[k])
        return Answer(a.id, a.value, d[key], d)

    def accepts(self, decision: str, backend: str, a: Answer) -> bool:
        return self.adjust(decision, backend, a).probability >= self._get(decision, backend).threshold

    @classmethod
    def from_file(cls, path: Path) -> Calibrator:
        """The table written by scripts/calibrate_laya.py: {"decision/backend": {"temperature": t, "threshold": th}}."""
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        table = {tuple(k.split("/", 1)): Calib(float(v["temperature"]), float(v["threshold"])) for k, v in raw.items()}
        return cls(table)  # type: ignore[arg-type]

    def set(self, decision: str, backend: str, temperature: float, threshold: float) -> None:
        self.table[(decision, backend)] = Calib(temperature, threshold)
