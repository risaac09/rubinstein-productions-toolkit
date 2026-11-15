"""
rpresolve.music_lookup: a genre for each track from what the world already says about its
artist, instead of a model's guess. The artist is the left side of "Artist - Title" in the
title when there is one (a file's artist tag is often the channel that uploaded it), else the
artist tag; the genre is the top-voted genre of the best MusicBrainz match. One request per
artist, a second apart, a plain client name in the User-Agent. Output is a JSON file for
`music import-tags --source lookup`; a track with no confident match is left out.

    python3 -m rpresolve.music_lookup DIR OUT.json
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import music

UA = "rpresolve-music-lookup/1.0"
URL = "https://musicbrainz.org/ws/2/artist"
SPLIT = re.compile(r"\s*(?:,|&|\bx\b|\bft\.?\b|\bfeat\.?\b|\bfeaturing\b|\bwith\b)\s*", re.I)
DELAY = 1.2


def artist_of(row):
    """The artist to look up: left of ' - ' in the title, else the artist tag; the first of
    a list ('A, B', 'A ft. B', 'A & B')."""
    title, tag = row.get("title") or "", row.get("artist") or ""
    name = title.split(" - ", 1)[0] if " - " in title else tag
    first = SPLIT.split(name.strip(), 1)[0].strip()
    return first or None


def match_id(artist, results):
    """The id of the first search result whose name is the artist's and whose score is confident."""
    for r in results:
        if r.get("name", "").casefold() == artist.casefold() and r.get("score", 0) >= 90:
            return r.get("id")
    return None


def top_genre(detail):
    """The top-voted genre of an artist record fetched with inc=genres, else None."""
    g = sorted(detail.get("genres") or [], key=lambda x: -x.get("count", 0))
    return g[0]["name"] if g else None


def get(url):
    for k in range(5):
        time.sleep(DELAY + 3 * k)
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            return json.load(urllib.request.urlopen(req, timeout=30))
        except urllib.error.HTTPError as e:
            if e.code not in (429, 503):
                raise
    raise music.MusicError(f"MusicBrainz kept refusing {url[:80]}")


def fetch(artist):
    """The artist's top genre: one search, then one record with its genres (None for no match)."""
    q = urllib.parse.quote(f'artist:"{artist}"')
    aid = match_id(artist, get(f"{URL}?fmt=json&limit=3&query={q}").get("artists", []))
    return top_genre(get(f"{URL}/{aid}?fmt=json&inc=genres")) if aid else None


def run(root, out, log=lambda s: None):
    lib = music.load_library(root)
    cache, res = {}, {}
    for n, (h, t) in enumerate(sorted(lib["tracks"].items()), 1):
        a = artist_of(t)
        if not a or t.get("error"):
            continue
        if a not in cache:
            try:
                cache[a] = fetch(a)
            except (music.MusicError, OSError) as e:  # one refused or failed artist is skipped, the run goes on
                log(f"music_lookup: {a}: {e}")
                cache[a] = None
        if cache[a]:
            res[h] = {"genre": cache[a]}
        if n % 25 == 0:
            log(f"music_lookup: {n}/{len(lib['tracks'])}")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    return len(res), len(cache)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dir")
    ap.add_argument("out")
    a = ap.parse_args()
    got, artists = run(a.dir, a.out, lambda s: print(s, file=sys.stderr))
    print(f"{got} track(s) with a genre from {artists} artist(s)")
