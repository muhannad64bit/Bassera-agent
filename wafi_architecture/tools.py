from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


ToolHandler = Callable[[dict[str, str]], str]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    side_effecting: bool
    risk_level: str


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        self._specs[spec.name] = spec
        self._handlers[spec.name] = handler

    def get_spec(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def run(self, name: str, args: dict[str, str]) -> str:
        if name not in self._handlers:
            raise ValueError(f"Unknown tool: {name}")
        return self._handlers[name](args)


def read_file_tool(args: dict[str, str]) -> str:
    path = Path(args["path"]).expanduser()
    if not path.exists():
        return "File not found."
    if path.is_dir():
        return "Path is a directory."
    return path.read_text(encoding="utf-8")


def default_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="read_file", side_effecting=False, risk_level="low"),
        read_file_tool,
    )
    return registry
