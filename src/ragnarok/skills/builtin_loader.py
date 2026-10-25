"""Register the first-party skills that ship with Ragnarok. They are enabled on registration; their hash is still checked on every run."""

from __future__ import annotations

from pathlib import Path

from ragnarok.skills.registry import Registered, SkillRegistry

BUILTIN_DIR = Path(__file__).parent / "builtin"
NATIVE_DIR = Path(__file__).parent / "native"


def register_builtins(registry: SkillRegistry) -> list[Registered]:
    dirs = [d for root in (BUILTIN_DIR, NATIVE_DIR) for d in sorted(root.iterdir()) if (d / "manifest.json").exists()]
    return [registry.register(d, builtin=True) for d in dirs]
