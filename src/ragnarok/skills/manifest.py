"""Skill manifest (D-16, FR2.1). The tier is COMPUTED from the declared permissions; a manifest cannot lower its own risk.

A skill reads one JSON object on stdin and writes one JSON object on stdout. Any skill with side effects must support dry_run.
Compatible in spirit with the Agent Skills layout (SKILL.md instructions beside it); permissions and the sandbox are our extension.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

import jsonschema
from pydantic import BaseModel, Field, field_validator, model_validator

from ragnarok.policy.tiers import PERMISSION_TIER, Tier, skill_tier

_NAME = re.compile(r"^[a-z][a-z0-9_.-]{1,48}$")
EXTERNAL_WRITE_PERMISSIONS = {"mail.send", "chat.post", "calendar.write", "net", "fs.write.outside", "exec"}


class Permission(BaseModel):
    permission: str
    scope: str = ""  # e.g. "workspace", a host name, a folder alias


class Entrypoint(BaseModel):
    type: Literal["python", "native"] = "python"
    file: str = "main.py"
    action: str | None = None  # native actions are first-party code run by the daemon, still behind guard + broker


class Example(BaseModel):
    params: dict[str, Any]
    note: str = ""


class MemoryScope(BaseModel):
    read: list[str] = Field(default_factory=list)  # e.g. ["semantic:work"]
    write: Literal["none"] = "none"  # skills never write memory directly


class Manifest(BaseModel):
    name: str
    version: str = "0.1.0"
    description: str
    parameters: dict[str, Any]
    returns: dict[str, Any] = Field(default_factory=dict)
    permissions: list[Permission] = Field(default_factory=list)
    side_effects: Literal["none", "reversible", "irreversible"] = "none"
    idempotent: bool = True
    dry_run: bool = False
    timeout_s: int = Field(default=30, ge=1, le=600)
    memory_mb: int = Field(default=256, ge=32, le=2048)
    examples: list[Example] = Field(default_factory=list)
    memory_scope: MemoryScope = Field(default_factory=MemoryScope)
    entrypoint: Entrypoint = Field(default_factory=Entrypoint)
    hosts: list[str] = Field(default_factory=list)
    param_roles: dict[str, list[str]] = Field(default_factory=dict)  # role -> parameter names: recipients, paths, hosts, count

    @field_validator("name")
    @classmethod
    def _name_ok(cls, v: str) -> str:
        if not _NAME.match(v):
            raise ValueError("name must be a lowercase slug")
        return v

    @field_validator("parameters")
    @classmethod
    def _schema_ok(cls, v: dict[str, Any]) -> dict[str, Any]:
        jsonschema.Draft202012Validator.check_schema(v)
        if v.get("type") != "object":
            raise ValueError("parameters must be a JSON Schema of type object")
        return v

    @model_validator(mode="after")
    def _side_effects_need_dry_run(self) -> Manifest:
        if self.side_effects != "none" and not self.dry_run:
            raise ValueError("a skill with side effects must support dry_run (D-16)")
        return self

    @property
    def tier(self) -> Tier:
        return skill_tier([p.permission for p in self.permissions])

    @property
    def permission_names(self) -> list[str]:
        return [p.permission for p in self.permissions]

    @property
    def external_write(self) -> bool:
        return any(p in EXTERNAL_WRITE_PERMISSIONS for p in self.permission_names)

    @property
    def unknown_permissions(self) -> list[str]:
        return [p for p in self.permission_names if p not in PERMISSION_TIER]


def load_manifest(skill_dir: Path) -> Manifest:
    raw = json.loads((skill_dir / "manifest.json").read_text(encoding="utf-8"))
    return Manifest.model_validate(raw)
