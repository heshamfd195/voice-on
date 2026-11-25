"""Kokoro neural voice for speak.py. Runs inside the venv that `speak.py --setup kokoro` makes.

Reads a JSON list of chunks ({"text", "lang"}) on stdin and its settings (JSON) as
argv[1]: voice, speed, lang, german_voice, the models folder and the folder to write
audio into. For each chunk in turn it writes a wav file and prints its path, so
speak.py can play the first chunk while the next ones are made. speak.py plays them
and keeps the timings.

A German chunk ("de") is read by the Piper voice named in german_voice
(german_piper.py), when it is installed; otherwise Kokoro reads it.
"""
import json
import os
import sys
import tempfile
import wave

import numpy as np
from kokoro_onnx import Kokoro

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import german_piper  # noqa: E402

opts = json.loads(sys.argv[1])
chunks = json.loads(sys.stdin.read())
kokoro = Kokoro(os.path.join(opts["models"], opts.get("model", "kokoro-v1.0.int8.onnx")),
                os.path.join(opts["models"], "voices-v1.0.bin"))
speed = float(opts.get("speed", 1.0))
piper = german_piper.model_path(opts["models"], opts.get("german_voice") or "")

for i, chunk in enumerate(chunks):
    samples = None
    if chunk.get("lang") == "de" and piper:
        try:
            samples, rate = german_piper.speak(chunk["text"], speed, piper)
        except Exception:
            samples = None  # no piper package: Kokoro reads it
    if samples is None:
        samples, rate = kokoro.create(chunk["text"], voice=opts.get("voice", "af_heart"),
                                      speed=speed, lang=opts.get("lang", "en-us"))
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    fd, path = tempfile.mkstemp(prefix="%03d-" % i, suffix=".wav", dir=opts["audio"])
    with os.fdopen(fd, "wb") as f, wave.open(f, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    print(path, flush=True)
