"""
music: a library index for a folder of music, files never moved (rpresolve/music.py).

    music index DIR [--workers N]   measure every track (tags, tempo, steady pulse, loudness), write
                                    DIR/library.json and DIR/playlists/*.m3u; re-runs keep genre, feel
                                    and a person's edits, and redo only changed files
    music status DIR                counts: tracks, with tempo, steady pulse, genre, feel
    music suggest DIR (--method ID | --bpm LO-HI) [--feel F] [--any-pulse] [-n N]
                                    tracks that fit a method's tempo range (half and double time count)
    music set DIR KEY [--genre G] [--feel F ...]   a person's own tags; KEY is a sha256 prefix or path
    music import-tags DIR FILE      a tagger's JSON {path: {"genre": .., "feel": [..]}}, marked model

A measurement and a set of suggestions, nothing heard: model tags are drafts until a person checks them.
"""

import json
import sys

from rpresolve import cli
from rpresolve import music

NAME = "music"
HELP = "Offline: index a music folder by tempo, pulse, loudness, genre and feel; suggest tracks for a method"


def add_arguments(p):
    p.add_argument("action", choices=("index", "status", "suggest", "set", "import-tags"))
    p.add_argument("dir")
    p.add_argument("target", nargs="?", help="set: the track (sha256 prefix or path); import-tags: the JSON file")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--method", help="suggest: an edit method id")
    p.add_argument("--bpm", help="suggest: LO-HI")
    p.add_argument("--feel", action="append")
    p.add_argument("--genre")
    p.add_argument("--any-pulse", action="store_true", help="suggest: include tracks without a steady pulse")
    p.add_argument("-n", type=int, default=10)


def run(args):
    try:
        lib = music.load_library(args.dir)
        if args.action == "index":
            lib = music.index(args.dir, args.workers, log=lambda s: print(s, file=sys.stderr))
            music.suits(lib)
            music.save_library(args.dir, lib)
        elif args.action == "suggest":
            rng = [float(x) for x in args.bpm.split("-")] if args.bpm else None
            if not args.method and not rng:
                raise music.MusicError("suggest needs --method or --bpm LO-HI")
            for t in music.suggest(lib, args.method, rng, args.feel, args.n, not args.any_pulse):
                print(f"{t['bpm']:6.1f} BPM  gap {t['tempo_gap']:4.1f}  {t['lufs']} LUFS  {t['path']}")
            return 0
        elif args.action == "set":
            if not args.target:
                raise music.MusicError("set needs a track: a sha256 prefix or a path")
            music.set_fields(lib, args.target, args.genre, args.feel)
            music.write_playlists(args.dir, lib)
            music.save_library(args.dir, lib)
        elif args.action == "import-tags":
            with open(args.target, encoding="utf-8") as f:
                n = music.import_tags(lib, json.load(f))
            music.write_playlists(args.dir, lib)
            music.save_library(args.dir, lib)
            print(f"music: {n} field(s) set from the tagger")
        print(json.dumps(music.counts(lib)))
        return 0
    except (music.MusicError, ValueError) as e:
        print(f"music: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    cli.script(__name__)
