# Return Package

> **Status:** draft, 2026-10-16. The standard for what "everything is returned" means in files. Decided 2026-10-16: contents, medium, folder layout, checksums and retention below are the standard. No tool builds the package yet; a person or an agent assembles it by hand with the commands here.

The anti-extractive promise in the [measurement framework](../methodology/measurement-framework.md) says the client keeps the raw material, not only the cut. This document makes that checkable. A client who has the package can open every file RP shot or made for them, and can prove each file arrived intact.

It covers the return of footage, finals and project files to the client. The [delivery manifest](delivery-manifest.md) covers what shipped to destinations. The physical kit returns to RP under [camera shipping logistics](camera-shipping-logistics.md).

## What is in it

1. **Camera originals from every card, scraps included.** Every file on every card, in the folders it was shot in. A take that never made the edit is still returned. Nothing is filtered, renamed or re-encoded.
2. **The finals.** Every deliverable that passed `deliver_check`, as shipped.
3. **Captions.** The sidecar `.srt` for each final that has one.
4. **The Resolve project as a `.drp`, with media not consolidated.** The project file holds the timelines, grades and links, and points at the originals by path. It does not carry a copy of the media. A person exports it in Resolve with Export Project, not Export Project Archive, which would copy media into the file. When the client opens it on their machine, the clips show offline until the folder is relinked to `01_originals/`.
5. **The manifest.** `manifest.md` at the package root.

The client's own cards come back in the kit, as [camera shipping logistics](camera-shipping-logistics.md) sets out. RP copies them into `01_originals/` and sends the cards back with the original files still on them. Cards are never wiped.

## Medium

A download link is the default. A drive ships only when the client asks for one, or when the size passes a limit written into the scope. The limit lives in the scope for each engagement and is not stated here.

## Folder standard

One folder per engagement, named `<YYYY-MM>_<engagement-id>/`. The month is the month of return. The engagement id is the id from the engagement record, never a client name.

```
2026-11_<engagement-id>/
  01_originals/
    20261016_GH7_A01/       card folder exactly as shot
    20261016_GH7_A02/
    20261017_PYXIS_B01/
    SHA256SUMS
  02_finals/
    <destination key>/      one subfolder per destination, as rendered
  03_captions/
  04_project/
    <project>.drp
  manifest.md
```

Card folders are named `YYYYMMDD_<CAM>_<card>` under the capture standard in `production/capture-standard.md`: the shoot date, the camera, the card. The folder name is the only name RP adds. Camera file names are never changed, because `detect` and `ingest` read the native names and `relink` matches a clip on timecode, frames, resolution and frame rate, not on its name.

`02_finals/` keeps the destination subfolders the render queue wrote, so the file names stay under the house rule from the [Deliver section](resolve-template-spec.md#file-names). `03_captions/` holds a copy of each sidecar `.srt`; the originals also stay beside their videos in `02_finals/`, and the manifest lists both.

The package never sits inside a git working tree.

## Scraps and the negative archive

The [negative archive ritual](../methodology/negative-archive-ritual.md) keeps a project in three folders: `working/`, `final/` and `scraps/`. They map to the package like this.

| Ritual folder | Package folder | Notes |
|---|---|---|
| `working/` | `04_project/` and the cards in `01_originals/` | The live draft is the Resolve project. The footage it uses is the originals. |
| `final/` | `02_finals/` and `03_captions/` | What shipped. |
| `scraps/` | `01_originals/` | Unused takes are footage, so they return with every other take. The ritual's cut drafts, notes and `COLOPHON.md` are RP's internal record and are not part of the package unless the client asks. |

The ritual notes that scraps from client work can carry client information, and says a redaction protocol is needed before any client scrap is filed. That question stays open there. Nothing in this package depends on it, because the returned material is the client's own.

## Checksums

SHA-256 throughout. The cut tooling and the [delivery manifest](delivery-manifest.md) already use it.

**Originals** get a `SHA256SUMS` file at the root of `01_originals/`, since there are too many files to list by hand. Make it from inside the folder so the paths are relative:

```sh
cd 01_originals
find . -type f ! -name SHA256SUMS ! -name .DS_Store -print0 | sort -z | xargs -0 shasum -a 256 > SHA256SUMS
```

Each line is the format `shasum -a 256` writes: the lowercase hex hash, two spaces, then the path. Verify on macOS from the same folder:

```sh
cd 01_originals
shasum -a 256 -c SHA256SUMS
```

Every line prints `OK`. Any `FAILED` line names a file that changed or did not copy. Files added after the sums were made are not listed, so the originals folder is checked last, after the copy is complete.

**Finals** are not in `SHA256SUMS`. Their hashes are the Checksum column of the manifest table, taken after the last loudness fix as the delivery manifest requires. A client checks one with `shasum -a 256 <file>` and compares it to the row.

## How the client confirms receipt

The client does one of two things and says so by reply:

- Runs the verify command above and reports that every line is `OK`.
- Reads the manifest, checks that each listed file is present, and spot-checks finals against the table.

RP records the date of that reply. It starts the retention clock below.

## The manifest

`manifest.md` here is the same file the [delivery manifest](delivery-manifest.md) describes, at the package root beside the numbered folders. The delivery manifest's rules hold: one row per file, a checksum per row, an approval reference on every row, no edits after delivery. For the package, the file list also covers `03_captions/` and `04_project/`. The `.drp` gets a row with its SHA-256 and `n/a` in the video columns. The originals are covered by `SHA256SUMS` and named in the manifest as one line pointing to it, not listed row by row.

## What RP keeps afterward

- **Originals:** RP deletes its copy 90 days after the client confirms the checksums. The date of confirmation and the date of deletion go in the engagement record.
- **Finals:** RP keeps them only where the client has granted consent tier 3, defined in section 8 of the [measurement framework](../methodology/measurement-framework.md). The evidence that a client granted it is `production/consent-evidence.md`. Without tier 3 evidence, RP deletes its copy of the finals on the same 90-day clock.
- **Manifest:** kept. It names no client and holds hashes, not footage.

Until the client confirms, RP keeps everything. A package the client never confirms is chased, not deleted.

## Needs Isaac

All of the choices above are decisions of record as of 2026-10-16. Two details are mine.

1. **The month in the folder name is the month of return,** not the month of the shoot. A multi-month engagement then has one folder, dated when it closes.
2. **The `.drp` is exported by hand in Resolve.** No tool in `production/` exports a project, so this step has no check behind it.
