# Return Package

> **Status:** draft, 2026-10-16. The standard for what "everything is returned" means in files. Decided 2026-10-16: contents, medium, folder layout, checksums and retention below are the standard. No tool builds the package yet; a person or an agent assembles it by hand with the commands here.

The anti-extractive safeguards in section 6 of the [measurement framework](../methodology/measurement-framework.md) give clients their raw data on request. The camera-shipping model goes further and returns everything, cut and raw material both. This document makes that checkable. A client who has the package can open every file RP shot or made for them, and can prove each file arrived intact.

It covers the return of footage, finals and project files to the client. The [delivery manifest](delivery-manifest.md) covers what shipped to destinations.

## What is in it

1. **Camera originals from every card, scraps included.** Every file on every card, in the folders it was shot in. A take that never made the edit is still returned. Nothing is filtered, renamed or re-encoded.
2. **The finals.** Every deliverable that passed `deliver_check`, as shipped.
3. **Captions.** The sidecar `.srt` for each final that has one.
4. **The Resolve project as a `.drp`, with media not consolidated.** The project file holds the timelines, grades and links, and points at the originals by path. It does not carry a copy of the media. A person exports it in Resolve with Export Project, not Export Project Archive, which would copy media into the file. When the client opens it on their machine, the clips show offline until the folder is relinked to `01_originals/`.
5. **The session recordings and transcripts.** Every recording and transcript of the client's sessions, in `05_session/`. Coded passages and aggregate response rows are not in the package. They stay under the consent tier the client chose, as section 6 of the [measurement framework](../methodology/measurement-framework.md) sets out.
6. **The manifest.** `manifest.md` at the package root.

The cards are not in the package and are not the client's. The footage on them is, and it reaches the client as the files in `01_originals/`. The cards travel in the kit. RP offloads them on arrival and wipes one only at kit reset, after the client confirms the checksums of the return package.

## Medium

A download link is the default. A drive ships only when the client asks for one, or when the size passes a limit written into the scope. The limit lives in the scope for each engagement and is not stated here.

**Hosting rule for the link.** The link is private and unguessable, and opens without an account. It expires 30 days after RP sends it, or when the client confirms receipt, whichever comes first. RP sends it again on request until RP deletes its copy. The rule names no vendor.

## Folder standard

One folder per engagement, named `<YYYY-MM>_<engagement-id>/`. The month is the month of return. The engagement id is the id from the engagement record, never a client name.

```
2026-11_<engagement-id>/
  01_originals/
    GH7-3fa9c1/            card folder exactly as shot
    GH7-b07e42/
    PYXIS-91d5aa/
    SHA256SUMS
  02_finals/
    <destination key>/      one subfolder per destination, as rendered
  03_captions/
  04_project/
    <project>.drp
  05_session/
    recordings and transcripts
  manifest.md
```

Card folders are named `<CAM>-<id6>` under the [capture standard](capture-standard.md): the camera and the card's id. The folder name is the only name RP adds. Camera file names are never changed: `detect` uses the native names as one of its clues, and the client should find their files under the names their camera gave them.

`02_finals/` keeps the destination subfolders the render queue wrote, so the file names stay under the house rule from the [Deliver section](resolve-template-spec.md#file-names). `03_captions/` holds a copy of each sidecar `.srt`; the originals also stay beside their videos in `02_finals/`, and the manifest lists both.

The package never sits inside a git working tree.

## Scraps and the negative archive

The [negative archive ritual](../methodology/negative-archive-ritual.md) keeps a project in three folders: `working/`, `final/` and `scraps/`. They map to the package like this.

| Ritual folder | Package folder | Notes |
|---|---|---|
| `working/` | `04_project/` and the footage in `01_originals/` | The live draft is the Resolve project. The footage it uses is the originals. |
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

**Finals** are not in `SHA256SUMS`. Their hashes are the Checksum column of the manifest table, taken after the last loudness fix as the delivery manifest requires. A client checks one with the steps below and compares the result to the row.

## How the client verifies

A checksum is a string worked out from a file's exact contents. If the string you work out matches the one RP wrote down, the file arrived whole. Nothing here needs more than the built-in terminal on your computer. Do the steps for your system.

**macOS.** Open Terminal, then move into the folder.

- The originals, all at once: `cd 01_originals`, then `shasum -a 256 -c SHA256SUMS`. Each line ends in `OK` or `FAILED`.
- One final: `shasum -a 256 <file>`. Compare the result to the Checksum column in `manifest.md`.

**Windows.** Open PowerShell, then move into the folder with `cd`. PowerShell ships with Windows. This is untested on Windows: the commands are standard PowerShell and the loop was checked by reading it through, not by running it.

- One final: `Get-FileHash -Algorithm SHA256 <file>`. Compare the Hash it prints to the Checksum column in `manifest.md`. PowerShell prints capitals and the manifest uses lowercase; they are the same value.
- The originals, all at once, from inside `01_originals`:

```powershell
Get-Content SHA256SUMS | ForEach-Object {
  if (-not $_) { return }
  $hash, $path = $_ -split '  ', 2
  $path = $path -replace '^\./', ''
  $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $path -ErrorAction SilentlyContinue).Hash
  if ($actual -and $actual -eq $hash) { "OK: $path" } else { "FAILED: $path" }
}
```

The loop reads each line of `SHA256SUMS`, skips a blank line, splits it into the hash and the path, drops the leading `./`, works out the file's hash and prints `OK` or `FAILED` for it. A file that is missing prints `FAILED`. On a large originals folder it takes a while, because it reads every file.

**If a line says FAILED.** Tell RP which file, and keep the files you have; do not delete or re-download the whole package. RP sends that one file again, and you check it again. A file is confirmed when its line says `OK`. The other files that said `OK` stand.

**What opens the `.drp`.** DaVinci Resolve opens it. Clips show offline until you relink the folder to `01_originals/`, as described under "What is in it". This repo states no Resolve version for opening the file, so ask RP which version the project was saved in before you install one.

## How the client confirms receipt

The client does one of two things and says so by reply:

- Runs the verify steps above for their system and reports that every line is `OK`.
- Reads the manifest, checks that each listed file is present, and spot-checks finals against the table with the steps above.

RP records the date of that reply. It starts the retention clock below.

## The manifest

`manifest.md` here is the same file the [delivery manifest](delivery-manifest.md) describes, at the package root beside the numbered folders. The delivery manifest's rules hold: one row per file, a checksum per row, an approval reference on every row, no edits after delivery. For the package, the file list also covers `03_captions/`, `04_project/` and `05_session/`. The `.drp` gets a row with its SHA-256 and `n/a` in the video columns. The originals are covered by `SHA256SUMS` and named in the manifest as one line pointing to it, not listed row by row. The manifest also carries the client-safe extract of the licence register, as its "Licences used" block: asset, licence and attribution required, with the private columns left out. The client therefore sees which licensed music and fonts the work uses and what credit each asks for.

## What RP keeps afterward

- **Originals:** RP deletes its copy 90 days after the client confirms the checksums. The date of confirmation and the date of deletion go in the engagement record.
- **Session recordings and transcripts:** RP deletes its copy on the same 90-day clock as the originals.
- **Finals:** RP keeps them only where the client has granted consent tier 3, defined in section 6 of the [measurement framework](../methodology/measurement-framework.md). The evidence that a client granted it is set out in [consent evidence](consent-evidence.md). Without tier 3 evidence, RP deletes its copy of the finals on the same 90-day clock.
- **The client master:** RP deletes it with the finals on the 90-day clock, unless the client asks in writing for RP to keep it. Tier 3 keeps the finals and does not keep the master. The client holds the master, and re-making a version ends with retention. [Platform specs](platform-specs.md) says the same.
- **Coded passages and aggregate rows:** kept under the consent tier the client chose.
- **Manifest:** kept. It names no client and holds hashes, not footage.

Until the client confirms, RP keeps everything. A package the client never confirms is chased, not deleted.
