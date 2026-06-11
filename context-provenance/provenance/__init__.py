"""
Context Provenance — four-channel attribution for AI-mediated text.

Tracks what shaped an AI response across four channels:
  1. Skill Instructions — system prompts, loaded skills, methodology
  2. Retrieved Context — files read, tools called, searches run
  3. Conversation Memory — prior turns, stored memories
  4. Training Interpolation — the unattributable remainder
"""

__version__ = "0.1.0"

from provenance.channels import ProvenanceRecord, Channel, Source, ChannelType
from provenance.analyzer import analyze_transcript
from provenance.renderer import render_swimlane
