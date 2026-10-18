# motion

Remotion project for graphics that sit over footage. Two components: the lower third, registered as three compositions, and the Say Why intro, a still. Work on it from the toolkit's `tools/video-editor/README.md`, which also has the alpha render recipe.

## Compositions

| Id | Size | Use |
|---|---|---|
| `LowerThird` | 3840 by 2160 | Landscape, the project default |
| `LowerThirdPortrait` | 2160 by 3840 | 9:16, raised above the vertcut caption bar |
| `LowerThirdSquare` | 2160 by 2160 | 1:1, same raised offset |

`SayWhyIntro` is a still (3840 by 2160 by default; `width` and `height` props give any 16:9 size). See [Say Why intro](#say-why-intro).

The three lower thirds run at 24000/1001 fps (23.976) and last 76 frames, about 3.2 seconds: a slide in, a hold and a slide out, timed in seconds so the length stays the same at another frame rate.

## Props

| Prop | Default | Meaning |
|---|---|---|
| `name` | Isaac Rubinstein | Title line |
| `role` | Facilitator / Filmmaker | Subtitle line |
| `width` | The composition's own | Frame width in pixels |
| `height` | The composition's own | Frame height in pixels |
| `fps` | 24000/1001 | Frame rate. The frame count follows it. |
| `tokens` | Placeholders | Token values by token name: `color.marrow`, `color.amber`, `color.bone`, `color.text-on-dark`, `type.display`, `type.body`. Names and placeholders live in `src/tokens.ts`, shared by both components. Values come from the private brand context and never go into the repo. |

Geometry is a set of fractions of the frame, so any width and height scale cleanly. The fractions are listed in `production/design-tokens.md`, under the lower-third geometry. Colours and type come from the `tokens` prop. The section on tokens in `production/design-tokens.md` says what each one paints and where the values live.

## Say Why intro

```console
npx remotion still src/index.ts SayWhyIntro out/say-why-intro.png
npx remotion still src/index.ts SayWhyIntro out/intro-hd.png --props='{"width":1920,"height":1080}'
```

Layout follows the "Intro layout (say-why-intro)" table in `production/design-tokens.md`: `color.marrow` ground, "Say Why" in `type.display` and `color.bone`, a `color.amber` rule below. Only `tokens`, `width` and `height` are props.

Cap height and baseline are measured, not guessed: the component loads the display font, measures the height of "H" on a canvas, and sets the font size so the cap height is 9% of the frame. "Visual centre" is read as the middle of the cap-height band (descenders of "y" hang below it). The baseline is the band's lower edge; the rule sits 5% of the height under it.

Measured on a 3840 by 2160 still with the placeholder font (Helvetica) and the placeholder colours, from the PNG with Pillow (the "W" glyph for cap height and baseline, the amber pixels for the rule):

| Quantity | Proposed | Measured |
|---|---|---|
| Cap height, of frame height | 9% | 9.03% (195 px) |
| Centre of the cap band, of height | 46% | 46.00% |
| Rule width, of frame width | 16.67% | 16.67% |
| Rule thickness, of frame height | 0.4% | 0.37% (8 whole px; 8.64 asked, the antialiased edge row falls under the colour threshold) |
| Rule top below baseline, of height | 5% | 5.00% |
| Rule and words centred horizontally | 50% | rule 50.00%, ink of the words 50.08% (glyph side bearings) |

The numbers hold for any font, since the size comes from the measured cap ratio. The measured values sit within a pixel or two of the proposal: the size and position are set from the fractions, and the render is the check that the browser's font metrics did not drift. The still reads as intended: the wording sits just above the middle with the rule close beneath it.

## Commands

```console
npm ci               # install from the lockfile
npm run dev          # Remotion Studio
npx tsc --noEmit     # typecheck
npx remotion compositions src/index.ts
```

Render recipes, including the ProRes 4444 file with alpha, are in `tools/video-editor/README.md`. Rendered files go to `out/`, which git ignores.

## Dependency audit

On 2026-10-16 the project moved from Remotion 4.0.518 to 4.0.536 (every `@remotion` package and `remotion` together, as Remotion requires) and from `eslint` 9.19.0 to 9.39.5. That cleared the last 12 `npm audit` advisories, all in the lint chain: `braces` (GHSA-vfj7-8cjw-p6xm) through `fast-glob` and `typescript-eslint`, and `@eslint/plugin-kit` (GHSA-xffm-g5w8-qvg7). Both are patch or minor moves within the pinned lines; no `npm audit fix --force`. After the move `npm audit` reports 0, `npm run lint` passes, and `npx remotion bundle` and `npx remotion compositions` succeed. The alpha render recipe in `tools/video-editor/README.md` was re-run on 4.0.536 and gives the same file: ProRes 4444, 76 frames at 24000/1001, a transparent first frame and an opaque frame mid-hold.

Read Remotion's licence terms before a larger team uses it.
