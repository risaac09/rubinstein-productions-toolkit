# Platform Specs

> **Status:** operational, accepted 2026-10-16. The rows restate what the deliver tooling already enforces.

One row per destination key in `production/resolve-config.json`, and the config has thirteen. A destination class is a kind of place a deliverable goes, and each row names the class its key belongs to. Where two keys share a class, each still gets its own row, because `queue_render` and `deliver_check` apply that key's numbers. A test in `production/tests/test_platform_specs.py` fails when a key in the config has no row here. The source of every value is the [Deliver section of the Resolve spec](resolve-template-spec.md#deliver) and the config file; this document does not replace either. If they disagree, the config wins.

## The table

| Destination class | Destination key | Container, video | Size | Frame rate | Audio | Loudness | Captions |
|---|---|---|---|---|---|---|---|
| Long-form video, UHD | `youtube_16x9` | mp4, H.265, 4:2:0, 60 Mb/s | 3840x2160 | the timeline's | AAC 48 kHz stereo, 384 kbps | -14 LUFS | sidecar `.srt` |
| Long-form video, HD | `youtube_16x9_hd` | mp4, H.265, 4:2:0, 16 Mb/s | 1920x1080 | the timeline's | AAC 48 kHz stereo, 384 kbps | -14 LUFS | sidecar `.srt` |
| Long-form video, UHD, music-only montage | `youtube_16x9_music` | mp4, H.265, 4:2:0, 60 Mb/s | 3840x2160 | the timeline's | AAC 48 kHz stereo, 384 kbps | -14 LUFS | none |
| Social landscape 16:9 | `linkedin_16x9` | mp4, H.264, 8-bit 4:2:0 | 1920x1080 | the timeline's | AAC 48 kHz stereo, 320 kbps | -16 LUFS | sidecar `.srt` |
| Web embed | `substack_16x9` | mp4, H.264, 8-bit 4:2:0 | 1920x1080 | the timeline's | AAC 48 kHz stereo, 320 kbps | -16 LUFS | sidecar `.srt` |
| Social vertical 9:16 | `linkedin_9x16` | mp4, H.264, 8-bit 4:2:0 | 1080x1920 | the timeline's | AAC 48 kHz stereo, 320 kbps | -16 LUFS | burnt in |
| Social vertical 9:16, TikTok and Instagram | `social_9x16` | mp4, H.264 High, 8-bit 4:2:0, 16 Mb/s | 1080x1920 | the timeline's | AAC 48 kHz stereo, 320 kbps | -14 LUFS | burnt in |
| Social vertical 9:16, music-only montage | `social_9x16_music` | mp4, H.264 High, 8-bit 4:2:0, 16 Mb/s | 1080x1920 | the timeline's | AAC 48 kHz stereo, 320 kbps | -14 LUFS | none |
| Social 4:5, TikTok and Instagram | `social_4x5` | mp4, H.264 High, 8-bit 4:2:0, 16 Mb/s | 1080x1350 | the timeline's | AAC 48 kHz stereo, 320 kbps | -14 LUFS | burnt in |
| Social square 1:1, TikTok and Instagram | `social_1x1` | mp4, H.264 High, 8-bit 4:2:0, 16 Mb/s | 1080x1080 | the timeline's | AAC 48 kHz stereo, 320 kbps | -14 LUFS | burnt in |
| Short-form vertical, YouTube Shorts | `youtube_9x16` | mp4, H.265, 4:2:0, 60 Mb/s | 2160x3840 | the timeline's | AAC 48 kHz stereo, 384 kbps | -14 LUFS | burnt in |
| Social square 1:1 | `linkedin_1x1` | mp4, H.264, 8-bit 4:2:0 | 1080x1080 | the timeline's | AAC 48 kHz stereo, 320 kbps | -16 LUFS | burnt in |
| Archive master | `client_master` | mov, ProRes 422 HQ, 10-bit 4:2:2 | the timeline's | the timeline's | LPCM 24-bit 48 kHz | none, unnormalized | none |

Every value is the repo's own, copied from `resolve-config.json`. The video rate is Resolve's `VideoQuality` sent as a bit rate; a destination with none (LinkedIn, Substack, the master) still takes the Deliver page's. H.264 High is sent as `EncodingProfile` and not yet asserted by the check. The practice delivers no broadcast masters, so the table has no broadcast row.

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

## Platform assessment, read 2026-11-24

Read on the platforms' own pages unless marked third-party. Specs change; read them again before a first delivery.

| | YouTube long-form | YouTube Shorts | Instagram Reels | TikTok |
|---|---|---|---|---|
| Upload size | 2160p and 1440p are on the recommended list ([1]) | a square or vertical video up to 3 minutes is a Short ([2]); no size stated | at most 1920 columns on the publishing route ([3]); minimum 720 ([4]) | 360 to 4096 pixels per side ([5]) |
| Frame rate | the rate it was recorded at; 24, 25, 30, 48, 50, 60 and others ([1]) | as long-form | 23 to 60 ([3]); 30 minimum ([4]) | 23 to 60 ([5]) |
| Codec | H.264 High, CABAC, 2 B frames, closed GOP of half the rate, VBR, 4:2:0; mp4 with no edit lists and the moov atom first ([1]) | as long-form | HEVC or H.264, progressive, closed GOP, 4:2:0; no edit lists, moov first ([3]) | H.264 recommended, H.265, VP8, VP9 ([5]) |
| Upload bit rate | SDR 2160p 35 to 45 Mb/s at 24 to 30 fps, 53 to 68 at 48 to 60; 1080p 8 and 12 ([1]) | as long-form | VBR at most 25 Mb/s; file at most 300 MB; 3 s to 15 min ([3]) | file at most 4 GB; 3 minutes for every creator, 5 or 10 for some ([5]) |
| Audio | AAC-LC 48 kHz, stereo 384 kb/s ([1]) | as long-form | AAC at most 48 kHz, mono or stereo, 128 kb/s ([3]) | not stated |
| Colour | BT.709 primaries, transfer and matrix (H.273 value 1); an unspecified tag is read as BT.709; full range is converted to limited; BT.2020 without an HDR transfer becomes 8-bit BT.709 ([1]). HDR needs 10-bit Rec.2020 with PQ or HLG ([6]) | as long-form | not stated | not stated |
| Loudness | not published; -14 LUFS is the widely measured reference (third-party, [7]) | as long-form | not published | not published |
| 9:16 UI zone | none | not stated in figures (the ads spec page shows diagrams only, [8]) | Meta's figures not read; 14% top, 35% bottom, 6% sides is the third-party figure | not stated; 240 px top, 660 px bottom, 120 px sides of 1080x1920 is a third-party measurement |

[1]: https://support.google.com/youtube/answer/1722171
[2]: https://support.google.com/youtube/answer/15424877
[3]: https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media (Reel specifications, for a Reel published through the API; Instagram's help pages state no ceiling for the app's own upload)
[4]: https://help.instagram.com/1038071743007909
[5]: https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide (for a video sent through the API; TikTok's help pages state no video spec)
[6]: https://support.google.com/youtube/answer/7126552
[7]: https://productionadvice.co.uk/youtube-loudness/ (third-party)
[8]: https://support.google.com/google-ads/answer/13547298

**Render once at 4K, or at the platform's size.** One recommendation per platform:

- **YouTube long-form: render once at 4K** (`youtube_16x9`). YouTube makes its lower tiers from the upload, and its own page says a viewer needs VP9 to see a 4K upload in 4K. Whether that also lifts the 1080p tier is not stated; a forum test says it does (third-party). `youtube_16x9_hd` stays for a timeline that is HD at source.
- **YouTube Shorts: render once at 4K** (`youtube_9x16`, 2160x3840), for the same reason. Queue it from a 2160x3840 timeline: a smaller one is scaled up, and `queue_render` warns when the timeline is smaller than any destination's frame. What resolution Shorts plays back is not stated.
- **Instagram: render at its size**, 1080x1920 (`social_9x16` and the 4:5 and 1:1 shapes). A 2160-wide file is over Meta's 1920-column limit for a published Reel, and no page says the app keeps more than that. The scale-down is then Resolve's, on the graded picture, not the platform's.
- **TikTok: render at the platform's size**, the same `social_*` files. TikTok accepts 4K uploads but states no playback resolution, so a 4K file buys nothing that TikTok states. One file then serves Instagram and TikTok.

**What changed in the destinations.** No video bit rate was set before, so every job took the Deliver page's last value, Automatic included. The YouTube destinations now send 60 Mb/s at UHD and 16 Mb/s at HD, which clears YouTube's H.264 figures at either frame-rate band, and 384 kb/s audio through the loudness fix. The social destinations send 16 Mb/s and H.264 High: under Meta's 25 Mb/s, and 300 MB holds about 150 seconds at that rate. The check reports Meta's three ceilings and TikTok's three minutes as WARN rows (`video_bitrate_max`, `file_size`, `duration`, with one frame of slack on the duration), never FAIL, since each limit binds one upload route; `youtube_9x16` warns past the 3 minutes that make a Short. The YouTube destinations carry YouTube's lowest figure for their size as `min_video_kbps` (35 Mb/s UHD, 8 Mb/s HD), and `video_bitrate` now WARNs under it instead of failing: a VBR render of still pictures may sit low on its own, but one far under means the bit rate did not take. That `VideoQuality` is read in kb/s, as the Deliver page's "Restrict to" field is, is unverified until a live queue: the queue read-back warns when the job reports another value, and `video_bitrate` shows the rate ffprobe read on every destination. The 384 kb/s audio is the loudness fix's re-encode; Resolve's own AAC rate is not set, so a file is at 384 only after `deliver-fix-loudness`.

**What stays as it was.** Colour: Rec.709 primaries and matrix with the Rec.709-A transfer tag already write the three BT.709 values YouTube asks for, in limited range. The tag Apple's players and Safari read differently from Gamma 2.4 is why Rec.709-A was chosen on 2026-09-29; no platform page says how Instagram or TikTok read the tags. Loudness: -14 LUFS for YouTube, TikTok and Instagram, since none publishes a target. No HDR destination: every one is SDR. The safe-zone margins in `rpresolve/safezone.py` (14% top, 35% bottom, 6% sides on a portrait frame) match the third-party Meta figure; TikTok's third-party side margin is wider (11%), and Shorts' is not stated, so they are left for a ruling.

**Open: edit lists.** YouTube and Meta both ask for an mp4 with no edit lists. ffmpeg's mp4 muxer writes one per track by default, so every file the loudness fix remuxes carries them (seen 2026-11-24 on a test encode). Taking them out (`-use_editlist 0`) moves where B-frame video and primed AAC start, so it waits for a real run against a Resolve render; nothing here changes it yet.

## Not recorded here

No recommended caption position beyond the characters-per-line figures above, and no platform safe-area margin beyond `safezone.py`'s working defaults. `deliver_check` checks what `resolve-config.json` states and nothing more. `video_bitrate` asserts a floor only where a destination's `expect` block sets `min_video_kbps` (the YouTube ones, as a WARN); elsewhere the row skips and reports the rate. `safe_zone` takes the caption and super boxes passed with `--boxes` (`overlay-check` and `captionlint` report them) and fails any outside `rpresolve/safezone.py`'s working margins: 5% on each edge of a landscape or square frame, and 14% top, 35% bottom and 6% each side of a portrait one. Look the limits up again at the time of queueing, and put what was found in the shipment's [manifest](delivery-manifest.md) as a note.
