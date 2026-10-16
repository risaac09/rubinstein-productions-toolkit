# Platform Specs

> **Status:** draft, 2026-10-16. The rows restate what the deliver tooling already enforces. Anything the repo does not state is marked "draft default, verify against the platform's current spec" and is not enforced by any tool. [Needs Isaac](#needs-isaac) lists the choices made by default.

One row per destination class. A destination class is a kind of place a deliverable goes. Each row names the destination key in `production/resolve-config.json` that implements it, so `queue_render` and `deliver_check` apply the row's numbers, or says that none exists. The source of every value is the [Deliver section of the Resolve spec](resolve-template-spec.md#deliver) and the config file; this document does not replace either. If they disagree, the config wins.

## The table

| Destination class | Destination key | Container, video | Size | Frame rate | Audio | Loudness | Captions |
|---|---|---|---|---|---|---|---|
| Web embed | `substack_16x9` | mp4, H.264, 8-bit 4:2:0 | 1920x1080 | the timeline's | AAC 48 kHz stereo, 320 kbps | -16 LUFS | sidecar `.srt` |
| Social vertical 9:16 | `linkedin_9x16` | mp4, H.264, 8-bit 4:2:0 | 1080x1920 | the timeline's | AAC 48 kHz stereo, 320 kbps | -16 LUFS | burnt in |
| Social square 1:1 | `linkedin_1x1` | mp4, H.264, 8-bit 4:2:0 | 1080x1080 | the timeline's | AAC 48 kHz stereo, 320 kbps | -16 LUFS | burnt in |
| Broadcast-style master | none yet | mov, ProRes 422 HQ | the timeline's | the timeline's | LPCM 24-bit 48 kHz | -23 LUFS (draft default, verify against the platform's current spec) | per recipient (draft default, verify against the platform's current spec) |
| Archive master | `client_master` | mov, ProRes 422 HQ, 10-bit 4:2:2 | the timeline's | the timeline's | LPCM 24-bit 48 kHz | none, unnormalized | none |

The first three rows and the archive row are the repo's own numbers, from `resolve-config.json`. The broadcast-style row is a draft default, verify against the platform's current spec: no destination implements it, and no tool checks it.

## What every row shares

These come from the house rules under `deliver` in the config and are checked on every destination that has a loudness target.

- **Frame rate** equals the timeline's, exactly. The accepted rates are 24000/1001, 24, 25, 30000/1001, 30, 50, 60000/1001 and 60. A render never changes the rate.
- **Colour**: Rec.709 primaries and matrix. The web classes tag the transfer as bt709 (`Rec.709-A` in Resolve). The archive master keeps Gamma 2.4, which writes the transfer unspecified, and the check reports it without asserting it.
- **Range**: limited. A `yuvj*` pixel format or a full-range flag fails.
- **Loudness**: integrated loudness within 0.5 LU of the target, true peak at or under -1.0 dBTP. The loudness fix runs on every render as the last step, except on the archive master.
- **Data Burn-in** is `None` on every deliverable. A review copy may carry a timecode burn-in; a deliverable never does.
- **File name** follows the house rule, and each destination renders into its own subfolder (see [the manifest](delivery-manifest.md#file-and-folder-names)).

## Notes by class

**Web embed.** The row is the Substack destination, used here as the stand-in for a page that plays an mp4 inline. A client's own site may take a different player and a different container; check the host's current spec before a first delivery. H.264 at 8-bit 4:2:0 is the repo's choice for every web destination; whether a given host plays it is for the host's current spec to say. `NetworkOptimization` is on, so the file starts playing before it finishes loading.

**Social vertical 9:16.** Built from the timeline's 9:16 copy. `queue_render` refuses a 16:9 timeline for a 9:16 destination, because Resolve would scale it into the frame with bars and every check would still pass. The 9:16 copy comes from `reframe_plan` and `cut`, or `reframe_apply` for a planned timeline, which keep the subject's whole head with room above it. Captions are burnt in at 16 characters per line on up to two lines, because 42 characters overflows a 1080-wide frame. The 4:5 shape is planned by `reframe_plan` but has no destination, so a 4:5 delivery needs a destination added through an overlay.

**Social square 1:1.** Same rules as vertical. Burnt-in captions run 24 characters per line on up to two lines.

**Broadcast-style master.** The repo has no broadcast destination, so this row is a draft. The defaults reuse the archive master's container, codec and audio, since a recipient who needs broadcast delivery will usually ask for an intermediate codec and not for a web file. The loudness target of -23 LUFS is the published EBU R128 target; a recipient in another region may ask for another (ATSC A/85 uses -24 LKFS). The frame rate of a broadcast delivery is the recipient's call and may not be one the timeline has. Captions for broadcast usually need a format the tooling does not make (`deliver_captions` produces `.srt` only). Everything in this row is a draft default, verify against the platform's current spec. Do not deliver to a broadcaster from this row without the recipient's written delivery specification in hand.

**Archive master.** The client master. ProRes 422 HQ at the timeline's own size, LPCM 24-bit 48 kHz, no loudness normalization, no captions. The check expects a 10-bit 4:2:2 profile and takes `--size` to assert the timeline's size. It is the file the platform versions can be re-made from, and it is kept with the project.

## Not recorded here

The repo states no platform limits on duration, file size or bitrate, and no recommended caption position or safe-area margin beyond the characters-per-line figures above. Platforms change these without notice. Look them up at the time of queueing, and put what was found in the shipment's [manifest](delivery-manifest.md) as a note.

## Needs Isaac

1. **Which destination stands in for each class.** `substack_16x9` for the web embed and `linkedin_9x16` and `linkedin_1x1` for the social classes were picked because they match the class by shape and codec. Say if a class should have its own destination key.
2. **The broadcast-style row.** The container, audio and -23 LUFS are draft defaults. Decide whether the practice delivers broadcast-style masters at all. If not, strike the row.
3. **A 4:5 destination.** The planner supports it and no destination exists. Add one or leave it.
4. **Platform limits.** Whether to record duration and size caps here, and who checks them.
