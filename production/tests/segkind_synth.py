"""
Synthetic audio for the segkind tests and calibration: sung note sequences,
a speech-like syllable stream, wind, and a beat-driven instrument. numpy
only, deterministic from a seed, 16 kHz mono float32. No recorded speech or
song is used anywhere.
"""

import math

import numpy as np

SR = 16000


def _rng(seed):
    return np.random.RandomState(seed)


def _lowpass_noise(n, rng, cutoff_hz, sr=SR):
    spec = np.fft.rfft(rng.randn(n))
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    spec *= 1.0 / (1.0 + (freqs / cutoff_hz) ** 4)
    out = np.fft.irfft(spec, n)
    return out / (np.abs(out).max() + 1e-9)


def _formant_weights(f0, harmonics, formants=((700.0, 250.0), (1200.0, 300.0))):
    """Amplitude of each harmonic: a 1/h tilt shaped by two formant bumps."""
    w = []
    for h in range(1, harmonics + 1):
        f = f0 * h
        bump = 0.15 + sum(math.exp(-0.5 * ((f - c) / bw) ** 2) for c, bw in formants)
        w.append(bump / h)
    return w


def _voiced(freq_track, sr=SR, harmonics=14):
    """A harmonic tone following a per-sample f0 track (Hz)."""
    phase = 2 * np.pi * np.cumsum(freq_track) / sr
    f0 = float(np.median(freq_track))
    out = np.zeros(len(freq_track))
    for h, a in enumerate(_formant_weights(f0, harmonics), 1):
        if f0 * h < sr / 2.2:
            out += a * np.sin(h * phase)
    return out


def sung(seconds, seed=0, voices=1, snr_db=25.0, vibrato_cents=35.0, sr=SR):
    """A melody of held, vibrato notes (196 to 392 Hz, 0.35 to 0.9 s each),
    one voice or a small unison choir with each singer a few cents off."""
    rng = _rng(seed)
    n = int(seconds * sr)
    scale = [196.0, 220.0, 247.0, 262.0, 294.0, 330.0, 392.0]
    out = np.zeros(n)
    for v in range(voices):
        vr = _rng(seed * 31 + v)
        detune = 2 ** (vr.uniform(-18, 18) / 1200.0)
        t, tone = 0, np.zeros(n)
        r = _rng(seed)            # same melody for every singer
        while t < n:
            dur = int(r.uniform(0.35, 0.9) * sr)
            f = scale[r.randint(len(scale))] * detune
            k = np.arange(min(dur, n - t))
            vib = 2 ** (vibrato_cents / 1200.0 * np.sin(2 * np.pi * 5.5 * k / sr)
                        * np.minimum(1.0, k / (0.15 * sr)))
            seg = _voiced(f * vib, sr)
            env = np.minimum(1.0, k / (0.04 * sr)) * np.minimum(1.0, (len(k) - k) / (0.06 * sr))
            tone[t:t + len(k)] = seg * env
            t += len(k) + int(r.uniform(0.0, 0.05) * sr)
        out += tone * (1.0 if v == 0 else 0.7)
    out /= np.abs(out).max() + 1e-9
    return _add_noise(out * 0.6, snr_db, rng)


def speech_like(seconds, seed=0, snr_db=25.0, sr=SR):
    """Syllable-sized voiced bursts, 0.10 to 0.25 s, with f0 gliding by up to
    six semitones inside each, irregular pauses, and fricative bursts: the
    contour of talking, without any words."""
    rng = _rng(seed)
    n = int(seconds * sr)
    out = np.zeros(n)
    t = 0
    base = rng.uniform(105, 210)
    while t < n:
        dur = int(rng.uniform(0.10, 0.25) * sr)
        k = np.arange(min(dur, n - t))
        if rng.rand() < 0.2:                     # fricative
            seg = rng.randn(len(k)) * 0.15
        else:
            glide = rng.uniform(-6, 6) / 12.0
            f0 = base * 2 ** (glide * k / max(1, len(k)) + rng.uniform(-0.1, 0.1))
            seg = _voiced(f0, sr, harmonics=10)
        env = np.hanning(len(k) + 2)[1:-1]
        out[t:t + len(k)] += seg * env
        t += len(k) + int(rng.exponential(0.12) * sr)
        base *= 2 ** (rng.uniform(-2, 2) / 12.0)
        base = min(max(base, 90.0), 240.0)
    out /= np.abs(out).max() + 1e-9
    return _add_noise(out * 0.6, snr_db, rng)


def wind(seconds, seed=0, level=0.05, sr=SR):
    """Low-passed noise with a slow swell: no pitch, no syllables."""
    rng = _rng(seed)
    n = int(seconds * sr)
    swell = 0.6 + 0.4 * np.sin(2 * np.pi * 0.2 * np.arange(n) / sr + rng.rand() * 6)
    return (_lowpass_noise(n, rng, 600.0) * swell * level).astype(np.float32)


def instrument(seconds, seed=0, bpm=110.0, snr_db=30.0, sr=SR):
    """A chord stream on a steady beat: sustained harmonic stacks, a decay
    on every beat."""
    rng = _rng(seed)
    n = int(seconds * sr)
    beat = int(60.0 / bpm * sr)
    out = np.zeros(n)
    chords = [(220.0, 277.0, 330.0), (196.0, 247.0, 294.0), (175.0, 220.0, 262.0),
              (196.0, 247.0, 294.0)]
    for i, t in enumerate(range(0, n, beat)):
        k = np.arange(min(beat, n - t))
        env = np.exp(-3.0 * k / beat)
        chord = chords[(i // 2) % len(chords)]
        seg = sum(_voiced(np.full(len(k), f), sr, harmonics=8) for f in chord)
        out[t:t + len(k)] += seg * env
    out /= np.abs(out).max() + 1e-9
    return _add_noise(out * 0.6, snr_db, rng)


def _add_noise(x, snr_db, rng):
    p = float(np.mean(x ** 2)) + 1e-12
    noise = rng.randn(len(x))
    noise *= math.sqrt(p / (10 ** (snr_db / 10.0)) / (float(np.mean(noise ** 2)) + 1e-12))
    return (x + noise).astype(np.float32)


def write_wav(path, x, sr=SR):
    import wave
    pcm = (np.clip(x, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
