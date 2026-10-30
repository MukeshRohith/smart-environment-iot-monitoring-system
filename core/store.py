"""Thread-safe bounded in-memory Store for IoT monitoring system."""
from __future__ import annotations

import copy
import math
import threading
import time
from collections import deque
from typing import Any

import numpy as np

import config
from core.models import (
    ActuationEvent,
    CleanedReading,
    Forecast,
    Level,
    Limits,
    Priority,
    Reading,
)


class Store:
    """Central synchronized in-memory state repository."""

    def __init__(self, initial_profile: str | None = None) -> None:
        """Initialize all bounded deques, configuration caches, and RLock."""
        self._lock = threading.RLock()
        self._profile: str = initial_profile or config.DEFAULT_PROFILE
        self._settings: dict[str, Any] = {
            "strategy": config.STRATEGY,
            "pipeline_mode": config.PIPELINE_MODE,
            "workers": config.WORKER_COUNT,
            "processing_delay_ms": config.PROCESSING_DELAY_MS,
            "horizon_min": config.HORIZON_MIN,
            "time_scale": config.SIM_TIME_SCALE,
        }

        self._registered_nodes: set[str] = set()
        self._node_controls: dict[str, dict[str, Any]] = {}

        # Telemetry storage
        self._raw_data: dict[str, deque[Reading]] = {}
        self._cleaned_data: dict[str, deque[CleanedReading]] = {}
        self._latest_cleaned: dict[str, CleanedReading] = {}
        self._latest_reading_time: dict[str, float] = {}
        self._latest_status: dict[str, str] = {}
        self._exposure: dict[str, dict[str, dict[str, float]]] = {}

        # Forecasts and errors
        self._forecasts: dict[str, dict[str, Forecast]] = {}
        self._forecast_errors: dict[str, deque[float]] = {
            m: deque(maxlen=500) for m in config.METRICS
        }

        # Actuation and alert levels
        self._devices: dict[str, dict[str, Any]] = {}
        self._device_on_seconds: dict[str, dict[str, float]] = {}
        self._device_state_since: dict[str, dict[str, float]] = {}
        self._levels: dict[str, dict[str, Level]] = {}
        self._level_transitions: deque[dict[str, Any]] = deque(maxlen=config.EVENT_LOG_LEN)
        self._actuations: deque[dict[str, Any]] = deque(maxlen=config.ACTUATION_LOG_LEN)

        # Events, counters, latencies
        self._events: deque[dict[str, Any]] = deque(maxlen=config.EVENT_LOG_LEN)
        self._counters: dict[str, Any] = {
            "received": 0,
            "processed": 0,
            "rejected": 0,
            "dropped": 0,
            "errors": 0,
            "by_priority": {"0": 0, "1": 0, "2": 0},
        }
        self._latencies: dict[str, deque[float]] = {
            "0": deque(maxlen=config.LATENCY_SAMPLES),
            "1": deque(maxlen=config.LATENCY_SAMPLES),
            "2": deque(maxlen=config.LATENCY_SAMPLES),
        }

    # ================= Profile & Limits =================

    def get_profile(self) -> str:
        """Return the active environment profile name."""
        with self._lock:
            return self._profile

    def set_profile(self, name: str) -> None:
        """Change the active environment profile name if valid."""
        with self._lock:
            if name in config.PROFILES:
                self._profile = name

    def limits(self, metric: str) -> Limits:
        """Return derived limits for a metric under the active profile."""
        with self._lock:
            return config.derive_limits(self._profile, metric)

    def all_limits(self) -> dict[str, Limits]:
        """Return derived limits for all metrics under the active profile."""
        with self._lock:
            return config.derive_all_limits(self._profile)

    # ================= Settings =================

    def get_settings(self) -> dict[str, Any]:
        """Return a copy of current pipeline and simulation settings."""
        with self._lock:
            return dict(self._settings)

    def update_settings(self, partial: dict[str, Any]) -> dict[str, Any]:
        """Update settings keys and return the updated dictionary."""
        with self._lock:
            for k in (
                "strategy",
                "pipeline_mode",
                "workers",
                "processing_delay_ms",
                "horizon_min",
                "time_scale",
            ):
                if k in partial:
                    self._settings[k] = partial[k]
            return dict(self._settings)

    # ================= Node Management =================

    def _default_controls(self) -> dict[str, Any]:
        """Generate default control settings centered in safe bounds."""
        lims = self.all_limits()
        target = {m: (lims[m].safe_min + lims[m].safe_max) / 2.0 for m in config.METRICS}
        return {
            "target": dict(target),
            "current": dict(target),
            "noise": config.DEFAULT_NOISE,
            "send_rate_hz": config.DEFAULT_SEND_HZ,
            "fault_mode": "none",
            "enabled": True,
        }

    def _default_devices(self) -> dict[str, Any]:
        """Generate default initial states for node actuators."""
        return {
            "fan": {"state": "off", "mode": "auto"},
            "hvac": {"state": "off", "mode": "auto"},
            "dehumidifier": {"state": "off", "mode": "auto"},
            "purifier": {"state": "off", "mode": "auto"},
            "shutdown": False,
        }

    def register_node(self, node: str, controls: dict[str, Any] | None = None) -> None:
        """Register a node with default or provided controls."""
        with self._lock:
            self._registered_nodes.add(node)
            if controls is not None:
                self._node_controls[node] = dict(controls)
            elif node not in self._node_controls:
                self._node_controls[node] = self._default_controls()

            if node not in self._raw_data:
                self._raw_data[node] = deque(maxlen=config.HISTORY_LEN)
            if node not in self._cleaned_data:
                self._cleaned_data[node] = deque(maxlen=config.HISTORY_LEN)
            if node not in self._exposure:
                self._exposure[node] = {
                    m: {"peak": float("-inf"), "seconds_outside_safe": 0.0, "seconds_above_hard": 0.0}
                    for m in config.METRICS
                }
            if node not in self._devices:
                self._devices[node] = self._default_devices()
                self._device_on_seconds[node] = {d: 0.0 for d in ("fan", "hvac", "dehumidifier", "purifier")}
                self._device_state_since[node] = {d: time.time() for d in ("fan", "hvac", "dehumidifier", "purifier")}
            if node not in self._levels:
                self._levels[node] = {m: Level.OK for m in config.METRICS}

    def unregister_node(self, node: str) -> None:
        """Unregister a node."""
        with self._lock:
            self._registered_nodes.discard(node)

    def set_node_controls(self, node: str, controls: dict[str, Any]) -> None:
        """Update operational controls for a registered node."""
        with self._lock:
            if node not in self._registered_nodes:
                self.register_node(node)
            current = self._node_controls.get(node, self._default_controls())
            current.update(controls)
            self._node_controls[node] = current

    def node_ids(self) -> list[str]:
        """Return sorted list of all registered node IDs."""
        with self._lock:
            return sorted(list(self._registered_nodes))

    # ================= Telemetry Data & Exposure =================

    def add_raw(self, reading: Reading) -> None:
        """Record a raw reading into bounded history."""
        with self._lock:
            node = reading.node_id
            if node not in self._registered_nodes:
                self.register_node(node)
            self._raw_data[node].append(reading)
            self._latest_reading_time[node] = reading.received_at or reading.ts or time.time()
            self._latest_status[node] = reading.status

    def add_cleaned(self, cleaned: CleanedReading) -> None:
        """Record cleaned reading and integrate exposure statistics."""
        with self._lock:
            node = cleaned.node_id
            if node not in self._registered_nodes:
                self.register_node(node)

            prev = self._latest_cleaned.get(node)
            dt = 0.0
            if prev is not None and cleaned.ts > prev.ts:
                dt = cleaned.ts - prev.ts

            lims = self.all_limits()
            if node not in self._exposure:
                self._exposure[node] = {
                    m: {"peak": float("-inf"), "seconds_outside_safe": 0.0, "seconds_above_hard": 0.0}
                    for m in config.METRICS
                }

            for m in config.METRICS:
                val = cleaned.clean.get(m)
                if val is not None:
                    exp = self._exposure[node][m]
                    if val > exp["peak"]:
                        exp["peak"] = val
                    if val < lims[m].safe_min or val > lims[m].safe_max:
                        exp["seconds_outside_safe"] += dt
                    if val > lims[m].hard_max:
                        exp["seconds_above_hard"] += dt

            self._cleaned_data[node].append(cleaned)
            self._latest_cleaned[node] = cleaned
            self._latest_reading_time[node] = cleaned.received_at or cleaned.ts or time.time()
            self._latest_status[node] = cleaned.status

    def latest_cleaned(self, node: str) -> CleanedReading | None:
        """Return the most recent CleanedReading for a node, or None."""
        with self._lock:
            return self._latest_cleaned.get(node)

    def exposure(self, node: str) -> dict[str, dict[str, float]]:
        """Return exposure statistics for a node."""
        with self._lock:
            if node not in self._exposure:
                return {
                    m: {"peak": 0.0, "seconds_outside_safe": 0.0, "seconds_above_hard": 0.0}
                    for m in config.METRICS
                }
            return {
                m: dict(self._exposure[node][m]) for m in config.METRICS
            }

    def raw_data(self, node: str) -> list[Reading]:
        """Return copy of raw readings for a node."""
        with self._lock:
            return list(self._raw_data.get(node, []))

    def history(self, node: str, limit: int | None = None) -> dict[str, Any]:
        """Return rolling history series bounded by limit points."""
        with self._lock:
            if self._cleaned_data.get(node):
                pts = list(self._cleaned_data[node])
                if limit is not None and limit > 0:
                    pts = pts[-limit:]
                return {
                    "ts": [p.ts for p in pts],
                    "raw": {m: [p.raw.get(m) for p in pts] for m in config.METRICS},
                    "smooth": {m: [p.smooth.get(m) for p in pts] for m in config.METRICS},
                }
            elif self._raw_data.get(node):
                pts = list(self._raw_data[node])
                if limit is not None and limit > 0:
                    pts = pts[-limit:]
                return {
                    "ts": [p.ts for p in pts],
                    "raw": {m: [getattr(p, m, None) for p in pts] for m in config.METRICS},
                    "smooth": {m: [getattr(p, m, None) for p in pts] for m in config.METRICS},
                }
            return {
                "ts": [],
                "raw": {m: [] for m in config.METRICS},
                "smooth": {m: [] for m in config.METRICS},
            }

    # ================= Forecasts =================

    def set_forecasts(self, node: str, forecasts: dict[str, Forecast]) -> None:
        """Store predictive forecasts for a node."""
        with self._lock:
            self._forecasts[node] = dict(forecasts)

    def forecasts(self, node: str) -> dict[str, Forecast]:
        """Return cached forecasts for a node."""
        with self._lock:
            return dict(self._forecasts.get(node, {}))

    def record_forecast_error(self, metric: str, node: str, abs_err: float) -> None:
        """Record forecast prediction absolute error."""
        with self._lock:
            if metric in self._forecast_errors:
                self._forecast_errors[metric].append(float(abs_err))

    def forecast_error_stats(self) -> dict[str, dict[str, Any]]:
        """Compute MAE, RMSE, and sample count for each metric's forecasts."""
        with self._lock:
            stats = {}
            for m in config.METRICS:
                errs = list(self._forecast_errors[m])
                n = len(errs)
                if n == 0:
                    stats[m] = {"mae": 0.0, "rmse": 0.0, "n": 0}
                else:
                    arr = np.array(errs, dtype=float)
                    stats[m] = {
                        "mae": float(np.mean(arr)),
                        "rmse": float(np.sqrt(np.mean(arr**2))),
                        "n": n,
                    }
            return stats

    # ================= Actuation & Alert Levels =================

    def set_devices(self, node: str, devices: dict[str, Any]) -> None:
        """Update device states and accumulate operating time."""
        with self._lock:
            if node not in self._devices:
                self.register_node(node)

            now = time.time()
            for dev in ("fan", "hvac", "dehumidifier", "purifier"):
                old_info = self._devices[node].get(dev, {"state": "off", "mode": "auto"})
                old_state = old_info.get("state", "off") if isinstance(old_info, dict) else old_info
                last_change = self._device_state_since[node].get(dev, now)

                if old_state in ("low", "high"):
                    self._device_on_seconds[node][dev] += max(0.0, now - last_change)
                self._device_state_since[node][dev] = now

            self._devices[node] = copy.deepcopy(devices)

    def devices(self, node: str) -> dict[str, Any]:
        """Return current device states for a node."""
        with self._lock:
            return copy.deepcopy(self._devices.get(node, self._default_devices()))

    def device_on_seconds(self, node: str) -> dict[str, float]:
        """Return cumulative active run duration per device."""
        with self._lock:
            return dict(self._device_on_seconds.get(node, {d: 0.0 for d in ("fan", "hvac", "dehumidifier", "purifier")}))

    def set_levels(self, node: str, levels: dict[str, Level]) -> None:
        """Set alert severity levels for a node."""
        with self._lock:
            self._levels[node] = dict(levels)

    def levels(self, node: str) -> dict[str, Level]:
        """Return alert severity levels for a node."""
        with self._lock:
            return dict(self._levels.get(node, {m: Level.OK for m in config.METRICS}))

    def record_level_change(self, node: str, metric: str, old: Level, new: Level, ts: float) -> None:
        """Record an alert level transition."""
        with self._lock:
            old_name = old.name if isinstance(old, Level) else str(old)
            new_name = new.name if isinstance(new, Level) else str(new)
            self._level_transitions.append({
                "ts": ts,
                "node_id": node,
                "metric": metric,
                "old": old_name,
                "new": new_name,
            })

    def level_transitions(self) -> list[dict[str, Any]]:
        """Return list of recent alert level transitions."""
        with self._lock:
            return list(self._level_transitions)

    def add_actuation(self, event: ActuationEvent) -> None:
        """Log an actuation state change event."""
        with self._lock:
            self._actuations.append(event.to_dict())

    def actuations(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent actuation events up to limit."""
        with self._lock:
            return list(self._actuations)[-limit:]

    # ================= Events, Counters & Latency =================

    def log_event(self, level: str, source: str, message: str) -> None:
        """Append an event to the bounded event log."""
        with self._lock:
            self._events.append({
                "ts": time.time(),
                "level": level,
                "source": source,
                "message": message,
            })

    def events(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return the most recent events up to limit."""
        with self._lock:
            return list(self._events)[-limit:]

    def incr(self, name: str, n: int = 1, priority: int | Priority | None = None) -> None:
        """Increment telemetry counters."""
        with self._lock:
            if name in self._counters:
                self._counters[name] += n
            if priority is not None:
                p_key = str(int(priority))
                if p_key in self._counters["by_priority"]:
                    self._counters["by_priority"][p_key] += n

    def counters(self) -> dict[str, Any]:
        """Return copy of system counters."""
        with self._lock:
            return copy.deepcopy(self._counters)

    def record_latency(self, priority: int | Priority, seconds: float) -> None:
        """Record a pipeline processing latency sample."""
        with self._lock:
            p_key = str(int(priority))
            if p_key in self._latencies:
                self._latencies[p_key].append(float(seconds))

    def latency_stats(self) -> dict[str, dict[str, float]]:
        """Calculate latency distribution per priority tier."""
        with self._lock:
            stats = {}
            for p_key in ("0", "1", "2"):
                samples = list(self._latencies[p_key])
                n = len(samples)
                if n == 0:
                    stats[p_key] = {"avg": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "n": 0}
                else:
                    arr = np.array(samples, dtype=float)
                    stats[p_key] = {
                        "avg": float(np.mean(arr)),
                        "p50": float(np.percentile(arr, 50)),
                        "p95": float(np.percentile(arr, 95)),
                        "p99": float(np.percentile(arr, 99)),
                        "n": n,
                    }
            return stats

    # ================= Reset & Snapshot =================

    def reset_runtime(self) -> None:
        """Reset runtime data while preserving profiles, settings, and registered nodes."""
        with self._lock:
            self._raw_data = {node: deque(maxlen=config.HISTORY_LEN) for node in self._registered_nodes}
            self._cleaned_data = {node: deque(maxlen=config.HISTORY_LEN) for node in self._registered_nodes}
            self._latest_cleaned.clear()
            self._latest_reading_time.clear()
            self._latest_status.clear()
            self._exposure = {
                node: {
                    m: {"peak": float("-inf"), "seconds_outside_safe": 0.0, "seconds_above_hard": 0.0}
                    for m in config.METRICS
                }
                for node in self._registered_nodes
            }
            self._forecasts.clear()
            for m in config.METRICS:
                self._forecast_errors[m].clear()
            self._devices = {node: self._default_devices() for node in self._registered_nodes}
            self._device_on_seconds = {
                node: {d: 0.0 for d in ("fan", "hvac", "dehumidifier", "purifier")}
                for node in self._registered_nodes
            }
            self._device_state_since = {
                node: {d: time.time() for d in ("fan", "hvac", "dehumidifier", "purifier")}
                for node in self._registered_nodes
            }
            self._levels = {node: {m: Level.OK for m in config.METRICS} for node in self._registered_nodes}
            self._level_transitions.clear()
            self._actuations.clear()
            self._events.clear()
            self._counters = {
                "received": 0,
                "processed": 0,
                "rejected": 0,
                "dropped": 0,
                "errors": 0,
                "by_priority": {"0": 0, "1": 0, "2": 0},
            }
            for p_key in ("0", "1", "2"):
                self._latencies[p_key].clear()

    def snapshot(self) -> dict[str, Any]:
        """Return store-owned subset of the system state snapshot."""
        with self._lock:
            now = time.time()
            all_lims = self.all_limits()

            nodes_snap: dict[str, Any] = {}
            for node_id in sorted(list(self._registered_nodes)):
                controls = self._node_controls.get(node_id, self._default_controls())
                latest = self._latest_cleaned.get(node_id)
                raw_latest = self._raw_data[node_id][-1] if self._raw_data.get(node_id) else None

                latest_ts = 0.0
                status_reading = "ok"
                if latest is not None:
                    latest_ts = latest.received_at or latest.ts
                    status_reading = latest.status
                elif raw_latest is not None:
                    latest_ts = raw_latest.received_at or raw_latest.ts
                    status_reading = raw_latest.status

                if status_reading == "shutdown":
                    node_status = "shutdown"
                elif latest_ts > 0.0 and (now - latest_ts) < 5.0:
                    node_status = "online"
                else:
                    node_status = "offline"

                node_levels = self.levels(node_id)
                worst_level = Level.OK
                if node_levels:
                    worst_level = max(node_levels.values(), key=lambda l: int(l))

                fcasts = self.forecasts(node_id)

                nodes_snap[node_id] = {
                    "controls": copy.deepcopy(controls),
                    "status": node_status,
                    "latest": latest.to_dict() if latest else None,
                    "levels": {m: lvl.name for m, lvl in node_levels.items()},
                    "level": worst_level.name,
                    "forecast": {m: f.to_dict() for m, f in fcasts.items()},
                    "devices": self.devices(node_id),
                    "history": self.history(node_id, config.CHART_POINTS),
                }

            return {
                "ts": now,
                "profile": self._profile,
                "limits": {m: all_lims[m].to_dict() for m in config.METRICS},
                "settings": self.get_settings(),
                "counters": self.counters(),
                "nodes": nodes_snap,
                "events": self.events(100),
                "actuations": self.actuations(50),
            }
