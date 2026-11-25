"""German speech from a Piper voice, for kokoro.py: text the reply wrote «like this».

Optional: it needs the piper-tts package in the voice venv and a Piper voice in
models/piper/<name>.onnx (with its .onnx.json), named by "german_voice" in config.json,
such as de_DE-kerstin-low, de_DE-ramona-low or de_DE-thorsten-high.

A word said alone came out rushed and clipped: the low-quality voices end a very short
input early. So a single word is said at the end of a carrier sentence, «Er sagt Sohn.»,
and only the word is kept, cut at the last space using Piper's per-sound sample counts
(needs the onnx package). It gets the falling, finished sound of a word at the end of a
sentence, at dictionary pace.
"""
import os

import numpy as np

CARRIER = "Er sagt"
WORD_PACE = 1.4  # a lone word is said this much slower than a sentence
_voice, _path, _aligned = None, None, False


def _load(path):
    global _voice, _path, _aligned
    from piper import PiperVoice
    if _voice is None or path != _path:
        try:
            _voice, _aligned = PiperVoice.load(path, include_alignments=True), True
        except Exception:  # no onnx package: plain loading, lone words as they come
            _voice, _aligned = PiperVoice.load(path), False
        _path = path
        # espeak writes the soft ch of «ich» as c + a combining cedilla. Some voices' sound
        # tables have ç but not the lone cedilla, so they drop it and say a hard c.
        plain = _voice.phonemize

        def joined(text):
            out = []
            for sent in plain(text):
                fixed = []
                for p in sent:
                    if p == "̧" and fixed and fixed[-1] == "c":
                        fixed[-1] = "ç"
                    else:
                        fixed.append(p)
                out.append(fixed)
            return out
        _voice.phonemize = joined
    return _voice


def model_path(models, name):
    path = os.path.join(models, "piper", name + ".onnx")
    return path if name and os.path.exists(path) else None


def _lone_word(v, word, cfg):
    """The word cut from the end of «Er sagt <word>.», or None if that fails."""
    space = (v.config.phoneme_id_map.get(" ") or [None])[0]
    out = []
    for c in v.synthesize(f"{CARRIER} {word}.", syn_config=cfg, include_alignments=True):
        ids, counts = list(c.phoneme_ids or []), c.phoneme_id_samples
        if counts is None or space not in ids:
            return None
        last = max(i for i, x in enumerate(ids) if x == space)
        out.append(c.audio_float_array[int(sum(counts[:last + 1])):])
    if not out:
        return None
    audio = np.concatenate(out).astype(np.float32)
    fade = np.linspace(0, 1, min(len(audio), int(0.012 * v.config.sample_rate)))
    audio[:len(fade)] *= fade  # no click where the cut is
    return audio


def speak(text, speed, path):
    """(float32 audio, sample rate) for German text at the given speed, from the voice at `path`."""
    from piper import SynthesisConfig
    v = _load(path)
    word = text.strip().strip(".,!?;:«»\"'")
    if _aligned and word and not any(ch.isspace() for ch in word):
        cfg = SynthesisConfig(length_scale=WORD_PACE / max(0.5, speed))
        try:
            audio = _lone_word(v, word, cfg)
        except Exception:
            audio = None
        if audio is not None and len(audio):
            return audio, v.config.sample_rate
    cfg = SynthesisConfig(length_scale=1.0 / max(0.5, speed))
    parts = [c.audio_float_array for c in v.synthesize(text, syn_config=cfg)]
    if not parts:
        return np.zeros(int(0.3 * v.config.sample_rate), dtype=np.float32), v.config.sample_rate
    return np.concatenate(parts).astype(np.float32), v.config.sample_rate
