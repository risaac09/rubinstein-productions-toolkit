# Capture Brief

> **Status:** draft, 2026-10-20, pending the first shoot. One page per camera kit, to print or carry on a phone. Nothing here has been used on a shoot yet; the miss list (VP-101c) is written after the first one and this page is corrected from it.

The values come from [camera-transforms.json](camera-transforms.json) and [capture-standard.md](capture-standard.md). This page cites them and does not restate them as new numbers. If a row disagrees with those files, they win.

**How to read the labels.** Every number is one of:

- **house working default**: a setting we chose to start from. It has not been checked against a shoot.
- **maker spec**: the manufacturer's published figure, from the capture standard.
- **computed**: derived from the curve code in `production/rpresolve/color.py`, not read off a camera.
- **to be measured**: no figure yet. Do not fill it in from another body.

Nothing on this page is *measured on a shoot* yet. That label appears only after the VP-101c miss list.

## Before any camera page

Each page below covers the picture. These apply to every kit and are not repeated:

- 23.976 fps, shutter 180 degrees (1/48 s), UHD 3840x2160 (house working defaults, [capture-standard.md](capture-standard.md)).
- White balance by hand and fixed for the scene: 5600 K daylight, 3200 K tungsten. Never auto.
- Hold exposure with ND, not the shutter.
- Landscape framing, even for a vertical deliverable.
- Record the per-clip settings listed on the camera's page (they cannot be recovered from the file later).

## Light plan (one per scene, any kit)

Fill four lines before the first take of each scene. The values are decisions for that scene, not defaults:

1. **Key side:** which side of the lens the key sits on.
2. **Ratio goal:** key to fill, in stops.
3. **Practical colour:** the colour temperature of any lamp in frame.
4. **Negative fill:** where it goes, or "none".

Scene: ______ Key side: ______ Ratio (stops): ______ Practical: ______ Negative fill: ______

## Blackmagic Pyxis, BRAW Gen 5

| Item | Value | Label |
|---|---|---|
| Profile | Blackmagic RAW, Film Gen 5; BRAW constant bitrate 8:1 | house working default |
| Base ISO | 400 or 3200, dual native | maker spec |
| Exposure target | 18% grey at 38.4% of the full-range signal | maker spec (curve definition, via the capture standard) |
| Code value | to be measured (BRAW is not a 10-bit code on the card; the offline tools measure a Resolve-rendered ramp) | to be measured |
| Zebra and waveform | not set; confirm the settings on the camera | to be measured |
| Resolve input | auto (camera RAW), no per-clip tag | camera table |
| Record per clip | ISO (400 or 3200); BRAW Gen (5; Gen 6 only after the chart-day measurement) | camera table |

Exposure is set by eye on the log image, then doubtful shots are checked against the grey level above. Monitoring LUT: 33-point `.cube` on the camera, screen only.

## Panasonic S5II, V-Log

| Item | Value | Label |
|---|---|---|
| Profile | V-Log, 10-bit 4:2:2 | house working default |
| Base ISO | 640 or 4000, dual native | maker spec |
| Exposure target | 18% grey at 10-bit code 433, about 42% of the full-range signal (also quoted as about 42 IRE; check which scale your waveform reads) | computed from the V-Log curve (Panasonic V-Log reference manual, Rev 1.0) |
| Zebra and waveform | not set; the S5II manual's zebra and false colour sections are not yet read for this page | to be measured |
| Resolve input | Panasonic V-Gamut/V-Log | camera table |
| Record per clip | ISO (640 or 4000) | camera table |

Monitoring LUT: the S5II loads house looks from the card (`lutlab looks --monitor`). Screen only; the camera records V-Log.

## Panasonic GH7, V-Log

| Item | Value | Label |
|---|---|---|
| Profile | V-Log, 10-bit 4:2:2; Open Gate (5760x4320) only for a planned vertical or square cut | house working default |
| Base ISO | 500, confirmed in the camera menu 2026-10-16; Auto ISO capped at 3200 | maker spec for 500; house working default for the 3200 cap |
| Exposure target | 18% grey at 10-bit code 433, about 42% of the full-range signal (also quoted as about 42 IRE; check which scale your waveform reads) | computed from the V-Log curve |
| Zebra and waveform | not set; the GH7 manual's zebra and false colour sections are not yet read for this page | to be measured |
| Resolve input | Panasonic V-Gamut/V-Log | camera table |
| Record per clip | ISO (base 500) | camera table |

No monitoring LUT route is known for the GH7. Monitor in log or with the camera's own assist.

## iPhone 16 Pro Max, HLG

| Item | Value | Label |
|---|---|---|
| Profile | HLG (ITU-R BT.2100), Rec.2020 primaries. The capture standard's Apple Log row is a separate profile with its own kit. | camera table |
| Base ISO | to be measured (no figure is set in the capture standard) | to be measured |
| Exposure target | to be measured. The curve code encodes scene light, not a grey-card reading, so no grey level is quoted here. | to be measured |
| Zebra and waveform | not set; the camera app has no waveform, so judge exposure on the image and check on the first clip in the scopes | to be measured |
| Resolve input | Rec.2100 HLG (Resolve's own name, unverified through the API; confirm on screen) | camera table |
| Record per clip | Profile tag (HLG or Apple Log); ISO as shown in the camera app, if shown | camera table |

Codec and resolution for HLG are not set in the capture standard. Record what the phone writes and add it to the standard after the shoot. No monitoring LUT route is known; monitor with the phone's own display.

## Sound checklist (every kit)

The same list for all four kits. Source: the capture standard's Audio, Sync and Slate sections, plus the sound capture checklist.

1. **Gear:** a separate recorder, 48 kHz, 24-bit WAV, 32-bit float if the recorder has it. Lavalier plus shotgun on every speaking setup.
2. **Float and gain test before the first take:** record a short speech sample at each mic, check the level on the meters, and confirm the recorder is writing 32-bit float. The test is part of the sound checklist; the steps here are a house working default and will be corrected from the first shoot.
3. **Camera scratch audio on, every take, every camera.** `sync_measure` cannot line up a take with the camera microphone off.
4. **Room tone: 30 seconds per setup**, everyone silent, recorded on the same mics and gain as the take. House working default.
5. **Slate:** spoken ident (date, camera, take), then one clap in view of the camera and in range of the recorder. If the head was missed, slate at the tail.
6. **Card folder:** `YYYYMMDD_<CAM>_<card>`, native file names untouched.

## After the shoot

Offload and checksum before wiping any card ([capture-standard.md](capture-standard.md#cards-and-file-names)). Then write the miss list: for each kit, where the footage missed this page (exposure, ratio, room tone, sync) and which row of this page to change. That list is VP-101c, and this page stays a draft until it exists.
