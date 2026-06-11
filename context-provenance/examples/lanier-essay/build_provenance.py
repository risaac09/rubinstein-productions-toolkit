"""
Worked example: provenance record for "Side by Side (on Lanier at Brown)"
by Isaac Rubinstein, April 2026.

This script builds the provenance manually — the essay existed before
this tool did, so we reconstruct what shaped it from the essay's own
chain of custody section and the conversation history that produced it.
"""

import sys
from pathlib import Path

# Add parent to path for local dev
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from provenance.channels import ProvenanceRecord, Source, ChannelType
from provenance.renderer import render_swimlane


def build():
    record = ProvenanceRecord(
        title="Side by Side (on Lanier at Brown)",
        author="Isaac Rubinstein",
        created="2026-04-24",
        model="claude-opus-4-6",
        model_version="claude-opus-4-6",
    )

    # ── Channel 1: Skill Instructions ──
    record.add_source(Source(
        channel=ChannelType.SKILL,
        label="isaac-voice",
        description="Voice enforcement skill — 30+ source texts distilled into positive patterns, anti-patterns, and a 50-point scoring rubric. Shaped sentence architecture, register jumps, and metaphor standards.",
        retrievable=True,
    ))
    record.add_source(Source(
        channel=ChannelType.SKILL,
        label="stop-slop",
        description="AI pattern removal — em-dash elimination, rule-of-three breaking, throat-clearing cuts, active voice enforcement. Applied across all five drafts.",
        retrievable=True,
    ))

    # ── Channel 2: Retrieved Context ──
    record.add_source(Source(
        channel=ChannelType.RETRIEVED,
        label="Lanier event captions",
        description="Automated speech-to-text captions from Isaac's recording of Jaron Lanier at Brown, April 23 2026. Captioning model unknown. Contained errors: 'Jaren' for 'Jaron', 'Entropic' for 'Anthropic', 'Jesus Anunda' for 'Jesús Andujar'.",
        retrievable=True,
    ))
    record.add_source(Source(
        channel=ChannelType.RETRIEVED,
        label="Claude transcript cleaning",
        description="Captions were cleaned using Claude (claude-opus-4-6). The model's training data almost certainly includes Lanier's prior essays, meaning his prior voice was interpolated back into his current words during cleaning.",
        retrievable=True,
    ))
    record.add_source(Source(
        channel=ChannelType.RETRIEVED,
        label="Isaac's video footage",
        description="Isaac's own audiovisual recording of the event. Source material for the captions and for the first-person observational passages.",
        retrievable=True,
    ))
    record.add_source(Source(
        channel=ChannelType.RETRIEVED,
        label="Bergsonism (Deleuze, 1966)",
        description="Referenced directly in the essay. Isaac was reading this with Paul (history PhD at Brown) in a book club that predated the Lanier event. Shaped the Bergson-duration-cutting argument.",
        retrievable=True,
        url="https://www.zonebooks.org/books/53-bergsonism",
    ))
    record.add_source(Source(
        channel=ChannelType.RETRIEVED,
        label="Arena V1-V3 research corpus",
        description="Six months of adversarial synthesis across 14 research domains, producing 19 surviving claims. The value-creation/capture fault-line, Deleuzian-Bergsonian framework, and affordance theory were tested in 82+ Arena rounds before appearing in this essay.",
        retrievable=True,
    ))
    record.add_source(Source(
        channel=ChannelType.RETRIEVED,
        label="Gibson — ecological perception",
        description="James Gibson's affordance theory, the third kind of information in the essay's argument. Arrived in Isaac's system through Arena encounters and foundation research, not from the Lanier event itself.",
        retrievable=True,
    ))

    # ── Channel 3: Conversation Memory ──
    record.add_source(Source(
        channel=ChannelType.CONVERSATION,
        label="'Jaron Lanier at Brown' session",
        description="Claude Code session where initial draft was developed from captions and Isaac's observations. Multiple revision rounds across v1-v5.",
        retrievable=True,
    ))
    record.add_source(Source(
        channel=ChannelType.CONVERSATION,
        label="'Convert Lanier essay to Substack draft' session",
        description="Follow-up session converting the essay for publication format.",
        retrievable=True,
    ))
    record.add_source(Source(
        channel=ChannelType.CONVERSATION,
        label="Auto-memory: Isaac's voice patterns",
        description="Persistent memory files storing Isaac's preferences, feedback on prior drafts, and voice enforcement history. Shaped how Claude approached the revision process.",
        retrievable=True,
    ))
    record.add_source(Source(
        channel=ChannelType.CONVERSATION,
        label="Book club with Paul",
        description="Ongoing Bergsonism reading sessions between Isaac and Paul. Intellectual context that predated and shaped the essay's theoretical framework. Entered the system through Isaac's descriptions in conversation.",
        retrievable=False,  # The conversations happened outside the system
    ))
    record.add_source(Source(
        channel=ChannelType.CONVERSATION,
        label="Post-event conversation with Stephon Alexander",
        description="Isaac reached out to Stephon after the event. Response pending at time of publication.",
        retrievable=False,
    ))
    record.add_source(Source(
        channel=ChannelType.CONVERSATION,
        label="Post-event exchange with Donnie Aikins",
        description="Donnie gave Isaac his number after the concert. Named in acknowledgments.",
        retrievable=False,
    ))

    # ── Channel 4: Training Interpolation ──
    # (Added automatically by the analyzer, but we add specifics here)
    record.channels[ChannelType.TRAINING].sources.clear()
    record.add_source(Source(
        channel=ChannelType.TRAINING,
        label="Lanier's prior published work",
        description="Claude's training data almost certainly includes Lanier's books (You Are Not a Gadget, Who Owns the Future?, Dawn of the New Everything) and essays. When Claude helped clean captions and draft text, Lanier's prior voice shaped the output. The specific training examples cannot be identified.",
        retrievable=False,
    ))
    record.add_source(Source(
        channel=ChannelType.TRAINING,
        label="Deleuze/Bergson scholarly corpus",
        description="Claude's training data includes academic writing on Deleuze and Bergson. The essay's philosophical framing was shaped both by Isaac's direct reading (Channel 2) and by Claude's absorbed patterns from the broader scholarly conversation. The two influences cannot be separated.",
        retrievable=False,
    ))
    record.add_source(Source(
        channel=ChannelType.TRAINING,
        label="Gibson/ecological psychology corpus",
        description="Same as above for Gibson. Claude's training includes ecological psychology literature that shaped how affordance theory was articulated in the essay.",
        retrievable=False,
    ))
    record.add_source(Source(
        channel=ChannelType.TRAINING,
        label="Essay/longform writing patterns",
        description="Sentence-level choices, paragraph transitions, argumentative structure, and prose rhythm absorbed from the training corpus. Even with voice enforcement skills active, the base model's stylistic tendencies are present in every sentence.",
        retrievable=False,
    ))
    record.channels[ChannelType.TRAINING].note = (
        "This channel is opaque by design. Four specific clusters are named above "
        "because the essay's subject matter makes them identifiable as probable "
        "influences — but naming them does not make them retrievable. The model "
        "cannot verify which training examples contributed to a given sentence. "
        "This is Bergson's point applied to itself: the trace remains after the "
        "cutting, but the relation was what the cutting destroyed."
    )

    # ── Chain of Custody ──
    record.chain_of_custody = (
        "Captions came from an automated speech-to-text model trained on recordings "
        "whose speakers cannot be named. Captions were cleaned using Claude "
        "(claude-opus-4-6, Anthropic), whose weights almost certainly include "
        "Lanier's prior essays — his prior voice was interpolated back into his "
        "current words during cleaning. The essay went through five drafts in "
        "Claude Code sessions, with isaac-voice and stop-slop skills enforcing "
        "voice fidelity across all revisions. Isaac corrected 'Jaren' to 'Jaron', "
        "'Entropic' to 'Anthropic', and 'Jesus Anunda' to 'Jesús Andujar' "
        "(Providence-based Latin percussionist, confirmed via Brown's coverage).\n\n"
        "This provenance record was itself generated with Claude (claude-opus-4-6). "
        "It is a working prototype of the transparency Lanier proposed."
    )

    # ── Output ──
    out_dir = Path(__file__).parent

    # JSON
    json_path = out_dir / "provenance.json"
    json_path.write_text(record.to_json(), encoding="utf-8")
    print(f"JSON: {json_path}")

    # HTML
    html_path = out_dir / "provenance.html"
    render_swimlane(record, str(html_path))
    print(f"HTML: {html_path}")

    # Summary
    print(f"\nSources: {record.total_sources} total, {record.retrievable_sources} retrievable")
    print(f"Opacity: {int(record.opacity_ratio * 100)}%")


if __name__ == "__main__":
    build()
