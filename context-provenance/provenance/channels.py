"""
Four-channel provenance model.

Every AI-mediated output draws from four source channels.
Three are retrievable. One is not. The format marks all four.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
import json


class ChannelType(str, Enum):
    SKILL = "skill_instructions"
    RETRIEVED = "retrieved_context"
    CONVERSATION = "conversation_memory"
    TRAINING = "training_interpolation"


@dataclass
class Source:
    """A single retrievable source within a channel."""
    channel: ChannelType
    label: str
    description: str
    path: Optional[str] = None
    url: Optional[str] = None
    weight: Optional[float] = None  # 0.0-1.0, self-reported confidence
    retrievable: bool = True
    timestamp: Optional[str] = None

    def to_dict(self) -> dict:
        d = {
            "channel": self.channel.value,
            "label": self.label,
            "description": self.description,
            "retrievable": self.retrievable,
        }
        if self.path:
            d["path"] = self.path
        if self.url:
            d["url"] = self.url
        if self.weight is not None:
            d["weight"] = self.weight
        if self.timestamp:
            d["timestamp"] = self.timestamp
        return d


@dataclass
class Channel:
    """One of the four provenance channels."""
    channel_type: ChannelType
    sources: list[Source] = field(default_factory=list)
    note: Optional[str] = None

    @property
    def total_weight(self) -> float:
        weights = [s.weight for s in self.sources if s.weight is not None]
        return sum(weights) if weights else 0.0

    @property
    def retrievable_count(self) -> int:
        return sum(1 for s in self.sources if s.retrievable)

    def to_dict(self) -> dict:
        d = {
            "channel": self.channel_type.value,
            "source_count": len(self.sources),
            "retrievable_count": self.retrievable_count,
            "sources": [s.to_dict() for s in self.sources],
        }
        if self.note:
            d["note"] = self.note
        if self.total_weight > 0:
            d["total_weight"] = round(self.total_weight, 3)
        return d


@dataclass
class ProvenanceRecord:
    """Full provenance metadata for a piece of AI-mediated output."""
    title: str
    author: str
    created: str
    model: Optional[str] = None
    model_version: Optional[str] = None
    channels: dict[ChannelType, Channel] = field(default_factory=dict)
    chain_of_custody: Optional[str] = None
    version: str = "0.1.0"

    def __post_init__(self):
        # Ensure all four channels exist
        for ct in ChannelType:
            if ct not in self.channels:
                self.channels[ct] = Channel(channel_type=ct)

    def add_source(self, source: Source):
        self.channels[source.channel].sources.append(source)

    @property
    def total_sources(self) -> int:
        return sum(len(ch.sources) for ch in self.channels.values())

    @property
    def retrievable_sources(self) -> int:
        return sum(ch.retrievable_count for ch in self.channels.values())

    @property
    def opacity_ratio(self) -> float:
        """What fraction of sources are NOT retrievable."""
        total = self.total_sources
        if total == 0:
            return 1.0
        return 1.0 - (self.retrievable_sources / total)

    def to_dict(self) -> dict:
        return {
            "context_provenance": {
                "version": self.version,
                "title": self.title,
                "author": self.author,
                "created": self.created,
                "model": self.model,
                "model_version": self.model_version,
                "summary": {
                    "total_sources": self.total_sources,
                    "retrievable_sources": self.retrievable_sources,
                    "opacity_ratio": round(self.opacity_ratio, 3),
                },
                "channels": {
                    ct.value: ch.to_dict()
                    for ct, ch in self.channels.items()
                },
                "chain_of_custody": self.chain_of_custody,
            }
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict) -> "ProvenanceRecord":
        cp = data.get("context_provenance", data)
        record = cls(
            title=cp["title"],
            author=cp["author"],
            created=cp["created"],
            model=cp.get("model"),
            model_version=cp.get("model_version"),
            chain_of_custody=cp.get("chain_of_custody"),
            version=cp.get("version", "0.1.0"),
        )
        for ch_key, ch_data in cp.get("channels", {}).items():
            ct = ChannelType(ch_key)
            for s in ch_data.get("sources", []):
                record.add_source(Source(
                    channel=ct,
                    label=s["label"],
                    description=s["description"],
                    path=s.get("path"),
                    url=s.get("url"),
                    weight=s.get("weight"),
                    retrievable=s.get("retrievable", True),
                    timestamp=s.get("timestamp"),
                ))
            if ch_data.get("note"):
                record.channels[ct].note = ch_data["note"]
        return record
