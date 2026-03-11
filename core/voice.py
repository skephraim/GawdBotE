"""
Voice I/O — wake word detection, speech-to-text, text-to-speech.
Requires: openwakeword, faster-whisper, piper-tts (optional)
All processing is local / offline.
"""
from __future__ import annotations
import asyncio
import io
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

import config

log = logging.getLogger(__name__)

# ── Text-to-Speech ─────────────────────────────────────────────────────────────

def speak(text: str) -> None:
    """Convert text to speech using Piper TTS (local, offline)."""
    if not config.VOICE_ENABLED:
        return
    model = config.PIPER_MODEL
    if not Path(model).exists():
        log.warning("Piper model not found: %s — skipping TTS", model)
        print(f"[SuperAI] {text}")
        return
    try:
        proc = subprocess.Popen(
            ["piper", "--model", model, "--output-raw"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        raw, _ = proc.communicate(input=text.encode())
        # Play raw PCM at 22050 Hz, 16-bit, mono
        play = subprocess.Popen(
            ["aplay", "-r", "22050", "-f", "S16_LE", "-c", "1", "-"],
            stdin=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        play.communicate(input=raw)
    except FileNotFoundError:
        log.warning("piper or aplay not found — printing response instead")
        print(f"[SuperAI] {text}")
    except Exception as e:
        log.error("TTS error: %s", e)
        print(f"[SuperAI] {text}")


# ── Speech-to-Text ─────────────────────────────────────────────────────────────

def transcribe(audio_path: str) -> str:
    """Transcribe an audio file using faster-whisper."""
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(config.WHISPER_MODEL, device=config.WHISPER_DEVICE)
        segments, _ = model.transcribe(audio_path, beam_size=5)
        return " ".join(seg.text.strip() for seg in segments).strip()
    except ImportError:
        log.warning("faster-whisper not installed — cannot transcribe audio")
        return ""
    except Exception as e:
        log.error("Transcription error: %s", e)
        return ""


def transcribe_bytes(audio_bytes: bytes, suffix: str = ".wav") -> str:
    """Transcribe audio from raw bytes (e.g. from Telegram voice message)."""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(audio_bytes)
        path = f.name
    try:
        return transcribe(path)
    finally:
        Path(path).unlink(missing_ok=True)


# ── Wake-word listener ─────────────────────────────────────────────────────────

async def listen_for_wake_word(callback: Callable[[str], None]) -> None:
    """
    Continuously listen for the configured wake word.
    When detected, record speech until silence, transcribe, and call callback(text).
    Requires: openwakeword, pyaudio
    """
    if not config.VOICE_ENABLED:
        log.info("Voice disabled — wake word listener not started")
        return

    try:
        import numpy as np
        import pyaudio
        from openwakeword.model import Model as WakeModel
    except ImportError as e:
        log.warning("Wake-word dependencies missing (%s) — listener disabled", e)
        return

    log.info("Starting wake-word listener for %r", config.WAKE_WORD)
    oww = WakeModel(inference_framework="onnx")

    pa = pyaudio.PyAudio()
    stream = pa.open(
        rate=16000, channels=1, format=pyaudio.paInt16,
        input=True, frames_per_buffer=1280,
    )

    try:
        while True:
            pcm = stream.read(1280, exception_on_overflow=False)
            audio_data = np.frombuffer(pcm, dtype=np.int16)
            preds = oww.predict(audio_data)

            triggered = any(score > 0.5 for score in preds.values())
            if triggered:
                log.info("Wake word detected — recording…")
                speak("Yes?")
                text = await asyncio.get_event_loop().run_in_executor(None, _record_and_transcribe, pa)
                if text:
                    log.info("Transcribed: %r", text)
                    callback(text)

            await asyncio.sleep(0)  # yield to event loop

    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()


def _record_and_transcribe(pa) -> str:
    """Record until ~1.5s silence, then transcribe."""
    import pyaudio
    import wave

    stream = pa.open(
        rate=16000, channels=1, format=pyaudio.paInt16,
        input=True, frames_per_buffer=1024,
    )
    frames = []
    silence_threshold = 500
    max_silence_chunks = 24  # ~1.5s at 1024 frames/chunk @ 16 kHz

    silence_count = 0
    import numpy as np

    while True:
        data = stream.read(1024, exception_on_overflow=False)
        frames.append(data)
        amp = np.abs(np.frombuffer(data, dtype=np.int16)).mean()
        if amp < silence_threshold:
            silence_count += 1
            if silence_count >= max_silence_chunks:
                break
        else:
            silence_count = 0

    stream.stop_stream()
    stream.close()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = f.name
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(pa.get_sample_size(pyaudio.paInt16))
        wf.setframerate(16000)
        wf.writeframes(b"".join(frames))

    try:
        return transcribe(path)
    finally:
        Path(path).unlink(missing_ok=True)
