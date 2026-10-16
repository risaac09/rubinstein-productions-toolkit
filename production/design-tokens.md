# Design Tokens

> **Status:** draft, 2026-10-16. Token names, lower-third geometry, the caption standard and a look-book stub. The values behind the names (colours, typefaces, type sizes from the brand) live in the private brand context and are not published here.

A deliverable carries the brand through a small set of named tokens. A graphic, a caption or a title refers to a token by name and never to a literal colour or font. The build step resolves each name to a value from the private brand context, so a brand change is made once, in one place, and no value is copied into this repo. This document is the public side of that arrangement: the names, what each is for, and the geometry that places them in the frame.

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

Values are resolved at build time from the private brand context. If a name has no value there, the build stops and does not fall back to a literal.

`color.amber` is the only token that marks emphasis. A second accent colour is a change to the token set, not a one-off.

## Lower-third geometry

The lower third is built in `tools/video-editor/motion/src/LowerThird.tsx` (Remotion, 1920 by 1080). Geometry is stated as a fraction of frame width for horizontal measures and of frame height for vertical ones, with the 1080p pixel value beside it. A render at another frame size scales the fractions, not the pixels.

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
| Subtitle line | Size | 2.22% of height | 24 px, regular, 4 px above | `type.body`, `color.text-on-dark` |

The bar and the panel sit on one row, centred on each other. The bar is a fixed 90 px tall and the panel grows around its text.

Motion: the block slides 80 px in from the left over 20 frames, holds for 60, and slides 80 px back out over 15 frames, 95 frames in all, while fading and while the bar scales on its vertical axis from the bottom. Those numbers are the file's defaults, not a standard of their own.

**The file does not use the tokens yet.** `LowerThird.tsx` carries literal colours and a system sans-serif for both lines. Wiring it to the token names is an open follow-up: the geometry above is already the standard, the colour and type are not.

## Caption standard

One caption style for every deliverable, taken from the Resolve spec's Subtitles section and the `deliver.captions` config:

- Text in `color.text-on-dark`, set in `type.caption`.
- On a semi-transparent bar in `color.marrow`. The spec says "semi-transparent" and states no opacity; the number is open (see below).
- Lower third of the frame, in the same place on every caption of a piece.
- Two lines at most.
- Line length by frame shape, as `create_captions` sets it from `deliver.captions` in `production/resolve-config.json`:

| Shape | Characters per line | Lines |
|---|---|---|
| Landscape | 42 | one |
| Portrait | 16 | up to two |
| Square | 24 | up to two |

Resolve reads the number as a ceiling and not a rule, and the style itself (colour, face, bar) is applied by hand in Resolve, since the scripting API does not set it. The character numbers are verified against `production/resolve-config.json` and the defaults in `production/rpresolve/config.py`, which agree; `create_captions` passes them to Resolve as the subtitle characters-per-line and line-break settings. See [Captions in the Resolve spec](resolve-template-spec.md#captions) for how the ceiling behaved on real renders.

### How vertcut differs today

`tools/vertcut/lib/vertcut_cut.py` draws captions as Pillow PNG overlays on a 1080 by 1920 frame. Against the standard:

| | Standard | vertcut today |
|---|---|---|
| Ground | Semi-transparent `color.marrow` bar | No bar. White text with a black outline, stroke width one eleventh of the font size, at least 4 px. |
| Text colour | `color.text-on-dark` | Literal white. |
| Typeface | `type.caption` | Arial Bold from the macOS system folder by default; `--font` overrides it. |
| Size | By token | 66 px by default (3.4% of the 1920 height); `--font-size` overrides it. |
| Line length | 16 characters per line for portrait | A cue holds at most 3 words and 26 characters (`--max-words`, `--max-chars`); lines wrap by pixel width within the frame width less 140 px. |
| Lines | Two at most | Not capped in code. |
| Position | Lower third, fixed | The top of the cue image sits 1400 px down the 1920 px frame (72.9%), centred (`--caption-y`). |

### Open: bring vertcut into line

A follow-up change to vertcut, not made in this PR:

1. Draw a `color.marrow` bar at partial opacity behind each cue and drop the outline.
2. Set the cue text in `color.text-on-dark` and the face from `type.caption`, resolved from the private brand context when the tool runs. vertcut is a public tool, so it takes the resolved values as options or from a local file, and ships none.
3. Wrap by the portrait standard, 16 characters a line and two lines, and refuse or re-group a cue that will not fit.
4. Keep `--caption-y` as an override, with the default moved to the standard's lower-third position.
5. Decide the bar opacity once, here, and use it in both Resolve and vertcut.

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
