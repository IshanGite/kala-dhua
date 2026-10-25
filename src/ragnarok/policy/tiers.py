"""Risk tiers (D-08): 0 read-only, 1 reversible local write, 2 external or irreversible, 3 critical (step-up).

The tier of a skill is COMPUTED from its declared permissions; a manifest cannot lower its own risk.
An unknown permission is treated as tier 2 (fail closed).
"""

from __future__ import annotations

from enum import IntEnum


class Tier(IntEnum):
    READ = 0
    LOCAL_WRITE = 1
    EXTERNAL = 2
    CRITICAL = 3


PERMISSION_TIER: dict[str, Tier] = {
    "fs.read": Tier.READ,
    "memory.read": Tier.READ,
    "mail.read": Tier.READ,
    "calendar.read": Tier.READ,
    "chat.read": Tier.READ,
    "drive.read": Tier.READ,
    "fs.write": Tier.LOCAL_WRITE,  # inside the approved workspace only
    "note.write": Tier.LOCAL_WRITE,
    "mail.draft": Tier.LOCAL_WRITE,
    "calendar.draft": Tier.LOCAL_WRITE,
    "mail.send": Tier.EXTERNAL,
    "chat.post": Tier.EXTERNAL,
    "calendar.write": Tier.EXTERNAL,
    "fs.write.outside": Tier.EXTERNAL,
    "fs.delete": Tier.EXTERNAL,
    "exec": Tier.EXTERNAL,
    "net": Tier.EXTERNAL,
    "skill.enable": Tier.CRITICAL,
    "grant.write_scope": Tier.CRITICAL,
    "policy.rule.create": Tier.CRITICAL,
    "key.rotate": Tier.CRITICAL,
    "fs.delete.bulk": Tier.CRITICAL,
}


def permission_tier(permission: str) -> Tier:
    return PERMISSION_TIER.get(permission, Tier.EXTERNAL)


def skill_tier(permissions: list[str]) -> Tier:
    return max((permission_tier(p) for p in permissions), default=Tier.READ)
