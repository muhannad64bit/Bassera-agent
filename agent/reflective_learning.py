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
    OwnerDoctrineRule,
    SkillCandidate,
    build_memory_key,
    owner_dna_trait_to_record,
    owner_doctrine_rule_to_record,
    parse_owner_dna_trait,
    parse_owner_doctrine_rule,
    parse_skill_candidate,
    skill_candidate_to_record,
)


# Negation/safety prefixes that flip a would-be "negative" token into positive
# evidence. "avoid destructive", "non-destructive", and "no rewrite" express
# the *safe* stance a rule is looking for, not the recklessness the negative
# list is meant to penalize. Without this, "Avoids destructive rewrites" is
# scored as a contradiction against the very safety doctrine it supports,
# silently suppressing it. We look at a small window before each occurrence
# so a token only counts as a real contradiction when it appears in a
# non-negated context.
_NEGATION_PREFIXES = (
    "avoid ",
    "avoids ",
    "avoided ",
    "non-",
    "no ",
    "without ",
    "not ",
    "never ",
    "rather than ",
    "instead of ",
)


def _has_real_negative(text: str, negative_tokens: Iterable[str]) -> bool:
    """True if any negative token occurs in a non-negated context.

    A token is ignored when every occurrence is preceded (within ~14 chars) by
    a negation/safety prefix such as "avoid ", "non-", or "no ". This keeps the
    contradiction signal precise: "destructive" in "avoid destructive changes"
    is positive evidence, while "destructive" in "aggressive destructive
    refactor" is a real contradiction.
    """
    for token in negative_tokens:
        idx = 0
        while True:
            pos = text.find(token, idx)
            if pos == -1:
                break
            prefix = text[max(0, pos - 14):pos]
            if not any(neg in prefix for neg in _NEGATION_PREFIXES):
                return True
            idx = pos + len(token)
    return False


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
        "min_distinct_records": 2,
        "applicability": "Applies when presenting answers, plans, and summaries.",
    },
    {
        "trait_name": "incremental_validation_workflow",
        "category": "workflow_preference",
        "positive": ("focused test", "pytest", "incremental", "before broader", "validation", "workflow reuse"),
        "negative": ("one-shot", "skip validation", "big bang"),
        "sources": {"procedural", "skill_candidate", "episodic"},
        "min_distinct_records": 2,
        "applicability": "Applies to debugging, implementation, and verification tasks.",
    },
    {
        "trait_name": "backward_compatible_change_bias",
        "category": "decision_preference",
        "positive": ("backward compatibility", "incremental", "safe", "non-destructive", "low-risk"),
        "negative": ("rewrite the whole runtime", "destructive", "big-bang"),
        "sources": {"owner_preference", "reflective", "owner_dna", "episodic"},
        "min_distinct_records": 2,
        "applicability": "Applies when choosing between refactors, migrations, and patches.",
    },
    {
        "trait_name": "controlled_execution_risk_posture",
        "category": "risk_posture",
        "positive": ("avoid destructive", "safe", "approvals", "verify", "controlled", "check inputs"),
        "negative": ("reckless", "force through", "ignore approvals"),
        "sources": {"owner_preference", "reflective", "owner_dna", "episodic"},
        "min_distinct_records": 2,
        "applicability": "Applies to execution, tool use, and environment changes.",
    },
    {
        "trait_name": "owner_specific_memory_growth_mission",
        "category": "mission_pattern",
        "positive": ("memory", "owner-specific", "personal", "continuity", "self-improving", "maintainability"),
        "negative": (),
        "sources": {"episodic", "skill_candidate", "owner_preference", "owner_dna"},
        "min_distinct_records": 2,
        "applicability": "Applies to long-term product direction and prioritization.",
    },
)

OWNER_DOCTRINE_RULES: tuple[Dict[str, Any], ...] = (
    {
        "doctrine_name": "plan_incrementally_before_expanding_scope",
        "category": "planning_style",
        "guidance": "Prefer incremental planning that starts with the smallest viable verification loop before broadening scope.",
        "positive": ("incremental", "focused test", "before broader", "validation", "workflow reuse"),
        "negative": ("big bang", "rewrite the whole runtime", "one-shot"),
        "sources": {"owner_dna", "owner_preference", "procedural", "skill_candidate"},
        "min_distinct_records": 2,
        "applicability": "Default planning behavior; explicit user urgency or scope overrides this.",
    },
    {
        "doctrine_name": "execute_with_minimal_safe_change_first",
        "category": "execution_style",
        "guidance": "Default to the smallest safe change that resolves the task before considering broader redesign.",
        "positive": ("safe", "incremental", "minimal", "non-destructive", "backward compatibility"),
        "negative": ("aggressive refactor", "destructive", "rewrite the whole runtime"),
        "sources": {"owner_dna", "owner_preference", "reflective", "episodic"},
        "min_distinct_records": 2,
        "applicability": "Applies when choosing an implementation path under ambiguity.",
    },
    {
        "doctrine_name": "safety_over_speed_for_high_impact_actions",
        "category": "safety_posture",
        "guidance": "For high-impact actions, optimize for safety and reversibility before speed.",
        "positive": ("avoid destructive", "safe", "verify", "controlled", "approvals"),
        "negative": ("force through", "ignore approvals", "reckless"),
        "sources": {"owner_dna", "owner_preference", "reflective"},
        "min_distinct_records": 2,
        "applicability": "Applies to destructive commands, risky edits, and environment changes.",
    },
    {
        "doctrine_name": "ask_before_high_impact_or_ambiguous_actions",
        "category": "autonomy_threshold",
        "guidance": "Act directly on low-risk clear tasks, but ask first when actions are high-impact, ambiguous, or hard to reverse.",
        "positive": ("ask before", "approvals", "controlled", "verify", "high-impact"),
        "negative": ("act without asking", "ignore approvals"),
        "sources": {"owner_preference", "owner_dna", "reflective"},
        "min_distinct_records": 2,
        "applicability": "Doctrine guides defaults only; explicit user commands and platform approval flows still govern.",
    },
    {
        "doctrine_name": "preserve_structure_before_refactoring",
        "category": "refactor_bias",
        "guidance": "Preserve working structure and interfaces unless repeated evidence shows redesign is necessary.",
        "positive": ("backward compatibility", "preserve", "incremental", "safe"),
        "negative": ("redesign", "rewrite", "aggressive refactor"),
        "sources": {"owner_dna", "owner_preference", "episodic", "reflective"},
        "min_distinct_records": 2,
        "applicability": "Applies to existing codepaths and stable runtime components.",
    },
    {
        "doctrine_name": "respond_directly_and_concisely_by_default",
        "category": "verbosity_preference",
        "guidance": "Default to direct, concise communication unless the user explicitly asks for depth.",
        "positive": ("concise", "brief", "direct", "focused", "no fluff"),
        "negative": ("verbose", "digression", "thorough"),
        "sources": {"owner_dna", "owner_preference"},
        "min_distinct_records": 2,
        "applicability": "Applies to explanations, plans, and status updates.",
    },
    {
        "doctrine_name": "intervene_when_repeated_patterns_are_reusable",
        "category": "intervention_preference",
        "guidance": "Promote reusable patterns and continuity help only after repeated success signals, not from one-off events.",
        "positive": ("workflow reuse", "repeated", "candidate", "continuity", "formalizing"),
        "negative": ("one-off", "single event"),
        "sources": {"skill_candidate", "reflective", "episodic"},
        "min_distinct_records": 2,
        "applicability": "Applies to suggesting process improvements and reusable workflows.",
    },
    {
        "doctrine_name": "validate_before_promoting_or_finishing",
        "category": "validation_rigor",
        "guidance": "Prefer focused validation before claiming completion or promoting a pattern as doctrine.",
        "positive": ("focused test", "validation", "validate", "verify", "verified", "pytest", "before broader"),
        "negative": ("skip validation", "ship without checking"),
        "sources": {"owner_dna", "owner_preference", "procedural", "skill_candidate", "reflective", "episodic"},
        "min_distinct_records": 2,
        "applicability": "Applies to code changes, debugging, and memory promotion.",
    },
    {
        "doctrine_name": "maintain_workflow_discipline_and_continuity",
        "category": "workflow_discipline",
        "guidance": "Prefer continuity with the current task thread and reuse established workflows before inventing a new path.",
        "positive": ("continuity", "workflow reuse", "current task", "preserve", "ongoing work"),
        "negative": ("reset context", "start over", "discard workflow"),
        "sources": {"owner_dna", "episodic", "skill_candidate", "procedural"},
        "min_distinct_records": 2,
        "applicability": "Applies to follow-up work and ongoing task threads.",
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

    def __init__(
        self,
        *,
        max_records: int = 4,
        max_candidates: int = 2,
        max_owner_dna_traits: int = 3,
        max_owner_doctrine_rules: int = 4,
    ):
        self.max_records = max_records
        self.max_candidates = max_candidates
        self.max_owner_dna_traits = max_owner_dna_traits
        self.max_owner_doctrine_rules = max_owner_doctrine_rules

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
        for record in self._build_owner_doctrine_records(
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

    def _build_owner_doctrine_records(
        self,
        *,
        store,
        messages: List[Dict[str, Any]],
        user_message: str,
        assistant_response: str,
        base_records: List[MemoryRecord],
    ) -> List[MemoryRecord]:
        rules = self._derive_owner_doctrine_rules(
            store=store,
            messages=messages,
            user_message=user_message,
            assistant_response=assistant_response,
            base_records=base_records,
        )
        return [
            owner_doctrine_rule_to_record(rule)
            for rule in rules[: self.max_owner_doctrine_rules]
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
        evidence_by_category: Dict[str, List[tuple[str, str]]] = {}
        for record in evidence_records:
            evidence_by_category.setdefault(record.category, []).append(
                (record.content.lower(), record.content)
            )
        for trait in existing_dna:
            evidence_by_category.setdefault("owner_dna", []).append(
                (
                    f"{trait.trait_name} {trait.category} {' '.join(trait.supporting_signals).lower()} "
                    f"{' '.join(trait.supporting_examples).lower()}",
                    f"{trait.trait_name}: {'; '.join(trait.supporting_examples or trait.supporting_signals)}",
                )
            )

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        traits: List[OwnerDNATrait] = []
        existing_dna_by_name = {trait.trait_name: trait for trait in existing_dna}

        for rule in OWNER_DNA_RULES:
            evidence_items: List[tuple[str, str, str]] = []
            for source in rule["sources"]:
                for normalized_text, original_text in evidence_by_category.get(source, []):
                    evidence_items.append((source, normalized_text, original_text))
            if not evidence_items:
                continue
            positive_hits = 0
            matched_records: List[tuple[str, str]] = []
            supporting_signals: List[str] = []
            supporting_examples: List[str] = []
            supporting_sources: set[str] = set()
            for source, text, original_text in evidence_items:
                matched = [token for token in rule["positive"] if token in text]
                if not matched:
                    continue
                positive_hits += 1
                matched_records.append((source, original_text))
                supporting_sources.add(source)
                supporting_signals.extend(matched[:2])
                supporting_examples.append(_summarize_prompt(original_text, limit=96))
            contradiction_hits = sum(
                1
                for _, text, _ in evidence_items
                if _has_real_negative(text, rule["negative"])
            )
            distinct_records = len({example for _, example in matched_records})
            if positive_hits < 2:
                continue
            if distinct_records < int(rule.get("min_distinct_records", 2)):
                continue
            # A single source is acceptable when at least two distinct records
            # corroborate it (min_distinct_records already enforces distinctness).
            if len(supporting_sources) < 2 and positive_hits < 2:
                continue
            if contradiction_hits >= positive_hits:
                continue
            stability = min(1.0, distinct_records / max(distinct_records + contradiction_hits, 1))
            confidence = min(0.95, 0.58 + min(positive_hits, 5) * 0.07 + stability * 0.12)
            if confidence < 0.72:
                continue
            existing_trait = existing_dna_by_name.get(rule["trait_name"])
            evidence_count = positive_hits
            if existing_trait is not None:
                evidence_count = max(existing_trait.evidence_count + 1, positive_hits)
                supporting_signals = list(existing_trait.supporting_signals) + supporting_signals
                supporting_examples = list(existing_trait.supporting_examples) + supporting_examples
            traits.append(
                OwnerDNATrait(
                    trait_name=rule["trait_name"],
                    category=rule["category"],
                    confidence=confidence,
                    evidence_count=evidence_count,
                    last_updated=now,
                    supporting_signals=tuple(dict.fromkeys(supporting_signals))[:5],
                    supporting_examples=tuple(dict.fromkeys(supporting_examples))[:3],
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

    def _derive_owner_doctrine_rules(
        self,
        *,
        store,
        messages: List[Dict[str, Any]],
        user_message: str,
        assistant_response: str,
        base_records: List[MemoryRecord],
    ) -> List[OwnerDoctrineRule]:
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
        existing_doctrine = [
            rule
            for rule in (
                parse_owner_doctrine_rule(record)
                for record in existing_user_records
            )
            if rule is not None
        ]

        evidence_by_category: Dict[str, List[tuple[str, str]]] = {}
        for record in [*existing_user_records, *existing_memory_records, *current_records]:
            evidence_by_category.setdefault(record.category, []).append(
                (record.content.lower(), record.content)
            )
        for trait in existing_dna:
            evidence_by_category.setdefault("owner_dna", []).append(
                (
                    f"{trait.trait_name} {trait.category} {' '.join(trait.supporting_signals).lower()} {' '.join(trait.supporting_examples).lower()}",
                    f"{trait.trait_name}: {'; '.join(trait.supporting_examples or trait.supporting_signals)}",
                )
            )
        for rule in existing_doctrine:
            evidence_by_category.setdefault("owner_doctrine", []).append(
                (
                    f"{rule.doctrine_name} {rule.category} {rule.guidance.lower()} {' '.join(rule.supporting_signals).lower()}",
                    f"{rule.doctrine_name}: {rule.guidance}",
                )
            )

        existing_doctrine_by_name = {rule.doctrine_name: rule for rule in existing_doctrine}
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        doctrine_rules: List[OwnerDoctrineRule] = []

        for doctrine in OWNER_DOCTRINE_RULES:
            evidence_items: List[tuple[str, str, str]] = []
            for source in doctrine["sources"]:
                for normalized_text, original_text in evidence_by_category.get(source, []):
                    evidence_items.append((source, normalized_text, original_text))
            if not evidence_items:
                continue

            positive_hits = 0
            contradiction_hits = 0
            supporting_signals: List[str] = []
            supporting_examples: List[str] = []
            supporting_sources: set[str] = set()
            distinct_examples: set[str] = set()

            for source, text, original_text in evidence_items:
                positive = [token for token in doctrine["positive"] if token in text]
                if _has_real_negative(text, doctrine["negative"]):
                    contradiction_hits += 1
                if not positive:
                    continue
                positive_hits += 1
                supporting_sources.add(source)
                distinct_examples.add(original_text)
                supporting_signals.extend(positive[:2])
                supporting_examples.append(_summarize_prompt(original_text, limit=96))

            distinct_records = len(distinct_examples)
            if positive_hits < 2:
                continue
            if distinct_records < int(doctrine.get("min_distinct_records", 2)):
                continue
            if len(supporting_sources) < 2 and positive_hits < 3:
                continue

            stability = min(1.0, distinct_records / max(distinct_records + contradiction_hits, 1))
            confidence = min(0.95, 0.56 + min(positive_hits, 5) * 0.07 + stability * 0.14 - min(contradiction_hits, 3) * 0.04)
            if confidence < 0.74:
                continue

            existing_rule = existing_doctrine_by_name.get(doctrine["doctrine_name"])
            evidence_count = positive_hits
            if existing_rule is not None:
                evidence_count = max(existing_rule.evidence_count + 1, positive_hits)
                supporting_signals = list(existing_rule.supporting_signals) + supporting_signals
                supporting_examples = list(existing_rule.supporting_examples) + supporting_examples

            doctrine_rules.append(
                OwnerDoctrineRule(
                    doctrine_name=doctrine["doctrine_name"],
                    category=doctrine["category"],
                    guidance=doctrine["guidance"],
                    confidence=confidence,
                    evidence_count=evidence_count,
                    last_updated=now,
                    supporting_signals=tuple(dict.fromkeys(supporting_signals))[:5],
                    supporting_examples=tuple(dict.fromkeys(supporting_examples))[:3],
                    stability_score=stability,
                    applicability_notes=doctrine["applicability"],
                )
            )

        doctrine_rules.sort(
            key=lambda rule: (
                rule.confidence,
                rule.evidence_count,
                rule.stability_score,
            ),
            reverse=True,
        )
        return doctrine_rules

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
