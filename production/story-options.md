# Story options

> **Status:** draft 2026-10-20. A protocol for proposing two or three structural options for a cut, between the selects and the paper edit, and a catalogue of structures to draw them from. It reads the story packet that `resolve_workflow.py story-packet` writes. Nothing here builds a timeline or chooses: the editor picks an option, and the paper edit made from it is the contract for the assembly.

An assistant, a person or a language model, drafts the options. The editor chooses, mixes or sends them back. Every option is advice in the editor's hands. Published benchmarks give the reason: on Timeline-Bench, a preprint of 56 editing tasks judged against professional editors, the best agent passed about a quarter, and the authors traced most failures to judging footage from stills and transcripts. MEDit-Bench showed the same footage cut differently for each message an editor wanted to carry. So the assistant gets the message before anything else, says in every option what it saw and heard, and leaves the choice to the editor.

## Inputs

The brief supplies these before any option is written. A blank stops the protocol; the assistant asks.

| Input | What it is |
|---|---|
| Message | One line: what the piece says to its audience. Options built without it drift toward whatever the transcript says most. |
| Runtime and shapes | Target length for each deliverable, and its shape (a 16:9 piece, a 9:16 reel). A reel is its own edit, never a cut-down. |
| Tempo profile | `document` or `short-form` (see [Tempo](#tempo)). |
| Structure school | `found` (log the material, set most aside, cut long, find the shape in the edit) or `planned` (choose the ending, work back from it). Editors use both; the brief names one. |
| Grammar and mode | The editing grammar and documentary mode the brief allows (see [Grammar and mode](#grammar-and-mode)). |
| Story packet | The `story-packet` Markdown for the shoot. For footage with little or no speech, run it with `--strips`, so the options can rest on the picture and not only on sound and words. |
| Limits | The off-limits list (people, places, clips), the consent list, and the music the project may use. |

## Step 1: the story index

Before any option, write an index of what the material holds: one line per usable moment.

```text
clip id            in      out     what happens                          functions        rests on
CAM-a1b2c3/P0042   12.40   19.85   hands tune a fiddle, room goes quiet  hook, setup      picture, sound
CAM-a1b2c3/P0051    3.00   41.20   first song, crowd joins the chorus    payoff, release  sound, picture
```

- Times are seconds from the clip's first frame, as the packet gives them.
- Functions come from this list: `hook`, `setup`, `turn`, `payoff`, `release`, `bridge`, `texture`, `ending`. A moment may carry several.
- "Rests on" says what the line was judged from: `picture` (a strip, frames or picture labels), `sound` (the energy line or an audio report) or `words` (the transcript). Write `words` alone when that is all there was.
- Include the moments the selects set aside, marked `reject`. Rejects often supply the transitions a structure is missing.

The editor corrects the index before step 2. An option built on a wrong index is wrong in every beat that uses the error.

## Step 2: the options

Write two or three options. Three is the default; two when the material only supports two shapes.

- **Different in structure.** Options differ in spine or in ending, never in shot order alone. Two options with the same spine and the same ending are one option; drop the weaker.
- **Tied to the index.** Every beat cites a clip id with in and out from the index.
- **Inside the runtime.** Beat durations add to the target within ten percent; the option states its total.
- **Honest about evidence.** Every beat says what it rests on. A beat whose job is visual (a match cut, a reveal, a change of scale) that rests on `words` says so, and the critic reads it as unverified.

Each option fills this template:

```markdown
### Option A: <a name for the shape, in a few words>

Spine: <catalogue entry> · school: <found | planned> · grammar: <...> · mode: <...>
Message fit: <how this shape carries the one-line message>
Runtime: <total> against <target>

Hook: first 2 s <clip id, in-out, what the viewer sees and hears>
      first 10 s <what question is open by then>
Ending: <clip id, in-out, and what it closes>

| # | beat | clip id | in | out | seconds | function | rests on |
|---|---|---|---|---|---|---|---|

Tempo plan: <per section: long or short shots, where it holds, where it speeds up, in the profile's terms>
Energy arc: <the expected shape, read from the packet's energy lines: where it lifts, where it rests>
Sound: <what carries time across cuts: music, ambience, a voice; where silence is kept>
Gives up: <the strongest moment in the index this option leaves out, and why>
Fails if: <the way this option goes wrong for this brief>
```

## Step 3: the critic pass

After all options exist, run one separate pass over them with a fixed rubric. Use a fresh context and a different prompt from the drafting pass: a model asked to judge its own drafts in the same context tends to favour them.

The rubric follows Walter Murch's order of what a cut must keep, highest first: emotion, story, rhythm, eye trace, the two-dimensional plane of the screen, three-dimensional space. Use it as an order only. Secondary sources give a weight to each of the six, but nobody has tested them, so they never become a score.

For each option, note against its beats:

- **Message.** Does the shape carry the brief's one line, or a different one?
- **Hook.** Does the piece deliver what its first two seconds promise? A hook that promises something the piece never shows is a finding.
- **Spliced speech.** List every beat that joins two separate ranges of one speaker. Read both ranges in the index and say whether the joined sentence still means what the speaker meant.
- **Tempo.** Does the plan fit the profile? A short-form habit (a change every few seconds, a cold open on a reaction) inside a `document` profile is a finding.
- **Evidence.** Which checks used picture or sound, and which used words only.
- **Limits.** Anything on the off-limits list; any person not on the consent list.

The pass names no winner. If the editor asks for a ranking, run it twice with the options in a different order and report both. A ranking that changes with the order is a tie.

## Step 4: the editor chooses

The editor picks one option, mixes two (naming which beats come from which), or sends them back with notes. The choice and its reason go in the project's change log. The chosen beats become the paper edit, a TSV with these columns, which the assembly follows:

```text
clip_id	in	out	beat	function	speaker
```

## Structure catalogue

Proportions are starting points for a beat sheet, never targets.

| Structure | Shape | Rough proportions | Fits | Fits badly |
|---|---|---|---|---|
| Chronological | Events in the order they happened | Follows the clock | Material with a clock of its own: a day, a race, an event from setup to close | Material with no change over time; order alone then reads as a log |
| Three-act | Setup, confrontation, resolution | 25 / 50 / 25 | A person with a goal and an obstacle the camera saw | Observational material. Documentary theorists, Bill Nichols among them, argue this screenwriting model fits nonfiction poorly; forcing it invents a conflict |
| Kishōtenketsu | Introduction, development, a turn, reconciliation | About a quarter each | Pieces with no antagonist, where tension comes from contrast: a gathering, a workshop, a landscape | An argument that needs opposition. It comes from Chinese poetic form, by way of its Japanese name; no documentary examples were found |
| Vignette or montage | Short pieces joined by theme or rhythm, no causal chain | Even, or building | Mood, place, a festival, a season; music-led cutting | Anything the viewer must understand in order |
| Circular | Ends where it began, changed | Opening and close mirror | A return, a cycle, a ritual | An opening image that cannot carry a second meaning at the end |
| Essay | An argument carried by a voice, with picture as evidence and counterpoint | Thesis, evidence, counterpoint, conclusion | Interviews or narration that make a case | Material where no voice makes an argument |
| Observational | Scenes play out in time, few cuts, no narration | Scene by scene | Scenes with their own shape and strong sound | Short runtimes; a reel rarely holds one scene |

## Grammar and mode

Each option names its editing grammar and its documentary mode, so the editor sees the choice instead of inheriting a default.

- **Grammar:** continuity (invisible cuts, match on action), jump cut (urgency, fragments), montage (meaning from collision), long take (time as the subject), L and J cuts (sound leads or trails the picture), match cut (a graphic rhyme).
- **Mode,** after Nichols: expository (a guiding voice; risks lecturing), observational (the camera watches; raises questions of intimacy and consent), participatory (the filmmaker is visible), reflexive (the film questions itself), performative (subjective, emotional truth), poetic (rhythm and association; risks losing the viewer).

## Tempo

Keep the two profiles apart.

- **`document`.** Shot lengths follow the material. A long take is a choice, and the plan says why it holds. Describe the plan with Karen Pearlman's three rhythms: physical (movement in picture and sound), emotional (energy passed from shot to shot) and event (the rhythm of the story's events).
- **`short-form`.** A hook window (two seconds by default; a parameter, since no study fixes it), one question opened early and closed before the end, the payoff close to the last frame and no outro. The common rule of "a change every three to five seconds" has no primary source; read it as a question to ask of the cut.

Numbers describe a cut and never judge it. Average shot length alone hides the spread: report it with the median and the coefficient of variation (Barry Salt's measurements across 1,520 sound features put median over average near 0.62 and the coefficient near 1.1). Shuffle the shot order and compare before reading structure into a curve; random orders show trends too. A beat the editor marks by hand outranks any measured value.

## Hooks

Practitioners report these; test them against the brief instead of applying them.

- Open on a concrete image in the middle of an action, never a logo or title card.
- Open one question and close it before the end.
- Stop on the payoff.
- Make it work with the sound off.
- A clean loop can lift the share of the piece people watch; nobody has shown it helps every piece.

Platforms publish no universal threshold for a good opening; compare a post against the channel's own history. Figures that circulate without a source ("86.6% of top shorts use animated captions", "+22% for question openings") are folklore; leave them out of the reasoning.

## Timeline names

An option built for viewing gets its own timeline, named `<project> Option A v01 [auto]`. The assembly is `<project> Assembly v01 [auto]`, a fine cut `<project> Fine v03 [auto]`, the locked cut `<project> Lock v01 [auto]`. The ` [auto]` suffix stays last, because the MCP write tools work only on timelines whose name ends with it. Nobody renames an old version; a new version is a duplicate with the next number.

## Privacy

The index, the options and the critic notes name people and quote them. Keep them with the project, outside any git working tree, and never paste them into a commit, a pull request or a public page.

## Sources

- Walter Murch, *In the Blink of an Eye*, 2nd ed., Silman-James, 2001: the rule of six, used here as an order.
- Karen Pearlman, *Cutting Rhythms*, 3rd ed., Routledge, 2025: physical, emotional and event rhythm.
- Barry Salt, shot-length distributions, on [Cinemetrics](https://cinemetrics.uchicago.edu/article/616e7ecc-7915-4768-b84d-7dec79aa77c2).
- Bill Nichols, *Introduction to Documentary*: the documentary modes.
- Frederick Wiseman in [The Threepenny Review](https://www.threepennyreview.com/?p=2845): logging, setting most material aside, and reviewing rejects at the end.
- Timeline-Bench, [arXiv 2609.35143](https://arxiv.org/abs/2609.35143), preprint.
- MEDit-Bench, [arXiv 2607.25300](https://arxiv.org/abs/2607.25300).
