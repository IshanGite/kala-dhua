"""models.lock: the allowlist of local models (name + digest). Anything else is refused (SEC / NFR1.3).

Recent Ollama versions can route some model names to a hosted service, so a name that is not in the
lock file is refused before any request is made.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ragnarok.core.errors import ConfigError, ModelNotAllowed

ROLES = ("embedder", "arbiter", "planner", "writer", "vision")


@dataclass(frozen=True)
class ModelEntry:
    name: str
    digest: str = ""


@dataclass(frozen=True)
class ModelLock:
    entries: dict[str, ModelEntry] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> ModelLock:
        if not path.exists():
            return cls({})
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ConfigError(f"models.lock is unreadable: {exc}") from exc
        return cls({role: ModelEntry(v["name"], v.get("digest", "")) for role, v in raw.items()})

    def save(self, path: Path) -> None:
        data = {r: {"name": e.name, "digest": e.digest} for r, e in sorted(self.entries.items())}
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def allowed_names(self) -> set[str]:
        return {e.name for e in self.entries.values()}

    def for_role(self, role: str) -> ModelEntry:
        # extractor and judge share the small arbiter model
        mapped = {"extractor": "arbiter", "judge": "arbiter"}.get(role, role)
        entry = self.entries.get(mapped) or (self.entries.get("arbiter") if mapped == "writer" else None)  # no separate writer model locked: the arbiter writes
        if entry is None:
            raise ModelNotAllowed(f"no model locked for role {role}")
        return entry

    def check_name(self, name: str) -> None:
        if name not in self.allowed_names():
            raise ModelNotAllowed(f"model {name!r} is not in models.lock")

    def check_digest(self, name: str, digest: str) -> None:
        for e in self.entries.values():
            if e.name == name and e.digest and digest and e.digest != digest:
                raise ModelNotAllowed(f"model {name!r} digest does not match models.lock")
