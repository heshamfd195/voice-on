"""Kokoro neural voice for speak.py. Runs inside the venv that `speak.py --setup kokoro` makes.

Reads a JSON list of text chunks on stdin and its settings (JSON) as argv[1]: voice,
speed, lang, the models folder and the folder to write audio into. For each chunk in
turn it writes a wav file and prints its path, so speak.py can play the first chunk
while the next ones are made. speak.py plays them and keeps the timings.
"""
import json
import os
import sys
import tempfile
import wave

import numpy as np
from kokoro_onnx import Kokoro

opts = json.loads(sys.argv[1])
chunks = json.loads(sys.stdin.read())
kokoro = Kokoro(os.path.join(opts["models"], opts.get("model", "kokoro-v1.0.int8.onnx")),
                os.path.join(opts["models"], "voices-v1.0.bin"))

for i, chunk in enumerate(chunks):
    samples, rate = kokoro.create(chunk, voice=opts.get("voice", "af_heart"),
                                  speed=float(opts.get("speed", 1.0)),
                                  lang=opts.get("lang", "en-us"))
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    fd, path = tempfile.mkstemp(prefix="%03d-" % i, suffix=".wav", dir=opts["audio"])
    with os.fdopen(fd, "wb") as f, wave.open(f, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    print(path, flush=True)
