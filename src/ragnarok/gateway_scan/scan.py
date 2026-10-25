"""scan(text, direction) -> ScanResult.

One function, one place: hidden text is stripped, instruction-like text is flagged (never silently deleted,
so the user can still read what was sent), and secrets, one-time codes and card numbers are masked on the way
into memory and prompts or block a draft on the way out.

Detection is an extra signal, not the barrier (ARCHITECTURE 5.4): the barrier is least privilege, deny-by-default
egress and content-bound approvals. Findings carry only a kind and a position, never the matched text.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Literal

Direction = Literal["ingest", "prompt", "output", "draft", "memory"]

_ZERO_WIDTH = "​‌‍⁠﻿­"
_BIDI = "‪‫‬‭‮⁦⁧⁨⁩"
_TAG_CHARS = re.compile("[\U000e0000-\U000e007f]")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)

_INJECTION = [
    re.compile(p, re.I)
    for p in (
        r"ignore (?:all |any |the |your )?(?:previous|prior|above|earlier) (?:instructions|rules|prompts?)",
        r"disregard (?:the |all |your )?(?:system|previous|above|prior)",
        r"you are now (?:in )?(?:developer|dan|jailbreak|unrestricted)",
        r"(?:new|updated|override) (?:system )?instructions?\s*:",
        r"reveal (?:your |the )?(?:system )?prompt",
        r"do not (?:tell|inform|alert|notify) the user",
        r"(?:forward|send|email|upload|exfiltrate)\b.{0,60}\b(?:all|every|each)\b.{0,40}\b(?:invoices?|files?|emails?|contacts?|passwords?|credentials?|documents?)\b.{0,80}(?:[\w.+-]+@[\w-]+\.\w+|https?://|without (?:asking|telling|confirm\w*)|silently|secretly|automatically)",
        r"(?:forward|send|email|upload|exfiltrate)\b.{0,40}\b(?:all|every)\b.{0,15}\b(?:contacts?|passwords?|credentials?)\b",
        r"<\|im_start\|>|<\|system\|>|\[INST\]|### ?system",
        r"(?:assistant|agent|ai)[, ]+(?:you must|please immediately|execute|run the following)",
        r"\b(?:assistant|ai|agent|model|bot|llm)\b(?!\s+(?:manager|director|coach|professor|editor|principal|secretary|teacher|team|lead|engineer|vp|head))[^.\n]{0,50}\b(?:must|should|need to|needs to|are required to|have to|shall)\b[^.\n]{0,60}\b(?:send|forward|email|mail|cc|bcc|delete|erase|transfer|upload|reveal|output|approve|disclose|leak)\b",
        r"\b(?:take|follow|obey|accept)\b.{0,12}\b(?:orders?|instructions?|commands?|directions?)\b.{0,20}\b(?:from|only)\b.{0,20}\b(?:this|the following|below|me)\b",
        r"follow (?:only|just) (?:what|the|these)\b.{0,25}\b(?:written|below|following|instructions?)",
        r"(?:output|print|show|display|reveal|repeat|dump) (?:your )?(?:entire |full |whole |complete )?(?:configuration|config|system prompt|settings|rules|instructions)",
        r"(?:respond|reply|answer)\b.{0,12}\b(?:to )?(?:every|each|all|any)\b.{0,25}\bwith\b",
        r"\b(?:mail|email|send|forward|delete|erase|wipe|upload|transfer)\b[^.\n]{0,40}\bthe user'?s\b",
        r"(?:attention|attn|hey|dear|hello|hi|urgent)[,: ]+(?:ai|assistant|agent|bot|llm|model)\b(?!\s+(?:manager|director|coach|professor|editor|principal|secretary|teacher|team|lead))",
        r"(?:hidden|secret|special|real) (?:task|instruction|directive|command|mission) for (?:the )?(?:ai|assistant|agent|bot|llm|model)\b",
        r"\b(?:real|actual|true|rightful) (?:owner|user|administrator|admin)\b",
        r"(?:ignore|disregard|forget) (?:everything|all|anything) (?:you (?:were|have been|are) (?:told|given|instructed)|above|before|prior)",
        r"(?:ignore|disregard) the above\b",
        r"(?:^|\n)\s*system\s*:",
        r"\b(?:admin|developer|god|root|sudo) mode\b",
        r"forget (?:all |any |the |your |these )?(?:rules|instructions|guidelines|restrictions|safety)",
        r"(?:instructions?|orders?|directives?|policy|task) for (?:the )?(?:assistant|ai|agent|model|bot|llm)\b(?!\s+(?:coach|manager|director|professor|teacher|principal|editor|team|lead))",
        r"(?:note|message|memo) to (?:the )?(?:ai|assistant|agent|model|bot|llm)\b",
        r"(?:assistant|ai|agent)[:,]\s*(?:email|send|forward|post|delete|approve|reply|call|execute|reveal)\b",
        r"(?:approve|accept|confirm) (?:all|every|any)(?: pending)?(?: actions?| requests?| cards?)|auto(?:matically)?[- ]approve|disable (?:the )?approval",
        r"(?:ignore|disregard|bypass|override) (?:the |all |any |your )?(?:user|safety|guard ?rails?|filters?|restrictions?|rules)\b",
        r"\b(?:your |the )?(?:real|actual|true|hidden) (?:task|goal|job|purpose)\b",
        r"(?:user|owner) (?:has )?(?:authori[sz]ed|permitted|allowed|approved) you to",
        r"(?:print|show|reveal|leak|dump|output) (?:your |the )?(?:hidden |secret |full )?(?:system )?(?:prompt|instructions)",
        r"(?:append|include|add|attach|insert)\b.{0,30}\b(?:passwords?|credentials?|secrets?|api keys?|tokens?)\b.{0,40}\b(?:to|in|into) (?:the |your |this |each |every )?(?:summary|reply|answer|response|message|email|output|digest)\b",
        r"\b(?:curl|wget)\b.{0,80}\|\s*(?:sh|bash)\b|\bexecute (?:this|the following) (?:tool|command|call)",
        r"[\"\']skill[\"\']\s*:\s*[\"\'](?:mail|chat|calendar|drive|python|fs)\.",
        r"\b(?:ignor(?:ez|a|e|iere)|olvida|oublie[sz]?|vergiss)\b.{0,25}\b(?:instrucciones|instructions|anweisungen|r[eè]gles|reglas|regeln)\b",
        r"act as (?:an? )?(?:dan|unrestricted|uncensored|jailbroken)|\bjailbreak\b|\bdo anything now\b",
        r"don'?t (?:tell|mention (?:this )?to) the user|(?:tell|inform) (?:nobody|no one) (?:about|that you|you (?:did|sent|read))",
    )
]

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("stripe_key", re.compile(r"\b[rs]k_live_[0-9A-Za-z]{16,}\b")),
    ("provider_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{24,}\b")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
]
_KEYWORD_SECRET = re.compile(r"(?i)\b(password|passwd|pwd|passcode|secret|api[_ -]?key|access[_ -]?token|auth[_ -]?token|client[_ -]?secret)\b(\s*(?:is|:|=)\s*)(\S{6,})")
_OTP = re.compile(r"(?i)\b(otp|one[- ]time (?:pass(?:word|code)|code)|verification code|security code|login code|2fa code|authentication code)\b([^\d\n]{0,25})(\d{4,8})\b")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_AADHAAR = re.compile(r"\b\d{4}\s\d{4}\s\d{4}\b")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d{1,3}[ -]?)?(?:\d[ -]?){9,11}\d(?!\d)")
_HIGH_ENTROPY = re.compile(r"\b[A-Za-z0-9_\-]{36,}\b")


@dataclass(frozen=True)
class Finding:
    kind: str
    start: int
    end: int
    severity: Literal["info", "warn", "block"]


@dataclass
class ScanResult:
    text: str
    findings: list[Finding] = field(default_factory=list)
    blocked: bool = False

    def kinds(self) -> set[str]:
        return {f.kind for f in self.findings}

    @property
    def injection_suspected(self) -> bool:
        return "injection" in self.kinds()


def luhn_ok(digits: str) -> bool:
    nums = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(nums) <= 19:
        return False
    total = 0
    for i, n in enumerate(reversed(nums)):
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = {c: s.count(c) for c in set(s)}
    return -sum((n / len(s)) * math.log2(n / len(s)) for n in counts.values())


def _mask(tag: str) -> str:
    return f"[REDACTED:{tag}]"


def scan(text: str, direction: Direction = "ingest") -> ScanResult:
    findings: list[Finding] = []
    out = text

    # 1. hidden text: stripped, because a human cannot see it but a model can read it
    if any(c in out for c in _ZERO_WIDTH + _BIDI) or _TAG_CHARS.search(out):
        findings.append(Finding("hidden_text", 0, len(out), "warn"))
        out = _TAG_CHARS.sub("", out.translate({ord(c): None for c in _ZERO_WIDTH + _BIDI}))
    if _HTML_COMMENT.search(out):
        findings.append(Finding("hidden_text", 0, len(out), "warn"))
        for cm in _HTML_COMMENT.findall(out):  # an instruction hidden in a comment is an attack by definition: nobody reads comments, models do
            if re.search(r"\b(?:assistant|ai|agent|email|send|forward|delete|ignore|instruction|approve)\b", cm, re.I):
                findings.append(Finding("injection", 0, len(cm), "block"))
        out = _HTML_COMMENT.sub("", out)

    # 2. injection-like text: flagged, kept visible
    for pat in _INJECTION:
        m = pat.search(out)
        if m:
            findings.append(Finding("injection", m.start(), m.end(), "warn"))
            break

    # 3. secrets: masked (ingest, memory, prompt) or blocking (draft, output)
    secret_hit = False

    def sub_secret(kind: str, pattern: re.Pattern[str], text_in: str) -> str:
        nonlocal secret_hit

        def repl(m: re.Match[str]) -> str:
            nonlocal secret_hit
            secret_hit = True
            findings.append(Finding(kind, m.start(), m.end(), "block"))
            return _mask(kind)

        return pattern.sub(repl, text_in)

    for kind, pat in _SECRET_PATTERNS:
        out = sub_secret(kind, pat, out)

    def kw_repl(m: re.Match[str]) -> str:
        nonlocal secret_hit
        secret_hit = True
        findings.append(Finding("credential", m.start(), m.end(), "block"))
        return f"{m.group(1)}{m.group(2)}{_mask('credential')}"

    out = _KEYWORD_SECRET.sub(kw_repl, out)

    def otp_repl(m: re.Match[str]) -> str:
        nonlocal secret_hit
        secret_hit = True
        findings.append(Finding("otp", m.start(), m.end(), "block"))
        return f"{m.group(1)}{m.group(2)}{_mask('otp')}"

    out = _OTP.sub(otp_repl, out)

    def card_repl(m: re.Match[str]) -> str:
        nonlocal secret_hit
        if not luhn_ok(m.group(0)):
            return m.group(0)
        secret_hit = True
        findings.append(Finding("card_number", m.start(), m.end(), "block"))
        return _mask("card_number")

    out = _CARD.sub(card_repl, out)

    for m in _HIGH_ENTROPY.finditer(out):
        if _entropy(m.group(0)) > 4.2 and any(ch.isdigit() for ch in m.group(0)) and any(ch.isalpha() for ch in m.group(0)):
            findings.append(Finding("high_entropy_token", m.start(), m.end(), "warn"))

    # 4. personal data: flagged only
    for kind, pat in (("pan", _PAN), ("aadhaar", _AADHAAR), ("iban", _IBAN), ("email", _EMAIL), ("phone", _PHONE)):
        for m in pat.finditer(out):
            findings.append(Finding(f"pii_{kind}", m.start(), m.end(), "info"))

    blocked = secret_hit and direction in ("draft", "output")
    return ScanResult(text=out, findings=findings, blocked=blocked)


def contains_any(text: str, tokens: list[str]) -> bool:
    """Canary check (SEC canaries): did a decoy token appear in this text?"""
    return any(t and t in text for t in tokens)
