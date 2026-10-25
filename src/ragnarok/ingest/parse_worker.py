"""Parser worker: runs as a SEPARATE process so a hostile file can only crash this process, never the daemon.

Usage: python -m ragnarok.ingest.parse_worker <path>   (prints one JSON object on stdout)

PDF, Office and image libraries are large native codebases; the daemon never parses anything itself. In Phase 4 this
process is started under the OS sandbox (no network, one input file). Caps below make zip bombs and giant files
end in a structured error, not a hang.
"""

from __future__ import annotations

import json
import sys
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_UNCOMPRESSED = 120 * 1024 * 1024
MAX_TEXT_CHARS = 2_000_000
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".log", ".json", ".yaml", ".yml", ".rst", ".org"}


class ParseFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace")


def parse_eml_bytes(data: bytes) -> dict:
    from ragnarok.ingest.textutil import html_to_text

    msg = BytesParser(policy=policy.default).parsebytes(data)
    plain = msg.get_body(preferencelist=("plain",))
    html = msg.get_body(preferencelist=("html",))
    hidden = False
    if plain is not None:
        body = plain.get_content()
    elif html is not None:
        body, hidden = html_to_text(html.get_content())
    else:
        body = ""
    atts = sum(1 for _ in msg.iter_attachments())
    headers = {k: str(msg.get(k, "")) for k in ("From", "To", "Cc", "Subject", "Date", "Message-ID", "In-Reply-To", "References", "Authentication-Results")}
    return {"kind": "message", "title": headers["Subject"] or None, "text": body, "hidden": hidden, "meta": {"headers": headers, "attachments": atts}}


def parse_ics(text: str) -> dict:
    events, cur = [], None
    for raw in text.replace("\r\n ", "").splitlines():
        line = raw.strip()
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT" and cur is not None:
            events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            k, _, v = line.partition(":")
            cur[k.split(";")[0].upper()] = v
    lines = [f"Event: {e.get('SUMMARY', '(no title)')} | starts {e.get('DTSTART', '?')} | ends {e.get('DTEND', '?')} | where {e.get('LOCATION', '-')}" for e in events]
    return {"kind": "event", "title": "calendar", "text": "\n".join(lines), "hidden": False, "meta": {"events": len(events)}}


def parse_pdf(path: Path) -> dict:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ParseFailure("E_PARSER_MISSING_PDF") from exc
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        raise ParseFailure("E_PDF_ENCRYPTED")
    pages = [(p.extract_text() or "") for p in reader.pages[:400]]
    text = "\n\n".join(pages)
    if len(text.strip()) < 20 * max(1, len(pages)) // 4:
        raise ParseFailure("E_NEEDS_OCR")  # a scan: handled by the OCR path, never guessed
    return {"kind": "file", "title": path.stem, "text": text, "hidden": False, "meta": {"pages": len(pages)}}


def parse_docx(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        if sum(i.file_size for i in z.infolist()) > MAX_UNCOMPRESSED:
            raise ParseFailure("E_ZIP_BOMB")
    try:
        import docx
    except ImportError as exc:
        raise ParseFailure("E_PARSER_MISSING_DOCX") from exc
    d = docx.Document(str(path))
    return {"kind": "file", "title": path.stem, "text": "\n\n".join(p.text for p in d.paragraphs if p.text.strip()), "hidden": False, "meta": {}}


def parse_image_ocr(path: Path) -> dict:
    import shutil
    import subprocess

    exe = shutil.which("tesseract")
    if not exe:
        raise ParseFailure("E_OCR_UNAVAILABLE")
    out = subprocess.run([exe, str(path), "stdout"], capture_output=True, timeout=60, check=False)  # noqa: S603
    return {"kind": "file", "title": path.stem, "text": out.stdout.decode("utf-8", errors="replace"), "hidden": False, "meta": {"ocr": True}}


def parse_path(path: Path) -> dict:
    if not path.is_file():
        raise ParseFailure("E_NOT_FOUND")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ParseFailure("E_TOO_LARGE")
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        out = {"kind": "file", "title": path.stem, "text": _read_text(path), "hidden": False, "meta": {}}
    elif suffix in {".html", ".htm"}:
        from ragnarok.ingest.textutil import html_to_text

        text, hidden = html_to_text(_read_text(path))
        out = {"kind": "file", "title": path.stem, "text": text, "hidden": hidden, "meta": {}}
    elif suffix == ".eml":
        out = parse_eml_bytes(path.read_bytes())
    elif suffix == ".ics":
        out = parse_ics(_read_text(path))
    elif suffix == ".pdf":
        out = parse_pdf(path)
    elif suffix == ".docx":
        out = parse_docx(path)
    elif suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
        out = parse_image_ocr(path)
    else:
        raise ParseFailure("E_UNSUPPORTED_TYPE")
    out["text"] = out["text"][:MAX_TEXT_CHARS]
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(json.dumps({"ok": False, "code": "E_USAGE"}))
        return 2
    try:
        result = parse_path(Path(argv[1]))
        print(json.dumps({"ok": True, **result}))
        return 0
    except ParseFailure as exc:
        print(json.dumps({"ok": False, "code": exc.code}))
        return 1
    except Exception as exc:  # noqa: BLE001 - a hostile file may raise anything; report a code, never content
        print(json.dumps({"ok": False, "code": f"E_PARSE_{type(exc).__name__.upper()}"}))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
