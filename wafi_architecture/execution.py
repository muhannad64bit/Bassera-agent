from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from queue import Queue
from typing import Any

from .tools import ToolRegistry


@dataclass(frozen=True)
class ExecutionRequest:
    idempotency_key: str
    tool_name: str
    args: dict[str, str]
    timeout_s: float = 5.0
    max_retries: int = 1
    sandbox_profile: str = "personal-trusted"


@dataclass
class ExecutionResult:
    success: bool
    output: str
    attempts: int
    sandbox_profile: str


class ExecutionBroker:
    """Execution orchestration with queueing, retries, timeout, cancellation, idempotency."""

    def __init__(self, tool_registry: ToolRegistry) -> None:
        self.tool_registry = tool_registry
        self._queue: Queue[ExecutionRequest] = Queue()
        self._results: dict[str, ExecutionResult] = {}
        self._cancelled: set[str] = set()

    def enqueue(self, request: ExecutionRequest) -> None:
        self._queue.put(request)

    def cancel(self, idempotency_key: str) -> None:
        self._cancelled.add(idempotency_key)

    def run_next(self) -> ExecutionResult:
        request = self._queue.get_nowait()
        return self.execute(request)

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        if request.idempotency_key in self._results:
            return self._results[request.idempotency_key]

        if request.idempotency_key in self._cancelled:
            result = ExecutionResult(False, "Cancelled", 0, request.sandbox_profile)
            self._results[request.idempotency_key] = result
            return result

        attempts = 0
        output = "Timed out"
        success = False

        while attempts <= request.max_retries and not success:
            attempts += 1
            holder: dict[str, Any] = {"done": False, "value": "", "error": ""}

            def _run() -> None:
                try:
                    holder["value"] = self.tool_registry.run(request.tool_name, request.args)
                except Exception as exc:  # noqa: BLE001
                    holder["error"] = str(exc)
                finally:
                    holder["done"] = True

            worker = threading.Thread(target=_run, daemon=True)
            worker.start()
            worker.join(timeout=request.timeout_s)

            if not holder["done"]:
                output = "Timed out"
                continue

            if holder["error"]:
                output = f"Execution error: {holder['error']}"
                continue

            output = str(holder["value"])
            success = True

            # brief pause keeps retries deterministic in tests for flaky handlers
            time.sleep(0)

        result = ExecutionResult(success, output, attempts, request.sandbox_profile)
        self._results[request.idempotency_key] = result
        return result
