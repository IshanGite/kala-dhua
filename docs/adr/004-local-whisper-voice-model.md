# ADR 004: a local Whisper model for voice input (one-time download)

Context: voice-to-text must run locally and understand many languages. macOS's built-in on-device speech only had English installed on the demo machine, so it cannot be multilingual without the user installing languages by hand.
Choice: `openai/whisper-base` (about 290 MB, revision e37978b90ca9030d5170a5c07aadb050351a65bb), downloaded ONCE from the Hugging Face hub with the user's explicit approval in chat, then loaded with `local_files_only=True` and `HF_HUB_OFFLINE=1`. Nothing is fetched at run time and no audio leaves the machine. Apple's on-device recognizer remains the fallback when Whisper is not on disk.
Why: about 99 languages with automatic language detection, 0.2 s per short sentence on Apple silicon (measured, `docs/evidence/voice.json`), no network at run time. Hugging Face is used only to obtain the weights, which is an install step, not a runtime service (D-10 stands).
How to reverse: delete `~/.cache/huggingface/hub/models--openai--whisper-base`; `platform/voice.py` then falls back to the Apple recognizer without code changes.
