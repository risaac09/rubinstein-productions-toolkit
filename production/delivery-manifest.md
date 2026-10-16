# Delivery Manifest

> **Status:** accepted 2026-10-16. A template and the rules for filling it, written to match the deliver tooling's naming rule and checks. No tool writes a manifest; a person or an agent fills it from `deliver_check` output after the last render. A tool is worth revisiting after three real deliveries, once a hand-filled manifest shows which columns matter.

A manifest is the list of what was delivered for one piece of work: every file, what it is, where it went and which approval it carries. It is written once, after the [final-approval gate](resolve-template-spec.md#the-final-approval-gate) and after the last `deliver_check` passes, and it travels with the delivery. A client who opens it should be able to confirm that each file is the one that was approved, and an auditor six months on should be able to tell the same.

The manifest covers produced deliverables. It does not cover the return of raw footage and data to the client; that return is set out in the [return package](return-package.md).

## File and folder names

The names are the house rule from the [Deliver section](resolve-template-spec.md#file-names), validated by `queue_render` and again by `deliver_check`:

```
SW001_Guest_01_example-clip_16x9.mp4      show code, episode, guest, clip, slug, aspect
Client_example-slug_master.mov            client master
```

Each destination renders into its own subfolder of the delivery folder, named by the destination key, so one clip delivered to several places keeps one name per aspect and never collides. The sidecar `.srt` of a sidecar destination shares the video's stem and sits beside it.

The manifest lives at the root of the delivery folder, beside the destination subfolders and not inside one. `deliver_check` reports a file in a folder no destination owns as SKIP on its folder row, which is the right result for the manifest. The manifest file name is `manifest.md`. Delivery folders never sit inside a git working tree; the queue refuses it.

## The template

Copy this block, fill it in, and keep it with the delivery.

```markdown
# Delivery manifest: <project slug>

| | |
|---|---|
| Project | <slug, as used in the file names> |
| Delivered | <date> |
| Prepared by | <name and role> |
| Approval reference | <id of the approval record; see the gate> |
| Machine-assisted steps | <list; see credits-and-disclosure.md> |
| Credits | <where the credits line appears, or "none requested"> |
| Platform notes | <limits looked up at queue time, or "none looked up"> |

## Files

| File | Format | Codec and container | Resolution | Frame rate | Duration | Loudness | Checksum | Destination | Approval reference |
|---|---|---|---|---|---|---|---|---|---|
| <name by the house rule> | <container> | <video codec, pixel format> | <WxH> | <exact rate> | <hh:mm:ss.ff> | <integrated LUFS, true peak dBTP> | <sha256> | <destination key> | <id> |
```

One row per file. A sidecar caption file gets its own row: format `srt`, codec and resolution `n/a`, loudness `n/a`, and the same destination and approval reference as its video.

## How to fill each column

| Column | Source | Notes |
|---|---|---|
| File | The file name on disk | Must pass the name rule. A name that fails does not ship. |
| Format | The extension and container | `mp4`, `mov` or `srt` for the tooling's destinations. |
| Codec and container | `deliver_check`'s codec and pixel format rows | Write what was found, not what was asked for. A destination's expected values are in [platform specs](platform-specs.md). |
| Resolution | `deliver_check`'s size row | The master takes the timeline's size, so record it. |
| Frame rate | `deliver_check`'s fps row | Exact: write `24000/1001`, not `23.976`. |
| Duration | `ffprobe` on the file | Read from the file, not from the timeline. |
| Loudness | `deliver_check`'s loudness row | Integrated loudness and true peak. The master is unnormalized; record what it measures, or `n/a`. |
| Checksum | `shasum -a 256 <file>` | SHA-256, lowercase hex. The tooling already hashes source files with SHA-256 for cut manifests, so the same algorithm applies here. Taken after the loudness fix, since the fix replaces the file. |
| Destination | The destination key | The key from `resolve-config.json`, such as `linkedin_9x16`. |
| Approval reference | The approval record | The same id on every row of one approved cut. Different cuts of one project carry different ids. |

Checksums are taken last. A loudness fix, a caption change or a re-render after the checksum makes the row wrong, so a file touched after the manifest is written means the manifest is written again.

## Rules

1. **Every file in a destination subfolder has a row, and every row names a file in one.** A file with no row is not delivered; a row with no file is a mistake.
2. **Only `deliver_check` PASS files are listed as delivered.** A file with a FAIL is either fixed and checked again or listed in a short "Not delivered" note with the reason.
3. **The approval reference is required.** A file with no approval reference was not approved, and does not ship. See the gate.
4. **The manifest is not edited after delivery.** A correction is a new manifest with a new date, and the first one is kept.
5. **The client sees the manifest in full.** It is their delivery. Nothing client-identifying goes in a public copy: the manifest holds a project slug and a guest field in the file names, and a manifest published in this repo or anywhere public uses placeholders.

## A filled example

Values here are placeholders, not measurements.

```markdown
# Delivery manifest: example-slug

| | |
|---|---|
| Project | example-slug |
| Delivered | <date> |
| Prepared by | <name, role> |
| Approval reference | AP-<n> |

| File | Format | Codec and container | Resolution | Frame rate | Duration | Loudness | Checksum | Destination | Approval reference |
|---|---|---|---|---|---|---|---|---|---|
| SW001_Guest_01_example-clip_16x9.mp4 | mp4 | h264, yuv420p | 1920x1080 | 25 | <hh:mm:ss.ff> | <LUFS>, <dBTP> | <sha256> | substack_16x9 | AP-<n> |
| SW001_Guest_01_example-clip_16x9.srt | srt | n/a | n/a | n/a | <hh:mm:ss.ff> | n/a | <sha256> | substack_16x9 | AP-<n> |
| Client_example-slug_master.mov | mov | prores HQ, yuv422p10 | <timeline size> | 25 | <hh:mm:ss.ff> | <LUFS>, <dBTP> | <sha256> | client_master | AP-<n> |
```
