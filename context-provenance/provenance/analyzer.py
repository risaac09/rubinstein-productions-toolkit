"""
Transcript analyzer — extracts provenance from Claude conversation logs.

Parses a conversation transcript (JSON lines or structured JSON) and
identifies sources across the four channels. Channel 4 (training
interpolation) is always present and always marked as non-retrievable.
"""

import json
import re
from pathlib import Path
from typing import Optional

from provenance.channels import (
    ProvenanceRecord,
    Source,
    ChannelType,
)


def analyze_transcript(
    transcript_path: str,
    title: str = "Untitled",
    author: str = "Unknown",
    model: Optional[str] = None,
) -> ProvenanceRecord:
    """
    Analyze a conversation transcript and extract provenance sources.

    Args:
        transcript_path: Path to a .jsonl or .json transcript file
        title: Title of the output being analyzed
        author: Author of the output
        model: Model identifier (e.g., "claude-opus-4-6")

    Returns:
        ProvenanceRecord with sources populated across all four channels
    """
    path = Path(transcript_path)
    messages = _load_transcript(path)

    record = ProvenanceRecord(
        title=title,
        author=author,
        created=_extract_date(messages),
        model=model,
    )

    # Track seen sources to prevent duplicates.
    # Key: (channel, normalized_label) → avoids the same file appearing
    # once from a tool_call and again from a text-regex match.
    seen: set[tuple[str, str]] = set()

    for msg in messages:
        _extract_sources(msg, record, seen)

    # Channel 4 always present
    _add_training_channel(record)

    return record


def _load_transcript(path: Path) -> list[dict]:
    """Load messages from .jsonl or .json format."""
    messages = []
    text = path.read_text(encoding="utf-8")

    if path.suffix == ".jsonl":
        for line in text.strip().splitlines():
            line = line.strip()
            if line:
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    else:
        data = json.loads(text)
        if isinstance(data, list):
            messages = data
        elif "messages" in data:
            messages = data["messages"]
        elif "conversation" in data:
            messages = data["conversation"]
        else:
            messages = [data]

    return messages


def _extract_date(messages: list[dict]) -> str:
    """Pull a date from the first message, or return today."""
    from datetime import date
    for msg in messages:
        ts = msg.get("timestamp") or msg.get("created_at") or msg.get("date")
        if ts:
            return str(ts)[:10]
    return str(date.today())


def _add_source_deduped(
    record: ProvenanceRecord,
    source: Source,
    seen: set[tuple[str, str]],
):
    """Add a source only if we haven't seen this (channel, label) before."""
    key = (source.channel.value, source.label.lower().strip())
    if key not in seen:
        seen.add(key)
        record.add_source(source)


def _extract_sources(
    msg: dict,
    record: ProvenanceRecord,
    seen: set[tuple[str, str]],
):
    """Extract sources from a single message."""
    content = msg.get("content", "")
    if isinstance(content, list):
        content = " ".join(
            block.get("text", "") for block in content
            if isinstance(block, dict)
        )

    # Tool use blocks — these are the authoritative source; process first.
    tool_uses = msg.get("tool_calls", []) or msg.get("tool_use", [])
    if isinstance(tool_uses, dict):
        tool_uses = [tool_uses]

    for tool in tool_uses:
        name = tool.get("name", "") or tool.get("function", {}).get("name", "")
        if not name:
            continue
        args = tool.get("input", {}) or tool.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}

        _classify_tool(name, args, record, seen)

    # Skill references in text (only if not already captured via tool call)
    if "SKILL.md" in content or "skill:" in content.lower():
        skills = re.findall(r"skills?[/:\\](\S+?)(?:/SKILL\.md)?[\s\)\]\"']", content)
        for skill_name in skills:
            _add_source_deduped(record, Source(
                channel=ChannelType.SKILL,
                label=skill_name,
                description=f"Skill loaded: {skill_name}",
                retrievable=True,
            ), seen)

    # File paths in text (only if not already captured via tool call)
    file_refs = re.findall(
        r"(?:Reading|read|Read|loaded|Loading)\s+(?:file\s+)?[`\"']?([/~][\w/\-. ]+\.\w+)",
        content,
    )
    for fpath in file_refs:
        _add_source_deduped(record, Source(
            channel=ChannelType.RETRIEVED,
            label=Path(fpath).name,
            description=f"File read: {fpath}",
            path=fpath,
            retrievable=True,
        ), seen)


def _classify_tool(
    name: str,
    args: dict,
    record: ProvenanceRecord,
    seen: set[tuple[str, str]],
):
    """Classify a tool call into the appropriate channel."""
    name_lower = name.lower()

    # File reads → Channel 2
    if any(k in name_lower for k in ["read", "cat", "file", "download"]):
        path = args.get("file_path") or args.get("path") or args.get("filePath", "")
        _add_source_deduped(record, Source(
            channel=ChannelType.RETRIEVED,
            label=Path(path).name if path else name,
            description=f"File read via {name}",
            path=path or None,
            retrievable=True,
        ), seen)

    # Web tools → Channel 2
    elif any(k in name_lower for k in ["web", "search", "fetch", "navigate", "browse"]):
        query = args.get("query") or args.get("url") or args.get("search_query", "")
        _add_source_deduped(record, Source(
            channel=ChannelType.RETRIEVED,
            label=str(query)[:60] if query else name,
            description=f"Web access via {name}",
            url=args.get("url"),
            retrievable=True,
        ), seen)

    # Memory tools → Channel 3
    elif any(k in name_lower for k in ["memory", "transcript", "session"]):
        _add_source_deduped(record, Source(
            channel=ChannelType.CONVERSATION,
            label=name,
            description=f"Memory/session access: {name}",
            retrievable=True,
        ), seen)

    # Skill invocations → Channel 1
    elif "skill" in name_lower:
        skill_name = args.get("skill") or args.get("name", name)
        _add_source_deduped(record, Source(
            channel=ChannelType.SKILL,
            label=str(skill_name),
            description=f"Skill invoked: {skill_name}",
            retrievable=True,
        ), seen)

    # Other tools → Channel 2 (tool results are retrievable context)
    else:
        _add_source_deduped(record, Source(
            channel=ChannelType.RETRIEVED,
            label=name,
            description=f"Tool call: {name}",
            retrievable=True,
        ), seen)


def _add_training_channel(record: ProvenanceRecord):
    """
    Channel 4 is always present. It cannot be enumerated.
    Mark it honestly.
    """
    record.add_source(Source(
        channel=ChannelType.TRAINING,
        label="Model training data",
        description=(
            "Everything not attributable to Channels 1-3. Includes language "
            "patterns, factual knowledge, reasoning strategies, and stylistic "
            "tendencies absorbed from training data whose specific sources "
            "cannot be identified from outside the model."
        ),
        retrievable=False,
        weight=None,  # Cannot be estimated
    ))
    record.channels[ChannelType.TRAINING].note = (
        "This channel is opaque by design. The model cannot introspect on which "
        "training examples influenced a given output. Marking the channel as "
        "present and non-retrievable is itself the act of transparency — an honest "
        "admission that the loss is permanent and the best anyone can do is mark "
        "where it happened."
    )
