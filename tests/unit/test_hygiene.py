from __future__ import annotations

from pathlib import Path

from ragnarok.hygiene import scan


def test_reserved_names_and_case_collisions_detected(tmp_path):
    files = ["src/ok.py", "docs/NUL.md", "src/aux.py", "A/Readme.md", "a/readme.md", "x/bad:name.txt"]
    problems = {f.path: f.problem for f in scan(tmp_path, files)}
    assert "reserved" in problems["docs/NUL.md"]
    assert "reserved" in problems["src/aux.py"]
    assert "case" in problems["a/readme.md"]
    assert "not allowed" in problems["x/bad:name.txt"]
    assert "src/ok.py" not in problems


def test_long_paths_and_crlf_detected(tmp_path):
    (tmp_path / "f.md").write_bytes(b"a\r\nb\r\n")
    long_rel = "d/" * 60 + "f.py"
    probs = {f.path: f.problem for f in scan(tmp_path, ["f.md", long_rel])}
    assert "CRLF" in probs["f.md"] and "longer" in probs[long_rel]


def test_this_repository_is_clean():
    root = Path(__file__).resolve().parents[2]
    assert scan(root) == []
