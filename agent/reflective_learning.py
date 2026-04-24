"""Bounded post-turn reflective learning for WAFI.

This module intentionally stays deterministic and in-process. It does not
spawn a second cognitive loop or create a second memory backend. It analyzes
completed turns and emits structured memory records that are written through
the existing MemoryStore.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from agent.memory_schema import (
    DEFAULT_CATEGORY_TARGETS,
    MemoryRecord,
    OwnerDNATrait,
    SkillCandidate,
    build_memory_key,
    owner_dna_trait_to_record,
    parse_owner_dna_trait,
    parse_skill_candidate,
    skill_candidate_to_record,
)


PREFERENCE_PATTERNS = (
    re.compile(r"\b(?:i\s+)?prefer\s+(?P<body>[^.;\n]{4,160})", re.IGNORECASE),
    re.compile(r"\b(?:please\s+)?(?:be|keep)\s+(?P<body>[^.;\n]{4,160})", re.IGNORECASE),
    re.compile(r"\b(?:don't|do not|never|avoid)\s+(?P<body>[^.;\n]{4,160})", re.IGNORECASE),
)

OWNER_DNA_RULES: tuple[Dict[str, Any], ...] = (
    {
        "trait_name": "concise_direct_communication",
        "category": "response_style",
        "positive": ("concise", "brief", "focused", "direct", "no fluff"),
        "negative": ("detailed", "thorough", "verbose"),
        "sources": {"owner_preference", "owner_dna"},
        "applicability": "Applies when presenting answers, plans, and summaries.",
    },
    {
        "trait_name": "incremental_validation_workflow",
        "category": "workflow_preference",
        "positive": ("focused test", "pytest", "incremental", "before broader", "validation", "workflow reuse"),
        "negative": ("one-shot", "skip validation", "big bang"),
        "sources": {"procedural", "skill_candidate", "episodic"},
        "applicability": "Applies to debugging, implementation, and verification tasks.",
    },
    {
        "trait_name": "backward_compatible_change_bias",
        "category": "decision_preference",
        "positive": ("backward compatibility", "incremental", "safe", "non-destructive", "low-risk"),
        "negative": ("rewrite the whole runtime", "destructive", "big-bang"),
        "sources": {"owner_preference", "reflective", "owner_dna"},
        "applicability": "Applies when choosing between refactors, migrations, and patches.",
    },
    {
        "trait_name": "controlled_execution_risk_posture",
        "category": "risk_posture",
        "positive": ("avoid destructive", "safe", "approvals", "verify", "controlled", "check inputs"),
        "negative": ("reckless", "force through", "ignore approvals"),
        "sources": {"owner_preference", "reflective", "owner_dna"},
        "applicability": "Applies to execution, tool use, and environment changes.",
    },
    {
        "trait_name": "owner_specific_memory_growth_mission",
        "category": "mission_pattern",
        "positive": ("memory", "owner-specific", "personal", "continuity", "self-improving", "maintainability"),
        "negative": (),
        "sources": {"episodic", "skill_candidate", "owner_preference", "owner_dna"},
        "applicability": "Applies to long-term product direction and prioritization.",
    },
)


@dataclass(frozen=True)
class PersistedReflection:
    """One structured reflection that has been persisted."""

    action: str
    target: str
    entry: str
    record: MemoryRecord


def _summarize_prompt(text: str, *, limit: int = 80) -> str:
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3].rstrip() + "..."


def _extract_tool_name_map(messages: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for tool_call in msg.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            tool_id = tool_call.get("id")
            function = tool_call.get("function") or {}
            name = function.get("name")
            if tool_id and name:
                mapping[str(tool_id)] = str(name)
    return mapping


def _extract_tool_outcomes(messages: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    mapping = _extract_tool_name_map(messages)
    counts: Dict[str, Dict[str, int]] = {}
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        tool_name = mapping.get(str(msg.get("tool_call_id") or ""))
        if not tool_name:
            continue
        bucket = counts.setdefault(tool_name, {"success": 0, "failure": 0})
        try:
            payload = json.loads(msg.get("content", "") or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        is_failure = False
        if isinstance(payload, dict):
            if payload.get("success") is False:
                is_failure = True
            elif payload.get("error"):
                is_failure = True
        if is_failure:
            bucket["failure"] += 1
        else:
            bucket["success"] += 1
    return counts


def _extract_last_tool_chain(messages: List[Dict[str, Any]]) -> List[str]:
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            continue
        chain: List[str] = []
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            name = (tool_call.get("function") or {}).get("name")
            if isinstance(name, str) and name:
                chain.append(name)
        if chain:
            return chain
    return []


def _extract_reusable_tool_chain(messages: List[Dict[str, Any]]) -> List[str]:
    """Return the strongest multi-step tool chain in the current turn."""
    best: List[str] = []
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            continue
        chain: List[str] = []
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            name = (tool_call.get("function") or {}).get("name")
            if isinstance(name, str) and name:
                chain.append(name)
        distinct = list(dict.fromkeys(chain))
        if len(distinct) > len(best):
            best = distinct
    if best:
        return best
    return list(dict.fromkeys(_extract_last_tool_chain(messages)))


class ReflectiveLearningEngine:
    """Deterministic extractor for structured reflective memory."""

    def __init__(self, *, max_records: int = 4, max_candidates: int = 2, max_owner_dna_traits: int = 3):
        self.max_records = max_records
        self.max_candidates = max_candidates
        self.max_owner_dna_traits = max_owner_dna_traits

    def build_records(
        self,
        *,
        messages: List[Dict[str, Any]],
        user_message: str,
        assistant_response: str,
    ) -> List[MemoryRecord]:
        records: List[MemoryRecord] = []
        records.extend(self._build_owner_preference_records(user_message))
        records.extend(self._build_repeated_outcome_records(messages))
        records.extend(self._build_workflow_records(messages, user_message))
        records.extend(self._build_episode_records(messages, user_message, assistant_response))
        return records[: self.max_records]

    def persist_records(
        self,
        *,
        store,
        messages: List[Dict[str, Any]],
        user_message: str,
        assistant_response: str,
    ) -> List[PersistedReflection]:
        persisted: List[PersistedReflection] = []
        base_records = self.build_records(
            messages=messages,
            user_message=user_message,
            assistant_response=assistant_response,
        )
        seen: set[tuple[str, str]] = set()
        for record in base_records:
            dedupe = (record.target, record.key)
            if dedupe in seen:
                continue
            seen.add(dedupe)
            result = store.upsert_record(record)
            if not result.get("success"):
                continue
            persisted.append(
                PersistedReflection(
                    action=str(result.get("action") or "add"),
                    target=record.target,
                    entry=str(result.get("entry") or ""),
                    record=record,
                )
            )
        for record in self._build_skill_candidate_records(
            store=store,
            messages=messages,
            user_message=user_message,
            assistant_response=assistant_response,
            base_records=base_records,
        ):
            dedupe = (record.target, record.key)
            if dedupe in seen:
                continue
            seen.add(dedupe)
            result = store.upsert_record(record)
            if not result.get("success"):
                continue
            persisted.append(
                PersistedReflection(
                    action=str(result.get("action") or "add"),
                    target=record.target,
                    entry=str(result.get("entry") or ""),
                    record=record,
                )
            )
        for record in self._build_owner_dna_records(
            store=store,
            messages=messages,
            user_message=user_message,
            assistant_response=assistant_response,
            base_records=base_records,
        ):
            dedupe = (record.target, record.key)
            if dedupe in seen:
                continue
            seen.add(dedupe)
            result = store.upsert_record(record)
            if not result.get("success"):
                continue
            persisted.append(
                PersistedReflection(
                    action=str(result.get("action") or "add"),
                    target=record.target,
                    entry=str(result.get("entry") or ""),
                    record=record,
                )
            )
        return persisted

    def _build_owner_preference_records(self, user_message: str) -> List[MemoryRecord]:
        text = " ".join((user_message or "").split())
        records: List[MemoryRecord] = []
        for pattern in PREFERENCE_PATTERNS:
            for match in pattern.finditer(text):
                body = (match.group("body") or "").strip(" .")
                if len(body) < 4:
                    continue
                if pattern.pattern.startswith("\\b(?:don't|do not|never|avoid)"):
                    content = f"Avoids {body}"
                elif "prefer" in pattern.pattern:
                    content = f"Prefers {body}"
                else:
                    content = f"Wants responses to be {body}"
                key = build_memory_key("owner-pref", content)
                records.append(
                    MemoryRecord(
                        category="owner_preference",
                        content=content,
                        key=key,
                        target=DEFAULT_CATEGORY_TARGETS["owner_preference"],
                        confidence=0.85,
                        source="reflection",
                    )
                )
        return records

    def _build_repeated_outcome_records(self, messages: List[Dict[str, Any]]) -> List[MemoryRecord]:
        counts = _extract_tool_outcomes(messages)
        records: List[MemoryRecord] = []
        for tool_name, summary in counts.items():
            if summary["success"] >= 2:
                content = (
                    f"Repeated success pattern: tool '{tool_name}' succeeded "
                    f"{summary['success']} times in this session."
                )
                records.append(
                    MemoryRecord(
                        category="reflective",
                        content=content,
                        key=build_memory_key("reflect-success", tool_name),
                        target=DEFAULT_CATEGORY_TARGETS["reflective"],
                        hits=summary["success"],
                        confidence=0.8,
                        source="reflection",
                    )
                )
            if summary["failure"] >= 2:
                content = (
                    f"Repeated failure pattern: tool '{tool_name}' failed "
                    f"{summary['failure']} times in this session; check inputs, approvals, or environment before retrying."
                )
                records.append(
                    MemoryRecord(
                        category="reflective",
                        content=content,
                        key=build_memory_key("reflect-failure", tool_name),
                        target=DEFAULT_CATEGORY_TARGETS["reflective"],
                        hits=summary["failure"],
                        confidence=0.9,
                        source="reflection",
                    )
                )
        return records

    def _build_workflow_records(self, messages: List[Dict[str, Any]], user_message: str) -> List[MemoryRecord]:
        chain = _extract_reusable_tool_chain(messages)
        if len(chain) < 2:
            return []
        distinct = list(dict.fromkeys(chain))
        workflow = " -> ".join(distinct)
        task_summary = _summarize_prompt(user_message)
        content = f"Reusable workflow for '{task_summary}': {workflow}"
        return [
            MemoryRecord(
                category="procedural",
                content=content,
                key=build_memory_key("workflow", workflow),
                target=DEFAULT_CATEGORY_TARGETS["procedural"],
                confidence=0.7,
                source="reflection",
            )
        ]

    def _build_episode_records(
        self,
        messages: List[Dict[str, Any]],
        user_message: str,
        assistant_response: str,
    ) -> List[MemoryRecord]:
        outcomes = _extract_tool_outcomes(messages)
        total_tools = sum(summary["success"] + summary["failure"] for summary in outcomes.values())
        if total_tools < 2:
            return []
        had_failure = any(summary["failure"] for summary in outcomes.values())
        prompt = _summarize_prompt(user_message)
        answer = _summarize_prompt(assistant_response, limit=120)
        if had_failure:
            content = f"Episode: task '{prompt}' encountered tool failures before finishing. Final response: {answer}"
        else:
            content = f"Episode: task '{prompt}' completed through tool use. Final response: {answer}"
        return [
            MemoryRecord(
                category="episodic",
                content=content,
                key=build_memory_key("episode", f"{prompt}:{answer}"),
                target=DEFAULT_CATEGORY_TARGETS["episodic"],
                confidence=0.6,
                source="reflection",
            )
        ]

    def _build_skill_candidate_records(
        self,
        *,
        store,
        messages: List[Dict[str, Any]],
        user_message: str,
        assistant_response: str,
        base_records: List[MemoryRecord],
    ) -> List[MemoryRecord]:
        candidates = self._derive_skill_candidates(
            store=store,
            messages=messages,
            user_message=user_message,
            assistant_response=assistant_response,
            base_records=base_records,
        )
        return [
            skill_candidate_to_record(candidate)
            for candidate in candidates[: self.max_candidates]
        ]

    def _build_owner_dna_records(
        self,
        *,
        store,
        messages: List[Dict[str, Any]],
        user_message: str,
        assistant_response: str,
        base_records: List[MemoryRecord],
    ) -> List[MemoryRecord]:
        traits = self._derive_owner_dna_traits(
            store=store,
            messages=messages,
            user_message=user_message,
            assistant_response=assistant_response,
            base_records=base_records,
        )
        return [
            owner_dna_trait_to_record(trait)
            for trait in traits[: self.max_owner_dna_traits]
        ]

    def _derive_skill_candidates(
        self,
        *,
        store,
        messages: List[Dict[str, Any]],
        user_message: str,
        assistant_response: str,
        base_records: List[MemoryRecord],
    ) -> List[SkillCandidate]:
        existing_records = store.list_structured_records(target="memory")
        existing_candidates = [
            candidate
            for candidate in (
                parse_skill_candidate(record)
                for record in existing_records
            )
            if candidate is not None
        ]
        outcomes = _extract_tool_outcomes(messages)
        procedural_workflow = self._extract_workflow_from_records(base_records)
        chain = procedural_workflow.split(" -> ") if procedural_workflow else list(dict.fromkeys(_extract_last_tool_chain(messages)))
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        task_summary = _summarize_prompt(user_message)

        candidates: List[SkillCandidate] = []

        total_success = sum(summary["success"] for summary in outcomes.values())
        total_failure = sum(summary["failure"] for summary in outcomes.values())
        if len(chain) >= 2 and total_success >= len(chain):
            workflow = " -> ".join(chain)
            prior_related = sum(
                1
                for record in existing_records
                if record.category in {"procedural", "episodic", "skill_candidate"}
                and any(tool in record.content for tool in chain)
            )
            owner_applicability = self._owner_applicability_note(store)
            candidates.append(
                SkillCandidate(
                    candidate_id=build_memory_key("skill-candidate", f"workflow:{workflow}"),
                    title=f"Workflow reuse: {workflow}",
                    category="workflow_reuse",
                    source_memory_types=("procedural", "episodic", "reflective"),
                    pattern_summary=(
                        f"For tasks like '{task_summary}', the workflow {workflow} repeatedly completed successfully "
                        f"and may be worth formalizing later."
                    ),
                    evidence_count=max(2, total_success + prior_related),
                    success_count=max(total_success, 1),
                    failure_count=total_failure,
                    confidence=min(0.92, 0.65 + min(prior_related, 3) * 0.06 + min(total_success, 4) * 0.03),
                    last_seen_at=now,
                    owner_specific_applicability=owner_applicability,
                )
            )

        for tool_name, summary in outcomes.items():
            if summary["success"] >= 2:
                prior_reflections = sum(
                    1
                    for record in existing_records
                    if record.category in {"reflective", "skill_candidate"}
                    and tool_name in record.content
                )
                if prior_reflections < 1:
                    continue
                candidates.append(
                    SkillCandidate(
                        candidate_id=build_memory_key("skill-candidate", f"tool:{tool_name}"),
                        title=f"Reliable pattern: {tool_name}",
                        category="reliable_pattern",
                        source_memory_types=("reflective", "procedural"),
                        pattern_summary=(
                            f"The tool '{tool_name}' has repeated successful usage for similar tasks and is a candidate "
                            f"for reuse guidance."
                        ),
                        evidence_count=summary["success"] + prior_reflections,
                        success_count=summary["success"] + prior_reflections,
                        failure_count=summary["failure"],
                        confidence=min(0.9, 0.6 + min(summary['success'] + prior_reflections, 5) * 0.05),
                        last_seen_at=now,
                        owner_specific_applicability=self._owner_applicability_note(store),
                    )
                )

        episodic_matches = sum(
            1
            for record in existing_records
            if record.category == "episodic"
            and task_summary
            and task_summary.split(" ")[0].lower() in record.content.lower()
        )
        if episodic_matches >= 1 and len(chain) >= 2:
            workflow = " -> ".join(chain)
            candidates.append(
                SkillCandidate(
                    candidate_id=build_memory_key("skill-candidate", f"continuity:{workflow}:{task_summary}"),
                    title=f"Continuity pattern: {task_summary}",
                    category="continuity_pattern",
                    source_memory_types=("episodic", "procedural"),
                    pattern_summary=(
                        f"Ongoing work on '{task_summary}' tends to follow the workflow {workflow}; preserving that "
                        f"context improves continuity on similar follow-up tasks."
                    ),
                    evidence_count=episodic_matches + len(chain),
                    success_count=max(total_success, 1),
                    failure_count=total_failure,
                    confidence=min(0.85, 0.58 + min(episodic_matches, 3) * 0.08),
                    last_seen_at=now,
                    owner_specific_applicability=self._owner_applicability_note(store),
                )
            )

        deduped: List[SkillCandidate] = []
        seen_ids: set[str] = {candidate.candidate_id for candidate in existing_candidates}
        for candidate in candidates:
            if candidate.candidate_id in seen_ids:
                deduped.append(candidate)
                continue
            seen_ids.add(candidate.candidate_id)
            deduped.append(candidate)
        deduped.sort(
            key=lambda candidate: (
                candidate.confidence,
                candidate.evidence_count,
                candidate.success_count,
                -candidate.failure_count,
            ),
            reverse=True,
        )
        return deduped

    def _derive_owner_dna_traits(
        self,
        *,
        store,
        messages: List[Dict[str, Any]],
        user_message: str,
        assistant_response: str,
        base_records: List[MemoryRecord],
    ) -> List[OwnerDNATrait]:
        current_records = list(base_records)
        existing_user_records = store.list_structured_records(target="user")
        existing_memory_records = store.list_structured_records(target="memory")
        existing_dna = [
            trait
            for trait in (
                parse_owner_dna_trait(record)
                for record in existing_user_records
            )
            if trait is not None
        ]

        evidence_records = [
            *existing_user_records,
            *existing_memory_records,
            *current_records,
        ]
        evidence_by_category: Dict[str, List[str]] = {}
        for record in evidence_records:
            evidence_by_category.setdefault(record.category, []).append(record.content.lower())
        for trait in existing_dna:
            evidence_by_category.setdefault("owner_dna", []).append(
                f"{trait.trait_name} {trait.category} {' '.join(trait.supporting_signals).lower()}"
            )

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        traits: List[OwnerDNATrait] = []

        for rule in OWNER_DNA_RULES:
            texts: List[str] = []
            for source in rule["sources"]:
                texts.extend(evidence_by_category.get(source, []))
            if not texts:
                continue
            positive_hits = 0
            supporting_signals: List[str] = []
            for text in texts:
                matched = [token for token in rule["positive"] if token in text]
                if not matched:
                    continue
                positive_hits += 1
                supporting_signals.extend(matched[:2])
            contradiction_hits = sum(
                1
                for text in texts
                if any(token in text for token in rule["negative"])
            )
            if positive_hits < 2:
                continue
            if contradiction_hits >= positive_hits:
                continue
            stability = min(1.0, positive_hits / max(positive_hits + contradiction_hits, 1))
            confidence = min(0.95, 0.58 + min(positive_hits, 5) * 0.07 + stability * 0.12)
            if confidence < 0.72:
                continue
            traits.append(
                OwnerDNATrait(
                    trait_name=rule["trait_name"],
                    category=rule["category"],
                    confidence=confidence,
                    evidence_count=positive_hits,
                    last_updated=now,
                    supporting_signals=tuple(dict.fromkeys(supporting_signals))[:5],
                    stability_score=stability,
                    applicability_notes=rule["applicability"],
                )
            )

        traits.sort(
            key=lambda trait: (
                trait.confidence,
                trait.evidence_count,
                trait.stability_score,
            ),
            reverse=True,
        )
        return traits

    @staticmethod
    def _owner_applicability_note(store) -> str:
        for record in store.list_structured_records(target="user"):
            if record.category == "owner_preference":
                return record.content
        return ""

    @staticmethod
    def _extract_workflow_from_records(records: List[MemoryRecord]) -> str:
        for record in records:
            if record.category != "procedural":
                continue
            marker = ": "
            if marker not in record.content:
                continue
            _, workflow = record.content.rsplit(marker, 1)
            workflow = workflow.strip()
            if " -> " in workflow:
                return workflow
        return ""
