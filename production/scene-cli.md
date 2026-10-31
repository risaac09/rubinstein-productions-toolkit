# Scene reads: `rpresolve.scene`

Per-frame reads from macOS Vision through a small Swift helper
(`production/rpresolve/scene_cli.swift`), compiled once into
`~/Library/Caches/rpresolve` the same way as the face helper. macOS and
`swiftc` only; elsewhere every call returns `None`.

| Subcommand | Returns |
|---|---|
| `saliency IMAGE` | Attention and objectness boxes, normalized, origin top left |
| `sharpness IMAGE` | Variance of the Laplacian over the attention region (`region`), the whole frame (`frame`) and their `ratio` |
| `motion A B` | Mean absolute difference of two frames at 160x90, 0 to 1 (`mad`). Not optical flow: the simpler of the two, and it needs no OS version check |
| `depth IMAGE [--depth-model PATH.mlmodelc]` | `{"depth": null, "reason": "no model"}` without a model path |

From Python: `rpresolve.scene.read(image, previous=None, depth_model=None)`
gathers these into one dict for a packet slot. `perceive` does not call it
yet, so its output is unchanged.

## Depth model

Depth uses Depth Anything V2 Small as a compiled Core ML model. The checkpoint
is Apache-2.0 and the only one allowed here. Model download pending the
maintainer's approval: nothing in this repo fetches weights, and a model path
today returns "model run not implemented" until the run is wired in.

## Not measured

The target of a 2-minute clip at 1 fps in 30 seconds or less needs real
footage and has not been timed. Tests use synthetic images made at test time.
