"""Throwaway benchmark: Kokoro synth speed under provider/chunk/parallel variants.

Measures real-time factor (audio_seconds / wall_seconds) on a real cached article
slice so we can pick the fastest config before wiring it into the engine. Higher
RT factor = faster. Not a test; safe to delete.
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from counterfactual_podcast import config
from counterfactual_podcast.cache import Cache
from counterfactual_podcast.tts.base import chunk_text

CARD = "69cffc8a2e3fdcd3aee1c97a"  # Dreyfus affair (147k chars)
N_CHARS = 8000  # representative slice; keeps each run quick


def get_text() -> str:
    ec = Cache(config.CACHE_DB).get_extracted(CARD)
    return ec.text[:N_CHARS]


def build(provider: str | None, intra_threads: int | None):
    import onnxruntime as rt
    from kokoro_onnx import Kokoro

    so = rt.SessionOptions()
    if intra_threads:
        so.intra_op_num_threads = intra_threads
    providers = [provider] if provider else ["CPUExecutionProvider"]
    sess = rt.InferenceSession(config.KOKORO_MODEL_PATH, sess_options=so, providers=providers)
    return Kokoro.from_session(sess, config.KOKORO_VOICES_PATH)


def synth_seq(model, text, max_chars):
    chunks = chunk_text(text, max_chars=max_chars) or [""]
    pieces = []
    for ch in chunks:
        s, sr = model.create(ch, voice=config.KOKORO_VOICE)
        pieces.append(np.asarray(s, dtype=np.float32))
    return float(len(np.concatenate(pieces))) / 24000.0


def synth_par(model, text, max_chars, workers):
    chunks = chunk_text(text, max_chars=max_chars) or [""]

    def one(ch):
        s, _ = model.create(ch, voice=config.KOKORO_VOICE)
        return np.asarray(s, dtype=np.float32)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        pieces = list(ex.map(one, chunks))
    return float(len(np.concatenate(pieces))) / 24000.0


def run(label, model, fn):
    synth_seq(model, "warmup hello there.", 400)  # warm up (CoreML compiles on 1st call)
    text = get_text()
    t0 = time.time()
    audio_s = fn(model, text, 400) if fn is synth_seq else fn(model, text, 400, 4)
    wall = time.time() - t0
    print(f"{label:42s}  audio={audio_s:6.1f}s  wall={wall:6.1f}s  RT={audio_s/wall:4.2f}x",
          flush=True)


def main():
    print(f"Benchmarking on {N_CHARS} chars of '{CARD}'\n")

    m_cpu = build("CPUExecutionProvider", None)
    run("CPU, seq, 400ch (current baseline)", m_cpu, synth_seq)
    run("CPU, parallel(4), 400ch", m_cpu, synth_par)
    del m_cpu

    try:
        m_cml = build("CoreMLExecutionProvider", None)
        run("CoreML, seq, 400ch", m_cml, synth_seq)
        run("CoreML, parallel(4), 400ch", m_cml, synth_par)
    except Exception as e:  # noqa: BLE001
        print(f"CoreML failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
