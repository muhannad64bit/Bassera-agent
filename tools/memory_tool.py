#!/usr/bin/env python3
"""
Memory Tool Module - Persistent Curated Memory

Provides bounded, file-backed memory that persists across sessions. Two stores:
  - MEMORY.md: agent's personal notes and observations (environment facts, project
    conventions, tool quirks, things learned)
  - USER.md: what the agent knows about the user (preferences, communication style,
    expectations, workflow habits)

Both are injected into the system prompt as a frozen snapshot at session start.
Mid-session writes update files on disk immediately (durable) but do NOT change
the system prompt -- this preserves the prefix cache for the entire session.
The snapshot refreshes on the next session start.

Entry delimiter: § (section sign). Entries can be multiline.
Character limits (not tokens) because char counts are model-independent.

Design:
- Single `memory` tool with action parameter: add, replace, remove, read
- replace/remove use short unique substring matching (not full text or IDs)
- Behavioral guidance lives in the tool schema description
- Frozen snapshot pattern: system prompt is stable, tool responses show live state
"""

import json
import logging
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from wafi_constants import get_wafi_home
from typing import Dict, Any, List, Optional
from agent.memory_schema import (
    MemoryRecord,
    format_memory_record,
    merge_memory_records,
    MemoryCategory,
    normalize_memory_record,
    normalize_memory_timestamp,
    parse_owner_dna_trait,
    parse_memory_record,
)

# fcntl is Unix-only; on Windows use msvcrt for file locking
msvcrt = None
try:
    import fcntl
except ImportError:
    fcntl = None
    try:
        import msvcrt
    except ImportError:
        pass

logger = logging.getLogger(__name__)

# Where memory files live — resolved dynamically so profile overrides
# (HERMES_HOME env var changes) are always respected.  The old module-level
# constant was cached at import time and could go stale if a profile switch
# happened after the first import.
def get_memory_dir() -> Path:
    """Return the profile-scoped memories directory."""
    return get_wafi_home() / "memories"

ENTRY_DELIMITER = "\n§\n"

_WORD_RE = re.compile(r"[a-z0-9_]{3,}")
_STOPWORDS = {
    "about", "after", "again", "agent", "also", "been", "being", "from", "have",
    "into", "just", "like", "make", "more", "need", "only", "over", "same",
    "some", "than", "that", "them", "then", "they", "this", "turn", "user",
    "using", "with", "your",
}
_PREFERENCE_HINTS = (
    "prefer", "preference", "style", "tone", "format", "always", "never",
    "avoid", "please", "concise", "detailed", "brief",
)
_PROCEDURAL_HINTS = (
    "fix", "debug", "implement", "build", "update", "run", "test", "edit",
    "write", "patch", "refactor", "execute", "deploy",
)
_FAILURE_HINTS = (
    "fail", "failure", "error", "broken", "issue", "problem", "stuck",
    "uncertain", "unsure", "retry", "wrong",
)
_CONTINUITY_HINTS = (
    "continue", "continued", "again", "earlier", "before", "previous",
    "last", "resume", "pick up", "we were",
)
_WORKFLOW_TERMS = (
    "debug", "fix", "implement", "patch", "refactor", "inspect", "trace",
    "run", "test", "build", "deploy", "review", "edit", "write", "update",
    "module", "file", "function", "class",
)
_COMMAND_PATTERN_RE = re.compile(r"\b(?:python|pytest|rg|git|npm|pnpm|yarn|uv|pip|make|cargo)\b[^\n]{0,80}")
_PATH_PATTERN_RE = re.compile(r"(?:[a-zA-Z0-9_.-]+/)+[a-zA-Z0-9_.-]+|[a-zA-Z0-9_.-]+\.(?:py|ts|tsx|js|jsx|md|json|yaml|yml|toml|sh|sql)\b")
_CODE_SYMBOL_RE = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]{2,}\b")
_RECENCY_WINDOW = 6


# ---------------------------------------------------------------------------
# Memory content scanning — lightweight check for injection/exfiltration
# in content that gets injected into the system prompt.
# ---------------------------------------------------------------------------

_MEMORY_THREAT_PATTERNS = [
    # Prompt injection
    (r'ignore\s+(previous|all|above|prior)\s+instructions', "prompt_injection"),
    (r'you\s+are\s+now\s+', "role_hijack"),
    (r'do\s+not\s+tell\s+the\s+user', "deception_hide"),
    (r'system\s+prompt\s+override', "sys_prompt_override"),
    (r'disregard\s+(your|all|any)\s+(instructions|rules|guidelines)', "disregard_rules"),
    (r'act\s+as\s+(if|though)\s+you\s+(have\s+no|don\'t\s+have)\s+(restrictions|limits|rules)', "bypass_restrictions"),
    # Exfiltration via curl/wget with secrets
    (r'curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)', "exfil_curl"),
    (r'wget\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)', "exfil_wget"),
    (r'cat\s+[^\n]*(\.env|credentials|\.netrc|\.pgpass|\.npmrc|\.pypirc)', "read_secrets"),
    # Persistence via shell rc
    (r'authorized_keys', "ssh_backdoor"),
    (r'\$HOME/\.ssh|\~/\.ssh', "ssh_access"),
    (r'\$HOME/\.wafi/\.env|\~/\.wafi/\.env', "wafi_env"),
]

# Subset of invisible chars for injection detection
_INVISIBLE_CHARS = {
    '\u200b', '\u200c', '\u200d', '\u2060', '\ufeff',
    '\u202a', '\u202b', '\u202c', '\u202d', '\u202e',
}


def _scan_memory_content(content: str) -> Optional[str]:
    """Scan memory content for injection/exfil patterns. Returns error string if blocked."""
    # Check invisible unicode
    for char in _INVISIBLE_CHARS:
        if char in content:
            return f"Blocked: content contains invisible unicode character U+{ord(char):04X} (possible injection)."

    # Check threat patterns
    for pattern, pid in _MEMORY_THREAT_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            return f"Blocked: content matches threat pattern '{pid}'. Memory entries are injected into the system prompt and must not contain injection or exfiltration payloads."

    return None


class MemoryStore:
    """
    Bounded curated memory with file persistence. One instance per AIAgent.

    Maintains two parallel states:
      - _system_prompt_snapshot: frozen at load time, used for system prompt injection.
        Never mutated mid-session. Keeps prefix cache stable.
      - memory_entries / user_entries: live state, mutated by tool calls, persisted to disk.
        Tool responses always reflect this live state.
    """

    def __init__(self, memory_char_limit: int = 2200, user_char_limit: int = 1375):
        self.memory_entries: List[str] = []
        self.user_entries: List[str] = []
        self.memory_char_limit = memory_char_limit
        self.user_char_limit = user_char_limit
        self._active_session_id: str = ""
        # Frozen snapshot for system prompt -- set once at load_from_disk()
        self._system_prompt_snapshot: Dict[str, str] = {"memory": "", "user": ""}

    def load_from_disk(self):
        """Load entries from MEMORY.md and USER.md, capture system prompt snapshot."""
        mem_dir = get_memory_dir()
        mem_dir.mkdir(parents=True, exist_ok=True)

        self.memory_entries = self._read_file(mem_dir / "MEMORY.md")
        self.user_entries = self._read_file(mem_dir / "USER.md")

        # Deduplicate entries (preserves order, keeps first occurrence)
        self.memory_entries = list(dict.fromkeys(self.memory_entries))
        self.user_entries = list(dict.fromkeys(self.user_entries))

        # Capture frozen snapshot for system prompt injection
        self._system_prompt_snapshot = {
            "memory": self._render_block("memory", self.memory_entries),
            "user": self._render_block("user", self.user_entries),
        }

    @staticmethod
    @contextmanager
    def _file_lock(path: Path):
        """Acquire an exclusive file lock for read-modify-write safety.

        Uses a separate .lock file so the memory file itself can still be
        atomically replaced via os.replace().
        """
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        if fcntl is None and msvcrt is None:
            yield
            return

        if msvcrt and (not lock_path.exists() or lock_path.stat().st_size == 0):
            lock_path.write_text(" ", encoding="utf-8")

        fd = open(lock_path, "r+" if msvcrt else "a+")
        try:
            if fcntl:
                fcntl.flock(fd, fcntl.LOCK_EX)
            else:
                fd.seek(0)
                msvcrt.locking(fd.fileno(), msvcrt.LK_LOCK, 1)
            yield
        finally:
            if fcntl:
                fcntl.flock(fd, fcntl.LOCK_UN)
            elif msvcrt:
                try:
                    fd.seek(0)
                    msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
                except (OSError, IOError):
                    pass
            fd.close()

    @staticmethod
    def _path_for(target: str) -> Path:
        mem_dir = get_memory_dir()
        if target == "user":
            return mem_dir / "USER.md"
        return mem_dir / "MEMORY.md"

    def _reload_target(self, target: str):
        """Re-read entries from disk into in-memory state.

        Called under file lock to get the latest state before mutating.
        """
        fresh = self._read_file(self._path_for(target))
        fresh = list(dict.fromkeys(fresh))  # deduplicate
        self._set_entries(target, fresh)

    def save_to_disk(self, target: str):
        """Persist entries to the appropriate file. Called after every mutation."""
        get_memory_dir().mkdir(parents=True, exist_ok=True)
        self._write_file(self._path_for(target), self._entries_for(target))

    def set_active_session(self, session_id: str) -> None:
        """Attach the current runtime session to structured memory writes/ranking."""
        self._active_session_id = (session_id or "").strip()

    def build_recall_context(
        self,
        query: str,
        *,
        max_entries: int = 6,
        max_chars: int = 1200,
    ) -> str:
        """Build a ranked recall block from the existing durable memory entries."""
        ranked = self.rank_entries_for_query(query, max_entries=max_entries)
        if not ranked:
            return ""

        lines: List[str] = ["BUILT-IN MEMORY RECALL"]
        current_chars = len(lines[0])
        for item in ranked:
            line = f"- {item['label']}: {item['content']}"
            projected = current_chars + 1 + len(line)
            if len(lines) > 1 and projected > max_chars:
                break
            lines.append(line)
            current_chars = projected
        return "\n".join(lines) if len(lines) > 1 else ""

    def list_structured_records(self, *, target: Optional[str] = None) -> List[MemoryRecord]:
        """Return parsed structured memory records from the existing memory files."""
        pairs: List[tuple[str, List[str]]] = []
        if target in (None, "memory"):
            pairs.append(("memory", self.memory_entries))
        if target in (None, "user"):
            pairs.append(("user", self.user_entries))

        records: List[MemoryRecord] = []
        for record_target, entries in pairs:
            for entry in entries:
                parsed = parse_memory_record(entry, default_target=record_target)
                if parsed is not None:
                    records.append(parsed)
        return records

    def rank_entries_for_query(self, query: str, *, max_entries: int = 6) -> List[Dict[str, Any]]:
        """Rank structured and legacy entries together for a user query."""
        candidates: List[Dict[str, Any]] = []
        candidates.extend(self._rank_target_entries("user", self.user_entries, query))
        candidates.extend(self._rank_target_entries("memory", self.memory_entries, query))
        self._apply_structured_freshness_adjustments(candidates)
        candidates.sort(
            key=lambda item: (
                item["score"],
                item["session_match"],
                item["updated_at_score"],
                item["is_structured"],
                item["hits"],
                item["confidence"],
                item["recency_rank"],
            ),
            reverse=True,
        )
        return candidates[:max_entries]

    def _rank_target_entries(
        self,
        target: str,
        entries: List[str],
        query: str,
    ) -> List[Dict[str, Any]]:
        query_terms = _extract_query_terms(query)
        query_signals = _extract_continuity_signals(query)
        candidates: List[Dict[str, Any]] = []
        total = len(entries)
        for idx, entry in enumerate(entries):
            parsed = parse_memory_record(entry, default_target=target)
            content = parsed.content if parsed else entry.strip()
            if not content:
                continue
            category = parsed.category if parsed else None
            owner_dna = parse_owner_dna_trait(parsed) if parsed and category == "owner_dna" else None
            if owner_dna is not None and (owner_dna.evidence_count < 2 or owner_dna.confidence < 0.72):
                continue
            overlap = len(query_terms & _extract_query_terms(content))
            continuity = self._continuity_signal_strength(
                query=query,
                content=content,
                query_signals=query_signals,
            )
            score = self._score_entry(
                query=query,
                target=target,
                content=content,
                category=category,
                overlap=overlap,
                hits=parsed.hits if parsed else 1,
                confidence=parsed.confidence if parsed else 0.45,
                recency_rank=total - idx,
                updated_at=parsed.updated_at if parsed else "",
                session_id=parsed.session_id if parsed else "",
                continuity=continuity,
            )
            if score <= 0:
                continue
            label = self._entry_label(target, category)
            updated_at_score = self._updated_at_score(parsed.updated_at if parsed else "")
            session_match = bool(parsed and self._active_session_id and parsed.session_id == self._active_session_id)
            candidates.append(
                {
                    "target": target,
                    "content": content,
                    "category": category,
                    "score": score,
                    "label": label,
                    "is_structured": bool(parsed),
                    "hits": parsed.hits if parsed else 1,
                    "confidence": parsed.confidence if parsed else 0.45,
                    "recency_rank": total - idx,
                    "updated_at": parsed.updated_at if parsed else "",
                    "session_id": parsed.session_id if parsed else "",
                    "updated_at_score": updated_at_score,
                    "session_match": session_match,
                    "overlap": overlap,
                    "continuity": continuity,
                    "key": parsed.key if parsed else "",
                    "namespace": self._key_namespace(parsed.key) if parsed else "",
                    "owner_dna": owner_dna,
                }
            )
        return candidates

    def _score_entry(
        self,
        *,
        query: str,
        target: str,
        content: str,
        category: Optional[MemoryCategory],
        overlap: int,
        hits: int,
        confidence: float,
        recency_rank: int,
        updated_at: str,
        session_id: str,
        continuity: Dict[str, Any],
    ) -> float:
        query_lower = (query or "").lower()
        score = 0.2
        score += min(overlap, 4) * 1.4
        score += min(max(hits, 1), 6) * 0.2
        score += max(0.0, min(confidence, 1.0)) * 0.8
        score += self._updated_at_score(updated_at)
        score += min(float(continuity.get("shared_terms_score", 0.0)), 2.5)
        if recency_rank <= _RECENCY_WINDOW:
            score += (_RECENCY_WINDOW - recency_rank + 1) * 0.15
        if session_id and self._active_session_id and session_id == self._active_session_id:
            score += 1.2

        if target == "user":
            score += 0.6

        if category is None:
            if overlap == 0 and not self._looks_globally_relevant(query_lower, content.lower()):
                if recency_rank > 2:
                    return 0.0
                score += 0.35
            return score

        score += self._category_base_score(category)

        if category == "owner_preference":
            if any(hint in query_lower for hint in _PREFERENCE_HINTS):
                score += 3.0
            elif overlap > 0:
                score += 1.6
            else:
                score += 0.8
        elif category == "procedural":
            if any(hint in query_lower for hint in _PROCEDURAL_HINTS):
                score += 2.2
            if overlap > 0:
                score += 1.0
        elif category == "reflective":
            if any(hint in query_lower for hint in _FAILURE_HINTS):
                score += 2.6
            if "how" in query_lower or "should" in query_lower:
                score += 0.8
        elif category == "episodic":
            if any(hint in query_lower for hint in _CONTINUITY_HINTS):
                score += 2.0
            if overlap > 0:
                score += 0.8
            if continuity.get("continuation_query"):
                score += 1.8
            if continuity.get("shared_paths"):
                score += min(len(continuity["shared_paths"]), 2) * 1.2
            if continuity.get("shared_commands"):
                score += min(len(continuity["shared_commands"]), 2) * 0.9
            if continuity.get("shared_workflows"):
                score += min(len(continuity["shared_workflows"]), 3) * 0.6
            if continuity.get("shared_symbols"):
                score += min(len(continuity["shared_symbols"]), 3) * 0.45
            if continuity.get("active_work_context"):
                score += 1.1
        elif category == "semantic":
            if overlap > 0:
                score += 0.9
        elif category == "skill_candidate":
            if any(hint in query_lower for hint in _PROCEDURAL_HINTS) or continuity.get("active_work_context"):
                score += 1.2
            if overlap > 0:
                score += 0.8
        elif category == "owner_dna":
            if any(hint in query_lower for hint in _PREFERENCE_HINTS):
                score += 2.6
            if any(term in query_lower for term in ("workflow", "plan", "strategy", "approach", "decision", "risk", "priority")):
                score += 2.0
            if overlap > 0:
                score += 1.1

        if overlap == 0 and not self._looks_globally_relevant(query_lower, content.lower(), category):
            score -= 1.2
        return max(score, 0.0)

    def _apply_structured_freshness_adjustments(self, candidates: List[Dict[str, Any]]) -> None:
        """Prefer newer relevant structured evidence over stale competing records."""
        structured = [item for item in candidates if item["is_structured"]]
        if not structured:
            return

        freshest_by_category: Dict[tuple[Optional[MemoryCategory], int], Dict[str, Any]] = {}
        freshest_by_namespace: Dict[tuple[str, int], Dict[str, Any]] = {}
        for item in structured:
            overlap_bucket = min(int(item.get("overlap", 0)), 2)
            category_key = (item.get("category"), overlap_bucket)
            namespace = item.get("namespace") or ""
            namespace_key = (namespace, overlap_bucket) if namespace else None

            current = freshest_by_category.get(category_key)
            if current is None or self._is_fresher(item, current):
                freshest_by_category[category_key] = item

            if namespace_key is not None:
                current_ns = freshest_by_namespace.get(namespace_key)
                if current_ns is None or self._is_fresher(item, current_ns):
                    freshest_by_namespace[namespace_key] = item

        for item in structured:
            overlap_bucket = min(int(item.get("overlap", 0)), 2)
            freshest = freshest_by_category.get((item.get("category"), overlap_bucket))
            if freshest is not None and freshest is not item and self._is_materially_staler(item, freshest):
                item["score"] = max(0.0, item["score"] - 0.9)

            namespace = item.get("namespace") or ""
            if namespace:
                freshest_ns = freshest_by_namespace.get((namespace, overlap_bucket))
                if freshest_ns is not None and freshest_ns is not item and self._is_materially_staler(item, freshest_ns):
                    item["score"] = max(0.0, item["score"] - 0.6)

            if item.get("session_match") and item.get("hits", 1) >= 2 and item.get("category") in {"procedural", "reflective"}:
                item["score"] += 0.5
            if item.get("category") == "episodic":
                continuity = item.get("continuity") or {}
                if continuity.get("active_work_context") and item.get("session_match"):
                    item["score"] += 0.8
                freshest_episode = freshest_by_category.get(("episodic", overlap_bucket))
                if (
                    freshest_episode is not None
                    and freshest_episode is not item
                    and self._is_materially_staler(item, freshest_episode)
                    and (freshest_episode.get("continuity") or {}).get("shared_terms_score", 0) >= (continuity.get("shared_terms_score", 0))
                ):
                    item["score"] = max(0.0, item["score"] - 1.1)

    def _is_fresher(self, candidate: Dict[str, Any], other: Dict[str, Any]) -> bool:
        candidate_rank = (
            bool(candidate.get("session_match")),
            float(candidate.get("updated_at_score", 0.0)),
            int(candidate.get("recency_rank", 0)),
            int(candidate.get("hits", 1)),
        )
        other_rank = (
            bool(other.get("session_match")),
            float(other.get("updated_at_score", 0.0)),
            int(other.get("recency_rank", 0)),
            int(other.get("hits", 1)),
        )
        return candidate_rank > other_rank

    def _is_materially_staler(self, candidate: Dict[str, Any], newer: Dict[str, Any]) -> bool:
        if bool(candidate.get("session_match")) and not bool(newer.get("session_match")):
            return False
        newer_delta = float(newer.get("updated_at_score", 0.0)) - float(candidate.get("updated_at_score", 0.0))
        recency_delta = int(newer.get("recency_rank", 0)) - int(candidate.get("recency_rank", 0))
        return newer_delta >= 0.6 or recency_delta >= 2

    @staticmethod
    def _key_namespace(key: str) -> str:
        raw = (key or "").strip()
        if not raw:
            return ""
        return raw.split(":", 1)[0]

    @staticmethod
    def _updated_at_score(updated_at: str) -> float:
        raw = normalize_memory_timestamp(updated_at)
        if not raw:
            return 0.0
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        age_seconds = max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds())
        if age_seconds <= 3600:
            return 1.8
        if age_seconds <= 6 * 3600:
            return 1.4
        if age_seconds <= 24 * 3600:
            return 1.0
        if age_seconds <= 3 * 24 * 3600:
            return 0.7
        if age_seconds <= 7 * 24 * 3600:
            return 0.4
        return 0.0

    @staticmethod
    def _category_base_score(category: MemoryCategory) -> float:
        weights: Dict[MemoryCategory, float] = {
            "owner_preference": 2.4,
            "procedural": 1.7,
            "reflective": 1.5,
            "semantic": 1.2,
            "episodic": 1.0,
            "skill_candidate": 1.1,
            "owner_dna": 2.1,
        }
        return weights.get(category, 0.0)

    @staticmethod
    def _looks_globally_relevant(
        query_lower: str,
        content_lower: str,
        category: Optional[MemoryCategory] = None,
    ) -> bool:
        if category == "owner_preference":
            return any(hint in query_lower for hint in _PREFERENCE_HINTS)
        if category == "procedural":
            return any(hint in query_lower for hint in _PROCEDURAL_HINTS)
        if category == "reflective":
            return any(hint in query_lower for hint in _FAILURE_HINTS)
        if category == "episodic":
            return any(hint in query_lower for hint in _CONTINUITY_HINTS)
        if category == "owner_dna":
            return any(
                hint in query_lower
                for hint in (
                    *list(_PREFERENCE_HINTS),
                    "workflow", "plan", "strategy", "approach", "decision",
                    "risk", "priority", "style", "habit",
                )
            )
        return any(term in content_lower for term in _extract_query_terms(query_lower))

    @staticmethod
    def _entry_label(target: str, category: Optional[MemoryCategory]) -> str:
        if category == "owner_preference":
            return "Owner Preference"
        if category == "procedural":
            return "Procedure"
        if category == "reflective":
            return "Lesson"
        if category == "episodic":
            return "Recent Episode"
        if category == "semantic":
            return "Knowledge"
        if category == "skill_candidate":
            return "Skill Candidate"
        if category == "owner_dna":
            return "Owner DNA"
        return "User Memory" if target == "user" else "Memory"

    @staticmethod
    def _continuity_signal_strength(
        *,
        query: str,
        content: str,
        query_signals: Dict[str, Any],
    ) -> Dict[str, Any]:
        content_signals = _extract_continuity_signals(content)
        shared_paths = sorted(query_signals["paths"] & content_signals["paths"])
        shared_commands = sorted(query_signals["commands"] & content_signals["commands"])
        shared_workflows = sorted(query_signals["workflow_terms"] & content_signals["workflow_terms"])
        shared_symbols = sorted(query_signals["symbols"] & content_signals["symbols"])
        shared_terms = query_signals["terms"] & content_signals["terms"]
        shared_terms_score = min(len(shared_terms), 5) * 0.35
        shared_terms_score += min(len(shared_paths), 2) * 0.8
        shared_terms_score += min(len(shared_commands), 2) * 0.6
        shared_terms_score += min(len(shared_workflows), 3) * 0.35
        shared_terms_score += min(len(shared_symbols), 3) * 0.25
        active_work_context = bool(shared_paths or shared_commands or len(shared_workflows) >= 2)
        return {
            "shared_paths": shared_paths,
            "shared_commands": shared_commands,
            "shared_workflows": shared_workflows,
            "shared_symbols": shared_symbols,
            "shared_terms_score": shared_terms_score,
            "continuation_query": bool(query_signals["continuation"]),
            "active_work_context": active_work_context,
        }

    def upsert_record(self, record: MemoryRecord) -> Dict[str, Any]:
        """Add or merge a structured memory record into the existing store.

        Structured records remain plain text entries in MEMORY.md / USER.md.
        This preserves the built-in memory files as the only durable authority.
        """
        normalized = normalize_memory_record(
            MemoryRecord(
                category=record.category,
                content=record.content,
                key=record.key,
                target=record.target,
                hits=record.hits,
                confidence=record.confidence,
                source=record.source,
                updated_at=record.updated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                session_id=record.session_id or self._active_session_id,
            )
        )
        entry = format_memory_record(normalized)

        scan_error = _scan_memory_content(normalized.content)
        if scan_error:
            return {"success": False, "error": scan_error}

        with self._file_lock(self._path_for(normalized.target)):
            self._reload_target(normalized.target)
            entries = self._entries_for(normalized.target)
            limit = self._char_limit(normalized.target)

            match_index = -1
            merged_record = normalized
            for idx, existing_entry in enumerate(entries):
                existing_record = parse_memory_record(
                    existing_entry,
                    default_target=normalized.target,
                )
                if existing_record and existing_record.key == normalized.key:
                    match_index = idx
                    merged_record = merge_memory_records(existing_record, normalized)
                    break

            new_entry = format_memory_record(merged_record)
            test_entries = list(entries)
            action = "add"
            message = "Structured memory added."
            if match_index >= 0:
                test_entries[match_index] = new_entry
                action = "replace"
                message = "Structured memory updated."
            else:
                test_entries.append(new_entry)

            new_total = len(ENTRY_DELIMITER.join(test_entries)) if test_entries else 0
            if new_total > limit:
                current = self._char_count(normalized.target)
                return {
                    "success": False,
                    "error": (
                        f"Memory at {current:,}/{limit:,} chars. "
                        f"Saving structured memory would exceed the limit."
                    ),
                }

            self._set_entries(normalized.target, test_entries)
            self.save_to_disk(normalized.target)

        persisted_record = parse_memory_record(new_entry, default_target=normalized.target) or merged_record
        result = self._success_response(normalized.target, message)
        result.update(
            {
                "action": action,
                "entry": new_entry,
                "record": {
                    "category": persisted_record.category,
                    "key": persisted_record.key,
                    "hits": persisted_record.hits,
                    "confidence": persisted_record.confidence,
                    "source": persisted_record.source,
                    "target": persisted_record.target,
                    "content": persisted_record.content,
                },
            }
        )
        return result

    def _entries_for(self, target: str) -> List[str]:
        if target == "user":
            return self.user_entries
        return self.memory_entries

    def _set_entries(self, target: str, entries: List[str]):
        if target == "user":
            self.user_entries = entries
        else:
            self.memory_entries = entries

    def _char_count(self, target: str) -> int:
        entries = self._entries_for(target)
        if not entries:
            return 0
        return len(ENTRY_DELIMITER.join(entries))

    def _char_limit(self, target: str) -> int:
        if target == "user":
            return self.user_char_limit
        return self.memory_char_limit

    def add(self, target: str, content: str) -> Dict[str, Any]:
        """Append a new entry. Returns error if it would exceed the char limit."""
        content = content.strip()
        if not content:
            return {"success": False, "error": "Content cannot be empty."}

        # Scan for injection/exfiltration before accepting
        scan_error = _scan_memory_content(content)
        if scan_error:
            return {"success": False, "error": scan_error}

        with self._file_lock(self._path_for(target)):
            # Re-read from disk under lock to pick up writes from other sessions
            self._reload_target(target)

            entries = self._entries_for(target)
            limit = self._char_limit(target)

            # Reject exact duplicates
            if content in entries:
                return self._success_response(target, "Entry already exists (no duplicate added).")

            # Calculate what the new total would be
            new_entries = entries + [content]
            new_total = len(ENTRY_DELIMITER.join(new_entries))

            if new_total > limit:
                current = self._char_count(target)
                return {
                    "success": False,
                    "error": (
                        f"Memory at {current:,}/{limit:,} chars. "
                        f"Adding this entry ({len(content)} chars) would exceed the limit. "
                        f"Replace or remove existing entries first."
                    ),
                    "current_entries": entries,
                    "usage": f"{current:,}/{limit:,}",
                }

            entries.append(content)
            self._set_entries(target, entries)
            self.save_to_disk(target)

        return self._success_response(target, "Entry added.")

    def replace(self, target: str, old_text: str, new_content: str) -> Dict[str, Any]:
        """Find entry containing old_text substring, replace it with new_content."""
        old_text = old_text.strip()
        new_content = new_content.strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}
        if not new_content:
            return {"success": False, "error": "new_content cannot be empty. Use 'remove' to delete entries."}

        # Scan replacement content for injection/exfiltration
        scan_error = _scan_memory_content(new_content)
        if scan_error:
            return {"success": False, "error": scan_error}

        with self._file_lock(self._path_for(target)):
            self._reload_target(target)

            entries = self._entries_for(target)
            matches = [(i, e) for i, e in enumerate(entries) if old_text in e]

            if not matches:
                return {"success": False, "error": f"No entry matched '{old_text}'."}

            if len(matches) > 1:
                # If all matches are identical (exact duplicates), operate on the first one
                unique_texts = set(e for _, e in matches)
                if len(unique_texts) > 1:
                    previews = [e[:80] + ("..." if len(e) > 80 else "") for _, e in matches]
                    return {
                        "success": False,
                        "error": f"Multiple entries matched '{old_text}'. Be more specific.",
                        "matches": previews,
                    }
                # All identical -- safe to replace just the first

            idx = matches[0][0]
            limit = self._char_limit(target)

            # Check that replacement doesn't blow the budget
            test_entries = entries.copy()
            test_entries[idx] = new_content
            new_total = len(ENTRY_DELIMITER.join(test_entries))

            if new_total > limit:
                return {
                    "success": False,
                    "error": (
                        f"Replacement would put memory at {new_total:,}/{limit:,} chars. "
                        f"Shorten the new content or remove other entries first."
                    ),
                }

            entries[idx] = new_content
            self._set_entries(target, entries)
            self.save_to_disk(target)

        return self._success_response(target, "Entry replaced.")

    def remove(self, target: str, old_text: str) -> Dict[str, Any]:
        """Remove the entry containing old_text substring."""
        old_text = old_text.strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}

        with self._file_lock(self._path_for(target)):
            self._reload_target(target)

            entries = self._entries_for(target)
            matches = [(i, e) for i, e in enumerate(entries) if old_text in e]

            if not matches:
                return {"success": False, "error": f"No entry matched '{old_text}'."}

            if len(matches) > 1:
                # If all matches are identical (exact duplicates), remove the first one
                unique_texts = set(e for _, e in matches)
                if len(unique_texts) > 1:
                    previews = [e[:80] + ("..." if len(e) > 80 else "") for _, e in matches]
                    return {
                        "success": False,
                        "error": f"Multiple entries matched '{old_text}'. Be more specific.",
                        "matches": previews,
                    }
                # All identical -- safe to remove just the first

            idx = matches[0][0]
            entries.pop(idx)
            self._set_entries(target, entries)
            self.save_to_disk(target)

        return self._success_response(target, "Entry removed.")

    def format_for_system_prompt(self, target: str) -> Optional[str]:
        """
        Return the frozen snapshot for system prompt injection.

        This returns the state captured at load_from_disk() time, NOT the live
        state. Mid-session writes do not affect this. This keeps the system
        prompt stable across all turns, preserving the prefix cache.

        Returns None if the snapshot is empty (no entries at load time).
        """
        block = self._system_prompt_snapshot.get(target, "")
        return block if block else None

    # -- Internal helpers --

    def _success_response(self, target: str, message: str = None) -> Dict[str, Any]:
        entries = self._entries_for(target)
        current = self._char_count(target)
        limit = self._char_limit(target)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0

        resp = {
            "success": True,
            "target": target,
            "entries": entries,
            "usage": f"{pct}% — {current:,}/{limit:,} chars",
            "entry_count": len(entries),
        }
        if message:
            resp["message"] = message
        return resp

    def _render_block(self, target: str, entries: List[str]) -> str:
        """Render a system prompt block with header and usage indicator."""
        if not entries:
            return ""

        limit = self._char_limit(target)
        content = ENTRY_DELIMITER.join(entries)
        current = len(content)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0

        if target == "user":
            header = f"USER PROFILE (who the user is) [{pct}% — {current:,}/{limit:,} chars]"
        else:
            header = f"MEMORY (your personal notes) [{pct}% — {current:,}/{limit:,} chars]"

        separator = "═" * 46
        return f"{separator}\n{header}\n{separator}\n{content}"

    @staticmethod
    def _read_file(path: Path) -> List[str]:
        """Read a memory file and split into entries.

        No file locking needed: _write_file uses atomic rename, so readers
        always see either the previous complete file or the new complete file.
        """
        if not path.exists():
            return []
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, IOError):
            return []

        if not raw.strip():
            return []

        # Use ENTRY_DELIMITER for consistency with _write_file. Splitting by "§"
        # alone would incorrectly split entries that contain "§" in their content.
        entries = [e.strip() for e in raw.split(ENTRY_DELIMITER)]
        return [e for e in entries if e]

    @staticmethod
    def _write_file(path: Path, entries: List[str]):
        """Write entries to a memory file using atomic temp-file + rename.

        Previous implementation used open("w") + flock, but "w" truncates the
        file *before* the lock is acquired, creating a race window where
        concurrent readers see an empty file. Atomic rename avoids this:
        readers always see either the old complete file or the new one.
        """
        content = ENTRY_DELIMITER.join(entries) if entries else ""
        try:
            # Write to temp file in same directory (same filesystem for atomic rename)
            fd, tmp_path = tempfile.mkstemp(
                dir=str(path.parent), suffix=".tmp", prefix=".mem_"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, str(path))  # Atomic on same filesystem
            except BaseException:
                # Clean up temp file on any failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except (OSError, IOError) as e:
            raise RuntimeError(f"Failed to write memory file {path}: {e}")


def memory_tool(
    action: str,
    target: str = "memory",
    content: str = None,
    old_text: str = None,
    store: Optional[MemoryStore] = None,
) -> str:
    """
    Single entry point for the memory tool. Dispatches to MemoryStore methods.

    Returns JSON string with results.
    """
    if store is None:
        return tool_error("Memory is not available. It may be disabled in config or this environment.", success=False)

    if target not in ("memory", "user"):
        return tool_error(f"Invalid target '{target}'. Use 'memory' or 'user'.", success=False)

    if action == "add":
        if not content:
            return tool_error("Content is required for 'add' action.", success=False)
        result = store.add(target, content)

    elif action == "replace":
        if not old_text:
            return tool_error("old_text is required for 'replace' action.", success=False)
        if not content:
            return tool_error("content is required for 'replace' action.", success=False)
        result = store.replace(target, old_text, content)

    elif action == "remove":
        if not old_text:
            return tool_error("old_text is required for 'remove' action.", success=False)
        result = store.remove(target, old_text)

    else:
        return tool_error(f"Unknown action '{action}'. Use: add, replace, remove", success=False)

    return json.dumps(result, ensure_ascii=False)


def check_memory_requirements() -> bool:
    """Memory tool has no external requirements -- always available."""
    return True


# =============================================================================
# OpenAI Function-Calling Schema
# =============================================================================

MEMORY_SCHEMA = {
    "name": "memory",
    "description": (
        "Save durable information to persistent memory that survives across sessions. "
        "Memory is injected into future turns, so keep it compact and focused on facts "
        "that will still matter later.\n\n"
        "WHEN TO SAVE (do this proactively, don't wait to be asked):\n"
        "- User corrects you or says 'remember this' / 'don't do that again'\n"
        "- User shares a preference, habit, or personal detail (name, role, timezone, coding style)\n"
        "- You discover something about the environment (OS, installed tools, project structure)\n"
        "- You learn a convention, API quirk, or workflow specific to this user's setup\n"
        "- You identify a stable fact that will be useful again in future sessions\n\n"
        "PRIORITY: User preferences and corrections > environment facts > procedural knowledge. "
        "The most valuable memory prevents the user from having to repeat themselves.\n\n"
        "Do NOT save task progress, session outcomes, completed-work logs, or temporary TODO "
        "state to memory; use session_search to recall those from past transcripts.\n"
        "If you've discovered a new way to do something, solved a problem that could be "
        "necessary later, save it as a skill with the skill tool.\n\n"
        "TWO TARGETS:\n"
        "- 'user': who the user is -- name, role, preferences, communication style, pet peeves\n"
        "- 'memory': your notes -- environment facts, project conventions, tool quirks, lessons learned\n\n"
        "ACTIONS: add (new entry), replace (update existing -- old_text identifies it), "
        "remove (delete -- old_text identifies it).\n\n"
        "SKIP: trivial/obvious info, things easily re-discovered, raw data dumps, and temporary task state."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "replace", "remove"],
                "description": "The action to perform."
            },
            "target": {
                "type": "string",
                "enum": ["memory", "user"],
                "description": "Which memory store: 'memory' for personal notes, 'user' for user profile."
            },
            "content": {
                "type": "string",
                "description": "The entry content. Required for 'add' and 'replace'."
            },
            "old_text": {
                "type": "string",
                "description": "Short unique substring identifying the entry to replace or remove."
            },
        },
        "required": ["action", "target"],
    },
}


# --- Registry ---
from tools.registry import registry, tool_error

registry.register(
    name="memory",
    toolset="memory",
    schema=MEMORY_SCHEMA,
    handler=lambda args, **kw: memory_tool(
        action=args.get("action", ""),
        target=args.get("target", "memory"),
        content=args.get("content"),
        old_text=args.get("old_text"),
        store=kw.get("store")),
    check_fn=check_memory_requirements,
    emoji="🧠",
)


def _extract_query_terms(text: str) -> set[str]:
    words = [
        match.group(0).lower()
        for match in _WORD_RE.finditer(text or "")
    ]
    return {word for word in words if word not in _STOPWORDS}


def _extract_continuity_signals(text: str) -> Dict[str, Any]:
    raw = text or ""
    lowered = raw.lower()
    terms = _extract_query_terms(lowered)
    paths = {match.group(0).lower() for match in _PATH_PATTERN_RE.finditer(raw)}
    commands = {
        _normalize_command_pattern(match.group(0))
        for match in _COMMAND_PATTERN_RE.finditer(lowered)
    }
    workflow_terms = {term for term in terms if term in _WORKFLOW_TERMS}
    symbols = {
        symbol.lower()
        for symbol in _CODE_SYMBOL_RE.findall(raw)
        if symbol.lower() not in _STOPWORDS and not symbol.isupper()
    }
    continuation = any(hint in lowered for hint in _CONTINUITY_HINTS)
    return {
        "terms": terms,
        "paths": paths,
        "commands": {cmd for cmd in commands if cmd},
        "workflow_terms": workflow_terms,
        "symbols": symbols,
        "continuation": continuation,
    }


def _normalize_command_pattern(command: str) -> str:
    collapsed = " ".join((command or "").strip().split())
    return collapsed[:80].lower()
