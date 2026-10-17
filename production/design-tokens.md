# Design Tokens

> **Status:** draft, 2026-10-16. Token names, lower-third geometry, the caption standard and a look-book stub. The values behind the names (colours, typefaces, type sizes from the brand) live in the private brand context and are not published here.

A deliverable carries the brand through a small set of named tokens. A graphic, a caption or a title refers to a token by name and never to a literal colour or font. Each name resolves to a value in the private brand context, so a brand change is made once, in one place, and no value is copied into this repo. This document is the public side of that arrangement: the names, what each is for, and the geometry that places them in the frame.

## Tokens

| Token | Used for |
|---|---|
| `color.marrow` | The dark ground. Backgrounds of intros and title cards, the bar behind captions, the panel behind a lower third. |
| `color.bone` | The warm light neutral. Large type and marks on a Marrow ground, and the page colour of printed or document-style frames. |
| `color.amber` | The single accent. The lower-third bar and the accent line on the branded intro. Nothing else. |
| `color.text-on-dark` | Text that sits on `color.marrow` or on any dark bar. Captions and lower-third titles. |
| `type.display` | Titles: the lower-third name line, intro and title-card wording. |
| `type.body` | Secondary lines: the lower-third role line and any running text on a frame. |
| `type.caption` | Subtitles, burnt in or imported. |

No build step resolves the names yet. Until one does, a person reads each value from the private brand context when making a graphic, and nothing in this repo carries a literal in place of a name.

`color.amber` is the only token that marks emphasis. A second accent colour is a change to the token set, not a one-off.

## Lower-third geometry

The lower third is built in `tools/video-editor/motion/src/LowerThird.tsx` (Remotion). It has three compositions: landscape at 3840 by 2160, portrait at 2160 by 3840 and square at 2160 by 2160, each at 24000/1001 fps (23.976). Width, height and fps are props, so another size renders from the same fractions.

### Landscape

Horizontal measures are a fraction of frame width and vertical ones a fraction of frame height. The 1080p pixel value sits beside each, as the numbers were first set at 1920 by 1080; at UHD every pixel value doubles.

| Element | Measure | Fraction | At 1920 by 1080 | Token |
|---|---|---|---|---|
| Block | Left offset | 1/24 of width (4.17%) | 80 px | none |
| Block | Bottom offset | 1/9 of height (11.1%) | 120 px | none |
| Accent bar | Width | 0.31% of width | 6 px | `color.amber` |
| Accent bar | Height | 1/12 of height (8.33%) | 90 px | `color.amber` |
| Accent bar | Gap to the panel | 1.25% of width | 24 px | none |
| Panel | Horizontal padding | 1.67% of width | 32 px | `color.marrow` at 88% opacity |
| Panel | Vertical padding | 1.48% of height | 16 px | `color.marrow` at 88% opacity |
| Panel | Corner radius | 0.37% of height | 4 px | none |
| Title line | Size | 3.89% of height | 42 px, bold, line height 1.1 | `type.display`, `color.text-on-dark` |
| Subtitle line | Size | 2.22% of height | 24 px, regular, 1/6 of its size above | `type.body`, `color.text-on-dark` |
| Slide-in distance | Travel | 1/24 of width (4.17%) | 80 px | none |

The bar and the panel sit on one row, centred on each other. The panel grows around its text.

### Portrait and square

In a narrow frame a measure taken from the height would be far too large across the width, so every measure except the bottom offset is a fraction of frame width. Portrait and square share one set of fractions. The pixel values are for a frame 2160 wide.

| Element | Measure | Fraction | At 2160 wide | Token |
|---|---|---|---|---|
| Block | Left offset | 1/18 of width (5.56%) | 120 px | none |
| Block | Bottom offset | 26% of height | 998 px portrait, 562 px square | none |
| Accent bar | Width | 0.4% of width | 9 px | `color.amber` |
| Accent bar | Height | 7% of width | 151 px | `color.amber` |
| Accent bar | Gap to the panel | 1.6% of width | 35 px | none |
| Panel | Horizontal padding | 2.5% of width | 54 px | `color.marrow` at 88% opacity |
| Panel | Vertical padding | 1.2% of width | 26 px | `color.marrow` at 88% opacity |
| Panel | Corner radius | 0.3% of width | 6 px | none |
| Title line | Size | 4.4% of width | 95 px, bold, line height 1.1 | `type.display`, `color.text-on-dark` |
| Subtitle line | Size | 2.6% of width | 56 px, regular | `type.body`, `color.text-on-dark` |
| Slide-in distance | Travel | 5% of width | 108 px | none |

**Why the bottom offset is 26%.** vertcut puts the bottom edge of the caption bar at y 1680 of 1920, which is 12.5% of the height up from the bottom. A two-line cue at the default 66 px Arial Bold is 202 px tall (measured with Pillow, using vertcut's own height formula), so the bar spans from 23% to 12.5% of the height up from the bottom. The lower third's bottom edge at 26% clears that by 3% of the height. A different caption font changes the bar height, so recheck this if `type.caption` resolves to a taller face. vertcut makes portrait captions only; no repo file places a square caption, so the square frame takes the same 26% by analogy and has not been checked against a real square caption.

The portrait and square fractions were chosen for this table, not derived from a brand source. On 2026-10-16 a mid-hold frame of each was viewed full screen on a phone: both read, and the size is right. The square caption position is still unchecked against a real square caption.

### Motion

The block slides in from the left over 2/3 of a second, holds for 2 seconds and slides back out over 1/2 second, while fading and while the bar scales on its vertical axis from the bottom. That is 76 frames at 23.976. The timing is in seconds so the motion lasts the same at another frame rate. These numbers are the file's defaults, not a standard of their own.

**The file does not use the tokens yet.** `LowerThird.tsx` carries literal colours and a system sans-serif for both lines. Wiring it to the token names is an open follow-up: the geometry above is already the standard, the colour and type are not.

## Intro layout (`say-why-intro`)

The Resolve spec lists a branded intro as `say-why-intro-4k.png`. No image ships in this repo, since the file is a brand asset. This is the layout a person or a script builds it from, at 3840 by 2160. All measures are fractions, so any 16:9 size follows.

| Element | Rule | Fraction | Token |
|---|---|---|---|
| Ground | Fills the frame | all | `color.marrow` |
| Wording | The words "Say Why", centred horizontally, one line | Cap height 9% of frame height; the line's visual centre at 46% of the height down | `type.display`, `color.bone` |
| Accent line | A horizontal rule centred under the wording | Width 1/6 of frame width, thickness 0.4% of height, top edge 5% of height below the wording's baseline | `color.amber` |
| Margins | Nothing else on the frame | none | none |

The values in this table were drafted from the Resolve spec's description (a Marrow ground, the wording in Bone, an Amber accent line). The spec gives no sizes, so the fractions are a proposal until someone builds the image and looks at it.

## Caption standard

One caption style for every deliverable, taken from the Resolve spec's Subtitles section and the `deliver.captions` config:

- Text in `color.text-on-dark`, set in `type.caption`.
- On a semi-transparent bar in `color.marrow`. The spec says "semi-transparent" and states no opacity; the number is open (see below).
- Lower third of the frame, in the same place on every caption of a piece.
- Two lines at most for portrait and square, one line for landscape, as the table below sets.
- Line length by frame shape, as `create_captions` sets it from `deliver.captions` in `production/resolve-config.json`:

| Shape | Characters per line | Lines |
|---|---|---|
| Landscape | 42 | one |
| Portrait | 16 | up to two |
| Square | 24 | up to two |

Resolve reads the number as a ceiling and not a rule, and the style itself (colour, face, bar) is applied by hand in Resolve, since the scripting API does not set it. The character numbers are verified against `production/resolve-config.json` and the defaults in `production/rpresolve/config.py`, which agree; `create_captions` passes them to Resolve as the subtitle characters-per-line and line-break settings. See [Captions in the Resolve spec](resolve-template-spec.md#captions) for how the ceiling behaved on real renders.

### Bar opacity

The bar is `color.marrow` at 88% opacity, the number the lower-third panel already uses. `tools/vertcut` applies it in code. In Resolve the bar is set by hand, so use the same number there (decided 2026-10-16).

### vertcut and the standard

`tools/vertcut/lib/vertcut_cut.py` draws captions as Pillow PNG overlays on a 1080 by 1920 frame and follows the standard:

| | Standard | vertcut |
|---|---|---|
| Ground | `color.marrow` bar at 88% | A bar at `--bar-opacity` (0.88), in `--bar-color`; no outline. |
| Text colour and face | `color.text-on-dark`, `type.caption` | `--text-color` and `--font`, or a local untracked style file (`--caption-style`, else `tools/vertcut/captions.local.json`). Neutral white and Arial Bold when none is given; no brand value ships. |
| Line length and lines | 16 characters, two lines at most for portrait | `--line-chars` 16. A cue that would need a third line is split into more cues; a word longer than a line stops the run. |
| Position | Lower third, the same place on every cue | The bar's bottom edge at y 1680 of 1920 (`--caption-y`), so one-line and two-line cues share a baseline. |

Size stays `--font-size` (66 px); the type token's size is not yet published.

## Look book (stub)

One row per lutlab look in `production/lutlab-looks/`. The description column reads each look's JSON parameters. The reference frame column is empty until frames are made.

**Rule for reference frames.** A reference frame is either a synthetic colour chart or one frame that Isaac owns and chooses. Never client footage, so a look can be shown without a client's permission or a client's face. No images are in this PR.

| Look | What it does, from its parameters | Reference frame |
|---|---|---|
| RP Neutral (`neutral.json`) | Sets no parameters: the base transform as it is. | to add |
| RP Bleach (`bleach.json`) | Raises contrast to 1.2, pulls saturation to 0.58 overall (0.85 in the highlights), and adds a cool blue tone to the shadows. | to add |
| RP Ice Blue (`ice-blue.json`) | Cools the white balance by 24, tints shadows and highlights blue and cyan, drops saturation to 0.9 on most hues while lifting red and orange so skin holds, with a small contrast rise to 1.04. | to add |
| RP Print Teal (`print-teal.json`) | Raises contrast to 1.08 with a slightly lifted black, pushes greens toward teal, boosts teal, cyan and orange, and tones the shadows teal and the highlights yellow. | to add |
| RP Soft Print (`soft-print.json`) | Lowers contrast to 0.8 and saturation to 0.82, lifts the black, lowers the white and softens the highlight shoulder, shifts greens toward cyan, and tones the shadows cyan. | to add |
| RP Tungsten (`tungsten.json`) | Warms the white balance by 28, tones the deep shadows teal and the highlights golden, and raises saturation slightly to 1.03. | to add |
