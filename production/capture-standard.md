# Capture Standard

> **Status:** draft, 2026-10-16. The settings below are decisions of record for how RP shoots and how a client camera ships. They match the Resolve tooling's defaults in `production/resolve-config.json`. Colour handling after capture is defined in the colour pipeline section of `production/resolve-template-spec.md`; this document stops at the card.

Capture decides what the edit can do. A frame rate that differs from the timeline, a profile the tools cannot recognise, or a take with no shared sound all cost hours later. Every camera below shoots the same way unless its row says otherwise, so footage from several cameras on one project cuts together without conversion.

## Per camera

| Camera | Profile | Codec | Resolution |
|---|---|---|---|
| iPhone | Apple Log | ProRes 422 HQ | UHD 3840x2160 |
| Panasonic GH7 | V-Log | 10-bit 4:2:2 | UHD 3840x2160; Open Gate only for a planned vertical or square cut |
| Panasonic GH5 | V-Log L | 10-bit 4:2:2 | UHD 3840x2160 |
| Panasonic S5II | V-Log | 10-bit 4:2:2 | UHD 3840x2160 |
| Blackmagic Pyxis | Blackmagic Gen 5 | BRAW Gen 5, constant bitrate 8:1 | UHD 3840x2160 |

10-bit log is what the lutlab looks and the colour-managed project were measured on. Constant bitrate makes card capacity predictable, which matters when cards ship.

## Frame rate and shutter

23.976 fps on every camera, shutter at 180 degrees (1/48 s). That is `default_framerate` in `production/resolve-config.json` and the timeline default, so capture matches the project and nothing is retimed. 29.97 only when a project's scope says so, and then on every camera of that project. Never mix rates inside one shoot.

## Resolution and framing

UHD 3840x2160, matching the project resolution, so nothing is scaled by default.

The GH7 shoots Open Gate only when the scope plans a vertical or square cut. The extra frame gives `reframe_plan` and `reframe_apply` room to crop each shape from one source. When the scope plans landscape only, shoot UHD 16:9.

Frame landscape even for a vertical deliverable. Vertical versions come from the reframe tools, so landscape loses nothing.

## Audio

Any speaking take gets a separate recorder or a lavalier, 48 kHz, 24-bit WAV, in 32-bit float if the recorder has it. Camera scratch audio stays on for every take, on every camera.

Scratch audio is not a backup. It is what `sync_measure` lines the recorder up against.

## Sync

No timecode gear is assumed. At the head of every take, clap once in view of the camera and in range of the recorder.

`sync_measure` then finds where the recorder file sits against the camera clip. It works offline from the sound both devices heard, by cross-correlating the two over windows at the head, the middle and the tail, and reports the offset in seconds and frames, clock drift over the overlap, and how confident each window is. A weak or inconsistent result is not called a match. `sync` then stacks the pair on a new `[auto]` timeline at that offset. It reports drift and does not correct it.

The clap helps a person check the placement by eye and ear. The tool does not read the clap alone: one loud moment over quiet noise correlates perfectly and proves nothing, so a window must still match with its strongest second left out. That is why scratch audio runs for the whole take and why the recorder should hear the room, not only the lavalier. A multicam clip stays a hand step.

## Slate

A spoken ident at the head of every take: date, camera, take. Then the clap. If the head was missed, slate at the tail instead: the same ident, the clap, camera still rolling. It costs seconds and needs no gear a client lacks.

## Cards and file names

One folder per card, named `YYYYMMDD_<CAM>_<card>`, for example `20261016_GH7_A01`. The camera writes its own file names inside, and they are never renamed. `detect` and `ingest` read the native names and folder structure, and `relink` finds a clip by timecode, frame count, resolution and frame rate, so a renamed file breaks the one link that survives a moved drive.

Cards are not wiped until the footage is safely copied and, for a shipped kit, until the client has their footage; see `production/camera-shipping-logistics.md`.

## Example

A past one-day shoot plan, at iPhone level, is in [examples/2026-03-31-shoot-plan.md](examples/2026-03-31-shoot-plan.md). It predates this standard and shoots 30 fps; follow the table above.
