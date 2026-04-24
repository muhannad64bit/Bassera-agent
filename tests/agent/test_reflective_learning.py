"""Tests for bounded reflective learning and structured post-turn memory writes."""

from __future__ import annotations

from pathlib import Path

from agent.reflective_learning import ReflectiveLearningEngine
from tools.memory_tool import MemoryStore
from agent.memory_schema import (
    MemoryRecord,
    parse_memory_record,
    parse_owner_dna_trait,
    parse_owner_doctrine_rule,
    parse_skill_candidate,
)


def _assistant_tool_call(tool_id: str, tool_name: str) -> dict:
    return {
        "role": "assistant",
        "tool_calls": [
            {
                "id": tool_id,
                "type": "function",
                "function": {"name": tool_name, "arguments": "{}"},
            }
        ],
    }


def _tool_result(tool_id: str, *, success: bool = True) -> dict:
    payload = {"success": success}
    if not success:
        payload["error"] = "failed"
    return {"role": "tool", "tool_call_id": tool_id, "content": __import__("json").dumps(payload)}


def test_reflective_learning_extracts_preferences_and_repeated_outcomes():
    engine = ReflectiveLearningEngine(max_records=4)
    messages = [
        _assistant_tool_call("tool-1", "terminal"),
        _tool_result("tool-1", success=True),
        _assistant_tool_call("tool-2", "terminal"),
        _tool_result("tool-2", success=True),
    ]

    records = engine.build_records(
        messages=messages,
        user_message="I prefer concise responses and avoid long digressions.",
        assistant_response="Done.",
    )

    categories = {record.category for record in records}
    assert "owner_preference" in categories
    assert "reflective" in categories


def test_reflective_learning_builds_procedural_workflow_record():
    engine = ReflectiveLearningEngine(max_records=4)
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "tool-1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
                {"id": "tool-2", "type": "function", "function": {"name": "terminal", "arguments": "{}"}},
            ],
        }
    ]

    records = engine.build_records(
        messages=messages,
        user_message="Check the failing test.",
        assistant_response="I traced it to the parser.",
    )

    procedural = [record for record in records if record.category == "procedural"]
    assert procedural
    assert "read_file -> terminal" in procedural[0].content


def test_reflective_learning_persists_through_memory_store(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: tmp_path)
    store = MemoryStore(memory_char_limit=2000, user_char_limit=1200)
    store.load_from_disk()

    messages = [
        _assistant_tool_call("tool-1", "terminal"),
        _tool_result("tool-1", success=True),
        _assistant_tool_call("tool-2", "terminal"),
        _tool_result("tool-2", success=True),
    ]

    persisted = ReflectiveLearningEngine(max_records=4).persist_records(
        store=store,
        messages=messages,
        user_message="I prefer concise responses.",
        assistant_response="Completed the task.",
    )

    assert persisted
    parsed_entries = [parse_memory_record(entry) for entry in store.memory_entries]
    parsed_entries = [entry for entry in parsed_entries if entry is not None]
    assert any(entry.category == "reflective" for entry in parsed_entries)
    assert any(entry.category == "skill_candidate" for entry in parsed_entries)
    skill_candidates = [parse_skill_candidate(entry) for entry in store.memory_entries]
    skill_candidates = [candidate for candidate in skill_candidates if candidate is not None]
    assert skill_candidates
    assert any(candidate.evidence_count >= 2 for candidate in skill_candidates)

    parsed_user_entries = [parse_memory_record(entry, default_target="user") for entry in store.user_entries]
    parsed_user_entries = [entry for entry in parsed_user_entries if entry is not None]
    assert any(entry.category == "owner_preference" for entry in parsed_user_entries)


def test_reflective_learning_builds_owner_dna_from_repeated_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: tmp_path)
    store = MemoryStore(memory_char_limit=3000, user_char_limit=1800)
    store.load_from_disk()
    store.set_active_session("session-live")

    # Existing repeated owner evidence
    store.upsert_record(
        MemoryRecord(
            category="owner_preference",
            content="Prefers concise responses",
            key="owner-pref:concise:test-a",
            target="user",
            confidence=0.85,
            source="reflection",
        )
    )
    store.upsert_record(
        MemoryRecord(
            category="owner_preference",
            content="Avoids destructive rewrites",
            key="owner-pref:safe:test-b",
            target="user",
            confidence=0.87,
            source="reflection",
        )
    )
    store.upsert_record(
        MemoryRecord(
            category="procedural",
            content="Reusable workflow for 'debug parser tests': read_file -> terminal",
            key="workflow:read-file-terminal:test",
            target="memory",
            confidence=0.75,
            source="reflection",
        )
    )
    store.upsert_record(
        MemoryRecord(
            category="skill_candidate",
            content="Title: Workflow reuse: read_file -> terminal | Candidate Category: workflow_reuse | Source Memory Types: procedural, reflective | Pattern Summary: Use read_file -> terminal for focused pytest debug loops. | Evidence Count: 3 | Success Count: 3 | Failure Count: 0 | Owner Applicability: Prefers concise responses",
            key="skill-candidate:workflow:test",
            target="memory",
            confidence=0.8,
            source="skill_candidate",
        )
    )

    messages = [
        _assistant_tool_call("tool-1", "terminal"),
        _tool_result("tool-1", success=True),
        _assistant_tool_call("tool-2", "terminal"),
        _tool_result("tool-2", success=True),
    ]

    ReflectiveLearningEngine(max_records=4, max_candidates=2, max_owner_dna_traits=3).persist_records(
        store=store,
        messages=messages,
        user_message="Keep the response concise and avoid destructive changes while debugging the parser test.",
        assistant_response="I traced the parser issue and used a safe incremental fix.",
    )

    parsed_owner = [parse_owner_dna_trait(entry) for entry in store.user_entries]
    parsed_owner = [trait for trait in parsed_owner if trait is not None]
    assert parsed_owner
    assert any(trait.category == "response_style" for trait in parsed_owner)
    assert any(trait.category in {"decision_preference", "risk_posture"} for trait in parsed_owner)
    assert all(trait.evidence_count >= 2 for trait in parsed_owner)


def test_reflective_learning_skips_owner_dna_when_evidence_is_contradictory(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: tmp_path)
    store = MemoryStore(memory_char_limit=3000, user_char_limit=1800)
    store.load_from_disk()

    store.upsert_record(
        MemoryRecord(
            category="owner_preference",
            content="Prefers concise responses",
            key="owner-pref:concise:test-a",
            target="user",
            confidence=0.9,
            source="reflection",
        )
    )
    store.upsert_record(
        MemoryRecord(
            category="owner_preference",
            content="Prefers detailed responses",
            key="owner-pref:detailed:test-b",
            target="user",
            confidence=0.9,
            source="reflection",
        )
    )

    ReflectiveLearningEngine(max_records=4, max_candidates=2, max_owner_dna_traits=3).persist_records(
        store=store,
        messages=[],
        user_message="Be concise here, but I also want detailed responses elsewhere.",
        assistant_response="Understood.",
    )

    parsed_owner = [parse_owner_dna_trait(entry) for entry in store.user_entries]
    parsed_owner = [trait for trait in parsed_owner if trait is not None]
    assert all(trait.trait_name != "concise_direct_communication" for trait in parsed_owner)


def test_reflective_learning_builds_owner_doctrine_from_repeated_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: tmp_path)
    store = MemoryStore(memory_char_limit=3200, user_char_limit=2200)
    store.load_from_disk()
    store.set_active_session("session-live")

    store.upsert_record(
        MemoryRecord(
            category="owner_preference",
            content="Avoids destructive rewrites",
            key="owner-pref:safe:test-a",
            target="user",
            confidence=0.9,
            source="reflection",
        )
    )
    store.upsert_record(
        MemoryRecord(
            category="owner_preference",
            content="Prefers concise responses",
            key="owner-pref:concise:test-b",
            target="user",
            confidence=0.9,
            source="reflection",
        )
    )
    store.upsert_record(
        MemoryRecord(
            category="procedural",
            content="Reusable workflow for 'debug parser tests': read_file -> terminal",
            key="workflow:read-file-terminal:test",
            target="memory",
            confidence=0.8,
            source="reflection",
        )
    )
    store.upsert_record(
        MemoryRecord(
            category="skill_candidate",
            content="Title: Workflow reuse: read_file -> terminal | Candidate Category: workflow_reuse | Source Memory Types: procedural, reflective | Pattern Summary: Run focused pytest validation before broader changes. | Evidence Count: 3 | Success Count: 3 | Failure Count: 0 | Owner Applicability: Prefers concise responses",
            key="skill-candidate:workflow:test",
            target="memory",
            confidence=0.84,
            source="skill_candidate",
        )
    )

    ReflectiveLearningEngine(max_records=4, max_candidates=2, max_owner_dna_traits=3, max_owner_doctrine_rules=4).persist_records(
        store=store,
        messages=[
            _assistant_tool_call("tool-1", "terminal"),
            _tool_result("tool-1", success=True),
            _assistant_tool_call("tool-2", "terminal"),
            _tool_result("tool-2", success=True),
        ],
        user_message="Keep this incremental, validate before broad changes, and avoid destructive rewrites.",
        assistant_response="I used a small safe fix and validated the focused path first.",
    )

    parsed_doctrine = [parse_owner_doctrine_rule(entry) for entry in store.user_entries]
    parsed_doctrine = [rule for rule in parsed_doctrine if rule is not None]
    assert parsed_doctrine
    assert any(rule.category == "execution_style" for rule in parsed_doctrine)
    assert any(rule.category == "validation_rigor" for rule in parsed_doctrine)
    assert all(rule.evidence_count >= 2 for rule in parsed_doctrine)


def test_reflective_learning_builds_workflow_skill_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: tmp_path)
    store = MemoryStore(memory_char_limit=2500, user_char_limit=1200)
    store.load_from_disk()
    store.set_active_session("session-live")

    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "tool-1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
                {"id": "tool-2", "type": "function", "function": {"name": "terminal", "arguments": "{}"}},
            ],
        },
        _tool_result("tool-1", success=True),
        _tool_result("tool-2", success=True),
        _assistant_tool_call("tool-3", "terminal"),
        _tool_result("tool-3", success=True),
    ]

    ReflectiveLearningEngine(max_records=4, max_candidates=2).persist_records(
        store=store,
        messages=messages,
        user_message="Debug the failing parser test and keep the response concise.",
        assistant_response="I traced the parser issue and fixed it.",
    )

    candidates = [parse_skill_candidate(entry) for entry in store.memory_entries]
    candidates = [candidate for candidate in candidates if candidate is not None]
    assert candidates
    assert any(candidate.category == "workflow_reuse" for candidate in candidates)
    assert any("procedural" in candidate.source_memory_types for candidate in candidates)


def test_run_agent_source_wires_reflective_learning_post_turn():
    src = Path("run_agent.py").read_text(encoding="utf-8")
    idx_sync = src.index("self._memory_manager.sync_all")
    idx_reflect = src.index("self._run_reflective_learning(")
    idx_review = src.index("self._spawn_background_review(")
    assert idx_sync < idx_reflect < idx_review


def test_run_agent_source_injects_doctrine_guidance():
    src = Path("run_agent.py").read_text(encoding="utf-8")
    assert "build_doctrine_guidance" in src
    assert "_doctrine_guidance_cache" in src
