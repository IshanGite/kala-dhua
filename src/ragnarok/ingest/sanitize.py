"""Sanitize ingested text: strip hidden content, mask secrets, flag injection, and label data for prompts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ragnarok.gateway_scan.scan import scan


@dataclass
class Sanitized:
    text: str
    flags: dict[str, Any] = field(default_factory=dict)


def sanitize(text: str, *, html_hidden_seen: bool = False, direction: str = "ingest") -> Sanitized:
    res = scan(text, direction)  # type: ignore[arg-type]
    kinds = res.kinds()
    flags: dict[str, Any] = {}
    if "injection" in kinds:
        flags["injection_suspected"] = True
    if "hidden_text" in kinds or html_hidden_seen:
        flags["hidden_text_stripped"] = True
    masked = sorted(k for k in kinds if k in {"aws_key", "github_token", "slack_token", "google_api_key", "stripe_key", "provider_key", "private_key", "jwt", "credential", "otp", "card_number"})
    if masked:
        flags["secrets_masked"] = masked
    pii = sorted(k for k in kinds if k.startswith("pii_") and k not in ("pii_email", "pii_phone"))
    if pii:
        flags["sensitive_ids"] = pii
    return Sanitized(res.text, flags)


def wrap_untrusted(text: str, *, source: str, trust: str) -> str:
    """Untrusted content enters a prompt only inside labelled delimiters (ARCHITECTURE 6.3, D-27).

    The closing tag is neutralised inside the text so content cannot break out of its block.
    """
    safe = text.replace("</untrusted>", "<​/untrusted>")
    return f'<untrusted source="{source}" trust="{trust}">\n{safe}\n</untrusted>'
