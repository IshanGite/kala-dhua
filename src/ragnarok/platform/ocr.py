"""Reading the text in a picture with the operating system's own recognizer, fully offline. macOS: the Vision framework through a tiny Swift program compiled once and cached.
Windows: Tushar (Windows.Media.Ocr). Returns plain text in reading order; never stores the picture."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from ragnarok.core.errors import RagnarokError

SWIFT = r'''
import Foundation
import Vision
import AppKit
let path = CommandLine.arguments[1]
guard let img = NSImage(contentsOfFile: path), let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { exit(2) }
let req = VNRecognizeTextRequest()
req.recognitionLevel = .accurate
req.usesLanguageCorrection = true
req.recognitionLanguages = ["en-US"]
let handler = VNImageRequestHandler(cgImage: cg, options: [:])
do { try handler.perform([req]) } catch { exit(3) }
var rows: [(Double, Double, String)] = []
for o in (req.results ?? []) {
    if let t = o.topCandidates(1).first { rows.append((Double(o.boundingBox.midY), Double(o.boundingBox.minX), t.string)) }
}
rows.sort { a, b in abs(a.0 - b.0) > 0.012 ? a.0 > b.0 : a.1 < b.1 }
for r in rows { print(r.2) }
'''


def _binary() -> Path:
    from platformdirs import user_cache_dir

    d = Path(user_cache_dir("ragnarok"))
    d.mkdir(parents=True, exist_ok=True)
    exe = d / "ocr-mac"
    if exe.exists():
        return exe
    src = d / "ocr.swift"
    src.write_text(SWIFT, encoding="utf-8")
    r = subprocess.run(["swiftc", "-O", str(src), "-o", str(exe)], capture_output=True, timeout=240, check=False)  # noqa: S603, S607
    if r.returncode != 0 or not exe.exists():
        raise RagnarokError("could not prepare the text reader (needs the Xcode command line tools: xcode-select --install)", code="E_OCR")
    return exe


def available() -> bool:
    return sys.platform == "darwin"


def warm() -> None:
    """Compile the reader ahead of time (about ten seconds, once per machine)."""
    if available():
        try:
            _binary()
        except Exception:  # noqa: BLE001
            pass


def read_text(png_or_jpeg: bytes, timeout: float = 30.0) -> str:
    if not available():
        raise RagnarokError("reading text from pictures is not implemented on this operating system yet", code="E_OCR")
    exe = _binary()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "in.png"
        p.write_bytes(png_or_jpeg)
        try:
            r = subprocess.run([str(exe), str(p)], capture_output=True, timeout=timeout, check=False)  # noqa: S603
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RagnarokError("the text reader did not finish", code="E_OCR") from exc
    if r.returncode != 0:
        raise RagnarokError("the text reader could not open that picture", code="E_OCR")
    return r.stdout.decode("utf-8", "replace").strip()
