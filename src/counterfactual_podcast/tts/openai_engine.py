"""OpenAI hosted TTS engine (pluggable alternative to local Kokoro).

The ``openai`` client is imported lazily and exposed via ``_client`` so tests
can patch it without network access or an API key.
"""
from __future__ import annotations

from pathlib import Path


class OpenAIEngine:
    """Synthesize speech with OpenAI's text-to-speech API."""

    def __init__(self, model: str = "tts-1", voice: str = "alloy") -> None:
        self.model = model
        self.voice = voice
        self._client_obj = None

    def _client(self):
        """Lazily construct and cache the OpenAI client."""
        if self._client_obj is None:
            from openai import OpenAI  # lazy: no import at module load

            self._client_obj = OpenAI()
        return self._client_obj

    def synthesize(self, text: str, out_path: Path) -> Path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        client = self._client()
        response = client.audio.speech.create(
            model=self.model,
            voice=self.voice,
            input=text,
        )

        # Newer SDKs return a streamed-response object; older ones expose
        # ``.content`` bytes directly. Support both.
        stream_to_file = getattr(response, "stream_to_file", None)
        if callable(stream_to_file):
            stream_to_file(str(out_path))
        else:
            content = getattr(response, "content", response)
            out_path.write_bytes(content)

        return out_path
