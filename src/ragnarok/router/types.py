"""Shared router types: questions put to a DecisionBackend, answers with calibrated probabilities, and the route decision."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

QType = Literal["choice", "noul", "score"]
FAMILIES = ("mail", "calendar", "files", "notes", "code", "chat", "memory", "system")


@dataclass(frozen=True)
class Question:
    id: str
    type: QType
    instructions: str
    options: dict[str, str] = field(default_factory=dict)  # choice: label -> description (at most 8 options, ARCHITECTURE 4.8)


@dataclass(frozen=True)
class Answer:
    id: str
    value: Any  # choice: label; noul: bool; score: float
    probability: float  # calibrated confidence in `value`
    distribution: dict[str, float] = field(default_factory=dict)


@dataclass
class RouteDecision:
    tier: Literal["L0", "L1", "L2", "L3"]
    intent_family: str
    skills: list[str]
    plan_needed: bool
    risk_floor: int
    memory_types: list[str]
    confidence: float
    latency_ms: float
    backend: str
    route_id: str | None = None
    low_confidence: bool = False
    injection_flag: bool = False
    route_nodes: list[dict[str, Any]] | None = None
    shadow_route_id: str | None = None
    shortlist: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
