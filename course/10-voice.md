# Lesson 10 — Voice

## The Voice Pipeline

GawdBotE has a complete local voice pipeline — everything runs on your machine, no cloud required:

```
Microphone → Wake Word Detection → Record → Whisper STT → Agent → Piper TTS → Speakers
```

1. Always listening for the wake word ("hey jarvis")
2. Wake word detected → start recording
3. Record until silence
4. Transcribe with Whisper (speech-to-text)
5. Send transcript to agent
6. Speak the response with Piper (text-to-speech)

---

## Wake Word Detection (openwakeword)

`openwakeword` listens to the microphone in a continuous loop. It uses a small neural network to detect specific phrases without sending audio to the cloud.

```python
from openwakeword.model import Model as WakeModel
import pyaudio, numpy as np

oww = WakeModel(inference_framework="onnx")  # loads tiny ONNX model

pa = pyaudio.PyAudio()
stream = pa.open(rate=16000, channels=1, format=pyaudio.paInt16,
                 input=True, frames_per_buffer=1280)

while True:
    pcm = stream.read(1280, exception_on_overflow=False)
    audio_data = np.frombuffer(pcm, dtype=np.int16)

    preds = oww.predict(audio_data)    # returns {model_name: confidence_score}

    if any(score > 0.5 for score in preds.values()):
        # Wake word detected!
        speak("Yes?")
        text = _record_and_transcribe(pa)
        if text:
            callback(text)    # send to agent
```

Key numbers:
- `frames_per_buffer=1280` — about 80ms of audio at 16kHz
- `score > 0.5` — confidence threshold (higher = fewer false positives)
- `rate=16000` — 16kHz is the standard for voice processing

The wake word detection runs in an `asyncio` event loop using `run_in_executor` to avoid blocking:

```python
tasks.append(asyncio.create_task(
    voice.listen_for_wake_word(voice_callback), name="voice"
))
```

---

## Recording Until Silence

When the wake word triggers, we record audio until the user stops talking:

```python
def _record_and_transcribe(pa) -> str:
    stream = pa.open(rate=16000, channels=1, format=pyaudio.paInt16,
                     input=True, frames_per_buffer=1024)
    frames = []
    silence_threshold = 500   # amplitude below this = silence
    max_silence_chunks = 24   # ~1.5 seconds of silence = stop recording

    silence_count = 0
    while True:
        data = stream.read(1024)
        frames.append(data)
        amp = np.abs(np.frombuffer(data, dtype=np.int16)).mean()
        if amp < silence_threshold:
            silence_count += 1
            if silence_count >= max_silence_chunks:
                break
        else:
            silence_count = 0   # reset counter if voice detected again
```

**Silence detection** works by measuring the average amplitude of each audio chunk. When the amplitude drops below `silence_threshold` for 1.5 seconds, we assume the person stopped talking.

The recorded frames are saved as a WAV file, then passed to Whisper.

---

## Speech-to-Text (faster-whisper)

`faster-whisper` is a highly optimized version of OpenAI's Whisper model that runs locally:

```python
from faster_whisper import WhisperModel

def transcribe(audio_path: str) -> str:
    model = WhisperModel(
        config.WHISPER_MODEL,   # "base", "small", "medium", "large"
        device=config.WHISPER_DEVICE   # "cuda" or "cpu"
    )
    segments, _ = model.transcribe(audio_path, beam_size=5)
    return " ".join(seg.text.strip() for seg in segments).strip()
```

**Model size tradeoffs:**

| Model | Size | Speed | Accuracy |
|-------|------|-------|---------|
| tiny | 75MB | Very fast | Basic |
| base | 145MB | Fast | Good |
| small | 466MB | Medium | Better |
| medium | 1.5GB | Slow | Great |
| large | 3GB | Slowest | Best |

For a personal assistant on a modern GPU, `base` or `small` is the sweet spot. The `beam_size=5` parameter controls how hard Whisper tries — higher = more accurate but slower.

---

## Text-to-Speech (Piper)

Piper is a fast, local, high-quality TTS engine. It takes text and produces raw audio:

```python
def speak(text: str) -> None:
    proc = subprocess.Popen(
        ["piper", "--model", config.PIPER_MODEL, "--output-raw"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    raw, _ = proc.communicate(input=text.encode())  # feed text, get raw PCM audio

    # Play the raw PCM audio
    play = subprocess.Popen(
        ["aplay", "-r", "22050", "-f", "S16_LE", "-c", "1", "-"],
        stdin=subprocess.PIPE,
    )
    play.communicate(input=raw)
```

- `--output-raw` — outputs raw PCM audio (no WAV header)
- `aplay` — Linux command to play raw audio (`-r 22050` = 22kHz sample rate, `-f S16_LE` = 16-bit signed little-endian, `-c 1` = mono)
- Piper runs offline — no internet, no API, your voice stays local

**Setting up a Piper voice model:**
```bash
mkdir -p ~/.local/share/piper
wget -P ~/.local/share/piper \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/northern_english_male/medium/en_GB-northern_english_male-medium.onnx \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/northern_english_male/medium/en_GB-northern_english_male-medium.onnx.json
```

There are dozens of voices across many languages at huggingface.co/rhasspy/piper-voices.

---

## How It All Connects

The voice callback in `main.py`:

```python
def voice_callback(text: str) -> None:
    asyncio.create_task(_handle_voice(text))  # don't block the wake-word loop

async def _handle_voice(text: str) -> None:
    response = await agent.run(text, source="voice")
    voice.speak(response)   # speak the response out loud
```

When the wake word triggers:
1. `listen_for_wake_word` calls `voice_callback(transcribed_text)`
2. `voice_callback` creates an async task (so the wake-word loop can keep listening)
3. `_handle_voice` sends the text to the agent and speaks the response

---

## The Entirely Local Stack

This is worth emphasizing: **nothing in the voice pipeline touches the internet.**

| Component | Library | Runs locally |
|-----------|---------|-------------|
| Wake word | openwakeword | ✓ (ONNX model, ~2MB) |
| Recording | pyaudio | ✓ (microphone input) |
| STT | faster-whisper | ✓ (model on your GPU) |
| TTS | piper | ✓ (model on your disk) |

The only cloud involvement is the LLM API call — and even that can be local if you use Ollama.

---

## Exercise

The voice pipeline has a `VOICE_ENABLED` flag in `.env`. Disable it, then re-enable it:

```
VOICE_ENABLED=false   # disable
VOICE_ENABLED=true    # enable
```

Then look at `core/voice.py` and change the `silence_threshold` from 500 to 200. This makes GawdBotE more sensitive — it'll stop recording sooner even if you speak quietly. Test the difference. Put it back to 500 when you're done.

---

## Key Takeaways

- Voice pipeline: wake word → record → STT → agent → TTS
- `openwakeword` detects the wake phrase with a tiny local neural net
- `faster-whisper` transcribes audio locally (no cloud, no API key)
- `piper` generates speech locally (no cloud, dozens of voices)
- Silence detection: measure audio amplitude, stop when quiet for 1.5s
- The whole voice stack can run entirely offline with Ollama as the LLM
