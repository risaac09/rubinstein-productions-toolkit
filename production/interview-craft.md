# Interview craft

> **Status:** draft 2026-10-20. Rules for cutting interview and dialogue footage: J and L cuts, cutaways, cutting back to the speaker, and multicam. Each rule says whether it comes from a practitioner source or is a house default. House defaults are starting values to tune on your own footage, not findings. The last section specifies a later `jl-audit`; nothing here is built yet.

## How to read the labels

- **Source:** a practitioner article says it. The sources are listed at the end, and each is a blog-level craft article, not a study.
- **House default:** this toolkit's starting value or rule. It has no outside source. Change it when the footage says otherwise, and write the new value down.

## J and L cuts

**Definitions (source: PremiumBeat, BroadcastBeat, Vimeo).** An L cut keeps the audio of picture A running over picture B. A J cut brings the audio of B in before its picture. In a documentary interview the usual uses are carrying the interview voice over B-roll, and bringing the next speaker's first words in before the picture changes.

**Short audio transitions (source: same three).** A short audio transition works better than a long default fade. Listen on headphones: breath and room-tone jumps show up there and are easy to miss on speakers.

**Offsets (house default).** The sources give no frame count. Start with the audio leading or trailing the picture by 6 to 12 frames at 24 or 25 fps, and tune on footage:

| Situation | Start at |
|---|---|
| Next speaker's first words before their picture (J) | 8 to 12 frames of lead |
| Interview voice trailing over the new picture (L) | 8 to 12 frames of tail |
| Quick back-and-forth between two speakers | 6 frames |
| Reflective or slow material | up to 12, or longer if the line asks for it |

Scale by frame rate: 12 frames at 24 fps is half a second, so at 50 or 60 fps double the frame counts to keep the same time. Under 6 frames at 24 fps the offset reads as a mistake, not a choice (house default). A split that is deliberate and a hard cut that is deliberate are both fine; a hard cut on dialogue that was not a decision is what the audit below flags.

**Room tone (house default).** When an audio edit moves earlier or later than its picture, the join needs matching room tone on both sides. If the two clips differ in tone, cross-fade over a few frames, or lay room tone from the same location under the join. Check by ear on headphones.

**When not to split (house default).** A line where the viewer needs the speaker's face to understand or believe it (a confession, a correction, a joke's reaction) stays a hard cut or a late split. Splits are for smoothing, not for hiding a face that matters.

## Cutaway discipline

**Definition (source: PremiumBeat, BroadcastBeat, Vimeo).** A cutaway leaves the talking subject for footage that shows the line while the person keeps speaking.

**Rules (all house defaults):**

1. **Never cut away on the emotional peak of a line.** The face carries it. This follows Murch's order of priorities, where emotion ranks above every other consideration in a cut (source: StudioBinder summary of Murch's rule of six; the numeric weights some summaries quote are disputed and are not used here). Cut away before the peak, or after the line lands, and return to the face for the peak.
2. **A cutaway shows the line or answers it.** A cutaway that illustrates a different thing than the line says is a new claim by the editor. Check it against the words.
3. **Hold long enough to read.** Start at about 2 seconds for a plain object or place, longer when the viewer must take in a scene. Tune on footage.
4. **Do not stack cutaways to cover a weak interview.** Two cutaways in a row for one answer is a flag to reread the answer, not a style.
5. **Do not cover a visible edit with a cutaway when the edit changes what the person meant.** Interview cuts must keep the meaning of the line. If a cut needs a cutaway to hide the join, check that the join is faithful first.

## Cut-back rules

All house defaults:

1. **Return on a breath, a new clause or the start of a new idea,** not mid-word.
2. **Return before the cutaway has made its point twice.** Once the picture has done its job, go back.
3. **Return to the same framing the subject left,** unless a second angle exists and the change is motivated by the line (see multicam).
4. **Let the audio lead the return** (a J into the face) when the speaker's next word matters, at the offsets above.
5. **Reaction before answer.** When a second person is in the room, a short hold on the listener before the speaker's next line is allowed, never longer than the listener's visible reaction.

## Multicam: a hand step

Resolve 20 adds a Multicam SmartSwitch that picks angles from speaker detection and lip movement, and multicam creation can use a reference audio track (source: Blackmagic's release material and trade coverage of Resolve 20; whether SmartSwitch is Studio-only is unconfirmed, so check Blackmagic's feature table before relying on it).

In this toolkit multicam clips are made and cut by hand. The Resolve tools say so: a multicam clip stays a hand step, and `sync` stacks a dual-system pair, not a multicam group. A SmartSwitch result is a proposal for the editor to review angle by angle, held to the same rules as above: no switch away from the face on an emotional peak, and every switch motivated by the line (house default). Nothing in the toolkit accepts a SmartSwitch result without review.

## `jl-audit` (specification only)

A planned read-only check that lists how each dialogue cut on a timeline is split between audio and picture, and flags hard cuts. It waits for a live Resolve session to probe the story-related scripting methods; this section only fixes what it will do.

**Input.** One timeline, named exactly, with dialogue on its own audio track or tracks. It reads `timeline_items` only, once with `track_type` video and once with `track_type` audio (start and end are timeline frames), so it never changes the project and never makes the timeline current. The timeline's frame rate comes from the same result.

**For each cut between two dialogue items** on the video track, find the audio items on the dialogue track nearest the cut and report:

| Column | Meaning |
|---|---|
| `cut` | Record frame of the video edit, and its timecode |
| `video_out`, `video_in` | The outgoing and incoming video items |
| `audio_out`, `audio_in` | The audio items whose edit sits nearest the cut |
| `audio_offset_out` | Frames the outgoing audio ends after (positive, an L) or before (negative) the video cut |
| `audio_offset_in` | Frames the incoming audio starts before (positive, a J) or after (negative) the video cut |
| `kind` | `hard` (both offsets 0), `J`, `L` or `JL` |

**Flags (all start as house defaults, tuned on footage):**

- `hard`: a dialogue cut with both offsets at 0.
- `short`: a split of 1 to 5 frames at 24 fps, which reads as an error.
- `long`: a split over 24 frames at 24 fps, which is worth a second look.

The frame thresholds are for 24 fps. At other frame rates the audit scales them by the timeline's frame rate, as in the offsets section.
- `unpaired`: a video cut with no dialogue audio edit near it.

Flags are questions for the editor, not defects. A hard cut can be right, and a flag never changes the timeline. Output is a table the editor reads, written outside any git repository; it names the project's clips and so is never pasted into a commit or pull request.

**Done when** the audit flags a hard dialogue cut planted on a probe timeline made from the editor's own footage.

## Sources

- PremiumBeat, "Video editing tips: cutting a documentary," <https://www.premiumbeat.com/blog/video-editing-tips-cutting-documentary/>
- BroadcastBeat, "Using L and J cuts for smoother video edits," <https://broadcastbeat.com/news/using-l-and-j-cuts-for-smoother-video-edits>
- Vimeo, "J cuts and L cuts," <https://vimeo.com/blog/post/j-cuts-l-cuts>
- StudioBinder, "Walter Murch's rule of six," <https://www.studiobinder.com/blog/walter-murch-rule-of-six/>
- Blackmagic Design, DaVinci Resolve 20 release notes, <https://www.blackmagicdesign.com/media/partial/release/20250404-02>
