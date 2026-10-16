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

Read Remotion's licence terms before a larger team uses it.
