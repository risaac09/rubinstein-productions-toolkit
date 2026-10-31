# Capture settings the readouts cannot confirm

> **Status:** draft, 2026-10-31.

VP-101c (the readout half; the capture brief's post-shoot miss list is separate and still to come). For every setting on the [capture brief](capture-brief.md), whether the offline `measure` and `perceive` readouts (`production/rpresolve/measure.py`, `perceive.py`) can confirm it from the footage. Both read one decoded video stream with ffprobe and ffmpeg, take it as display-encoded Rec.709, and report numbers about the picture. Neither reads audio, camera metadata beyond the stream's colour tags, or the Resolve project.

**Confirms:** `yes` the readout reports the setting itself; `partly` it reports something the setting influences, so it can contradict the setting but not prove it; `no` nothing in the readout output speaks to it. A function in backticks, such as `measure.probe`, is the one that does the work or the one that comes closest; a `no` row says what the file or the output does not carry.

The `Id` column is the key the capture brief's `setting-ids` markers use. `production/tests/test_capture_readout_gaps.py` checks that every id in the brief is a row here and that every cited function exists.

<!-- readout-gaps -->
| Id | Setting | Confirms | Function, or why not |
|---|---|---|---|
| frame-rate | 23.976 fps | no | `measure.probe` reads `avg_frame_rate`, but neither report carries it, and an average rate says nothing about the rate shot against the rate conformed. |
| shutter-angle | Shutter 180 degrees (1/48 s) | no | The stream data ffprobe returns has no exposure time or shutter angle. Motion blur is not measured. |
| resolution | UHD 3840x2160 (Open Gate where planned) | yes | `perceive.read` records the decoded width and height (rotation applied) in `source`. `measure.probe` reads the same but its report does not print it. |
| white-balance | 5600 K daylight, 3200 K tungsten, fixed for the scene | partly | `perceive.colour_numbers` gives the cast of near-neutral pixels as Lab a and b. That shows a drift or a tint, not a Kelvin figure, and means little on a log frame decoded as Rec.709. |
| nd-exposure | Hold exposure with ND, not the shutter | no | Nothing in the file records ND, aperture or shutter, and luma level cannot tell which of them held it. |
| landscape-framing | Landscape framing, even for a vertical deliverable | yes | `perceive.read` records width and height after `measure.rotation` is applied, so a portrait source shows as taller than wide. |
| record-per-clip | Per-clip settings (ISO, BRAW Gen, profile tag) | no | `measure.probe` asks ffprobe for codec, profile, size, rate and colour tags only. ISO and BRAW Gen are camera metadata, not in the stream fields it returns (ffmpeg cannot decode BRAW at all). |
| key-side | Key side of the lens | partly | `measure.lighting_ratio` returns `lit_side` (left or right in the image) for a face. It is the brighter half of the face in the image, which key, a practical or a bounce can all cause, so it can contradict the plan but should not confirm it, and it needs a face Vision finds. |
| ratio-goal | Key to fill ratio, in stops | partly | `measure.lighting_ratio` returns (key + fill) : fill in stops for a face, and `measure.pooled_lighting` pools it over the sampled frames. It is withheld for PQ, HLG, linear and log tags, and an untagged log file is read as Rec.709 and misreported. Not key to fill alone. |
| practical-colour | Colour temperature of any lamp in frame | no | `perceive.palette` lists dominant colours, but no function estimates a Kelvin value or separates a lamp from the rest of the frame. |
| negative-fill | Where negative fill goes, or none | no | `measure.shadow_classes` shares the frame into readable, sculpted and lost shadow, but a shadow share cannot be traced to negative fill. |
| profile | Camera profile (V-Log, BRAW, HLG) | partly | `measure.transfer_of` reports the `color_transfer` tag and withholds ratios for PQ, HLG, linear and log. `NOT_REC709` has no V-Log entry, and no camera original has been probed yet, so whether the tag names the profile is unchecked. `measure.bit_depth` reads 10-bit from the pixel format. |
| base-iso | Base ISO (dual native) | no | ISO is not in the stream fields `measure.probe` returns. |
| exposure-target | 18% grey at the brief's signal level | partly | `perceive.false_colour` paints the band around 38 to 42 percent of the signal (the ARRI layout) and `measure.luma_stats` gives luma percentiles in IRE. They read signal level of the file as decoded, not a grey card, and only compare with the brief's figures (percent of full range of the camera curve) on a file whose signal is that curve. |
| code-value | Code value of the grey target (Pyxis row) | partly | `measure.luma_stats` and `perceive.luma_numbers` give luma percentiles in IRE of a rendered file, so only a Resolve-rendered ramp or grey patch can be read, and its level is on the rendered scale. A BRAW original is not a 10-bit code on the card and ffmpeg cannot decode it. |
| zebra-waveform | Zebra and waveform settings on the camera | no | A camera monitoring setting; it leaves nothing in the footage. `perceive.waveform` draws a waveform of the file, which is not the camera's. |
| resolve-input | Resolve input (camera RAW or Panasonic V-Gamut/V-Log) | no | A Resolve project setting, not carried by the media file. The offline readouts never connect to Resolve. |
| monitoring-lut | Monitoring LUT on the camera, screen only | no | A monitor-only LUT is not baked into the recording, and neither readout can see it. |
| sound-recorder | Separate recorder, 48 kHz, 24-bit WAV, 32-bit float | no | `measure.probe` selects the first video stream (`v:0`) and no function reads audio. |
| float-gain-test | Float and gain test before the first take | no | Audio is not read, and a test before the first take is not in the footage. |
| scratch-audio | Camera scratch audio on, every take | no | Audio is not read, so a camera file with its microphone off passes unnoticed. |
| room-tone | 30 seconds of room tone per setup | no | Audio is not read. |
| slate | Spoken ident and one clap per take | no | Audio and the slate frame are not read. |
| card-folder | Card folder naming | no | `perceive.read` stores the file's absolute path, but nothing checks the path against the naming rule. |

<!-- /readout-gaps -->

Count: 24 settings. 2 confirmed, 6 partly, 16 not.

Where a `partly` row meets a log file, read the shot through the colour path first (the `perceive` note says the same): the readouts decode every file as Rec.709 with a 2.4 gamma.
