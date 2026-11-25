"""Kokoro neural voice for speak.py. Runs inside the venv that `speak.py --setup kokoro` makes.

Reads the text on stdin and its settings (JSON) as argv[1]: voice, speed, lang, and
from speak.py the models folder, the audio folder and the player command.
It speaks sentence by sentence: the next chunk is made while the last one plays,
so the first words come in about a second. It exits non-zero on any failure, and
speak.py then falls back to the system voice.
"""
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import wave

import numpy as np
from kokoro_onnx import Kokoro

signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # stopped: tidy up below
opts = json.loads(sys.argv[1])
audio_dir = opts["audio"]  # one speaker at a time, so old files here are leftovers
os.makedirs(audio_dir, exist_ok=True)
for old in os.listdir(audio_dir):
    os.remove(os.path.join(audio_dir, old))
kokoro = Kokoro(os.path.join(opts["models"], opts.get("model", "kokoro-v1.0.int8.onnx")),
                os.path.join(opts["models"], "voices-v1.0.bin"))
text = sys.stdin.read().strip()

chunks, current = [], ""
for sentence in re.split(r"(?<=[.!?:;])\s+|\n+", text):
    sentence = sentence.strip()
    if not re.search(r"\w", sentence):
        continue
    if current and len(current) + len(sentence) > 200:
        chunks.append(current)
        current = sentence
    else:
        current = (current + " " + sentence).strip()
    if not chunks and len(current) > 60:  # a short first chunk, so speech starts fast
        chunks.append(current)
        current = ""
if current:
    chunks.append(current)


def to_wav(samples, rate):
    f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False, dir=audio_dir)
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    with wave.open(f, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    f.close()
    return f.name


player, playing, path = None, None, None
try:
    for chunk in chunks:
        samples, rate = kokoro.create(chunk, voice=opts.get("voice", "af_heart"),
                                      speed=float(opts.get("speed", 1.0)),
                                      lang=opts.get("lang", "en-us"))
        path = to_wav(samples, rate)
        if player:
            player.wait()
            os.remove(playing)
        player, playing = subprocess.Popen(list(opts["player"]) + [path]), path
    if player:
        player.wait()
finally:
    if player and player.poll() is None:
        player.terminate()
    for leftover in {playing, path}:
        if leftover and os.path.exists(leftover):
            os.remove(leftover)
