# Delivery Manifest

> **Status:** accepted 2026-10-16. A template and the rules for filling it, written to match the deliver tooling's naming rule and checks. `resolve_workflow.py deliver-manifest <delivery>` writes `manifest.md` and `checksums.txt` from ffprobe and the files' bytes, takes the header facts as flags, refuses to overwrite without `--overwrite`, and re-checks the sums with `--verify`. It does not run `deliver_check`: run that first, since only PASS files are delivered.

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
| Approved review copy | <file name of the review copy the approver approved> |
| Review copy SHA-256 | <checksum of that review copy, lowercase hex> |
| Machine-assisted steps | <list; see credits-and-disclosure.md> |
| Credits | <where the credits line appears, or "none requested"> |
| Platform notes | <limits looked up at queue time, or "none looked up"> |

## Files

| File | Format | Codec and container | Resolution | Frame rate | Duration | Loudness | Checksum | Destination | Approval reference |
|---|---|---|---|---|---|---|---|---|---|
| <name by the house rule> | <container> | <video codec, pixel format> | <WxH> | <exact rate> | <hh:mm:ss.ff> | <integrated LUFS, true peak dBTP> | <sha256> | <destination key> | <id> |

## Licences used

| Asset | Licence | Attribution required |
|---|---|---|
| <track or font> | <licence name or type> | <the credit the licence demands, or "none"> |
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

The two review-copy rows in the header come from the approval record in the gate, which holds the review copy's file name and SHA-256. Copy both exactly. They are what lets a client tie a delivered file to the approval: the approval message names that file name and checksum, and the header repeats them. The review copy is not a deliverable and is not a row in the table.

The **Licences used** block is a client-safe extract of the licence register in [consent evidence](consent-evidence.md#licence-register). Copy three columns from the register: asset, licence, attribution required. Leave out source, scope of use, proof and the checked date, which stay in the private engagement record. A piece with no licensed music or fonts writes "none" under the heading. The [return package](return-package.md) carries this manifest, so the extract travels with it.

Checksums are taken last. A loudness fix, a caption change or a re-render after the checksum makes the row wrong, so a file touched after the manifest is written means the manifest is written again.

## Rules

1. **Every file in a destination subfolder has a row, and every row names a file in one.** A file with no row is not delivered; a row with no file is a mistake.
2. **Only `deliver_check` PASS files are listed as delivered.** Pass `--boxes` for any caption or super on screen so the safe-zone row runs; without boxes it skips. A file with a FAIL is either fixed and checked again or listed in a short "Not delivered" note with the reason.
3. **The approval reference is required.** A file with no approval reference was not approved, and does not ship. See the gate.
4. **The manifest is not edited after delivery.** A correction is a new manifest with a new date, and the first one is kept.
5. **The client sees the manifest in full, with the key and the approval message below.** It is their delivery. Nothing client-identifying goes in a public copy: the manifest holds a project slug and a guest field in the file names, and a manifest published in this repo or anywhere public uses placeholders.

## For the client: a key to the manifest

This section is addressed to you, the client. RP puts it beside your manifest. The manifest is a list of every file you received, with the facts about each one. You do not need to understand every column to use it. Check three things: the file names match your folder, the checksum of each file matches its row, and the approval reference is the one you approved.

| Term | What it means |
|---|---|
| File | The name of the file. It follows RP's naming rule, so it tells you the project, the clip and the shape of the picture (`16x9` is landscape, `9x16` is vertical, `1x1` is square). |
| Format | The kind of file. `mp4` and `mov` are video. `srt` is a captions file that sits beside its video. |
| Codec | How the picture is compressed to make the file small enough to send. `h264` and `hevc` play almost everywhere. `prores` is a large, high-quality file made for editing, used for the master. |
| Container | The wrapper around the picture and sound. It is the `mp4` or `mov` part. |
| Pixel format | How the colour of each dot is stored, such as `yuv420p`. You will not need to act on it. It is listed so a platform's requirement can be checked. |
| Resolution | The picture size in dots, width by height, such as `1920x1080`. |
| Frame rate | Pictures per second. A fraction is exact: `24000/1001` is the film rate often shown as 23.976, and `30000/1001` is the rate often shown as 29.97. A whole number such as `25` is exact as written. |
| Duration | The length, in hours, minutes, seconds and frames. |
| LUFS | How loud the whole file is on average. A lower number is quieter, and a platform asks for a target. A master may show `n/a` because it is not adjusted to a platform's level. |
| dBTP | The highest level the sound reaches, a ceiling to stay under so it does not distort. Closer to zero is higher, so `-1` is a higher peak than `-2`. |
| SHA-256, or checksum | A long string of letters and digits worked out from the exact contents of a file. Change one dot of one picture and the string changes. If the string you work out matches the row, the file is the one RP listed. The [return package](return-package.md#how-the-client-verifies) shows how to work it out on a Mac and on Windows. |
| Destination | Where the file was made to go. It is a short key. `linkedin_9x16` is the vertical cut for LinkedIn, and `client_master` is the master file for you to keep. |
| Approval reference | The short id of the approval you gave. Every file made from the cut you approved carries it. |
| Approved review copy | The file you watched and approved, named in the header with its checksum. |
| Attribution required | A credit that the licence for a track or font asks the work to carry. The [credits standard](credits-and-disclosure.md#credits) says where it appears. |

## For the client: the approval message

Before RP makes your final files, you approve one review copy in writing. RP gives you its file name and its SHA-256 checksum when it sends you the copy, in the same message. The [final-approval gate](resolve-template-spec.md#the-final-approval-gate) states what counts as approval: "A written statement from the approver, from an address the approver controls, that names the review copy by file name, carries its SHA-256 checksum and says it is approved." Silence, a thumbs-up in passing and "looks good" about a different version are not approval, and a spoken yes is confirmed in writing before anything is made.

Send this from your own email address. Fill in the angle brackets with what RP gave you:

```text
Subject: Approval of review copy <file name>

I approve the review copy named <file name>, SHA-256 <checksum>.

I have watched all of it: the picture, the sound, the captions and the credits line.
<Conditions, or "I have no conditions.">

<Your name, your role>
```

If you want a change, do not send this message. Send a list of changes by timecode, and RP sends a new review copy with a new file name and checksum. An approval covers one review copy only. Where an organization commissioned the work and the person on camera is someone else, the gate asks both to approve, each in their own message.

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
| Approved review copy | <file name> |
| Review copy SHA-256 | <sha256> |

| File | Format | Codec and container | Resolution | Frame rate | Duration | Loudness | Checksum | Destination | Approval reference |
|---|---|---|---|---|---|---|---|---|---|
| SW001_Guest_01_example-clip_16x9.mp4 | mp4 | h264, yuv420p | 1920x1080 | 25 | <hh:mm:ss.ff> | <LUFS>, <dBTP> | <sha256> | substack_16x9 | AP-<n> |
| SW001_Guest_01_example-clip_16x9.srt | srt | n/a | n/a | n/a | <hh:mm:ss.ff> | n/a | <sha256> | substack_16x9 | AP-<n> |
| Client_example-slug_master.mov | mov | prores HQ, yuv422p10 | <timeline size> | 25 | <hh:mm:ss.ff> | <LUFS>, <dBTP> | <sha256> | client_master | AP-<n> |
```
