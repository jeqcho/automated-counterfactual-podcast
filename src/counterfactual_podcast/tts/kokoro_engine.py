"""Local Kokoro TTS engine.

Heavy imports (``kokoro_onnx``, ``soundfile``, ``pydub``) are deferred so that
merely importing this module — e.g. for the factory or for tests — never
requires the package or a downloaded model. The model call is isolated in
``_synth_chunk`` so tests can monkeypatch it and exercise the file-writing path
without any real synthesis.
"""
from __future__ import annotations

from pathlib import Path

from .. import config
from .base import chunk_text


class KokoroEngine:
    """Synthesize speech locally with Kokoro (kokoro-onnx)."""

    def __init__(self, voice: str = config.KOKORO_VOICE) -> None:
        # No heavy imports here: construction must succeed without kokoro_onnx
        # installed (verified by the test-suite). The model is loaded lazily on
        # first synthesis.
        self.voice = voice
        self._model = None

    def _load_model(self):
        """Lazily build the underlying kokoro-onnx model (cached)."""
        if self._model is None:
            from kokoro_onnx import Kokoro  # lazy: only needed for real synth

            self._model = Kokoro(str(config.KOKORO_MODEL_PATH),
                                 str(config.KOKORO_VOICES_PATH))
        return self._model

    def _synth_chunk(self, text: str):
        """Synthesize one text chunk → (float32 samples, sample_rate).

        This is the model-call boundary; tests monkeypatch it.
        """
        model = self._load_model()
        samples, sample_rate = model.create(text, voice=self.voice)
        return samples, sample_rate

    def _synth_chunk_safe(self, text: str):
        """Synthesize a chunk; if it exceeds Kokoro's 510-token limit (IndexError),
        recursively split on whitespace and concatenate. Guarantees we never exceed
        the model's per-call cap regardless of phoneme density."""
        import numpy as np  # lazy
        try:
            samples, sr = self._synth_chunk(text)
            return np.asarray(samples, dtype=np.float32), sr
        except Exception:
            if len(text) <= 60:
                raise
            mid = len(text) // 2
            sp = text.rfind(" ", 0, mid)
            sp = sp if sp > 0 else mid
            s1, sr = self._synth_chunk_safe(text[:sp])
            s2, _ = self._synth_chunk_safe(text[sp:].lstrip())
            return np.concatenate([s1, s2]), sr

    def synthesize(self, text: str, out_path: Path) -> Path:
        import numpy as np  # lazy

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Kokoro caps each call at 510 phoneme tokens; keep chunks small (chars are a
        # rough proxy) and rely on _synth_chunk_safe to split any that still overflow.
        chunks = chunk_text(text, max_chars=400)
        if not chunks:
            chunks = [""]

        pieces = []
        sample_rate = 24000  # Kokoro default; overwritten by first real chunk
        for chunk in chunks:
            samples, sample_rate = self._synth_chunk_safe(chunk)
            pieces.append(samples)

        audio = (
            np.concatenate(pieces)
            if pieces
            else np.zeros(0, dtype=np.float32)
        )

        if str(out_path).lower().endswith(".mp3"):
            self._write_mp3(audio, sample_rate, out_path)
        else:
            self._write_wav(audio, sample_rate, out_path)

        return out_path

    @staticmethod
    def _write_wav(audio, sample_rate: int, out_path: Path) -> None:
        import soundfile as sf  # lazy

        sf.write(str(out_path), audio, sample_rate)

    def _write_mp3(self, audio, sample_rate: int, out_path: Path) -> None:
        """Write a temp WAV then transcode to MP3 via pydub/ffmpeg."""
        import tempfile

        from pydub import AudioSegment  # lazy (needs ffmpeg on PATH)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            self._write_wav(audio, sample_rate, tmp_path)
            AudioSegment.from_wav(str(tmp_path)).export(
                str(out_path), format="mp3"
            )
        finally:
            tmp_path.unlink(missing_ok=True)
