"""Data models and enums for the IoT environmental monitoring system."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class Priority(IntEnum):
    """Pipeline priority levels."""
    CRITICAL = 0
    ANOMALY = 1
    NORMAL = 2


class Level(IntEnum):
    """Health alert status levels."""
    OK = 0
    WATCH = 1
    WARNING = 2
    CRITICAL = 3


@dataclass
class Limits:
    """Safe, hard, and display limits for an environmental metric."""
    safe_min: float
    safe_max: float
    hard_min: float
    hard_max: float
    slider_min: float
    slider_max: float
    unit: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize limits to dictionary."""
        return {
            "safe_min": self.safe_min,
            "safe_max": self.safe_max,
            "hard_min": self.hard_min,
            "hard_max": self.hard_max,
            "slider_min": self.slider_min,
            "slider_max": self.slider_max,
            "unit": self.unit,
        }


@dataclass
class Reading:
    """Raw environmental telemetry reading from an IoT node."""
    node_id: str
    ts: float
    seq: int
    temperature: float | None = None
    humidity: float | None = None
    aqi: float | None = None
    status: str = "ok"
    received_at: float = 0.0
    priority: Priority = Priority.NORMAL

    def to_dict(self) -> dict[str, Any]:
        """Serialize reading to dictionary."""
        return {
            "node_id": self.node_id,
            "ts": self.ts,
            "seq": self.seq,
            "temperature": self.temperature,
            "humidity": self.humidity,
            "aqi": self.aqi,
            "status": self.status,
            "received_at": self.received_at,
            "priority": int(self.priority),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Reading:
        """Validate input dictionary and construct a Reading instance."""
        if not isinstance(data, dict):
            raise ValueError("Reading payload must be a JSON object")

        for key in ("node_id", "ts", "seq"):
            if key not in data or data[key] is None:
                raise ValueError(f"Missing required key: {key}")

        node_id = str(data["node_id"]).strip()
        if not node_id:
            raise ValueError("node_id must be non-empty string")

        raw_ts = data["ts"]
        if isinstance(raw_ts, bool) or not isinstance(raw_ts, (int, float)):
            raise ValueError("ts must be a numeric timestamp")
        if math.isnan(raw_ts) or math.isinf(raw_ts):
            raise ValueError("ts cannot be NaN or inf")
        ts = float(raw_ts)

        raw_seq = data["seq"]
        if isinstance(raw_seq, bool) or not isinstance(raw_seq, (int, float)):
            raise ValueError("seq must be an integer sequence number")
        if math.isnan(raw_seq) or math.isinf(raw_seq):
            raise ValueError("seq cannot be NaN or inf")
        seq = int(raw_seq)

        metrics: dict[str, float | None] = {}
        for metric in ("temperature", "humidity", "aqi"):
            if metric in data and data[metric] is not None:
                val = data[metric]
                if isinstance(val, bool) or not isinstance(val, (int, float)):
                    raise ValueError(f"{metric} must be numeric or null")
                if math.isnan(val) or math.isinf(val):
                    raise ValueError(f"{metric} cannot be NaN or inf")
                metrics[metric] = float(val)
            else:
                metrics[metric] = None

        status = str(data.get("status", "ok")).lower()
        if status not in ("ok", "shutdown"):
            status = "ok"

        raw_received = data.get("received_at", 0.0) or 0.0
        if isinstance(raw_received, bool) or not isinstance(raw_received, (int, float)):
            raw_received = 0.0
        received_at = float(raw_received)

        raw_priority = data.get("priority", Priority.NORMAL)
        if isinstance(raw_priority, Priority):
            priority = raw_priority
        elif isinstance(raw_priority, int) and raw_priority in (0, 1, 2):
            priority = Priority(raw_priority)
        else:
            priority = Priority.NORMAL

        return cls(
            node_id=node_id,
            ts=ts,
            seq=seq,
            temperature=metrics["temperature"],
            humidity=metrics["humidity"],
            aqi=metrics["aqi"],
            status=status,
            received_at=received_at,
            priority=priority,
        )


@dataclass
class CleanedReading:
    """Wrangled and validated telemetry reading with rolling statistics."""
    node_id: str
    ts: float
    seq: int
    priority: Priority | int
    raw: dict[str, float | None]
    clean: dict[str, float | None]
    smooth: dict[str, float | None]
    flags: dict[str, list[str]]
    rolling: dict[str, dict[str, float]]
    quality: float
    status: str
    processed_at: float
    received_at: float

    def to_dict(self) -> dict[str, Any]:
        """Serialize cleaned reading to dictionary."""
        return {
            "node_id": self.node_id,
            "ts": self.ts,
            "seq": self.seq,
            "priority": int(self.priority),
            "raw": self.raw,
            "clean": self.clean,
            "smooth": self.smooth,
            "flags": self.flags,
            "rolling": self.rolling,
            "quality": self.quality,
            "status": self.status,
            "processed_at": self.processed_at,
            "received_at": self.received_at,
        }


@dataclass
class Forecast:
    """Predictive trajectory for an environmental metric."""
    node_id: str
    metric: str
    made_at: float
    horizon_min: float
    points: list[dict[str, float]]
    lower: list[float]
    upper: list[float]
    predicted_end: float
    eta_safe_min: float | None
    eta_hard_min: float | None
    confidence: float
    model: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize forecast to dictionary."""
        return {
            "node_id": self.node_id,
            "metric": self.metric,
            "made_at": self.made_at,
            "horizon_min": self.horizon_min,
            "points": self.points,
            "lower": self.lower,
            "upper": self.upper,
            "predicted_end": self.predicted_end,
            "eta_safe_min": self.eta_safe_min,
            "eta_hard_min": self.eta_hard_min,
            "confidence": self.confidence,
            "model": self.model,
        }


@dataclass
class ActuationEvent:
    """Log record of device actuation or release."""
    ts: float
    node_id: str
    device: str
    from_state: str
    to_state: str
    trigger: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize actuation event to dictionary."""
        return {
            "ts": self.ts,
            "node_id": self.node_id,
            "device": self.device,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "trigger": self.trigger,
            "reason": self.reason,
        }
