# motion

Remotion project for graphics that sit over footage. One component today, the lower third, registered as three compositions. Work on it from the toolkit's `tools/video-editor/README.md`, which also has the alpha render recipe.

## Compositions

| Id | Size | Use |
|---|---|---|
| `LowerThird` | 3840 by 2160 | Landscape, the project default |
| `LowerThirdPortrait` | 2160 by 3840 | 9:16, raised above the vertcut caption bar |
| `LowerThirdSquare` | 2160 by 2160 | 1:1, same raised offset |

All three run at 24000/1001 fps (23.976) and last 76 frames, about 3.2 seconds: a slide in, a hold and a slide out, timed in seconds so the length stays the same at another frame rate.

## Props

| Prop | Default | Meaning |
|---|---|---|
| `name` | Isaac Rubinstein | Title line |
| `role` | Facilitator / Filmmaker | Subtitle line |
| `width` | The composition's own | Frame width in pixels |
| `height` | The composition's own | Frame height in pixels |
| `fps` | 24000/1001 | Frame rate. The frame count follows it. |

Geometry is a set of fractions of the frame, so any width and height scale cleanly. The fractions are listed in `production/design-tokens.md`, under the lower-third geometry. Colours and type in the component are still literals, not tokens.

## Commands

```console
npm ci               # install from the lockfile
npm run dev          # Remotion Studio
npx tsc --noEmit     # typecheck
npx remotion compositions src/index.ts
```

Render recipes, including the ProRes 4444 file with alpha, are in `tools/video-editor/README.md`. Rendered files go to `out/`, which git ignores.

## Dependency audit

On 2026-10-16 the project moved from Remotion 4.0.518 to 4.0.536 (every `@remotion` package and `remotion` together, as Remotion requires) and from `eslint` 9.19.0 to 9.39.5. That cleared the last 12 `npm audit` advisories, all in the lint chain: `braces` (GHSA-vfj7-8cjw-p6xm) through `fast-glob` and `typescript-eslint`, and `@eslint/plugin-kit` (GHSA-xffm-g5w8-qvg7). Both are patch or minor moves within the pinned lines; no `npm audit fix --force`. After the move `npm audit` reports 0, `npm run lint` passes, and `npx remotion bundle` and `npx remotion compositions` succeed. The render recipes in `tools/video-editor/README.md` were checked on 4.0.518 and not re-run.

Read Remotion's licence terms before a larger team uses it.
