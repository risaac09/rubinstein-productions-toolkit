# Platform Specs

> **Status:** operational, accepted 2026-10-16. The rows restate what the deliver tooling already enforces.

One row per destination key in `production/resolve-config.json`, and the config has eleven. A destination class is a kind of place a deliverable goes, and each row names the class its key belongs to. Where two keys share a class, each still gets its own row, because `queue_render` and `deliver_check` apply that key's numbers. A test in `production/tests/test_platform_specs.py` fails when a key in the config has no row here. The source of every value is the [Deliver section of the Resolve spec](resolve-template-spec.md#deliver) and the config file; this document does not replace either. If they disagree, the config wins.

## The table

| Destination class | Destination key | Container, video | Size | Frame rate | Audio | Loudness | Captions |
|---|---|---|---|---|---|---|---|
| Long-form video, UHD | `youtube_16x9` | mp4, H.265, 4:2:0 | 3840x2160 | the timeline's | AAC 48 kHz stereo, 320 kbps | -14 LUFS | sidecar `.srt` |
| Long-form video, HD | `youtube_16x9_hd` | mp4, H.265, 4:2:0 | 1920x1080 | the timeline's | AAC 48 kHz stereo, 320 kbps | -14 LUFS | sidecar `.srt` |
| Long-form video, UHD, music-only montage | `youtube_16x9_music` | mp4, H.265, 4:2:0 | 3840x2160 | the timeline's | AAC 48 kHz stereo, 320 kbps | -14 LUFS | none |
| Social landscape 16:9 | `linkedin_16x9` | mp4, H.264, 8-bit 4:2:0 | 1920x1080 | the timeline's | AAC 48 kHz stereo, 320 kbps | -16 LUFS | sidecar `.srt` |
| Web embed | `substack_16x9` | mp4, H.264, 8-bit 4:2:0 | 1920x1080 | the timeline's | AAC 48 kHz stereo, 320 kbps | -16 LUFS | sidecar `.srt` |
| Social vertical 9:16 | `linkedin_9x16` | mp4, H.264, 8-bit 4:2:0 | 1080x1920 | the timeline's | AAC 48 kHz stereo, 320 kbps | -16 LUFS | burnt in |
| Social vertical 9:16, TikTok and Instagram | `social_9x16` | mp4, H.264, 8-bit 4:2:0 | 1080x1920 | the timeline's | AAC 48 kHz stereo, 320 kbps | -14 LUFS | burnt in |
| Social vertical 9:16, music-only montage | `social_9x16_music` | mp4, H.264, 8-bit 4:2:0 | 1080x1920 | the timeline's | AAC 48 kHz stereo, 320 kbps | -14 LUFS | none |
| Social 4:5, TikTok and Instagram | `social_4x5` | mp4, H.264, 8-bit 4:2:0 | 1080x1350 | the timeline's | AAC 48 kHz stereo, 320 kbps | -14 LUFS | burnt in |
| Social square 1:1, TikTok and Instagram | `social_1x1` | mp4, H.264, 8-bit 4:2:0 | 1080x1080 | the timeline's | AAC 48 kHz stereo, 320 kbps | -14 LUFS | burnt in |
| Social square 1:1 | `linkedin_1x1` | mp4, H.264, 8-bit 4:2:0 | 1080x1080 | the timeline's | AAC 48 kHz stereo, 320 kbps | -16 LUFS | burnt in |
| Archive master | `client_master` | mov, ProRes 422 HQ, 10-bit 4:2:2 | the timeline's | the timeline's | LPCM 24-bit 48 kHz | none, unnormalized | none |

Every value is the repo's own, copied from `resolve-config.json`. The practice delivers no broadcast masters, so the table has no broadcast row.

## What every row shares

These come from the house rules under `deliver` in the config and are checked on every destination that has a loudness target.

- **Frame rate** equals the timeline's, exactly. The accepted rates are 24000/1001, 24, 25, 30000/1001, 30, 50, 60000/1001 and 60. A render never changes the rate.
- **Colour**: Rec.709 primaries and matrix. The web classes tag the transfer as bt709 (`Rec.709-A` in Resolve). The archive master keeps Gamma 2.4, which writes the transfer unspecified, and the check reports it without asserting it.
- **Range**: limited. A `yuvj*` pixel format or a full-range flag fails.
- **Loudness**: integrated loudness within 0.5 LU of the target, true peak at or under -1.5 dBTP, one ceiling for the deliver check, the loudness fix and the mix build (EBU Tech 3343 asks -2 dBTP for lossy chains and -1 for linear audio, Spotify asks under -1 and under -2 above -14 LUFS, Apple asks at least 1 dB and an AAC audition; -1.5 sits between them). The loudness fix aims `fix_true_peak_margin_db` (0.5 dB) under the ceiling, so -2.0 dBTP. The loudness fix runs on every render as the last step, except on the archive master.
- **Data Burn-in** is `None` on every deliverable. A review copy may carry a timecode burn-in; a deliverable never does.
- **File name** follows the house rule, and each destination renders into its own subfolder (see [the manifest](delivery-manifest.md#file-and-folder-names)).

## Notes by class

**Long-form video, UHD and HD.** The two keys differ only in size. Both are H.265 with no bit depth asserted, and the check expects an `hevc` stream at 4:2:0. The loudness target is -14 LUFS, which `social_9x16` also uses. Both keys are named for YouTube in the config.

**Social landscape 16:9.** The LinkedIn 16:9 destination. Its numbers match the web embed row. Captions are a sidecar `.srt`, where the two social shapes below burn them in.

**Web embed.** The row is the Substack destination, used here for a page that plays an mp4 inline. A client's own site may take a different player and a different container; check the host's current spec before a first delivery. H.264 at 8-bit 4:2:0 is the repo's choice for every web destination; whether a given host plays it is for the host's current spec to say. `NetworkOptimization` is on, so the file starts playing before it finishes loading.

**Social vertical 9:16.** Built from the timeline's 9:16 copy. `queue_render` refuses a 16:9 timeline for a 9:16 destination, because Resolve would scale it into the frame with bars and every check would still pass. The 9:16 copy comes from `reframe_plan` and `cut`, or `reframe_apply` for a planned timeline, which keep the subject's whole head with room above it. Captions are burnt in at 16 characters per line on up to two lines, because 42 characters overflows a 1080-wide frame. The 4:5 shape has its own destination, `social_4x5` (1080x1350, -14 LUFS, burnt-in captions at the portrait line length), added for VP-46; it needs a 4:5 timeline, which `reframe_plan` plans. It carries the same `min_fps` as `social_9x16`, and `social_1x1` below does too.

**Social vertical 9:16, TikTok and Instagram.** The `social_9x16` destination: the same file shape as `linkedin_9x16` at -14 LUFS, true peak at or under -1.5 dBTP, so `deliver-fix-loudness --dest social_9x16` normalises to -14. Same timeline rule: it needs a 9:16 timeline, and captions burn in at 16 characters per line. What the platforms' own help pages state, read on 2026-10-20 (specs change; read them again before a first delivery):

- Instagram, [Reel size & aspect ratios](https://help.instagram.com/1038071743007909): an aspect ratio between 1.91:1 and 9:16, a minimum frame rate of 30 FPS and a minimum resolution of 720 pixels. 1080x1920 is the full 9:16 frame and meets the minimum.
- TikTok, [Making a post](https://www.tiktok.com/support/faq_detail?id=7581826684085606968) and [Unable to post videos](https://www.tiktok.com/support/faq_detail?id=7078299667292756485): neither states a resolution, codec, frame rate or loudness target. 1080x1920 H.264 is the repo's choice for a vertical web file, not a figure TikTok publishes there.
- Neither platform's help pages state a loudness target. -14 LUFS is the repo's choice, the same level as the YouTube destinations.

The frame rate stays the timeline's, as on every destination, so a 23.976, 24 or 25 fps timeline renders below Instagram's 30 FPS minimum. The destination carries `min_fps` (30000/1001, so 29.97 passes): `deliver_check` gives a `fps_floor` WARN under it, SKIP on a destination with none, never FAIL, and a `queue_render` dry run adds a warning to its plan and still queues. Neither conforms the rate. Set the timeline to 30 fps or higher before cutting a vertical meant for Instagram; the help page does not say what happens to a lower rate.

**Social vertical 9:16, music-only montage.** The `social_9x16_music` destination is `social_9x16` for a montage with no speech, which is why it carries no captions and no frame rate floor: there is nothing to caption, and a 24 fps timeline is allowed. Its files take the montage name, `<Client>_<slug>_9x16.mp4`, since a montage has no show, guest or clip index. Use it only for music-only work. Anything with a voice belongs on `social_9x16`, whose burnt-in captions and `min_fps` warning stay as they were. Platforms that prefer 30 fps will resample a 24 fps file on upload; the destination does not conform the rate, and the resampling is the platform's. The timeline rule, the -14 LUFS target and the loudness fix are the same as for `social_9x16`. The destination also sets `tail_fade`: its music bed is baked with a one second fade out, and `deliver-check` fails a file whose last 100 ms of picture time (ending at the last video frame) sit less than 12 dB below the window 3 s to 1.5 s before that frame (`--expect-fade` asks for the same row on another destination).

**Long-form video, music-only montage.** `youtube_16x9_music` is `youtube_16x9` for a montage with no speech, the landscape twin of `social_9x16_music`: no captions (every other 16:9 destination wants a sidecar, so `queue_render` refuses a music-only timeline with no subtitle track, and auto captions on music either find nothing or caption the lyrics), the montage name `<Client>_<slug>_16x9.mp4`, and `tail_fade` for the baked music fade. Size, codec, audio and loudness are `youtube_16x9`'s. Anything with a voice stays on `youtube_16x9`.

**Social 4:5 and 1:1, TikTok and Instagram.** `social_4x5` (1080x1350) and `social_1x1` (1080x1080) are `social_9x16` in another frame: -14 LUFS, true peak at or under -1.5 dBTP, burnt-in captions (24 characters per line on the square), and `min_fps` 30000/1001 with the same `fps_floor` WARN. Instagram's Reels page states its aspect range as 1.91:1 to 9:16, so 4:5 (0.8) and 1:1 sit inside it; the page names the floor for reels in general, not per shape. Each destination needs a timeline of its own shape.

**Platform specs read for these destinations.** Every figure below is from the platform's own help page, read on 2026-11-21 (specs change; read them again before a first delivery). Anything a page does not state is "not stated" and the repo records no number for it.

- Instagram, [Reel size & aspect ratios](https://help.instagram.com/1038071743007909), read 2026-11-21. Stated: aspect ratio between 1.91:1 and 9:16; minimum frame rate 30 FPS; minimum resolution 720 pixels; for upload troubleshooting, video progressive scan (no interlacing), High profile, 2 consecutive B frames, closed GOP with a GOP of half the frame rate, CABAC, chroma subsampling 4:2:0; audio stereo or stereo + 5.1 at 96 kHz or 48 kHz; cover photo png or jpg, recommended 420x654. Not stated: maximum duration, maximum file size, container, bitrate, loudness. The page covers reels only; the help centre search for feed video aspect ratio (2026-11-21) found no other page that states a feed-video spec, so nothing here is claimed for feed posts.
- TikTok, [Making a post](https://www.tiktok.com/support/faq_detail?id=7581826684085606968) and [Unable to post videos](https://www.tiktok.com/support/faq_detail?id=7078299667292756485), read 2026-11-21. Not stated: resolution, aspect ratio, frame rate, duration, file size, container, codec, bitrate, loudness. The only number is a 35 photo limit for photo posts. No TikTok page was found that states a video spec, and third-party guides are not sources, so the destinations assert nothing on TikTok's behalf.

What the check does and does not cover against the Instagram list: size, 8-bit 4:2:0, H.264, AAC 48 kHz stereo and the 30 FPS floor (a WARN) are asserted. Progressive scan, High profile, B-frame count, GOP length and CABAC are not asserted: `expect.profile` stays null because Resolve's H.264 profile is unverified until a real render, and `deliver_check` has no scan or GOP row. A file Instagram's technical list would reject on those points still passes. The `sd-frontier` probe that re-reads these pages is still owed.

**Social square 1:1.** Same rules as vertical. Burnt-in captions run 24 characters per line on up to two lines.

**Archive master.** The client master. ProRes 422 HQ at the timeline's own size, LPCM 24-bit 48 kHz, no loudness normalization, no captions. The check expects a 10-bit 4:2:2 profile and takes `--size` to assert the timeline's size. It is the file the platform versions can be re-made from while RP holds it. RP deletes it with the finals under the [retention rule](return-package.md#what-rp-keeps-afterward), unless the client asks in writing for RP to keep it. Re-making a version ends with retention, and the client holds the master.

## Not recorded here

The repo states no platform limits on duration, file size or bitrate, and no recommended caption position or safe-area margin beyond the characters-per-line figures above. Platforms change these without notice, so none are recorded. `deliver_check` checks what `resolve-config.json` states and nothing more, with two rows that hold no published number. `video_bitrate` asserts a floor only when a destination's `expect` block sets `min_video_kbps`; none does, because no platform floor is published here, so the row skips. `safe_zone` takes the caption and super boxes passed with `--boxes` (`overlay-check` and `captionlint` report them) and fails any outside `rpresolve/safezone.py`'s working margins: 5% on each edge of a landscape or square frame, and 14% top, 35% bottom and 6% each side of a portrait one. Those margins are the repo's working defaults, not a platform's figures. Look the limits up at the time of queueing, and put what was found in the shipment's [manifest](delivery-manifest.md) as a note.
