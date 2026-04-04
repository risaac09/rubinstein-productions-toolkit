# DaVinci Resolve Template — Personal Post-Production
*Phase B: General-Purpose Multi-Camera Template*

---

## Project Settings

- **Resolution:** 3840 × 2160 (UHD 4K) — all exports, no exceptions
- **Timeline framerate:** 23.976 fps (default) or 29.97 fps — set per project
- **Color science:** DaVinci YRGB Color Managed

---

## Camera Sources & Color Pipeline

Three cameras, three different log profiles. Each needs its own conversion path before the shared creative grade.

| Camera | Log Profile | Conversion | Notes |
|--------|-----------|------------|-------|
| **iPhone 15 Pro** | Apple Log | TheOneLUT (AppleLog → Arri709 V5, 33pt .cube) | Already in Powergrades folder |
| **Panasonic GH7** | V-Log L (or V-Log) | V-Log to Rec.709 LUT (Panasonic-supplied or custom) | GH7 shoots 5.7K Open Gate — may need to scale/crop to 4K timeline |
| **Panasonic GH5** | V-Log L | V-Log L to Rec.709 LUT (Panasonic-supplied or custom) | Different sensor / color response than GH7 — may need separate base correction |

### Node Structure (per clip)

```
Node 1: Source Conversion (camera-specific LUT)
  → iPhone: TheOneLUT
  → GH7: V-Log to Rec.709
  → GH5: V-Log L to Rec.709

Node 2: PowerGrade — Low Contrast (shared across all sources)

Node 3: Manual creative grade (per clip, as needed)
```

The key: Node 1 normalizes all three cameras to the same Rec.709 starting point. Node 2 applies the unified Low Contrast look. Node 3 is where you do per-clip work to match across cameras or push the creative grade.

### PowerGrades to Save in Resolve

- **Low Contrast** — the base creative look (already exists)
- **iPhone Source** — TheOneLUT as a saved node
- **GH7 Source** — V-Log conversion as a saved node
- **GH5 Source** — V-Log L conversion as a saved node

Tag each clip on import so you know which source node to apply.

---

## Branded Intro

- **Asset:** `say-why-intro-4k.png` (3840 × 2160)
- Marrow background, "Say Why" in Bone, amber accent line
- Optional — use only when the project calls for it
- This is a personal creative brand, not RP client delivery

---

## Subtitles

- **Font:** Inter 500
- **Color:** White or Bone (#f2ece4) on semi-transparent dark bar
- **Position:** Lower third, consistent
- **Source:** Import .srt, apply style

---

## Export Presets — Always 4K (3840 × 2160)

| Preset | Codec | Bitrate | Use |
|--------|-------|---------|-----|
| YouTube | H.265 | 40–60 Mbps | Primary web delivery |
| LinkedIn | H.264 | 20–30 Mbps | Social feed |
| Master | ProRes 422 HQ | — | Archive / highest quality |
| Instagram / Story | H.264 (1080×1920 crop) | 15–20 Mbps | Vertical reformat |

---

## Project Bin Structure

```
📁 Source
   📁 iPhone
   📁 GH7
   📁 GH5
   📁 Audio (external)
📁 Selects
📁 Timeline
📁 Graphics
   └── say-why-intro-4k.png
📁 Exports
```

Separate source bins by camera so you can batch-apply the correct source conversion node.

---

## Workflow

1. Import media → Source bins (separated by camera)
2. Tag clips by camera source
3. Pull selects
4. Build timeline
5. Apply camera-specific source conversion (Node 1) — batch per camera bin
6. Apply Low Contrast PowerGrade (Node 2) — all clips
7. Manual creative grade (Node 3) — per clip, match across cameras
8. Subtitles if needed (.srt import → branded style)
9. Audio work in Fairlight
10. Queue 4K export presets in Deliver page
11. Render

---

## Phase C (Future): CLI Automation

When volume justifies it, build Python scripts via DaVinci Resolve Scripting API:
- Auto-create project with bin structure
- Auto-sort media into camera bins (by metadata)
- Auto-apply source conversion nodes by camera tag
- Auto-apply Low Contrast PowerGrade
- Auto-queue render presets
- Subtitle import and style application

Resolve must be running — API connects to live instance, not headless.

---

## Open Questions

- Do you have a preferred V-Log / V-Log L → Rec.709 LUT for the GH7 and GH5, or are you using the Panasonic-supplied ones?
- Is the GH7 shooting in Open Gate (5.7K) that needs crop/scale, or are you already shooting in a 4K mode?
- Do you want an outro card or is the intro sufficient for personal work?

---

*Version 1.1 — April 2026*
