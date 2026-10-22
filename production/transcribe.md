# transcribe

`python3 -m rpresolve.transcribe MEDIA --backend NAME` turns speech into the
Whisper-shaped JSON the rest of `rpresolve` already reads. Run it from
`production/`. Offline and stdlib only; a backend brings its own engine.

| Backend | Where | Needs | State |
|---|---|---|---|
| `mlx-whisper` | Apple silicon | `mlx_whisper` importable, a model already on disk | Wired. Downloads nothing. |
| `whisper.cpp` | any host | `whisper-cli` on PATH, `--model` a ggml file | Wired. |
| `resolve` | Resolve | | A later backend. Exits 2 and says so. |
| `fake` | any host | `--source` a JSON file of segments | For tests and for transcripts made elsewhere. |

A backend that cannot run exits 2 and names what is missing. For `mlx-whisper`
that is the module, or the model, and which decision it waits on: the model
is [roadmap](media-lane.md) decision D9 (name, size, licence, where it lives
on disk, off iCloud). Give it a model with `--model DIR`, a Hugging Face repo
id already in the local cache, or `RPRESOLVE_WHISPER_MODEL`. `whisper.cpp`
takes `--model FILE` or `RPRESOLVE_WHISPER_CPP_MODEL`.

## Output

`--out T.json` (default: stdout), `--srt T.srt`, `--txt T.txt`.

```json
{"language": "en", "text": "...", "transcribe": {"backend": "fake"},
 "segments": [{"start": 0.0, "end": 1.5, "text": "Hello there",
               "words": [{"word": "Hello", "start": 0.0, "end": 0.7}]}]}
```

Segments are sorted by start; empty ones are dropped. `words` appears only
when the backend has word times. This is the shape `cutlist.load_words`,
`wordsnap` and `pacing --words` read unchanged; `--srt` is what
`captionlint` and `pacing --captions` read. Segments without words give a
caption file and a text file but no word timings: `wordsnap` and `pacing`
need a backend with word times.
