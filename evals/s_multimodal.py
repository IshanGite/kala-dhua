"""Reading pictures and speech on this computer: OCR word recall on rendered text, question answering on synthetic invoices through the two vision engines, and speech-to-text with macOS synthetic voices. Requirements: FR2.6 screen explain, FR1.4 voice."""
from __future__ import annotations

import io
import re
import subprocess
import tempfile
import time
from pathlib import Path

from evals.common import lat, make_world, prop

SUITE = "multimodal"
REQ = "FR1.4 voice input, FR2.6 screen understanding (local only)"

PAGES = [
    "Invoice 7731\nBrightPrint Studio\nDate: 12 October 2026\nTotal due: 12,500 rupees\nPay by 5 November 2026",
    "Meeting reminder\nProject Falcon review\nThursday 3pm\nRoom Aurora\nBring the quarterly numbers",
    "Boarding pass\nPassenger Priya Sharma\nFlight AI 664\nGate 14\nDeparture 18:40",
    "Receipt\nGreen Leaf Cafe\nTwo coffees 360\nOne sandwich 220\nTotal 580",
]
VQA = [(0, "What is the total due?", r"12,?500"), (0, "When must it be paid?", r"5 nov"), (1, "Which room is the meeting in?", r"aurora"), (1, "What day is the meeting?", r"thursday"),
       (2, "What is the flight number?", r"664"), (2, "Which gate?", r"14"), (3, "What is the total?", r"580"), (3, "Which cafe is this from?", r"green leaf")]
VOICES = [("en", "Samantha", "Remind me to call mom tomorrow at nine in the morning"), ("de", "Anna", "Erinnere mich morgen an den Termin beim Arzt"),
          ("es", "Monica", "Recuérdame llamar a mi madre mañana por la tarde"), ("fr", "Thomas", "Rappelle moi d'appeler le médecin demain matin")]


def render(text: str) -> bytes:
    from PIL import Image, ImageDraw, ImageFont
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 34)
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    lines = text.split("\n")
    im = Image.new("RGB", (1100, 70 * len(lines) + 60), "white")
    d = ImageDraw.Draw(im)
    for i, ln in enumerate(lines):
        d.text((40, 30 + 70 * i), ln, fill="black", font=font)
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def words(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-zà-ÿ0-9]{3,}", t.lower())}


async def run() -> dict:
    from ragnarok.harness.vision import Vision
    from ragnarok.platform import ocr, voice

    out: dict = {"suite": SUITE, "requirement": REQ, "metrics": {}, "failures": []}
    imgs = [render(p) for p in PAGES]
    rec_k = rec_n = 0
    t_ocr: list[float] = []
    if ocr.available():
        for png, page in zip(imgs, PAGES):
            t = time.perf_counter()
            got = words(ocr.read_text(png))
            t_ocr.append((time.perf_counter() - t) * 1000)
            want = words(page)
            rec_k += len(want & got)
            rec_n += len(want)
        out["metrics"]["ocr_word_recall"] = prop(rec_k, rec_n)
        out["metrics"]["ocr_latency"] = lat(t_ocr)
    else:
        out["failures"].append("ocr unavailable")
    w = await make_world(mirror=False, sandbox=False)
    vis = Vision(w.gateway)
    for engine in ("read", "look"):
        ok = n = 0
        ts: list[float] = []
        for idx, q, rx in VQA:
            t = time.perf_counter()
            try:
                a = await vis.ask(imgs[idx], q, engine=engine)
            except Exception as exc:  # noqa: BLE001
                a = f"ERR {type(exc).__name__}"
            ts.append((time.perf_counter() - t) * 1000)
            good = bool(re.search(rx, a, re.I))
            ok += good
            n += 1
            if not good:
                out["failures"].append({"engine": engine, "q": q, "got": a[:140]})
        out["metrics"][f"vqa_{engine}_engine_correct"] = prop(ok, n)
        out["metrics"][f"vqa_{engine}_engine_latency"] = lat(ts)
    # speech
    lang_ok = n = 0
    overlap: list[float] = []
    tstt: list[float] = []
    if voice.whisper_ready():
        d = Path(tempfile.mkdtemp())
        for code, v, text in VOICES:
            aiff, wav = d / f"{code}.aiff", d / f"{code}.wav"
            r = subprocess.run(["say", "-v", v, "-o", str(aiff), text], capture_output=True)  # noqa: S603, S607
            if r.returncode or subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)], capture_output=True).returncode:  # noqa: S603, S607
                out["failures"].append(f"say voice {v} unavailable")
                continue
            t = time.perf_counter()
            res = voice.transcribe_whisper(str(wav))
            tstt.append((time.perf_counter() - t) * 1000)
            n += 1
            lang_ok += str(res.get("language", ""))[:2] == code
            a, b = words(str(res.get("text", ""))), words(text)
            overlap.append(len(a & b) / max(1, len(b)))
        out["metrics"]["stt_language_identified"] = prop(lang_ok, n)
        out["metrics"]["stt_word_overlap_mean"] = round(sum(overlap) / max(1, len(overlap)), 3)
        out["metrics"]["stt_latency"] = lat(tstt)
    out["n"] = rec_n + 16 + n
    out["notes"] = "Rendered Helvetica text, not photographs; synthetic macOS voices, not human speech (accented-synthetic accuracy with the base model is limited). 'read' engine = OCR then text model; 'look' = vision model on the picture."
    return out
