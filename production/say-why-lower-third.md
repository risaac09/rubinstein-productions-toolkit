# Say Why lower third (OGraf)

> **Status:** draft, 2026-10-23; placed and viewed in Resolve 21.1.1.10. A native OGraf template for a name and role lower third, with an optional title line. Resolve 21.1 hosts it on the Edit page; `ograf-render` checks it offline. Files: `tools/ograf-render/templates/say-why-lower-third/`.

## What it is

One template, two shapes. A landscape frame (1920x1080) puts the plate bottom-left, its bottom edge 12% up. A portrait frame (1080x1920) puts it across the width with the bottom edge 26% up, above the band that vertical platforms cover. The layout reads the frame size, so the same package serves both. Colours: ink plate `#1a1412`, name `#faf8f5`, role `#a89e93`, accent `#c8923a` (rule and title line). Type: EB Garamond for the name, IBM Plex Mono capitals for role and title, with Georgia and Menlo behind them. A missing face falls back; the geometry does not move, because each line has a fixed box and clips at the plate edge.

## Fields

| Field | Inspector label | Default | Notes |
|---|---|---|---|
| `name` | Name | Alex Example | Required. One line, clipped to the plate. |
| `role` | Role | Guest | One line. |
| `title` | Title line (optional) | empty | Empty hides the line and the plate gets shorter. Set it for the title variant. |
| `accentColor` | Accent Color | `#c8923a` | Lowercase `#rrggbb`. |

## Motion

The spec is `motion.json` beside the template; `python3 -m rpresolve.motionlint tools/ograf-render/templates/say-why-lower-third/motion.json` (from `production/`) passes it. Three curves, all inside the signed bans (no elastic, bounce or back, no control point outside 0 to 1): `out` (0.16, 1, 0.3, 1), `exhale` (0.45, 0, 0.2, 1), `inOut` (0.65, 0, 0.35, 1).

| Segment | Frames at 23.976 | What moves |
|---|---|---|
| build | 17 (700 ms play) | The rule grows (`out`, 480 ms), the plate fades in (`inOut`, 300 ms), title, name and role rise 14 px and fade in (`out`, 360 ms, 80 ms apart). |
| hold | 90 | Nothing moves. Holds until the stop, wherever the clip is trimmed. |
| resolve | 13 (500 ms stop) | The rule retracts (`exhale`) and everything fades (`inOut`). The last frame is empty. |

The template reads the action schedule on every seek, so trimming the clip moves the stop with it, and a value edited in the Inspector (a scheduled `updateAction`) applies from its time. Every frame is a pure function of the timestamp; no timers, no randomness.

## Checks

Run these before a change lands. `node --test tools/ograf-render/test-say-why-lower-third.mjs` runs all but the first (CI runs it).

```bash
python3 "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/OGraf HTML Templates/Scripts/lint-ograf.py" tools/ograf-render/templates/say-why-lower-third
node tools/ograf-render/ograf-render.mjs tools/ograf-render/templates/say-why-lower-third/say-why-lower-third.ograf.json --out out/say-why --data '{"name":"Alex Example","role":"Guest","title":"Episode title"}'
python3 production/resolve_workflow.py overlay-check out/say-why/say-why-lower-third.mov --fps 24000/1001
```

The test renders three data sets (name and role; with a title; overlong text) at landscape and portrait, decodes each ProRes 4444 master, and asserts: 120 frames with alpha; the first and last frame fully clear, so no partial-opacity frame at a cut; plate, rule and text follow the spec's curves frame by frame within 0.02; no overshoot; every visible pixel inside the safe area (5% landscape; 6% sides and 74% down portrait); text contrast at least 7:1 against the plate, which hides a light or a dark background behind it; identical pixels on a second run.

## Placing it in Resolve

Tested in Resolve Studio 21.1.1.10 on 2026-10-23.

1. Copy `say-why-lower-third.ograf.json` and `say-why-lower-third.js` into `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Templates/Edit/Titles/OGraf/`.
2. Restart Resolve. A running Resolve does not see a new template: before the restart `InsertFusionTitleIntoTimeline` returned nothing.
3. It lists on the Edit page under Effects, Toolbox, Titles, OGraf as `say-why-lower-third`.
4. By script, the title name is the manifest file name, `say-why-lower-third.ograf.json`: `timeline.InsertFusionTitleIntoTimeline("say-why-lower-third.ograf.json")` returns a 125-frame item on a 25 fps timeline (the 5 s default). The name `say-why-lower-third`, the template's display name and its id all fail.
5. The clip holds one Fusion tool, an OGrafLoader. Its text fields are `DynParamText0` (name), `DynParamText1` (role), `DynParamText2` (title) and the colour is `DynParamColor3_R/G/B/A` (accent): the Nth manifest property is slot N of its kind. `tool.SetInput("DynParamText0", "Alex Example")` set the fields and the Inspector and viewer showed them, with the plate, rule and three lines drawn at the expected place.

Resolve builds at most 20 property controls per clip; this template uses four. For distribution, package a `.drfx` with Resolve's `Scripts/prepare-drfx.py`.

## Against the hyperframes path

| | OGraf in Resolve | Hyperframes |
|---|---|---|
| Render step | None; Resolve plays it natively and exports with the timeline | A render to a ProRes 4444 movie, then an import |
| Editing a name | Inspector field, on the clip | Change the source and render again |
| Checked by | `lint-ograf.py`, `ograf-render`, this test | `overlay-check` on the render |
| Fits | Lower thirds, titles and other text overlays on an Edit timeline | Generated explainer video and motion pieces |

`ograf-render` also exports the template as a movie for a place that cannot host OGraf.

## Limits

- Fonts must be installed on the machine that renders. Without them the fallbacks draw, with the same layout.
- The portrait band (26% up) follows the toolkit's earlier lower third; check it against the platform's current safe-zone guidance before a vertical delivery.
