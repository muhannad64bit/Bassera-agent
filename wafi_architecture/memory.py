from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MemoryRecord:
    memory_type: str
    key: str
    value: Any


@dataclass
class MemoryManager:
    """Single memory authority for WAFI."""

    _store: dict[tuple[str, str], Any] = field(default_factory=dict)

    def write(self, memory_type: str, key: str, value: Any) -> None:
        self._store[(memory_type, key)] = value

    def read(self, memory_type: str, key: str) -> Any | None:
        return self._store.get((memory_type, key))

    def list_records(self) -> list[MemoryRecord]:
        return [MemoryRecord(memory_type=t, key=k, value=v) for (t, k), v in self._store.items()]
