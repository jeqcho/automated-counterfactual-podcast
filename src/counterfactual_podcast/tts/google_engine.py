"""Google Cloud Text-to-Speech engine (Neural2 by default).

Cloud TTS — no local model. Cheapest quality option at our volume thanks to the
ongoing 1M chars/month free tier (then ~$16/1M for Neural2). Auth via a GCP service
account (GOOGLE_APPLICATION_CREDENTIALS) — see reports/deploy-cloudflare.md.

Google caps a request at 5000 bytes, so we chunk (~4500 chars) and concatenate the
returned MP3 segments. The model-call boundary is `_synth_chunk` (tests patch it).
"""
from __future__ import annotations

import io
from pathlib import Path

from .. import config
from .base import chunk_text


class GoogleEngine:
    name = "google"

    def __init__(self, voice: str | None = None, language_code: str | None = None,
                 client=None):
        self.voice = voice or config.GOOGLE_TTS_VOICE
        self.language_code = language_code or config.GOOGLE_TTS_LANGUAGE
        self._client = client

    def _get_client(self):
        if self._client is None:
            from google.cloud import texttospeech  # lazy
            self._client = texttospeech.TextToSpeechClient()
        return self._client

    def _synth_chunk(self, text: str) -> bytes:
        """Synthesize one chunk → MP3 bytes. Patched in tests."""
        from google.cloud import texttospeech  # lazy
        client = self._get_client()
        resp = client.synthesize_speech(
            input=texttospeech.SynthesisInput(text=text),
            voice=texttospeech.VoiceSelectionParams(
                language_code=self.language_code, name=self.voice),
            audio_config=texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3),
        )
        return resp.audio_content

    def synthesize(self, text: str, out_path: Path) -> Path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        chunks = chunk_text(text, max_chars=4500) or [""]

        if len(chunks) == 1:
            out_path.write_bytes(self._synth_chunk(chunks[0]))
            return out_path

        # concatenate multiple MP3 segments via pydub/ffmpeg
        from pydub import AudioSegment  # lazy
        combined = None
        for c in chunks:
            seg = AudioSegment.from_file(io.BytesIO(self._synth_chunk(c)), format="mp3")
            combined = seg if combined is None else combined + seg
        combined.export(str(out_path), format="mp3")
        return out_path
