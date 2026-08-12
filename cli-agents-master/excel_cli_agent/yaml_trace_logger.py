"""Incremental YAML trace logging for per-task debugging.

Stores a chronological list of events and rewrites a single YAML
file after each append so the trace is always current, even if the run stops
mid-task.
"""

from __future__ import annotations

import os
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


class YAMLTraceLogger:
    """Write a chronological YAML trace for one task."""

    def __init__(self, trace_dir: str | Path):
        self.trace_dir = Path(trace_dir)
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = self.trace_dir / "debug_trace.yaml"
        self.data: Dict[str, Any] = {
            "run": {},
            "task": {},
            "events": [],
        }

    def set_run_metadata(self, metadata: Dict[str, Any]) -> None:
        self.data["run"] = self._normalize(metadata)
        self.flush()

    def set_task_metadata(self, metadata: Dict[str, Any]) -> None:
        self.data["task"] = self._normalize(metadata)
        self.flush()

    def append_event(
        self,
        event_type: str,
        payload: Any,
        iteration: Optional[int] = None,
        timestamp: Optional[str] = None,
    ) -> None:
        event = {
            "timestamp": timestamp or datetime.now().isoformat(),
            "event_type": event_type,
            "iteration": iteration,
            "payload": self._normalize(payload),
        }
        self.data["events"].append(event)
        self.flush()

    def flush(self) -> None:
        """Atomically rewrite the YAML file with the current trace state."""
        tmp_path = self.trace_path.with_suffix(self.trace_path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                self.data,
                f,
                sort_keys=False,
                allow_unicode=True,
                default_flow_style=False,
            )
        os.replace(tmp_path, self.trace_path)

    def _normalize(self, value: Any) -> Any:
        """Convert objects into YAML-safe primitives while preserving strings."""
        if value is None:
            return None
        if isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, Enum):
            return value.value
        if is_dataclass(value):
            return self._normalize(asdict(value))
        if isinstance(value, dict):
            return {str(k): self._normalize(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._normalize(item) for item in value]
        return str(value)
