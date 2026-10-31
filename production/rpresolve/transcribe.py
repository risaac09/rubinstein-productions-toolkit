"""
rpresolve.transcribe: speech to a transcript, behind one small interface.

    backend.transcribe(path, language) -> [{start, end, text, words?}]

A segment is {"start", "end", "text"} in seconds and, when the backend has
word times, "words": [{"word", "start", "end"}]. Backends:

    mlx-whisper  Apple silicon, local. Needs `mlx_whisper` importable and a
                 model already on disk: mlx-community/whisper-large-v3-turbo
                 from the Hugging Face cache by default, or --model DIR / a
                 cached repo id / RPRESOLVE_WHISPER_MODEL. It never downloads
                 one; a missing module or uncached model exits 2 and says which.
    whisper.cpp  Other hosts. Shells out to `whisper-cli` on PATH with
                 --model FILE (or RPRESOLVE_WHISPER_CPP_MODEL).
    resolve      A later backend (Resolve's own transcription). Exits 2.
    fake         Reads a JSON file of segments (--source FILE): for tests and
                 for transcripts made elsewhere.

Output is the Whisper JSON the rest of rpresolve reads (cutlist.load_words,
wordsnap, pacing): {"language", "text", "segments": [...]}, plus a
"transcribe" block naming the backend. --srt and --txt write the same
segments as SubRip and as plain text. Run from production/:

    python3 -m rpresolve.transcribe AUDIO_OR_VIDEO --backend NAME
        [--language CODE] [--model M] [--source FILE]
        [--out T.json] [--srt T.srt] [--txt T.txt]

Exit 0 on success, 2 when the backend is not usable or the input is bad.
"""

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

from . import captions, paths

DEFAULT_MLX_MODEL = "mlx-community/whisper-large-v3-turbo"  # roadmap D9; vertcut transcribe's default too


class TranscribeError(Exception):
    """The backend cannot run, or its input is not usable. Message is for the person."""


def _clean_words(raw):
    out = []
    for w in raw or []:
        text = (w.get("word") or w.get("text") or "").strip()
        if text and w.get("start") is not None and w.get("end") is not None:
            out.append({"word": text, "start": float(w["start"]), "end": float(w["end"])})
    return out


def normalize(raw_segments):
    """Segments in the one shape, sorted by start; a segment with no text and
    no words is dropped. Raises TranscribeError on a segment with no times."""
    segs = []
    for i, s in enumerate(raw_segments or []):
        if not isinstance(s, dict) or s.get("start") is None or s.get("end") is None:
            raise TranscribeError(f"segment {i} has no start and end")
        try:
            seg = {"start": float(s["start"]), "end": float(s["end"]),
                   "text": str(s.get("text") or "").strip()}
            words = _clean_words(s.get("words"))
        except (TypeError, ValueError, AttributeError):
            raise TranscribeError(f"segment {i} has a time or word that is not usable")
        if words:
            seg["words"] = words
            seg["text"] = seg["text"] or " ".join(w["word"] for w in words)
        if seg["end"] < seg["start"]:
            raise TranscribeError(f"segment {i} ends before it starts")
        if seg["text"]:
            segs.append(seg)
    segs.sort(key=lambda s: (s["start"], s["end"]))
    return segs


# --- backends ---------------------------------------------------------------

def fake(path, language, source=None, **_):
    if not source:
        raise TranscribeError("the fake backend needs --source FILE (a JSON file of segments)")
    try:
        with open(source, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise TranscribeError(f"fake backend cannot read {source}: {e}")
    segs = data.get("segments") if isinstance(data, dict) else data
    if not isinstance(segs, list):
        raise TranscribeError(f"{source} holds no segments (a list, or an object with segments)")
    return normalize(segs)


def resolve(path, language, **_):
    raise TranscribeError("the resolve backend is a later backend: Resolve's own transcription "
                          "is not wired yet. Use mlx-whisper or whisper.cpp, or feed a "
                          "transcript with --backend fake --source FILE")


def _hf_cached(repo):
    """The local snapshot dir of a Hugging Face repo id, or None. Reads the
    cache only; nothing is fetched."""
    base = os.environ.get("HF_HOME") or os.path.join(os.path.expanduser("~"), ".cache", "huggingface")
    hub = os.environ.get("HF_HUB_CACHE") or os.path.join(base, "hub")
    snaps = sorted(glob.glob(os.path.join(hub, "models--" + repo.replace("/", "--"), "snapshots", "*")))
    return snaps[-1] if snaps else None


def find_mlx_model(model):
    model = model or os.environ.get("RPRESOLVE_WHISPER_MODEL") or DEFAULT_MLX_MODEL
    if os.path.isdir(model):
        return model
    snap = _hf_cached(model) if "/" in model and not os.path.exists(model) else None
    if snap:
        return snap
    raise TranscribeError(f"mlx-whisper model not on disk: {model}. Nothing is downloaded: "
                          f"fetch the default ({DEFAULT_MLX_MODEL}) yourself, or pass --model "
                          f"DIR or set RPRESOLVE_WHISPER_MODEL")


def mlx_whisper(path, language, model=None, **_):
    try:
        import mlx_whisper as mw
    except ImportError:
        raise TranscribeError("mlx-whisper is not installed: `mlx_whisper` is not importable "
                              "(Apple silicon only; see requirements.txt)")
    local = find_mlx_model(model)
    try:
        res = mw.transcribe(path, path_or_hf_repo=local, word_timestamps=True,
                            **({"language": language} if language else {}))
    except Exception as e:  # the engine's own failures (bad audio, no ffmpeg) are not ours to classify
        raise TranscribeError(f"mlx-whisper failed: {e}")
    return normalize(res.get("segments"))


CPP_TIMEOUT_FLOOR_S = 600
CPP_TIMEOUT_PER_AUDIO_S = 4  # whisper.cpp on CPU runs near real time; 4x is generous
CPP_TIMEOUT_UNKNOWN_S = 6 * 3600  # length unreadable: a cap that only stops a hang


def _cpp_timeout(path):
    """Seconds to let whisper-cli run: the env override, else a multiple of the
    audio length with a floor; a long cap when the length cannot be read."""
    env = os.environ.get("RPRESOLVE_WHISPER_CPP_TIMEOUT_S")
    if env is not None:
        try:
            v = float(env)
        except ValueError:
            v = 0.0
        if not 0 < v < float("inf"):
            raise TranscribeError("RPRESOLVE_WHISPER_CPP_TIMEOUT_S must be a positive number "
                                  "of seconds")
        return v
    from . import delivercheck as dc
    try:
        dur = dc.duration(dc.probe(path)) or dc.format_duration(dc.probe(path))
    except Exception:  # unreadable length is not fatal here; the cap below still applies
        dur = None
    if not dur:
        return CPP_TIMEOUT_UNKNOWN_S
    return max(CPP_TIMEOUT_FLOOR_S, CPP_TIMEOUT_PER_AUDIO_S * dur)


def whisper_cpp(path, language, model=None, **_):
    exe = shutil.which("whisper-cli")
    if not exe:
        raise TranscribeError("whisper.cpp is not installed: no `whisper-cli` on PATH")
    model = model or os.environ.get("RPRESOLVE_WHISPER_CPP_MODEL")
    if not model or not os.path.isfile(model):
        raise TranscribeError("whisper.cpp needs a ggml model file: pass --model FILE or set "
                              "RPRESOLVE_WHISPER_CPP_MODEL")
    with tempfile.TemporaryDirectory() as tmp:
        prefix = os.path.join(tmp, "out")
        cmd = [exe, "-m", model, "-f", path, "-oj", "-ojf", "-of", prefix,
               "-l", language or "auto"]
        limit = _cpp_timeout(path)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=limit)
        except subprocess.TimeoutExpired:
            raise TranscribeError(f"whisper-cli timed out after {limit:.0f} s; raise the limit "
                                  "with RPRESOLVE_WHISPER_CPP_TIMEOUT_S (seconds)")
        if r.returncode or not os.path.isfile(prefix + ".json"):
            raise TranscribeError(f"whisper-cli failed ({r.returncode}): {r.stderr.strip()[-300:]}")
        with open(prefix + ".json", encoding="utf-8") as f:
            data = json.load(f)
    try:
        return normalize(_from_cpp(data))
    except (KeyError, TypeError):
        raise TranscribeError("whisper-cli wrote JSON in a shape this reader does not know")


def _from_cpp(data):
    """whisper.cpp full JSON: transcription[] with offsets in ms and, under
    -ojf, tokens[] with their own offsets. Tokens become words where a token
    starts with a space (whisper's word break); special tokens ([_BEG_]) drop."""
    segs = []
    for t in data.get("transcription") or []:
        words = []
        for tok in t.get("tokens") or []:
            text = tok.get("text", "")
            if text.startswith("[_") or not text.strip():
                continue
            a, b = tok["offsets"]["from"] / 1000, tok["offsets"]["to"] / 1000
            if text.startswith(" ") or not words:
                words.append({"word": text.strip(), "start": a, "end": b})
            else:
                words[-1]["word"] += text
                words[-1]["end"] = b
        segs.append({"start": t["offsets"]["from"] / 1000, "end": t["offsets"]["to"] / 1000,
                     "text": t.get("text", ""), "words": words})
    return segs


BACKENDS = {"mlx-whisper": mlx_whisper, "whisper.cpp": whisper_cpp,
            "resolve": resolve, "fake": fake}


# --- outputs ----------------------------------------------------------------

def to_json(segments, backend, language=None):
    return {"language": language, "text": " ".join(s["text"] for s in segments),
            "segments": segments, "transcribe": {"backend": backend}}


def to_srt(segments):
    return captions.format_srt([(s["start"], s["end"], s["text"]) for s in segments])


def to_txt(segments):
    return "\n".join(s["text"] for s in segments) + "\n"


def run_backend(name, path, language=None, **opts):
    return BACKENDS[name](path, language, **opts)


def add_arguments(p):
    p.add_argument("media", help="the audio or video file to transcribe")
    p.add_argument("--backend", required=True, choices=sorted(BACKENDS))
    p.add_argument("--language", help="language code (en); default: the backend's own detection")
    p.add_argument("--model", help="model directory or cached repo id (mlx-whisper), "
                                   "ggml file (whisper.cpp)")
    p.add_argument("--source", help="fake backend: the JSON file of segments")
    p.add_argument("--out", help="write the transcript JSON here (default: stdout)")
    p.add_argument("--srt", help="also write SubRip here")
    p.add_argument("--txt", help="also write plain text here")
    return p


def run(args):
    try:
        if args.backend != "fake" and not os.path.isfile(args.media):
            raise TranscribeError(f"no such media file: {args.media}")
        outs = [p for p in (args.out, args.srt, args.txt) if p]
        for p in outs:  # before the backend runs: no work wasted on a refused path
            problem = paths.out_problem(p, any_git_tree=True)
            if problem:
                raise TranscribeError(problem)
        segs = run_backend(args.backend, args.media, args.language,
                           model=args.model, source=args.source)
        if not segs:
            raise TranscribeError("the backend returned no speech")
        doc = json.dumps(to_json(segs, args.backend, args.language),
                         indent=2, ensure_ascii=False) + "\n"
        for path, text in ((args.out, doc),
                           (args.srt, to_srt(segs)), (args.txt, to_txt(segs))):
            if path:
                paths.write_private(path, text, any_git_tree=True)
        if not args.out:
            sys.stdout.write(doc)
    except (TranscribeError, OSError) as e:
        print(f"transcribe: {e}", file=sys.stderr)
        return 2
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m rpresolve.transcribe",
                                 description="Speech to a Whisper-shaped transcript.")
    return run(add_arguments(ap).parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
