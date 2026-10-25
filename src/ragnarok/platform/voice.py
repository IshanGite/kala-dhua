"""Speech to text that runs entirely on this computer. Two engines:
* Whisper (openai/whisper-base, about 290 MB, downloaded ONCE with the user's approval, then loaded offline): about 99 languages with automatic language detection. Preferred when it is on disk.
* macOS on-device Speech (a small Swift program compiled once and cached; no download, recognition forced on-device): the languages installed in System Settings > Keyboard > Dictation.
Windows: Tushar (the Whisper engine below is portable; only the microphone recording is macOS-specific).

Flow: start() records the microphone to a temporary WAV; stop() ends it and returns the text and the language that fit best. The audio is deleted at once and never stored."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

from ragnarok.core.errors import RagnarokError

PLIST = """<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd"><plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>local.ragnarok.voice</string><key>CFBundleName</key><string>Ragnarok Voice</string>
<key>NSMicrophoneUsageDescription</key><string>Ragnarok listens to you only while you press the microphone button, on this computer.</string>
<key>NSSpeechRecognitionUsageDescription</key><string>Ragnarok turns your voice into text on this computer, without sending audio anywhere.</string></dict></plist>"""

SWIFT = r'''
import Foundation
import AVFoundation
import Speech

func fail(_ code: Int32, _ msg: String) -> Never { FileHandle.standardError.write((msg + "\n").data(using: .utf8)!); exit(code) }
let a = CommandLine.arguments
if a.count < 2 { fail(2, "usage: voice-mac record|transcribe|locales") }

func micAllowed() -> Bool {
    var ok = false
    let s = DispatchSemaphore(value: 0)
    AVCaptureDevice.requestAccess(for: .audio) { g in ok = g; s.signal() }
    s.wait()
    return ok
}
func speechAllowed() -> Bool {
    var ok = false
    let s = DispatchSemaphore(value: 0)
    SFSpeechRecognizer.requestAuthorization { st in ok = (st == .authorized); s.signal() }
    s.wait()
    return ok
}

switch a[1] {
case "locales":
    let l = SFSpeechRecognizer.supportedLocales().filter { SFSpeechRecognizer(locale: $0)?.supportsOnDeviceRecognition ?? false }.map { $0.identifier }.sorted()
    print(String(data: try! JSONSerialization.data(withJSONObject: l), encoding: .utf8)!)
case "check":
    let m = AVCaptureDevice.authorizationStatus(for: .audio).rawValue
    let s = SFSpeechRecognizer.authorizationStatus().rawValue
    print("{\"mic\": \(m), \"speech\": \(s)}")
case "request":
    let m = micAllowed(); let s = speechAllowed()
    print("{\"mic\": \(m), \"speech\": \(s)}")
case "record":
    if a.count < 3 { fail(2, "record <out.wav> [max_seconds]") }
    let maxS = a.count > 3 ? (Double(a[3]) ?? 60) : 60
    if !micAllowed() { fail(10, "microphone permission denied") }
    let settings: [String: Any] = [AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: 16000, AVNumberOfChannelsKey: 1, AVLinearPCMBitDepthKey: 16, AVLinearPCMIsFloatKey: false, AVLinearPCMIsBigEndianKey: false]
    guard let rec = try? AVAudioRecorder(url: URL(fileURLWithPath: a[2]), settings: settings) else { fail(11, "could not open the microphone") }
    rec.isMeteringEnabled = true
    if !rec.record(forDuration: maxS) { fail(12, "could not start recording") }
    signal(SIGTERM, SIG_IGN); signal(SIGINT, SIG_IGN)
    let t = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
    t.setEventHandler { rec.stop(); print("stopped"); exit(0) }
    t.resume()
    let i = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
    i.setEventHandler { rec.stop(); print("stopped"); exit(0) }
    i.resume()
    print("recording"); fflush(stdout)
    RunLoop.main.run(until: Date(timeIntervalSinceNow: maxS + 1))
    rec.stop(); print("stopped")
case "transcribe":
    if a.count < 3 { fail(2, "transcribe <file> [locale,locale]") }
    if !speechAllowed() { fail(20, "speech recognition permission denied") }
    let locs = (a.count > 3 ? a[3] : "en-US").split(separator: ",").map(String.init)
    var best: (String, String, Double)? = nil
    for id in locs {
        guard let rec = SFSpeechRecognizer(locale: Locale(identifier: id)), rec.isAvailable, rec.supportsOnDeviceRecognition else { continue }
        let req = SFSpeechURLRecognitionRequest(url: URL(fileURLWithPath: a[2]))
        req.requiresOnDeviceRecognition = true
        req.shouldReportPartialResults = false
        var text = ""; var conf = 0.0; var done = false
        let task = rec.recognitionTask(with: req) { r, e in
            if let r = r, r.isFinal {
                text = r.bestTranscription.formattedString
                let segs = r.bestTranscription.segments
                conf = segs.isEmpty ? 0 : segs.map { Double($0.confidence) }.reduce(0, +) / Double(segs.count)
                done = true
            } else if e != nil { done = true }
        }
        let end = Date(timeIntervalSinceNow: 40)
        while !done && Date() < end { RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.05)) }
        task.cancel()
        if !text.isEmpty && (best == nil || conf > best!.2) { best = (id, text, conf) }
        if conf > 0.6 { break }
    }
    if let b = best {
        let o: [String: Any] = ["locale": b.0, "text": b.1, "confidence": b.2]
        print(String(data: try! JSONSerialization.data(withJSONObject: o), encoding: .utf8)!)
    } else { print("{\"locale\": \"\", \"text\": \"\", \"confidence\": 0}") }
default: fail(2, "unknown command")
}
'''


def available() -> bool:
    return sys.platform == "darwin"


def _binary() -> Path:
    from platformdirs import user_cache_dir

    d = Path(user_cache_dir("ragnarok"))
    d.mkdir(parents=True, exist_ok=True)
    exe = d / "voice-mac"
    if exe.exists():
        return exe
    (d / "voice.swift").write_text(SWIFT, encoding="utf-8")
    (d / "voice.plist").write_text(PLIST, encoding="utf-8")
    r = subprocess.run(["swiftc", "-O", str(d / "voice.swift"), "-o", str(exe), "-Xlinker", "-sectcreate", "-Xlinker", "__TEXT", "-Xlinker", "__info_plist", "-Xlinker", str(d / "voice.plist")],  # noqa: S603, S607
                       capture_output=True, timeout=300, check=False)
    if r.returncode != 0 or not exe.exists():
        raise RagnarokError("could not prepare the voice tool (needs the Xcode command line tools: xcode-select --install)", code="E_VOICE")
    return exe


def warm() -> None:
    if available():
        try:
            _binary()
        except Exception:  # noqa: BLE001
            pass


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run([str(_binary()), *args], capture_output=True, timeout=timeout, check=False)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RagnarokError("the voice tool did not respond", code="E_VOICE") from exc


def languages() -> list[str]:
    """On-device recognition languages this Mac offers (for example en-US, hi-IN, es-ES)."""
    if not available():
        return []
    r = _run(["locales"], 30)
    try:
        return list(json.loads(r.stdout or b"[]"))
    except ValueError:
        return []


def permissions() -> dict[str, str]:
    """mic and speech: 'allowed', 'denied', 'not asked yet' or 'restricted' (as the system reports them)."""
    names = {0: "not asked yet", 1: "restricted", 2: "denied", 3: "allowed"}
    try:
        d = json.loads(_run(["check"], 15).stdout or b"{}")
    except (ValueError, RagnarokError):
        return {"mic": "unknown", "speech": "unknown"}
    return {"mic": names.get(d.get("mic"), "unknown"), "speech": names.get(d.get("speech"), "unknown")}


def request_permissions() -> dict[str, bool]:
    """Triggers the system's one-time prompts (microphone, speech recognition). Needs the user to click Allow."""
    try:
        d = json.loads(_run(["request"], 120).stdout or b"{}")
    except (ValueError, RagnarokError):
        return {"mic": False, "speech": False}
    return {"mic": bool(d.get("mic")), "speech": bool(d.get("speech"))}


def default_locales() -> str:
    """The user's system languages first, then English: the recognizer tries them in order and keeps the most confident."""
    out: list[str] = []
    try:
        r = subprocess.run(["defaults", "read", "-g", "AppleLanguages"], capture_output=True, timeout=5, check=False)  # noqa: S603, S607
        for line in r.stdout.decode("utf-8", "replace").splitlines():
            tag = line.strip().strip('",()').replace("_", "-")
            if tag and tag not in out and len(tag) >= 2:
                out.append(tag)
    except (OSError, subprocess.TimeoutExpired):
        pass
    for fallback in ("en-US", "en-IN", "en-GB"):
        if fallback not in out:
            out.append(fallback)
    return ",".join(out[:5])


# ---------------------------------------------------------------- Whisper (local, multilingual)
WHISPER_ID = "openai/whisper-base"
_W: dict[str, object] = {}
_W_LOCK = threading.Lock()
HALLUCINATIONS = {"you", "thank you", "thanks", "thanks for watching", "bye", "okay", "ok", "so", "yeah", "uh", "hmm", "the"}
SILENCE_RMS = 0.006  # below this the clip is treated as silence: Whisper invents text for silence


def whisper_ready() -> bool:
    try:
        from huggingface_hub import try_to_load_from_cache

        return isinstance(try_to_load_from_cache(WHISPER_ID, "model.safetensors"), str)
    except Exception:  # noqa: BLE001
        return False


def _read_wav(path: str) -> object:
    import numpy as np

    with wave.open(path, "rb") as w:
        raw = w.readframes(w.getnframes())
        ch, rate, width = w.getnchannels(), w.getframerate(), w.getsampwidth()
    if width != 2:
        raise RagnarokError("unsupported audio format", code="E_VOICE")
    a = np.frombuffer(raw, dtype=np.int16).astype("float32") / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if rate != 16000:  # plain linear resampling is enough for speech
        a = np.interp(np.linspace(0, len(a) - 1, int(len(a) * 16000 / rate)), np.arange(len(a)), a).astype("float32")
    return a


def _load_whisper() -> tuple[object, object, str]:
    with _W_LOCK:
        if "m" not in _W:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")  # the weights were downloaded once; nothing is fetched at run time
            import torch
            from transformers import WhisperForConditionalGeneration, WhisperProcessor

            proc = WhisperProcessor.from_pretrained(WHISPER_ID, local_files_only=True)
            model = WhisperForConditionalGeneration.from_pretrained(WHISPER_ID, local_files_only=True).eval()
            dev = "mps" if torch.backends.mps.is_available() else "cpu"
            try:
                model.to(dev)
            except Exception:  # noqa: BLE001
                dev = "cpu"
                model.to(dev)
            _W.update(m=model, p=proc, d=dev)
        return _W["m"], _W["p"], str(_W["d"])


def transcribe_whisper(path: str, language: str | None = None) -> dict[str, object]:
    """{text, language, confidence}. language=None means detect it. Silence returns empty text."""
    import numpy as np
    import torch

    audio = _read_wav(path)
    rms = float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0
    frames = audio[: len(audio) // 320 * 320].reshape(-1, 320) if len(audio) >= 320 else np.zeros((1, 320), dtype="float32")
    voiced = float(np.mean(np.sqrt(np.mean(np.square(frames), axis=1)) > 0.012))  # share of 20 ms frames that are clearly louder than room noise
    if len(audio) < 4000 or rms < SILENCE_RMS or voiced < 0.08:
        return {"text": "", "language": "", "confidence": 0.0}
    model, proc, dev = _load_whisper()
    feats = proc(audio, sampling_rate=16000, return_tensors="pt").input_features.to(dev)  # type: ignore[attr-defined]
    with torch.no_grad():
        code = language
        conf = 0.0
        if code is None:
            ids = model.detect_language(feats)  # type: ignore[attr-defined]
            code = str(proc.tokenizer.decode(ids[0])).strip("<|>")  # type: ignore[attr-defined]
        seq = model.generate(feats, language=code, task="transcribe", max_new_tokens=220)  # type: ignore[attr-defined]
        text = proc.batch_decode(seq, skip_special_tokens=True)[0].strip()  # type: ignore[attr-defined]
        conf = 0.0  # Whisper gives no calibrated confidence here: it is reported as unknown (0.0), never invented
    if rms < 0.03 and text.lower().strip(" .!,") in HALLUCINATIONS:  # what Whisper writes for faint noise
        return {"text": "", "language": code or "", "confidence": 0.0}
    return {"text": text, "language": code or "", "confidence": round(conf, 3)}


def best_transcribe(path: str, locales: str | None = None) -> dict[str, object]:
    """Whisper when it is on disk, otherwise the macOS on-device recognizer."""
    if whisper_ready():
        try:
            d = transcribe_whisper(path)
            d["engine"] = "whisper"
            return d
        except Exception:  # noqa: BLE001 - fall back rather than fail the user's sentence
            pass
    r = _run(["transcribe", path, locales or default_locales()], 120)
    if r.returncode == 20:
        raise RagnarokError("Speech recognition is not allowed. " + SPEECH_HELP, code="E_SPEECH")
    try:
        d = json.loads(r.stdout or b"{}")
    except ValueError:
        d = {}
    return {"text": str(d.get("text", "")).strip(), "language": str(d.get("locale", "")), "confidence": float(d.get("confidence", 0) or 0), "engine": "apple"}


class Recorder:
    """One recording at a time."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen[bytes] | None = None
        self._path: Path | None = None
        self._started = 0.0

    @property
    def active(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, max_s: float = 60.0) -> None:
        if not available():
            raise RagnarokError("voice input is not implemented on this operating system yet (see docs/HANDOVER_TUSHAR.md)", code="E_VOICE")
        if self.active:
            return
        fd, name = tempfile.mkstemp(suffix=".wav", prefix="ragnarok-voice-")
        os.close(fd)
        self._path = Path(name)
        self._proc = subprocess.Popen([str(_binary()), "record", name, str(max_s)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)  # noqa: S603
        self._started = time.time()
        time.sleep(0.6)  # the tool exits at once if the microphone is not allowed
        if self._proc.poll() is not None:
            err = (self._proc.stderr.read() if self._proc.stderr else b"").decode("utf-8", "replace").strip()
            self._cleanup()
            raise RagnarokError("The microphone is not allowed. " + MIC_HELP if "permission" in err else f"could not start recording ({err[:80]})", code="E_MIC")

    def stop(self, locales: str | None = None) -> dict[str, object]:
        """Ends the recording and returns {text, language, confidence, seconds}. The audio file is deleted before this returns."""
        if self._proc is None or self._path is None:
            raise RagnarokError("nothing is being recorded", code="E_VOICE")
        seconds = round(time.time() - self._started, 1)
        try:
            if self._proc.poll() is None:
                self._proc.send_signal(signal.SIGTERM)
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            if not self._path.exists() or self._path.stat().st_size < 3000:
                return {"text": "", "language": "", "confidence": 0.0, "seconds": seconds}
            d = best_transcribe(str(self._path), locales)
            return {**d, "seconds": seconds}
        finally:
            self._cleanup()

    def cancel(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.kill()
        self._cleanup()

    def _cleanup(self) -> None:
        if self._path is not None:
            try:
                self._path.unlink(missing_ok=True)
            except OSError:
                pass
        self._proc, self._path = None, None


def transcribe_file(path: str, locales: str | None = None) -> dict[str, object]:
    """Transcribe an existing audio file (used by tests and by drag-and-drop audio)."""
    return best_transcribe(path, locales)


MIC_HELP = "Open System Settings > Privacy & Security > Microphone and turn on Terminal (or the app you start Ragnarok from), then restart it."
SPEECH_HELP = "Open System Settings > Privacy & Security > Speech Recognition and turn on Terminal (or the app you start Ragnarok from), then restart it."
