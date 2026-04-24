"""Structured memory record helpers for WAFI's unified memory store.

Structured records are still persisted as plain text entries inside the
existing MEMORY.md / USER.md files. This keeps the built-in memory store as
the single source of truth while allowing reflection and retrieval code to
attach stable categories and keys to durable notes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha1
import re
from typing import Literal, Optional


MemoryCategory = Literal[
    "episodic",
    "semantic",
    "procedural",
    "reflective",
    "owner_preference",
    "skill_candidate",
    "owner_dna",
]

MemoryTarget = Literal["memory", "user"]

STRUCTURED_MEMORY_VERSION = "v1"
STRUCTURED_MEMORY_PREFIX = "@wafi-memory"
STRUCTURED_MEMORY_RE = re.compile(
    r"^\[(?P<prefix>@wafi-memory)\s+"
    r"version=(?P<version>[^\s]+)\s+"
    r"category=(?P<category>[^\s]+)\s+"
    r"key=(?P<key>[^\s]+)\s+"
    r"hits=(?P<hits>\d+)\s+"
    r"confidence=(?P<confidence>\d+(?:\.\d+)?)"
    r"(?:\s+source=(?P<source>[^\s\]]+))?"
    r"(?:\s+updated_at=(?P<updated_at>[^\s\]]+))?"
    r"(?:\s+session_id=(?P<session_id>[^\s\]]+))?"
    r"\]\s*(?P<content>[\s\S]*)$"
)

DEFAULT_CATEGORY_TARGETS: dict[MemoryCategory, MemoryTarget] = {
    "episodic": "memory",
    "semantic": "memory",
    "procedural": "memory",
    "reflective": "memory",
    "owner_preference": "user",
    "skill_candidate": "memory",
    "owner_dna": "user",
}


def slugify_memory_key(text: str, *, fallback: str = "memory-record") -> str:
    """Build a stable, path-safe key fragment."""
    normalized = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    if normalized:
        return normalized[:64]
    return fallback


def build_memory_key(namespace: str, value: str) -> str:
    """Create a deterministic key with a readable prefix and short digest."""
    slug = slugify_memory_key(value)
    digest = sha1(value.strip().lower().encode("utf-8")).hexdigest()[:10]
    return f"{namespace}:{slug}:{digest}"


@dataclass(frozen=True)
class MemoryRecord:
    """Structured memory record stored inside the existing memory files."""

    category: MemoryCategory
    content: str
    key: str
    target: MemoryTarget
    hits: int = 1
    confidence: float = 0.5
    source: str = "manual"
    updated_at: str = ""
    session_id: str = ""

    def normalized_content(self) -> str:
        return " ".join((self.content or "").strip().split())


@dataclass(frozen=True)
class SkillCandidate:
    """Lightweight internal skill candidate derived from memory evidence."""

    candidate_id: str
    title: str
    category: str
    source_memory_types: tuple[str, ...]
    pattern_summary: str
    evidence_count: int
    success_count: int
    failure_count: int
    confidence: float
    last_seen_at: str = ""
    owner_specific_applicability: str = ""


@dataclass(frozen=True)
class OwnerDNATrait:
    """Evidence-based owner-specific identity trait for WAFI."""

    trait_name: str
    category: str
    confidence: float
    evidence_count: int
    last_updated: str
    supporting_signals: tuple[str, ...]
    stability_score: float = 0.0
    applicability_notes: str = ""


def normalize_skill_candidate(candidate: SkillCandidate) -> SkillCandidate:
    """Clamp skill-candidate fields for stable persistence."""
    title = " ".join((candidate.title or "").strip().split())
    summary = " ".join((candidate.pattern_summary or "").strip().split())
    category = (candidate.category or "workflow").strip() or "workflow"
    sources = tuple(
        sorted(
            {
                " ".join((source or "").strip().split())
                for source in candidate.source_memory_types
                if (source or "").strip()
            }
        )
    )
    evidence_count = max(1, int(candidate.evidence_count))
    success_count = max(0, int(candidate.success_count))
    failure_count = max(0, int(candidate.failure_count))
    confidence = min(1.0, max(0.0, float(candidate.confidence)))
    last_seen_at = normalize_memory_timestamp(candidate.last_seen_at)
    applicability = " ".join((candidate.owner_specific_applicability or "").strip().split())
    return SkillCandidate(
        candidate_id=(candidate.candidate_id or "").strip(),
        title=title,
        category=category,
        source_memory_types=sources,
        pattern_summary=summary,
        evidence_count=evidence_count,
        success_count=success_count,
        failure_count=failure_count,
        confidence=confidence,
        last_seen_at=last_seen_at,
        owner_specific_applicability=applicability,
    )


def normalize_owner_dna_trait(trait: OwnerDNATrait) -> OwnerDNATrait:
    """Clamp Owner DNA fields for stable persistence."""
    trait_name = " ".join((trait.trait_name or "").strip().split())
    category = (trait.category or "workflow").strip() or "workflow"
    evidence_count = max(1, int(trait.evidence_count))
    confidence = min(1.0, max(0.0, float(trait.confidence)))
    last_updated = normalize_memory_timestamp(trait.last_updated)
    signals = tuple(
        sorted(
            {
                " ".join((signal or "").strip().split())
                for signal in trait.supporting_signals
                if (signal or "").strip()
            }
        )
    )
    stability_score = min(1.0, max(0.0, float(trait.stability_score)))
    applicability = " ".join((trait.applicability_notes or "").strip().split())
    return OwnerDNATrait(
        trait_name=trait_name,
        category=category,
        confidence=confidence,
        evidence_count=evidence_count,
        last_updated=last_updated,
        supporting_signals=signals,
        stability_score=stability_score,
        applicability_notes=applicability,
    )


def skill_candidate_to_record(candidate: SkillCandidate) -> MemoryRecord:
    """Encode a skill candidate as a structured memory record."""
    normalized = normalize_skill_candidate(candidate)
    lines = [
        f"Title: {normalized.title}",
        f"Candidate Category: {normalized.category}",
        f"Source Memory Types: {', '.join(normalized.source_memory_types)}",
        f"Pattern Summary: {normalized.pattern_summary}",
        f"Evidence Count: {normalized.evidence_count}",
        f"Success Count: {normalized.success_count}",
        f"Failure Count: {normalized.failure_count}",
    ]
    if normalized.owner_specific_applicability:
        lines.append(f"Owner Applicability: {normalized.owner_specific_applicability}")
    content = " | ".join(lines)
    return normalize_memory_record(
        MemoryRecord(
            category="skill_candidate",
            content=content,
            key=normalized.candidate_id,
            target=DEFAULT_CATEGORY_TARGETS["skill_candidate"],
            hits=1,
            confidence=normalized.confidence,
            source="skill_candidate",
            updated_at=normalized.last_seen_at,
        )
    )


def parse_skill_candidate(value: str | MemoryRecord, *, default_target: MemoryTarget = "memory") -> Optional[SkillCandidate]:
    """Parse a skill candidate from a memory entry or parsed record."""
    record = value if isinstance(value, MemoryRecord) else parse_memory_record(value, default_target=default_target)
    if record is None or record.category != "skill_candidate":
        return None
    fields: dict[str, str] = {}
    for part in record.content.split(" | "):
        if ": " not in part:
            continue
        key, raw = part.split(": ", 1)
        fields[key.strip()] = raw.strip()
    title = fields.get("Title", "")
    summary = fields.get("Pattern Summary", "")
    if not title or not summary:
        return None
    sources = tuple(
        source.strip()
        for source in fields.get("Source Memory Types", "").split(",")
        if source.strip()
    )
    return normalize_skill_candidate(
        SkillCandidate(
            candidate_id=record.key,
            title=title,
            category=fields.get("Candidate Category", "workflow"),
            source_memory_types=sources,
            pattern_summary=summary,
            evidence_count=int(fields.get("Evidence Count", "1") or 1),
            success_count=int(fields.get("Success Count", "0") or 0),
            failure_count=int(fields.get("Failure Count", "0") or 0),
            confidence=record.confidence,
            last_seen_at=record.updated_at,
            owner_specific_applicability=fields.get("Owner Applicability", ""),
        )
    )


def owner_dna_trait_to_record(trait: OwnerDNATrait) -> MemoryRecord:
    """Encode an Owner DNA trait as a structured memory record."""
    normalized = normalize_owner_dna_trait(trait)
    content_parts = [
        f"Trait Name: {normalized.trait_name}",
        f"Trait Category: {normalized.category}",
        f"Evidence Count: {normalized.evidence_count}",
        f"Stability Score: {normalized.stability_score:.2f}",
        f"Supporting Signals: {', '.join(normalized.supporting_signals)}",
    ]
    if normalized.applicability_notes:
        content_parts.append(f"Applicability Notes: {normalized.applicability_notes}")
    content = " | ".join(content_parts)
    return normalize_memory_record(
        MemoryRecord(
            category="owner_dna",
            content=content,
            key=build_memory_key("owner-dna", f"{normalized.category}:{normalized.trait_name}"),
            target=DEFAULT_CATEGORY_TARGETS["owner_dna"],
            hits=normalized.evidence_count,
            confidence=normalized.confidence,
            source="owner_dna",
            updated_at=normalized.last_updated,
        )
    )


def parse_owner_dna_trait(value: str | MemoryRecord, *, default_target: MemoryTarget = "user") -> Optional[OwnerDNATrait]:
    """Parse an Owner DNA trait from a memory entry or parsed record."""
    record = value if isinstance(value, MemoryRecord) else parse_memory_record(value, default_target=default_target)
    if record is None or record.category != "owner_dna":
        return None
    fields: dict[str, str] = {}
    for part in record.content.split(" | "):
        if ": " not in part:
            continue
        key, raw = part.split(": ", 1)
        fields[key.strip()] = raw.strip()
    trait_name = fields.get("Trait Name", "")
    category = fields.get("Trait Category", "")
    signals = tuple(
        signal.strip()
        for signal in fields.get("Supporting Signals", "").split(",")
        if signal.strip()
    )
    if not trait_name or not category or not signals:
        return None
    return normalize_owner_dna_trait(
        OwnerDNATrait(
            trait_name=trait_name,
            category=category,
            confidence=record.confidence,
            evidence_count=int(fields.get("Evidence Count", str(record.hits)) or record.hits),
            last_updated=record.updated_at,
            supporting_signals=signals,
            stability_score=float(fields.get("Stability Score", "0") or 0),
            applicability_notes=fields.get("Applicability Notes", ""),
        )
    )


def normalize_memory_record(record: MemoryRecord) -> MemoryRecord:
    """Clamp fields and normalize whitespace for stable persistence."""
    content = record.normalized_content()
    hits = max(1, int(record.hits))
    confidence = min(1.0, max(0.0, float(record.confidence)))
    source = (record.source or "manual").strip() or "manual"
    updated_at = normalize_memory_timestamp(record.updated_at)
    session_id = (record.session_id or "").strip()
    return MemoryRecord(
        category=record.category,
        content=content,
        key=(record.key or "").strip(),
        target=record.target,
        hits=hits,
        confidence=confidence,
        source=source,
        updated_at=updated_at,
        session_id=session_id,
    )


def format_memory_record(record: MemoryRecord) -> str:
    """Render a structured record as a plain text memory entry."""
    normalized = normalize_memory_record(record)
    confidence = f"{normalized.confidence:.2f}"
    updated_at = f" updated_at={normalized.updated_at}" if normalized.updated_at else ""
    session_id = f" session_id={normalized.session_id}" if normalized.session_id else ""
    return (
        f"[{STRUCTURED_MEMORY_PREFIX} version={STRUCTURED_MEMORY_VERSION} "
        f"category={normalized.category} key={normalized.key} "
        f"hits={normalized.hits} confidence={confidence} source={normalized.source}"
        f"{updated_at}{session_id}] "
        f"{normalized.content}"
    )


def parse_memory_record(entry: str, *, default_target: MemoryTarget = "memory") -> Optional[MemoryRecord]:
    """Parse a structured memory entry, returning None for legacy free-text entries."""
    match = STRUCTURED_MEMORY_RE.match((entry or "").strip())
    if not match:
        return None
    if match.group("prefix") != STRUCTURED_MEMORY_PREFIX:
        return None
    category = match.group("category")
    if category not in DEFAULT_CATEGORY_TARGETS:
        return None
    return normalize_memory_record(
        MemoryRecord(
            category=category,  # type: ignore[arg-type]
            content=match.group("content").strip(),
            key=match.group("key").strip(),
            target=default_target,
            hits=int(match.group("hits")),
            confidence=float(match.group("confidence")),
            source=(match.group("source") or "manual").strip() or "manual",
            updated_at=normalize_memory_timestamp(match.group("updated_at") or ""),
            session_id=(match.group("session_id") or "").strip(),
        )
    )


def merge_memory_records(existing: MemoryRecord, new_record: MemoryRecord) -> MemoryRecord:
    """Merge a new observation into an existing structured record."""
    chosen_content = new_record.normalized_content() or existing.normalized_content()
    return normalize_memory_record(
        MemoryRecord(
            category=existing.category,
            content=chosen_content,
            key=existing.key,
            target=existing.target,
            hits=max(existing.hits, 0) + max(new_record.hits, 1),
            confidence=max(existing.confidence, new_record.confidence),
            source=new_record.source or existing.source,
            updated_at=new_record.updated_at or existing.updated_at,
            session_id=new_record.session_id or existing.session_id,
        )
    )


def normalize_memory_timestamp(value: str) -> str:
    """Normalize timestamps to compact ISO-8601 when possible."""
    raw = (value or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    return parsed.isoformat(timespec="seconds")
