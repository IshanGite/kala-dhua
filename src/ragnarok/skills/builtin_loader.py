"""Register the first-party skills that ship with Ragnarok. They are enabled on registration; their hash is still checked on every run."""

from __future__ import annotations

from pathlib import Path

from ragnarok.skills.registry import Registered, SkillRegistry

BUILTIN_DIR = Path(__file__).parent / "builtin"


def register_builtins(registry: SkillRegistry) -> list[Registered]:
    return [registry.register(d, builtin=True) for d in sorted(BUILTIN_DIR.iterdir()) if (d / "manifest.json").exists()]
