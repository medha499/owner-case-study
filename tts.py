"""Text-to-speech for sample call audio.

Uses OpenAI TTS when OPENAI_API_KEY is present.
Falls back to a tiny bundled silent WAV placeholder if not.
Caches generated audio under /tmp/owner_audio/.
"""

import os
import hashlib
import struct
import wave
from pathlib import Path

AUDIO_CACHE = Path(os.getenv("AUDIO_CACHE", "/tmp/owner_audio"))
AUDIO_CACHE.mkdir(parents=True, exist_ok=True)

# Voices to alternate between for rep/owner roles
VOICE_REP = "alloy"
VOICE_OWNER = "onyx"


def _cache_path(call_id: str) -> Path:
    return AUDIO_CACHE / f"{call_id}.mp3"


def _silent_wav_bytes(seconds: float = 1.0) -> bytes:
    """Return a tiny silent WAV so the player still loads."""
    import io
    sample_rate = 8000
    n_samples = int(sample_rate * seconds)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(struct.pack(f"<{n_samples}h", *([0] * n_samples)))
    return buf.getvalue()


def _parse_dialog(transcript: str):
    """Split a transcript into [(speaker, text), ...].
    Handles 'Rep: ...' / 'Owner: ...' / 'Sarah: ...' style or plain text.
    """
    if not transcript:
        return []
    lines = []
    for raw in transcript.split("\n"):
        raw = raw.strip()
        if not raw:
            continue
        if ":" in raw[:24]:
            speaker, text = raw.split(":", 1)
            speaker_l = speaker.strip().lower()
            role = "rep" if any(k in speaker_l for k in
                                ("rep", "sarah", "mike", "jess", "david", "agent")) else "owner"
            lines.append((role, text.strip()))
        else:
            lines.append(("rep", raw))
    return lines


def generate_audio(call_id: str, transcript: str) -> Path | None:
    """Generate an MP3 from a call transcript, cached to disk.

    Returns the path to the audio file, or None if generation isn't possible
    AND no fallback is desired. The caller can decide.
    """
    out = _cache_path(call_id)
    if out.exists() and out.stat().st_size > 0:
        return out

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        # Fallback: write a silent wav under .wav extension so the <audio> tag
        # at least loads instead of erroring out.
        wav_out = AUDIO_CACHE / f"{call_id}.wav"
        wav_out.write_bytes(_silent_wav_bytes(2.0))
        return wav_out

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        dialog = _parse_dialog(transcript)
        if not dialog:
            return None

        # Generate a single concatenated MP3 by alternating voices per turn.
        # OpenAI doesn't natively merge, so we render each turn and concat bytes.
        # MP3 frames concatenate cleanly enough for demo playback.
        chunks = []
        for role, text in dialog[:8]:  # cap turns so demo stays snappy
            if not text:
                continue
            voice = VOICE_REP if role == "rep" else VOICE_OWNER
            r = client.audio.speech.create(
                model="tts-1", voice=voice, input=text[:500]
            )
            chunks.append(r.content)
        out.write_bytes(b"".join(chunks))
        return out
    except Exception as e:
        print(f"[tts] generation failed for {call_id}: {e}")
        wav_out = AUDIO_CACHE / f"{call_id}.wav"
        wav_out.write_bytes(_silent_wav_bytes(2.0))
        return wav_out


def media_type_for(path: Path) -> str:
    return "audio/mpeg" if path.suffix == ".mp3" else "audio/wav"
