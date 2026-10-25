"""NFR1.3 and the lint rules from BUILD_PLAN section 3. These tests scan the source tree itself."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from ragnarok.core.errors import ModelNotAllowed, NetworkGuardError
from ragnarok.llm_gateway.client import OllamaGateway, assert_loopback
from ragnarok.llm_gateway.models import ModelEntry, ModelLock

SRC = Path(__file__).resolve().parents[2] / "src" / "ragnarok"

BANNED_SDKS = {"openai", "anthropic", "groq", "cohere", "mistralai", "replicate", "together", "ollama", "litellm", "langchain", "langgraph"}
BANNED_DOTTED = ("google.generativeai", "google.genai", "vertexai")
# Only these files may import network libraries (rule 1); connectors are added as they are built.
NETWORK_IMPORTS = {"httpx", "requests", "socket", "aiohttp", "websockets", "urllib3", "urllib.request"}
NETWORK_ALLOWED = {
    "llm_gateway/client.py",
    "doctor.py",
    "daemon/auth.py",
    "daemon/api.py",
}
NETWORK_ALLOWED_PREFIXES = ("connectors/",)


def _py_files():
    return [p for p in SRC.rglob("*.py")]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_NFR1_3_no_provider_sdk_imported_anywhere():
    for p in _py_files():
        for mod in _imports(p):
            top = mod.split(".")[0]
            assert top not in BANNED_SDKS, f"{p.name} imports provider SDK {mod}"
            assert not mod.startswith(BANNED_DOTTED), f"{p.name} imports {mod}"


def test_NFR1_3_no_api_keys_or_cloud_endpoints_in_source():
    pat = re.compile(r"(sk-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}|xox[bap]-[0-9A-Za-z-]{10,}|api\.openai\.com|api\.anthropic\.com|generativelanguage\.googleapis\.com|api\.groq\.com)")
    for p in _py_files():
        assert not pat.search(p.read_text(encoding="utf-8")), f"possible key or cloud endpoint in {p.name}"


def test_only_gateway_opens_model_socket():
    for p in _py_files():
        rel = p.relative_to(SRC).as_posix()
        if rel in NETWORK_ALLOWED or rel.startswith(NETWORK_ALLOWED_PREFIXES):
            continue
        bad = _imports(p) & NETWORK_IMPORTS
        assert not bad, f"{rel} imports network library {bad}; only the gateway and connectors may"


def test_no_write_sql_outside_memory():
    allowed = ("memory/", "store/", "ingest/commit.py")
    sql = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|CREATE\s+TABLE|DROP\s+TABLE|ALTER\s+TABLE)\b", re.I)
    for p in _py_files():
        rel = p.relative_to(SRC).as_posix()
        if rel.startswith(allowed):
            continue
        assert not sql.search(p.read_text(encoding="utf-8")), f"write SQL found outside memory/store in {rel}"


def test_no_os_specific_calls_outside_platform():
    pat = re.compile(r"\b(os\.fork|os\.killpg|signal\.SIGKILL|import fcntl|import resource)\b")
    for p in _py_files():
        rel = p.relative_to(SRC).as_posix()
        if rel.startswith("platform/"):
            continue
        assert not pat.search(p.read_text(encoding="utf-8")), f"OS-specific call in {rel}"


def test_every_text_open_names_utf8():
    for p in _py_files():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            if name not in {"open", "read_text", "write_text"}:
                continue
            kw = {k.arg for k in node.keywords}
            if name == "open":
                mode = ""
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    mode = str(node.args[1].value)
                for k in node.keywords:
                    if k.arg == "mode" and isinstance(k.value, ast.Constant):
                        mode = str(k.value.value)
                if "b" in mode:
                    continue
                # Path.open on a Path object has the same signature; skip bare os-level calls.
            assert "encoding" in kw, f"{p.relative_to(SRC).as_posix()}:{node.lineno} {name}() without encoding"


def test_gateway_refuses_non_loopback_endpoint():
    lock = ModelLock({"planner": ModelEntry("m1")})
    for url in ("http://10.0.0.5:11434", "http://example.com", "http://192.168.1.2:11434", "https://api.example.com"):
        with pytest.raises(NetworkGuardError):
            OllamaGateway(lock, url)
    assert_loopback("http://127.0.0.1:11434")
    assert_loopback("http://localhost:11434")
    assert_loopback("http://[::1]:11434")


def test_gateway_refuses_model_not_in_lock():
    lock = ModelLock({"planner": ModelEntry("good-model")})
    with pytest.raises(ModelNotAllowed):
        lock.check_name("hosted-model:cloud")
    lock.check_name("good-model")


def test_gateway_digest_mismatch_refused():
    lock = ModelLock({"planner": ModelEntry("good-model", "sha256:aaa")})
    with pytest.raises(ModelNotAllowed):
        lock.check_digest("good-model", "sha256:bbb")
    lock.check_digest("good-model", "sha256:aaa")
