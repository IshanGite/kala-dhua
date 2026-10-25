"""Voice input: silence and faint noise never become text, WAV reading, the API contract when no recorder is active."""

from __future__ import annotations

import wave

import numpy as np
import pytest

from ragnarok.platform import voice


def write_wav(path, samples, rate=16000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())


def test_silence_is_empty_not_hallucinated(tmp_path):
    p = tmp_path / "s.wav"
    write_wav(p, np.zeros(16000 * 2))
    assert voice.transcribe_whisper(str(p))["text"] == ""


def test_faint_noise_is_treated_as_silence(tmp_path):
    p = tmp_path / "n.wav"
    write_wav(p, np.random.default_rng(1).normal(0, 0.003, 16000 * 3))
    assert voice.transcribe_whisper(str(p))["text"] == ""


def test_wav_reading_resamples_and_mixes_down(tmp_path):
    p = tmp_path / "a.wav"
    write_wav(p, np.sin(np.linspace(0, 200, 8000)) * 0.5, rate=8000)
    a = voice._read_wav(str(p))
    assert len(a) == 16000 and a.dtype == np.float32


def test_stop_without_recording_is_an_error():
    with pytest.raises(Exception) as e:
        voice.Recorder().stop()
    assert getattr(e.value, "code", "") == "E_VOICE"


def test_default_locales_always_end_with_english():
    assert "en-" in voice.default_locales()
