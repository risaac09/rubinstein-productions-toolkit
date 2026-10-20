# Capture Standard

> **Status:** draft, 2026-10-16. The settings below are a standard for how to shoot so footage from several cameras cuts together. They match the Resolve tooling's defaults in [`resolve-config.json`](resolve-config.json). Colour handling after capture is defined in the [Colour Pipeline of Record](resolve-template-spec.md#colour-pipeline-of-record) of the Resolve template spec; this document stops at the card.

Capture decides what the edit can do. A frame rate that differs from the timeline, a profile the tools cannot recognise, or a take with no shared sound all cost hours later. Every camera below shoots the same way unless its row says otherwise, so footage from several cameras on one project cuts together without conversion.

## Per camera

| Camera | Profile | Codec | Resolution |
|---|---|---|---|
| iPhone 15 Pro or later | Apple Log | ProRes 422 HQ | UHD 3840x2160 |
| Panasonic GH7 | V-Log | 10-bit 4:2:2 | UHD 3840x2160; Open Gate only for a planned vertical or square cut |
| Panasonic GH5 | V-Log L | 10-bit 4:2:2 | UHD 3840x2160 |
| Panasonic S5II | V-Log | 10-bit 4:2:2 | UHD 3840x2160 |
| Blackmagic Pyxis | Blackmagic RAW, decoded as Film Gen 5 | BRAW, constant bitrate 8:1 | UHD 3840x2160 |

The iPhone floor is the 15 Pro, the first model with Apple Log. `detect` verifies one Apple Log tag, the one the first Apple Log writes, and pins that clip as Apple Log. A clip that carries any other Apple Log variant tag goes to review with no input colour space until a person checks it (rules 5 and 6 in `production/rpresolve/detect.py`). Apple Log 2 tagging is unverified.

The lutlab looks were measured on S5II V-Log and Pyxis BRAW clips (see [lutlab's README](lutlab/README.md)), so those two rows are the tested ones. Constant bitrate makes card capacity predictable, which matters when cards ship. How `measure` linearises each body's curve, and the per-clip settings to record, are in [camera-transforms.json](camera-transforms.json).

## Frame rate and shutter

23.976 fps on every camera, shutter at 180 degrees (1/48 s). That is `default_framerate` in `production/resolve-config.json` and the timeline default, so capture matches the project and nothing is retimed. 29.97 only when a project's scope says so, and then on every camera of that project. Never mix rates inside one shoot.

## White balance and exposure

**White balance** is set by hand and fixed for each scene: 5600 K for daylight, 3200 K for tungsten. Never auto, on any camera.

**Exposure** is set by eye on the log image, on site. Do not expose to false colour as a habit. The middle-grey levels in the table below are each log curve's own reference, for checking a doubtful shot with the camera's waveform, false colour or a grey card.

**ND** holds the 180-degree shutter from the section above. The shutter does not move to change exposure.

**Auto ISO** on the GH7 is capped at 3200. The camera allows more, but 3200 is the house ceiling for noise.

Native ISO figures are the makers' published specs, not measurements on any particular camera body; a row says when the figure was also confirmed in the camera's menu. Middle grey is where 18% grey falls on each log curve, from the maker's curve definition, given as a percentage of the full-range signal so the rows compare on one scale. The one cell still marked "to be measured" is the iPhone's native ISO, because Apple publishes none for Apple Log. Do not copy one camera's figure to another.

| Camera | Native ISO in log | Middle grey on the log curve | Open Gate size | Open Gate top frame rate |
|---|---|---|---|---|
| iPhone 15 Pro or later | to be measured | about 49% (Apple Log curve) | not shot | not shot |
| Panasonic GH7 | 500 (Panasonic spec, confirmed in the menu 2026-10-16) | 42% (V-Log) | 5760x4320, 5.8K 4:3 (Panasonic spec) | 29.97p, in MOV at 4:2:0 10-bit Long GOP, 200 Mbps, LPCM, HLG available (Panasonic spec, confirmed in the menu 2026-10-16) |
| Panasonic GH5 | 400, the V-Log L floor (Panasonic spec) | 42% (V-Log L) | not shot | not shot |
| Panasonic S5II | 640 and 4000, dual native (Panasonic spec) | 42% (V-Log) | not shot | not shot |
| Blackmagic Pyxis | 400 and 3200, dual native (Blackmagic spec) | 38.4% (Blackmagic Film Gen 5) | not shot | not shot |

## Resolution and framing

UHD 3840x2160, matching the project resolution, so nothing is scaled by default.

The GH7 shoots Open Gate only when the scope plans a vertical or square cut. The extra frame gives `reframe_plan` and `reframe_apply` room to crop each shape from one source. When the scope plans landscape only, shoot UHD 16:9.

Frame landscape even for a vertical deliverable. Vertical versions come from the reframe tools, so landscape loses nothing.

## Monitoring

A monitoring LUT is for the screen only. The camera records log, with the profile in the table above, whatever the monitor shows.

`lutlab looks --monitor` writes the house looks as files a camera can load: 33 points, clipped to 0..1, with names of 8 characters or fewer. The S5II loads them from the card and the Pyxis loads 33-point `.cube`; see [lutlab's README](lutlab/README.md) for the file names and the card route. The repo knows no route for the GH7, GH5 or iPhone. On those, monitor in log or with the camera's own assist.

Exposure targets are not set here.

## Audio

Any speaking take gets a separate recorder or a lavalier, 48 kHz, 24-bit WAV, in 32-bit float if the recorder has it. Camera scratch audio stays on for every take, on every camera.

`sync_measure` lines the recorder up against the scratch audio, so a take with the camera microphone off cannot be synced by the tools.

## Sync

No timecode gear is assumed. At the head of every take, clap once in view of the camera and in range of the recorder.

`sync_measure` then finds where the recorder file sits against the camera clip. It works offline from the sound both devices heard, by cross-correlating the two over windows across the overlap (a head, a tail and windows between), and reports the offset in seconds and frames, clock drift over the overlap, and how confident each window is. A weak or inconsistent result is not called a match. `sync` then stacks the pair on a new `[auto]` timeline at that offset. It reports drift and does not correct it.

The clap helps a person check the placement by eye and ear. The tool does not read the clap alone: one loud moment over quiet noise correlates perfectly and proves nothing, so a window must still match with its strongest second left out. That is why scratch audio runs for the whole take and why the recorder should hear the room, not only the lavalier. A multicam clip stays a hand step.

## Slate

A spoken ident at the head of every take: date, camera, take. Then the clap. If the head was missed, slate at the tail instead: the same ident, the clap, camera still rolling. It costs seconds and needs no gear a client lacks.

## Cards and file names

One folder per card, named `YYYYMMDD_<CAM>_<card>`, for example `20261016_GH7_A01`. The camera writes its own file names inside, and they are never renamed. `detect` uses the native names as one of its clues (the Blackmagic and Lumix file patterns, for example), and a renamed file loses that clue. `relink` never matches on a name, so it is unaffected either way.

Offload a card on arrival and wipe it only after the footage is checksummed and a second copy exists.
