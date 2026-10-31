# Platform Specs

> **Status:** operational, accepted 2026-10-16. The rows restate what the deliver tooling already enforces.

One row per destination key in `production/resolve-config.json`, and the config has eight. A destination class is a kind of place a deliverable goes, and each row names the class its key belongs to. Where two keys share a class, each still gets its own row, because `queue_render` and `deliver_check` apply that key's numbers. A test in `production/tests/test_platform_specs.py` fails when a key in the config has no row here. The source of every value is the [Deliver section of the Resolve spec](resolve-template-spec.md#deliver) and the config file; this document does not replace either. If they disagree, the config wins.

## The table

| Destination class | Destination key | Container, video | Size | Frame rate | Audio | Loudness | Captions |
|---|---|---|---|---|---|---|---|
| Long-form video, UHD | `youtube_16x9` | mp4, H.265, 4:2:0 | 3840x2160 | the timeline's | AAC 48 kHz stereo, 320 kbps | -14 LUFS | sidecar `.srt` |
| Long-form video, HD | `youtube_16x9_hd` | mp4, H.265, 4:2:0 | 1920x1080 | the timeline's | AAC 48 kHz stereo, 320 kbps | -14 LUFS | sidecar `.srt` |
| Social landscape 16:9 | `linkedin_16x9` | mp4, H.264, 8-bit 4:2:0 | 1920x1080 | the timeline's | AAC 48 kHz stereo, 320 kbps | -16 LUFS | sidecar `.srt` |
| Web embed | `substack_16x9` | mp4, H.264, 8-bit 4:2:0 | 1920x1080 | the timeline's | AAC 48 kHz stereo, 320 kbps | -16 LUFS | sidecar `.srt` |
| Social vertical 9:16 | `linkedin_9x16` | mp4, H.264, 8-bit 4:2:0 | 1080x1920 | the timeline's | AAC 48 kHz stereo, 320 kbps | -16 LUFS | burnt in |
| Social vertical 9:16, TikTok and Instagram | `social_9x16` | mp4, H.264, 8-bit 4:2:0 | 1080x1920 | the timeline's | AAC 48 kHz stereo, 320 kbps | -14 LUFS | burnt in |
| Social square 1:1 | `linkedin_1x1` | mp4, H.264, 8-bit 4:2:0 | 1080x1080 | the timeline's | AAC 48 kHz stereo, 320 kbps | -16 LUFS | burnt in |
| Archive master | `client_master` | mov, ProRes 422 HQ, 10-bit 4:2:2 | the timeline's | the timeline's | LPCM 24-bit 48 kHz | none, unnormalized | none |

Every value is the repo's own, copied from `resolve-config.json`. The practice delivers no broadcast masters, so the table has no broadcast row.

## What every row shares

These come from the house rules under `deliver` in the config and are checked on every destination that has a loudness target.

- **Frame rate** equals the timeline's, exactly. The accepted rates are 24000/1001, 24, 25, 30000/1001, 30, 50, 60000/1001 and 60. A render never changes the rate.
- **Colour**: Rec.709 primaries and matrix. The web classes tag the transfer as bt709 (`Rec.709-A` in Resolve). The archive master keeps Gamma 2.4, which writes the transfer unspecified, and the check reports it without asserting it.
- **Range**: limited. A `yuvj*` pixel format or a full-range flag fails.
- **Loudness**: integrated loudness within 0.5 LU of the target, true peak at or under -1.0 dBTP. The loudness fix runs on every render as the last step, except on the archive master.
- **Data Burn-in** is `None` on every deliverable. A review copy may carry a timecode burn-in; a deliverable never does.
- **File name** follows the house rule, and each destination renders into its own subfolder (see [the manifest](delivery-manifest.md#file-and-folder-names)).

## Notes by class

**Long-form video, UHD and HD.** The two keys differ only in size. Both are H.265 with no bit depth asserted, and the check expects an `hevc` stream at 4:2:0. The loudness target is -14 LUFS, the only class that uses it. Both keys are named for YouTube in the config.

**Social landscape 16:9.** The LinkedIn 16:9 destination. Its numbers match the web embed row. Captions are a sidecar `.srt`, where the two social shapes below burn them in.

**Web embed.** The row is the Substack destination, used here for a page that plays an mp4 inline. A client's own site may take a different player and a different container; check the host's current spec before a first delivery. H.264 at 8-bit 4:2:0 is the repo's choice for every web destination; whether a given host plays it is for the host's current spec to say. `NetworkOptimization` is on, so the file starts playing before it finishes loading.

**Social vertical 9:16.** Built from the timeline's 9:16 copy. `queue_render` refuses a 16:9 timeline for a 9:16 destination, because Resolve would scale it into the frame with bars and every check would still pass. The 9:16 copy comes from `reframe_plan` and `cut`, or `reframe_apply` for a planned timeline, which keep the subject's whole head with room above it. Captions are burnt in at 16 characters per line on up to two lines, because 42 characters overflows a 1080-wide frame. The 4:5 shape is planned by `reframe_plan` but has no destination, and none is added until a client asks.

**Social vertical 9:16, TikTok and Instagram.** The `social_9x16` destination: the same file shape as `linkedin_9x16` at -14 LUFS, true peak at or under -1.0 dBTP, so `deliver-fix-loudness --dest social_9x16` normalises to -14. Same timeline rule: it needs a 9:16 timeline, and captions burn in at 16 characters per line. What the platforms' own help pages state, read on 2026-10-20 (specs change; read them again before a first delivery):

- Instagram, [Reel size & aspect ratios](https://help.instagram.com/1038071743007909): an aspect ratio between 1.91:1 and 9:16, a minimum frame rate of 30 FPS and a minimum resolution of 720 pixels. 1080x1920 is the full 9:16 frame and meets the minimum.
- TikTok, [Making a post](https://www.tiktok.com/support/faq_detail?id=7581826684085606968) and [Unable to post videos](https://www.tiktok.com/support/faq_detail?id=7078299667292756485): neither states a resolution, codec, frame rate or loudness target. 1080x1920 H.264 is the repo's choice for a vertical web file, not a figure TikTok publishes there.
- Neither platform's help pages state a loudness target. -14 LUFS is the repo's choice, the same level as the YouTube destinations.

The frame rate stays the timeline's, as on every destination, so a 23.976, 24 or 25 fps timeline renders below Instagram's 30 FPS minimum, and `deliver_check` does not flag it. Set the timeline to 30 fps or higher before cutting a vertical meant for Instagram; the help page does not say what happens to a lower rate.

**Social square 1:1.** Same rules as vertical. Burnt-in captions run 24 characters per line on up to two lines.

**Archive master.** The client master. ProRes 422 HQ at the timeline's own size, LPCM 24-bit 48 kHz, no loudness normalization, no captions. The check expects a 10-bit 4:2:2 profile and takes `--size` to assert the timeline's size. It is the file the platform versions can be re-made from while RP holds it. RP deletes it with the finals under the [retention rule](return-package.md#what-rp-keeps-afterward), unless the client asks in writing for RP to keep it. Re-making a version ends with retention, and the client holds the master.

## Not recorded here

The repo states no platform limits on duration, file size or bitrate, and no recommended caption position or safe-area margin beyond the characters-per-line figures above. Platforms change these without notice, so none are recorded. `deliver_check` checks what `resolve-config.json` states and nothing more, with two rows that hold no published number. `video_bitrate` asserts a floor only when a destination's `expect` block sets `min_video_kbps`; none does, because no platform floor is published here, so the row skips. `safe_zone` takes the caption and super boxes passed with `--boxes` (`overlay-check` and `captionlint` report them) and fails any outside `rpresolve/safezone.py`'s working margins: 5% on each edge of a landscape or square frame, and 14% top, 35% bottom and 6% each side of a portrait one. Those margins are the repo's working defaults, not a platform's figures. Look the limits up at the time of queueing, and put what was found in the shipment's [manifest](delivery-manifest.md) as a note.
