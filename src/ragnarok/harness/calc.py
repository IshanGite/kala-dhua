"""Arithmetic by code, not by a model: 'calculate 18 percent GST on 42500', 'what is 12 times 37', '15% of 2000'. A small model is unreliable at sums and must never be
trusted with money. Only digits, + - * / ( ) and ** are ever evaluated, through the ast module (no eval of text)."""

from __future__ import annotations

import ast
import operator
import re

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Mod: operator.mod}
_LEAD = re.compile(r"^\s*(?:please\s+)?(?:can you\s+)?(?:calculate|compute|work out|what(?:'s| is)|how much is|solve|evaluate)\b[:\s]*", re.I)
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent|per cent)\s*(?:of\s+|(gst|tax|vat|tip|discount|interest|commission|service charge)\s+(?:on|of)\s+)?(?:rs\.?|inr|₹|\$)?\s*([\d,]+(?:\.\d+)?)", re.I)
_WORDS = [(r"\bplus\b", "+"), (r"\bminus\b", "-"), (r"\btimes\b|\bmultiplied by\b|×|\bx\b", "*"), (r"\bdivided by\b|\bover\b|÷", "/"), (r"\bto the power of\b|\^", "**"), (r"\bmod\b", "%")]


def _fmt(x: float) -> str:
    return f"{x:,.2f}".rstrip("0").rstrip(".") if abs(x) < 1e15 else f"{x:.6g}"


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        a, b = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and (abs(b) > 12 or abs(a) > 1e6):
            raise ValueError("too large")
        return float(_OPS[type(node.op)](a, b))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return float(_OPS[type(node.op)](_eval(node.operand)))
    raise ValueError("not arithmetic")


def try_calc(text: str) -> str | None:
    t = text.strip().rstrip("?.! ")
    m = _PCT.search(t)
    if m and (_LEAD.match(t) or m.group(2) or " of " in t.lower()):
        pct, kind, base = float(m.group(1)), (m.group(2) or "").lower(), float(m.group(3).replace(",", ""))
        part = base * pct / 100
        if kind in ("gst", "tax", "vat", "tip", "interest", "commission", "service charge"):
            return f"{_fmt(pct)}% {kind.upper() if kind in ('gst', 'vat') else kind} on {_fmt(base)} is {_fmt(part)}. Total with it: {_fmt(base + part)}. Without it: {_fmt(base)}."
        if kind == "discount":
            return f"{_fmt(pct)}% discount on {_fmt(base)} is {_fmt(part)}. You pay {_fmt(base - part)}."
        return f"{_fmt(pct)}% of {_fmt(base)} is {_fmt(part)}."
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}", t):
        return None  # a date, not a subtraction
    if not _LEAD.match(t) and not (re.fullmatch(r"[\d\s.,+\-*/()x×÷^%]+", t) and re.search(r"[+*/x×÷^%]", t)):
        return None
    expr = _LEAD.sub("", t).lower().replace(",", "")
    for pat, rep in _WORDS:
        expr = re.sub(pat, rep, expr)
    expr = re.sub(r"\s+", "", expr)
    if not re.fullmatch(r"[\d.+\-*/()%]+", expr) or not re.search(r"\d", expr) or not re.search(r"[+\-*/%]", expr):
        return None
    try:
        return f"{_fmt(_eval(ast.parse(expr, mode='eval')))}"
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError):
        return None
