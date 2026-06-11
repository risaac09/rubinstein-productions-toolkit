"""
CLI for context-provenance.

Usage:
    python -m provenance analyze transcript.jsonl --title "My Essay" --author "Name"
    python -m provenance render provenance.json --output report.html
    python -m provenance manual --title "My Essay" --author "Name" --output provenance.json
"""

import argparse
import json
import sys
from pathlib import Path

from provenance.channels import ProvenanceRecord, Source, ChannelType
from provenance.analyzer import analyze_transcript
from provenance.renderer import render_swimlane


def cmd_analyze(args):
    record = analyze_transcript(
        args.transcript,
        title=args.title,
        author=args.author,
        model=args.model,
    )
    out = args.output or args.transcript.rsplit(".", 1)[0] + ".provenance.json"
    Path(out).write_text(record.to_json(), encoding="utf-8")
    print(f"Provenance written to {out}")
    print(f"  Sources: {record.total_sources} ({record.retrievable_sources} retrievable)")
    print(f"  Opacity: {int(record.opacity_ratio * 100)}%")

    if args.html:
        html_out = out.rsplit(".", 1)[0] + ".html"
        render_swimlane(record, html_out)
        print(f"  Swim-lane: {html_out}")


def cmd_render(args):
    data = json.loads(Path(args.provenance).read_text(encoding="utf-8"))
    record = ProvenanceRecord.from_dict(data)
    out = args.output or args.provenance.rsplit(".", 1)[0] + ".html"
    render_swimlane(record, out)
    print(f"Swim-lane written to {out}")


def cmd_manual(args):
    """Interactive manual provenance entry."""
    record = ProvenanceRecord(
        title=args.title,
        author=args.author,
        created=args.date or str(__import__("datetime").date.today()),
        model=args.model,
    )

    print("\nManual provenance entry. Add sources per channel.")
    print("Type 'done' to finish a channel, 'quit' to save.\n")

    channel_names = {
        "1": ChannelType.SKILL,
        "2": ChannelType.RETRIEVED,
        "3": ChannelType.CONVERSATION,
    }

    for num, ct in channel_names.items():
        print(f"\n--- Channel {num}: {ct.value} ---")
        while True:
            label = input("  Source label (or 'done'): ").strip()
            if label.lower() == "done" or label.lower() == "quit":
                break
            desc = input("  Description: ").strip()
            path = input("  Path/URL (or blank): ").strip() or None
            record.add_source(Source(
                channel=ct,
                label=label,
                description=desc,
                path=path if path and not path.startswith("http") else None,
                url=path if path and path.startswith("http") else None,
                retrievable=True,
            ))
        if label.lower() == "quit":
            break

    # Channel 4 added automatically
    from provenance.analyzer import _add_training_channel
    _add_training_channel(record)

    coc = input("\nChain of custody note (or blank): ").strip()
    if coc:
        record.chain_of_custody = coc

    out = args.output or "provenance.json"
    Path(out).write_text(record.to_json(), encoding="utf-8")
    print(f"\nProvenance written to {out}")

    if args.html:
        html_out = out.rsplit(".", 1)[0] + ".html"
        render_swimlane(record, html_out)
        print(f"Swim-lane: {html_out}")


def main():
    parser = argparse.ArgumentParser(
        prog="provenance",
        description="Four-channel attribution for AI-mediated text",
    )
    sub = parser.add_subparsers(dest="command")

    # analyze
    p_analyze = sub.add_parser("analyze", help="Analyze a conversation transcript")
    p_analyze.add_argument("transcript", help="Path to .jsonl or .json transcript")
    p_analyze.add_argument("--title", default="Untitled")
    p_analyze.add_argument("--author", default="Unknown")
    p_analyze.add_argument("--model", default=None)
    p_analyze.add_argument("--output", default=None)
    p_analyze.add_argument("--html", action="store_true", help="Also generate HTML swim-lane")

    # render
    p_render = sub.add_parser("render", help="Render provenance JSON as HTML")
    p_render.add_argument("provenance", help="Path to .provenance.json file")
    p_render.add_argument("--output", default=None)

    # manual
    p_manual = sub.add_parser("manual", help="Manually enter provenance sources")
    p_manual.add_argument("--title", required=True)
    p_manual.add_argument("--author", required=True)
    p_manual.add_argument("--date", default=None)
    p_manual.add_argument("--model", default=None)
    p_manual.add_argument("--output", default=None)
    p_manual.add_argument("--html", action="store_true")

    args = parser.parse_args()
    if args.command == "analyze":
        cmd_analyze(args)
    elif args.command == "render":
        cmd_render(args)
    elif args.command == "manual":
        cmd_manual(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
