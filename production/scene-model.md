# Scene model

> **Status:** draft. A reference and design note, 2026-11-24. The framing axis is built (`rpresolve/framing_rules.py`, written onto every crop `reframe-plan` plans). Light, colour, contrast and saturation are measured today in separate commands and are not yet joined into one per-shot record. That join is the follow-up.

This page does two jobs. It collects the working rules of cinematography, videography, composition, light, colour and editing, with what each rule can be measured as, what the toolkit already measures, and where it acts in Resolve. And it is the design note for a per-shot **scene model**: one record per shot, with named fields and units, that the shot-choice and story-order tools can read.

## How to read the tables

| Mark | Meaning |
|---|---|
| MEASURABLE | A number from a frame or clip decides it. |
| TASTE | A person decides. The toolkit may describe, never fail or move anything. |
| MEASURABLE + TASTE | The number is real; the threshold is a choice. The threshold lives in a named constant and a person owns it. |

Each row carries (a) the rule and a source, (b) how to measure it, (c) its mark, (d) what the toolkit measures today, with the function or field, and (e) where it acts in the Resolve lane. Acts-in codes:

- **API**: the scripting API, written by a toolkit tool on an ` [auto]` timeline (Zoom, Pan, Tilt through `reframe_apply`, a LUT through `apply_grade`; a CDL through the item's `SetCDL`, which the toolkit does not call yet).
- **SCREEN**: a step on the Color or Fusion page that a person or computer use performs (power window, qualifier, tracking, Magic Mask, ResolveFX).
- **ASK**: a `media-review-loop` question for the person, one stimulus, a lettered answer.

Sourcing rule for this page: a link is given only for a page that was read. A claim that rests on a book the author did not read, or on a page that could not be fetched, is marked **UNVERIFIED**. No number below is invented; a number the toolkit chose as a working default is marked **house default**.

## 1. Composition and framing

Sources read: [Video Production Handbook, composition](https://human.libretexts.org/Courses/City_College_of_San_Francisco/Video_Production_Handbook_I/08:_Video_Aesthetics/8.05:_Video_Composition), [shot sizes](https://human.libretexts.org/Bookshelves/Theater%2C_Film%2C_and_Storytelling/Video_Production_Handbook/08%3A_Video_Aesthetics/8.01%3A_Basic_Shot_Sizes), [StudioBinder on thirds](https://studiobinder.com/blog/what-is-the-rule-of-thirds), [StudioBinder on shot sizes](https://www.studiobinder.com/blog/types-of-camera-shots-sizes-in-film/), [Wikipedia on headroom](https://en.wikipedia.org/wiki/Headroom_(photographic_framing)). The book texts these pages cite (Millerson, Thompson, Brown, Mascelli) were not readable, so claims taken from them are secondhand.

| Rule | (a) What it says | (b) Measure | (c) | (d) Toolkit today | (e) Acts in |
|---|---|---|---|---|---|
| Thirds | Lines at 1/3 and 2/3 of each axis; a subject on a line or intersection reads as set, not centred. Convention, not law. | Subject centre as a share of frame width and height, distance in output px from the nearest line. | MEASURABLE + TASTE | `framing_rules.placement` gives `face_x`, `eye_line`, `third_px`. `composition.compose` gives `negative_space` and gaps for any subject box. | API (Pan, Tilt), ASK for a hero shot |
| Eye line near the upper third | Eyes at about 33 percent down. The headroom page gives 28 percent down as "a subtle lack of headroom" and cites a 1997 text; that is the only sourced tolerance. | `eye_line` = eye y over frame height; `third_px` = (eye y minus H/3) in output px. Eye y is an estimate: `EYE_DROP` = 0.2 face heights below Vision's box top. | MEASURABLE + TASTE (tolerance is unsourced beyond the 28 percent example) | `framing_rules.target`, `placement`; fields `eye_line`, `third_px`. | API |
| Headroom | Less as the shot tightens; an extreme close-up may cut the head. Broadcast adds a little for overscan (secondhand). No per-size fraction found: **UNVERIFIED**. | Gap from head top to frame top, in output px and as a share of frame height. Head top = Vision box top minus `HEAD_ABOVE` (0.6) face heights. | MEASURABLE + TASTE | `reframe.lift` keeps the whole head with room; `composition.compose` returns `headroom_px`. | API |
| Look room, nose room | Open space in front of a face turned from camera; too little feels cramped. No numeric amount found. The "face on the third line, two thirds ahead" form is the thirds rule applied sideways, so a derived rule. | Face centre x at W/3 looking right, 2W/3 looking left (`LOOK_ROOM`). Look from Vision yaw, `LOOK_YAW` = 0.35 rad. | MEASURABLE + TASTE | `framing_rules.look_of`, `target` (rule `look room`), field `look`. Yaw sign is not confirmed on a frame (`LOOK_CONFIRMED` false), so the report flags it. | API, ASK until the sign is confirmed |
| Lead room | A moving subject gets more space ahead than behind. | `lead_px`: the gap on the side the subject drifts toward. | MEASURABLE + TASTE | `composition.compose` `lead_px`; `move_type` sets hold, pan or track from drift (`DRIFT_PAN` 0.04, `DRIFT_TRACK` 0.15 of the frame). | API |
| Shot sizes | ECU to WS named by where the frame cuts the body. The sources disagree on the medium close-up (chest, sternum, waist). | Head height share of the output frame: (1 + `HEAD_ABOVE`) x face height x scale / out height, banded by `SHOTS`. Bands are derived from body proportions, not measured. | MEASURABLE (bands are a description) | `framing_rules.shot_size`; field `shot`. | API (Zoom) |
| Do not cut at joints | Avoid a frame edge at knee, elbow, hip, wrist, ankle. Sources: the shot sizes page (hip, knee); a national film archive's framing guide was a snippet only, so **UNVERIFIED**. | Needs a pose skeleton to find joints. Not measured. | MEASURABLE (with a body-pose model) | Nothing. `vision_person.swift` finds a person box, not joints. | ASK |
| Balance, visual weight | A principle in Brown's text (table of contents only read). No numeric form found: **UNVERIFIED**. | Proxy: centroid of saliency or luma against the frame centre, in share of width. | TASTE | `composition.subject_box` (saliency union), `negative_space`. | ASK |
| Negative space | No sourced numeric form: **UNVERIFIED**. | Share of the crop outside the subject box. | TASTE | `composition.compose` `negative_space` (describes only). | ASK |
| Edge distractions | A bright or cut-off object at the edge pulls the eye. No readable source; the thresholds are the toolkit's own. | Second face cut by the crop edge; edge band (4 percent of the crop) at least 1.6 times the crop's mean luma and at least 96 of 255. | MEASURABLE + TASTE (house default thresholds) | `framing_rules.cut_faces`, `bright_edges`; field `edges`. | API (move the crop), SCREEN (a window to darken a lamp) |
| Frame safe areas | Title safe and platform UI bands. No platform publishes organic margins; every figure is third-party ([Bundle.social](https://bundle.social/tools/safe-zone-checker) says so). | Boxes as fractions of the frame; 9:16 bands. | MEASURABLE (house default margins) | `safezone.PORTRAIT` (top 0.14, bottom 0.35, side 0.06), `TITLE_SAFE` 0.05; `qc` safe-zone row. | ASK for any title |
| True resolution of a crop | Not a taste rule: a crop that zooms past native pixels softens the delivery. | Stored columns under the crop / deliverable columns; margin = min over both axes. Below 1.0 is an upscale by 1 / margin. | MEASURABLE | `framing_rules.native`, `limits`; fields `native_px`, `deliver`, `margin`, `max_zoom`. | API |

## 2. Exposure and light

Sources read: [Wikipedia, lighting ratio](https://en.wikipedia.org/wiki/Lighting_ratio) (it states the American Society of Cinematographers definition, (key + fill) : fill, and cites two practitioner pages), [three-point lighting](https://en.wikipedia.org/wiki/Three-point_lighting), [high-key lighting](https://en.wikipedia.org/wiki/High-key_lighting). The Wikipedia pages are flagged as needing citations; treat them as pointers. Skin levels: [Larry Jordan on scopes](https://larryjordan.com/articles/learn-how-to-read-video-scopes/) and a [Pixflow guide](https://pixflow.net/blog/read-video-scopes-fast/) were returned by search; the Pixflow page gives 65 to 75 IRE for lighter skin and a VFX page 50 to 65 for darker skin, which disagree with each other and with a 60 to 70 figure that circulates. **No authoritative skin IRE target was found, so none is adopted.**

| Rule | (a) What it says | (b) Measure | (c) | (d) Toolkit today | (e) Acts in |
|---|---|---|---|---|---|
| Lighting ratio | (key + fill) : fill. One stop of difference is 2:1 by the source method and 3:1 by the additive method, so always say which. | Linear luma of the lit cheek over the shadow cheek, as a ratio and in stops = log2(ratio). | MEASURABLE | `measure.lighting_ratio`, `pooled_lighting`; report `lighting` (`stops`, `ratio`, `lit_side`, `name`). Withheld when the cheek clips or the file is tagged PQ, HLG, linear or log. | ASK (more or less contrast), SCREEN (a window to open a shadow) |
| Ratio names | Flat, classic, dramatic, extreme by ratio. | The name is a label from stops. | TASTE | `measure.RATIO_NAMES`, the toolkit's own labels, unsourced, gating nothing. **UNVERIFIED** as a named-text table. | ASK |
| High-key and low-key | High-key reduces the lighting ratio for a bright, even look; low-key keeps ratio and shadow high. No numeric split in the page read. | Median luma `p50` and the shadow share (below). A split such as p50 above 50 IRE is a proposal, not a source. | MEASURABLE + TASTE | `measure.luma_stats` (`p1`...`p99` in IRE), `shadow_classes` (`readable_pct`, `sculpted_pct`, `lost_pct`, `shadow_pct`). | ASK |
| Three-point roles | Key 30 to 45 degrees off axis and above; fill opposite and dimmer; back light separates. | Not measurable from a frame without a lit-subject model. The ratio above is the readable consequence. | TASTE | Nothing direct. | ASK |
| Motivated light | Light that seems to come from a source in the scene. No numeric form. | A person's call. | TASTE | Nothing. | ASK |
| Skin exposure | Expose skin by the cheek, not the whole frame. | Cheek L* (sRGB curve) and luma IRE of the cheek patch. | MEASURABLE (target is a choice) | `measure.skin_stats` `L_srgb`; `lutlab balance --target-L` (default 64) sets cheek L*; `rpstills audit` reads the same patch. | API (CDL, LUT), via `lutlab balance` output |
| Shadow detail | Shadows that hold texture survive compression; crushed ones do not. | Tile classes under 25 IRE: lost at or under 2 IRE, readable above 0.75 IRE rms detail. | MEASURABLE (house defaults) | `measure.shadow_classes`, `shadow_compare` (`shadow_vs_delivery`, 10 point limit). | ASK |

## 3. Contrast

Sources: [ITU-R BT.709 summary](https://en.wikipedia.org/wiki/Rec._709) (8-bit legal luma 16 to 235, chroma 16 to 240, white D65), [BT.1886 summary](https://en.wikipedia.org/wiki/Rec._1886) (gamma 2.4 reference display), [BT.2100 summary](https://en.wikipedia.org/wiki/Rec._2100) (10-bit narrow range: black 64, nominal peak 940, valid 4 to 1019; HLG nominal peak 1,000 nits; reference surround 5 nits). The primary ITU documents were not fetched; the numbers above are from the summaries and agree with the toolkit's own constants.

| Rule | (a) What it says | (b) Measure | (c) | (d) Toolkit today | (e) Acts in |
|---|---|---|---|---|---|
| Legal range | SDR video luma lives in 16 to 235 (8-bit); 10-bit equivalents are 64 to 940. | Share of active picture over Y 235, at it, at 252 and up, at 4 and under, under 16; Y min and max. | MEASURABLE | `measure.luma_shares`, `luma_pass`; `qc` colour row (warn above 1.0 percent over Y 235, fail above 0.1 percent at hard clip: house defaults, calibrated on the toolkit's own renders). | API (LUT, CDL), SCREEN |
| Black and white points | Black near the floor, white near the ceiling, unless the look says otherwise. | Luma `p1` and `p99` in IRE. | MEASURABLE + TASTE | `measure.luma_stats`; `perceive.luma_numbers`. | API (CDL slope and offset) |
| Contrast width | Wider display contrast reads punchy; narrower reads soft. | `log2(p99 linear / p1 linear)` on a 1000:1 display floor (`DISPLAY_BLACK` 0.001). | MEASURABLE + TASTE | `perceive.luma_numbers` `display_contrast_stops`; `compare` gives the delta to a reference. | API (CDL power), ASK |
| Crush and clip share | A little is a look; a lot is lost data. | Share of pixels at the top code or all channels at zero. | MEASURABLE + TASTE | `measure.clipping` (`high_pct`, `low_pct`), `highlights` (`clip_pct`, `tail_ire_per_pct`); `skin_sample` warns above 5 percent clip (`CLIP_MAX`). | API |
| Dynamic range of the camera | Set by the sensor and the log curve, not by the grade. | Stops between noise floor and clip, from a chart. | MEASURABLE | Nothing at frame level; camera notes are in `capture-standard.md`. | ASK |
| Transfer awareness | A number read under the wrong curve is wrong. | The file's transfer tag. | MEASURABLE | `measure.transfer_of`; ratios and shadow classes withheld for PQ, HLG, linear, log. | API (colour management) |

## 4. Colour

Sources: BT.709 primaries and D65 from the [BT.709 summary](https://en.wikipedia.org/wiki/Rec._709); the 123 degree skin line, which Boris FX documents as the "I" line of an NTSC vectorscope, and Kdenlive calls a rule of thumb ([Kdenlive on I and Q lines](https://docs.kdenlive.org/en/tips_and_tricks/scopes/vectorscope_i_and_q_lines.html)). Forum reports say aligning to the line can push skin green on an uncalibrated monitor, so it is a guide. Resolve's manual describes the vectorscope skin-tone indicator as a rough guide; the official manual page could not be fetched, so that wording is **UNVERIFIED**. Colour harmony: [colour scheme summary](https://en.wikipedia.org/wiki/Color_scheme) (analogous at 30 and 330 degrees, triadic 120 and 240, split-complementary 150 and 210; its warm and cool claims are tagged "citation needed").

| Rule | (a) What it says | (b) Measure | (c) | (d) Toolkit today | (e) Acts in |
|---|---|---|---|---|---|
| Neutral white balance | A white or grey object reads neutral. | Lab a*, b* of near-neutral midtones (chroma under 8, L* 15 to 95), both near 0. | MEASURABLE | `perceive.colour_numbers` `neutral_cast` (`a`, `b`, `pct_of_frame`); `lutlab balance` solves `mired` and `tint` on a neutral and writes a CDL. | API (CDL, LUT) |
| Skin-tone line | Skin hue sits near 123 degrees on the vectorscope, near the "I" line. | Median `vectorscope_angle` of cheek pixels; `hue_off_skin_line_deg` = hue minus 123. | MEASURABLE + TASTE (never push skin hue to a number: the house rule in `lutlab-balance.md`) | `measure.skin_stats` (`hue_deg`, `hue_off_skin_line_deg`, `lab`, `chroma`); `SKIN_LINE_DEG`. | ASK, SCREEN (a skin qualifier) |
| Colour contrast | Hue difference between subject and background separates them. | Palette hue spread; hue of the subject against the background palette, degrees. | MEASURABLE + TASTE | `perceive.palette` (k-means in Lab: `hex`, `lab`, `chroma`, `hue_deg`, `share_pct`). | ASK |
| Colour harmony | Complementary, analogous, triadic schemes. | Angular distance between the palette's dominant hues, matched to a scheme within a tolerance. | TASTE (the tolerance is a choice) | The palette gives the hues; no scheme matcher exists. | ASK |
| Shadow and highlight tint | Split toning: shadows and highlights carry different hues. | `hue_shift_shadows_vs_highlights_deg` over fixed bands (under 33, 33 to 66, 66 and over IRE). | MEASURABLE + TASTE | `perceive.band_colour`. | API (CDL per node), SCREEN |
| Colour management | Know the input space and the timeline space before any look. | Tags and the project's settings. | MEASURABLE | `detect`, `set_color_management`; `colormgmt.py`. | API |

## 5. Saturation

| Rule | (a) What it says | (b) Measure | (c) | (d) Toolkit today | (e) Acts in |
|---|---|---|---|---|---|
| Chroma level | Saturation reads by Lab chroma C* = sqrt(a*^2 + b*^2); higher is more vivid. | Median and 90th percentile C*; per band C*. | MEASURABLE + TASTE | `perceive.colour_numbers` `chroma.p50`, `p90`; `band_colour`; `compare` `chroma_p50_vs_ref_pct`. | API (CDL saturation, a 10th number from CDL 1.2) |
| Gamut limit | A BT.709 picture lives inside the BT.709 primaries; out-of-gamut values are clipped or illegal. | Share of pixels with a channel under 0 or over 1 after conversion; chroma at the legal 16 to 240 limit on Cb and Cr. | MEASURABLE | Partly: `qc` colour row reads luma limits only. Chroma limits and gamut excursions are not measured. | API (LUT, CST) |
| Skin saturation | Skin chroma stays inside the complexion's own range; do not push it. | `skin.chroma` against the hero shot. | MEASURABLE + TASTE | `measure.camera_match` `chroma_vs_hero_pct`, `skin_chroma`. | ASK |
| Look | How much colour the story wants. | None; a person's call. | TASTE | Looks live in `lutlab-looks/` and `stills-looks/`. | ASK |

CDL source: the ASC CDL is one formula per channel, out = (in x slope + offset) ^ power, with a tenth number (saturation) added in version 1.2 ([summary](https://en.wikipedia.org/wiki/ASC_CDL), which cites the Pines and Reisner 2009 specification; the specification itself was not fetched).

## 6. Continuity across a scene

| Rule | (a) What it says | (b) Measure | (c) | (d) Toolkit today | (e) Acts in |
|---|---|---|---|---|---|
| Exposure match | Shots of one scene sit at the same brightness unless the story moves. | Luma `p50` difference between shots, IRE; cheek L* difference. | MEASURABLE + TASTE | `measure.camera_match` pairs: `luma_p50_delta_ire`; `lutlab balance` brings every cheek L* to one target. | API (CDL) |
| Skin and colour match | The same face reads the same across cameras. | CIEDE2000 between skin Lab medians; a few units reads as a visible step. The visible threshold is not sourced here: **UNVERIFIED**. | MEASURABLE + TASTE | `camera_match` `skin_de2000`; `lutlab balance` set table (`d_E00`, `d_L`, `d_hue_deg`); `lutlab shotmatch` fits a CDL plus a residual LUT between two shots of one scene. | API (CDL, LUT), ASK |
| White balance match | Neutrals agree across shots. | `neutral_cast` a*, b* difference. | MEASURABLE | `perceive.compare` `neutral_cast_delta`; `lutlab balance` `mired`, `tint`. | API |
| Match to a reference | A grade matches a hero still. | Luma deltas, contrast delta, palette and skin CIEDE2000. | MEASURABLE + TASTE | `perceive.compare`, `targets` (`cine.targets`). | API, ASK |

## 7. Videography

| Rule | (a) What it says | (b) Measure | (c) | (d) Toolkit today | (e) Acts in |
|---|---|---|---|---|---|
| 180 degree shutter | Exposure time = (angle / 360) x frame interval; 180 degrees at 24 fps is 1/48 s ([shutter angle summary](https://en.wikipedia.org/wiki/Shutter_angle), which calls 180 degrees the normal setting). | From the camera's metadata: shutter angle or time, and frame rate. A cheap proxy is motion blur extent. | MEASURABLE | The house standard is in `capture-standard.md` (23.976 fps, 1/48 s, ND to expose). No per-clip read of the shutter is made. | ASK, capture-side |
| Frame rate and cadence | Do not mix rates inside a shoot; conform deliberately. | Probe `r_frame_rate` per clip. | MEASURABLE | `measure.probe`; `shotlist.conform_speed` (a 30 fps source conforms to 0.8, a 60 fps source to 0.4, at a 24 fps list); `qc` `social_min_fps`. | API (retime) |
| Stability | A handheld wobble that is not motivated reads as error. | Median jerk of the stabiliser's tracked shift, in px of a 320 px picture. | MEASURABLE + TASTE | `qc` stabiliser row (`stab_jerk_px` 0.3, house default); `stabilize` MCP tool. | API (stabilise) |
| Camera movement is motivated | A move should follow action or reveal something. | Move type from subject drift: hold, pan, track. Whether it is motivated is a reading of the scene. | MEASURABLE + TASTE | `composition.move_type`, `drift`; `scene.read` `motion` (mean frame difference, not optical flow). | ASK |
| Tilt and level | A horizon that leans reads as error unless meant. | Horizon angle. | MEASURABLE | Not measured. `cmd_tilt_check` tests Resolve's Pan and Tilt convention, which is another matter. | API |
| Sharpness | Focus decides what the shot is about. | Variance of the Laplacian over mean luma, in a thumbnail. | MEASURABLE | `footage_index/gate.py` `sharpness`; `scene.sharpness` `ratio`; `composition.summarise` `sharpness`. | ASK |

## 8. Edit and story grammar

Sources: [180-degree rule summary](https://en.wikipedia.org/wiki/180-degree_rule) (it cites Bordwell and Thompson, and others, and notes that effect on viewers is weakly supported by research); [cutting on action](https://en.wikipedia.org/wiki/Cutting_on_action); Murch's six criteria and weights, from several secondary pages ([StudioBinder](https://www.studiobinder.com/blog/walter-murch-rule-of-six/), [a course wiki](https://wikis.utexas.edu/display/rtf318/Murch%27s+Rule+of+Six)): emotion 51, story 23, rhythm 10, eye trace 7, plane of the screen 5, space 4. The weights are Murch's own rough figures, not measurements. The 30 degree rule is named but not explained on the page read: **UNVERIFIED**.

| Rule | (a) What it says | (b) Measure | (c) | (d) Toolkit today | (e) Acts in |
|---|---|---|---|---|---|
| 180 degree rule | Keep the camera on one side of the line between two subjects so screen directions hold. | Screen side of each subject per shot, flipping across a cut. Needs a subject-identity track. | MEASURABLE (hard) + TASTE (it is often broken on purpose) | `look` (Vision yaw) gives a facing per shot, unconfirmed in sign. No cut-pair check. | ASK |
| Wide to close progression | Establish, then come in; vary size from cut to cut. | Sequence of `shot` values; a jump over more than two bands. | MEASURABLE + TASTE | Roles in `edit-methods` slots (`establishing` to `closer`); variety rules (cosine under 0.80 to every earlier pick). `framing.shot` supplies the size. | ASK |
| Cut on action | Cut mid-movement so the action bridges the cut. | Cut time against motion peaks. | MEASURABLE + TASTE | `scene.read` `motion`; `beats.py` for music beats. No action detector. | ASK |
| Rhythm | Shot length follows content and music. | Average and median shot length, spread, trend; words per minute; pauses. | MEASURABLE + TASTE | `rhythm.py`, `pacing.py` (`wpm`, `pause_ratio`), `besttake` `cuts_per_min`. | ASK |
| Eye trace | The eye should land where the last shot's did. | Distance between the subject centres across a cut, in share of the frame. | MEASURABLE + TASTE | `composition.summarise` `centre` per span; no cut-pair comparison yet. | ASK |
| Match across a cut | Size, angle, colour and brightness change by a step the story can carry. | Differences in `shot`, `eye_line`, `luma_p50`, `skin_de2000`. | MEASURABLE + TASTE | The parts exist (above); the pairing does not. | ASK |

## Scene model

One record per shot, written next to the manifest as plain JSON, so any tool reads it and nothing in it needs Resolve. Framing is filled today. Every other block names an existing measure as its source and is empty until the follow-up joins it.

### Framing (built)

Names copied from `framing_rules.record` and `reframe.FRAMING_COLUMNS`:

| Field | Unit | From |
|---|---|---|
| `subject` | `face`, `saliency` or `hand` | `reframe` |
| `rule` | rule id: `face centre`, `centred`, `look room`, `as shot`, `saliency`, `hand` (`rule_text` carries the sentence) | `framing_rules.choose` |
| `shot` | `ECU`, `CU`, `MCU`, `MS`, `MLS`, `FS`, `WS`, or null | `shot_size` |
| `look` | `left`, `right`, or null | `look_of` |
| `eye_line` | share of frame height | `placement` |
| `third_px` | output px from the upper third | `placement` |
| `native_px` | `COLSxROWS` source px under the crop | `native` |
| `deliver` | `WxH` px | `deliver_of` |
| `margin` | ratio; 1.0 is native, below 1 an upscale of 1 / margin | `native` |
| `max_zoom` | zoom over fill that stays native | `limits` |
| `edges` | list of text flags | `cut_faces`, `bright_edges` |

The record also holds `face_x` (face centre as a share of width) and a `native` flag; the table in the report leaves them out.

### Light (proposed)

| Field | Unit | Source |
|---|---|---|
| `light.ratio_stops` | stops, log2 of lit over shadow cheek | `measure.pooled_lighting` `stops` |
| `light.ratio` | (key + fill) : fill | `pooled_lighting` `ratio` |
| `light.lit_side` | `left` or `right` | `pooled_lighting` |
| `light.key` | `high`, `mid`, `low` from `luma.p50` | derived; thresholds are house defaults, a person's to set |
| `light.skin_l_srgb` | L* | `skin_stats` `L_srgb` |
| `light.shadow_readable_pct`, `light.shadow_lost_pct` | percent of tiles | `shadow_classes` |
| `light.withheld` | text or null | `measure.transfer_of` |

### Colour (proposed)

| Field | Unit | Source |
|---|---|---|
| `colour.cast_a`, `colour.cast_b` | Lab a*, b* of near-neutrals | `perceive.colour_numbers` `neutral_cast` |
| `colour.skin_hue_deg`, `colour.skin_off_line_deg` | degrees on the vectorscope | `skin_stats` `hue_deg`, `hue_off_skin_line_deg` |
| `colour.skin_lab` | Lab | `skin_stats` `lab` |
| `colour.palette` | up to 8 of `{lab, hue_deg, share_pct}` | `perceive.palette` |
| `colour.tint_shift_deg` | degrees, shadows minus highlights | `perceive.band_colour` |
| `colour.input_space` | text | `detect` |

### Contrast (proposed)

| Field | Unit | Source |
|---|---|---|
| `contrast.ire_p1`, `ire_p50`, `ire_p99` | IRE | `measure.luma_stats` |
| `contrast.stops` | stops on a 1000:1 floor | `perceive.luma_numbers` |
| `contrast.over_235_pct`, `contrast.hard_high_pct`, `contrast.hard_low_pct`, `contrast.under_16_pct` | percent of active picture | `measure.luma_shares` |
| `contrast.tail_ire_per_pct` | IRE per percentile point | `measure.highlights` |

### Saturation (proposed)

| Field | Unit | Source |
|---|---|---|
| `saturation.chroma_p50`, `saturation.chroma_p90` | Lab C* | `perceive.colour_numbers` |
| `saturation.skin_chroma` | Lab C* | `skin_stats` `chroma` |
| `saturation.gamut_out_pct` | percent of pixels outside 0 to 1 | not measured yet |

Every block carries `withheld` when its source withheld a number (a log or HDR transfer, a clipped cheek, no face). A missing number is null with a reason, never zero.

## How the axes feed shot choice and story order

### Shot choice (`selects`, `besttake`, the footage index)

Today `selects` proposes spans inside approved text (`cutlist.selects`) and `besttake` ranks takes on sound, with `face_share` and `cuts_per_min` as its only picture terms. The scene model adds picture terms that rank takes of one moment against each other.

1. **Reject, do not rank, on lost data.** A take whose `contrast.hard_high_pct` or `hard_low_pct` exceeds the `qc` fail limit, or whose `light.skin_l_srgb` sits under a floor on a clipped cheek, drops out first. These are numbers, so the rule is safe to automate.
2. **Prefer the take a grade can reach.** Among takes of one scene, rank by distance from the hero shot: `skin_de2000` and `luma_p50_delta_ire` (`camera_match`). The smaller the correction, the less `lutlab balance` has to push.
3. **Prefer the take that already frames.** Rank by `margin` and `max_zoom` for the delivery shape: a take native at fill is free to reframe, a take below native costs resolution.
4. **Break ties on sharpness and edge flags.** `scene.sharpness` ratio up, `edges` empty.
5. **Weights are a taste table.** `besttake` already keeps `RANKING` as a table of good, bad and weight; the picture terms join that table so a person can see and change them. A person reads the top two.

### Story order (`shotlist`, `edit-methods`)

1. **Roles fill from `shot`.** A slot role maps to a shot-size band (`establishing` to WS, `face` to CU and MCU, `detail` to ECU, `closer` to WS or MCU). The framing field replaces the image probe where a face is found.
2. **Progression is checked, not forced.** `shotlist check` can warn when two neighbours jump more than two `shot` bands, or repeat one size three times. A warning names the cut and the two values.
3. **Match across a cut.** For neighbours in one scene, `light.key` and `colour.skin_hue_deg` should agree within a house tolerance; a mismatch is a note for `trim_review`, not a failure.
4. **Rhythm stays with `rhythm.py` and `pacing.py`.** The scene model adds no tempo rule. Cut-on-action and the 180 degree rule are asked of a person with the two shots side by side.
5. **Variety and continuity pull apart.** `edit-methods` wants unlike picks (cosine under 0.80); continuity wants like ones. A method states which it wants, as the one-protagonist methods already do.

## Follow-ups

One line per axis: what to build, where, and what stays a person's.

| Axis | Build | Where | Stays with the person |
|---|---|---|---|
| Framing | Write `framing` blocks into one `scene.json` per timeline, not only the reframe report; confirm the yaw sign on a frame; a joints check if a pose model is added. | `reframe.py`, `scene-cli.md` | Thirds tolerance, balance, negative space, whether a flagged edge matters. |
| Light | Join `measure` lighting and shadow numbers into the record; add a `light.key` band with a proposed threshold and a review question. | `measure.py`, a new `scenemodel.py` | The look: high-key or low-key, the ratio, motivated light. |
| Colour | Join `perceive` cast, palette and band tint; add a hue-distance function for the palette; look up a source for any harmony tolerance before coding one. | `perceive.py` | Harmony, split toning, the skin hue (never pushed to a number). |
| Contrast | Join `luma_shares` and `luma_numbers`; add a per-shot match delta between neighbours. | `measure.py`, `qc.py` | Black and white points that set the look. |
| Saturation | Add a gamut-excursion and Cb, Cr limit measure; add it to the `qc` colour row. | `measure.py`, `qc.py` | How much colour the story wants. |
| Continuity | A pairwise report across a scene's shots, reusing `camera_match`, written as a `trim_review` note. | `measure.py`, `trimreview.py` | The tolerance a scene can carry. |
| Videography | Read shutter angle and frame rate from camera metadata into the record; a horizon-angle measure. | `measure.probe`, `detect.py` | Whether a move is motivated. |
| Edit grammar | A cut-pair check for shot-size jumps and screen-side flips in `shotlist check`. | `shotlist.py` | The 180 degree rule when broken on purpose; cut on action; rhythm. |
| Resolve | A CDL writer that applies `lutlab balance` output through the item's `SetCDL` on an ` [auto]` timeline, with read-back; the toolkit uses a LUT today. Confirm the call against the installed scripting documentation first. | `workflows.py`, `api.py` | Qualifiers, power windows and Magic Mask for secondaries: a screen step. |

## Sourcing gaps

- Book texts (Millerson, Thompson, Brown, Mascelli, Murch in full) were not readable; claims drawn from them are secondhand and marked.
- The Resolve reference manual and the ASC CDL specification were not fetched; the CDL formula and the vectorscope skin indicator rest on secondary pages.
- No authoritative skin IRE target, per-size headroom fraction, negative-space measure or visible CIEDE2000 threshold was found.
- The primary ITU documents (BT.709, BT.1886, BT.2100) were read only through summaries.
