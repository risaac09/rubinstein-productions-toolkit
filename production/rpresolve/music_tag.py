"""
rpresolve.music_tag: draft genre and feel tags for a library, by zero-shot CLAP
(laion/clap-htsat-unfused) on a 30 s slice from the middle of each track. Run in
an environment with torch and transformers; the model is a download the person
approves first. The output is a JSON file for `music import-tags`. The tags are
a model's guesses over words a person chose: drafts, never checked by ear.

    python3 -m rpresolve.music_tag DIR --feels "driving,chill,..." OUT.json [--limit N]
"""

import argparse
import json
import subprocess
import sys

from . import music

MODEL = "laion/clap-htsat-unfused"
SECONDS, RATE = 30, 48000
GENRES = ("house", "techno", "melodic techno", "deep house", "afro house", "drum and bass", "hip hop",
          "pop", "indie pop", "rock", "indie rock", "r&b", "electronic", "ambient", "jazz", "folk",
          "country", "latin", "reggae", "classical", "singer-songwriter", "dance pop", "disco")
SECOND_FEEL = 0.25  # a second feel is kept when its probability is at least this


def pick(probs, labels, second=SECOND_FEEL):
    """The top label, plus the runner-up when it is likely enough; [] for no labels."""
    order = sorted(range(len(labels)), key=lambda i: -probs[i])
    out = [labels[order[0]]] if order else []
    if len(order) > 1 and probs[order[1]] >= second:
        out.append(labels[order[1]])
    return out


def slice_pcm(path, dur):
    import numpy as np
    t0 = max(0.0, (dur or 0) / 2 - SECONDS / 2)
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0:.2f}", "-t", str(SECONDS), "-i", "file:" + path,
                        "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"], capture_output=True)
    return np.frombuffer(r.stdout, dtype="<f4")


def run(root, feels, out, limit=None):
    import torch
    from transformers import ClapModel, ClapProcessor
    import os
    proc, model = ClapProcessor.from_pretrained(MODEL), ClapModel.from_pretrained(MODEL).train(False)
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    model.to(dev)

    def text(labels, tpl):
        t = proc(text=[tpl.format(x) for x in labels], return_tensors="pt", padding=True).to(dev)
        with torch.no_grad():
            e = model.get_text_features(**t)
        return e / e.norm(dim=-1, keepdim=True)

    gt, ft = text(GENRES, "a {} song"), text(feels, "a {} song")
    res = {}
    rows = [(h, t) for h, t in music.load_library(root)["tracks"].items() if not t.get("error")][:limit]
    for n, (h, t) in enumerate(rows, 1):
        y = slice_pcm(os.path.join(root, t["path"]), t.get("duration_s"))
        if len(y) < RATE:
            continue
        a = proc(audios=[y], sampling_rate=RATE, return_tensors="pt").to(dev)
        with torch.no_grad():
            e = model.get_audio_features(**a)
        e = e / e.norm(dim=-1, keepdim=True)
        pg = (100 * e @ gt.T).softmax(-1)[0].tolist()
        pf = (100 * e @ ft.T).softmax(-1)[0].tolist()
        res[h] = {"genre": GENRES[max(range(len(GENRES)), key=pg.__getitem__)], "feel": pick(pf, list(feels))}
        if n % 25 == 0:
            print(f"music_tag: {n}/{len(rows)}", file=sys.stderr)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    return len(res)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dir")
    ap.add_argument("out")
    ap.add_argument("--feels", required=True)
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    print(run(a.dir, [x.strip() for x in a.feels.split(",")], a.out, a.limit), "tagged")
