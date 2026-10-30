"""Automated closed-loop actuation and safety control engine."""
from __future__ import annotations

import time
from typing import Any

import config
from core.models import ActuationEvent, CleanedReading, Forecast, Level


class ActuatorEngine:
    """Evaluates environmental alerts, executes hysteresis-governed actuations, and manages device states."""

    STATE_RANK = {"off": 0, "low": 1, "high": 2}
    RANK_STATE = {0: "off", 1: "low", 2: "high"}

    def __init__(self, store: Any) -> None:
        """Initialize actuator engine bound to system Store."""
        self.store = store
        self.reset()

    def reset(self) -> None:
        """Clear all node device states, hysteresis counters, and overrides."""
        self._devices: dict[str, dict[str, dict[str, str]]] = {}
        self._shutdown: dict[str, bool] = {}
        self._last_direction: dict[str, dict[str, str]] = {}
        self._step_down_count: dict[str, dict[str, int]] = {}
        self._turn_on_ts: dict[str, dict[str, float]] = {}
        self._prev_levels: dict[str, dict[str, Level]] = {}
        self._shutdown_clear_count: dict[str, int] = {}

    def _init_node(self, node: str) -> None:
        """Ensure node structures exist."""
        if node not in self._devices:
            self._devices[node] = {
                "fan": {"state": "off", "mode": "auto"},
                "hvac": {"state": "off", "mode": "auto"},
                "dehumidifier": {"state": "off", "mode": "auto"},
                "purifier": {"state": "off", "mode": "auto"},
            }
            self._shutdown[node] = False
            self._last_direction[node] = {m: "high" for m in config.METRICS}
            self._step_down_count[node] = {d: 0 for d in ("fan", "hvac", "dehumidifier", "purifier")}
            self._turn_on_ts[node] = {d: 0.0 for d in ("fan", "hvac", "dehumidifier", "purifier")}
            self._prev_levels[node] = {m: Level.OK for m in config.METRICS}
            self._shutdown_clear_count[node] = 0

    def is_shutdown(self, node: str) -> bool:
        """Return True if node is in emergency soft shutdown."""
        return self._shutdown.get(node, False)

    def restart(self, node: str) -> None:
        """Manually clear emergency shutdown and resume normal operation."""
        self._init_node(node)
        if self._shutdown[node]:
            self._shutdown[node] = False
            self._shutdown_clear_count[node] = 0
            event = ActuationEvent(
                ts=time.time(),
                node_id=node,
                device="shutdown",
                from_state="on",
                to_state="off",
                trigger="manual",
                reason="manual restart",
            )
            self.store.add_actuation(event)
            self._sync_store_devices(node)

    def override(self, node: str, device: str, action: str) -> None:
        """Manually override a device state ('on', 'off', 'auto')."""
        self._init_node(node)
        if device not in self._devices[node]:
            return

        old_state = self._devices[node][device]["state"]
        old_mode = self._devices[node][device]["mode"]

        if action == "on":
            new_state = "high"
            new_mode = "manual"
        elif action == "off":
            new_state = "off"
            new_mode = "manual"
        elif action == "auto":
            new_state = old_state
            new_mode = "auto"
        else:
            return

        self._devices[node][device] = {"state": new_state, "mode": new_mode}

        if new_state != old_state or new_mode != old_mode:
            event = ActuationEvent(
                ts=time.time(),
                node_id=node,
                device=device,
                from_state=old_state,
                to_state=new_state,
                trigger="manual",
                reason=f"manual override: {action}",
            )
            self.store.add_actuation(event)
            self._sync_store_devices(node)

    def _sync_store_devices(self, node: str) -> None:
        """Synchronize actuator state into central Store."""
        dev_copy = {d: dict(self._devices[node][d]) for d in ("fan", "hvac", "dehumidifier", "purifier")}
        dev_copy["shutdown"] = self._shutdown[node]
        self.store.set_devices(node, dev_copy)

    def evaluate(self, cleaned: CleanedReading, forecasts: dict[str, Forecast]) -> list[ActuationEvent]:
        """Evaluate readings and forecasts to update device states and alert levels."""
        node = cleaned.node_id
        self._init_node(node)
        ts = cleaned.ts

        settings = self.store.get_settings()
        strategy = settings.get("strategy", config.STRATEGY)
        horizon_min = float(settings.get("horizon_min", config.HORIZON_MIN))

        events: list[ActuationEvent] = []
        node_levels: dict[str, Level] = {}

        # Required device ranks per device
        req_ranks = {d: 0 for d in ("fan", "hvac", "dehumidifier", "purifier")}
        reasons = {d: "" for d in ("fan", "hvac", "dehumidifier", "purifier")}
        triggers = {d: "threshold" for d in ("fan", "hvac", "dehumidifier", "purifier")}
        requires_shutdown = False
        shutdown_reason = ""

        all_in_safe = True

        for m in config.METRICS:
            val = cleaned.clean.get(m)
            lims = self.store.limits(m)
            midpoint = (lims.safe_min + lims.safe_max) / 2.0

            if val is not None:
                self._last_direction[node][m] = "high" if val >= midpoint else "low"
            else:
                val = midpoint

            # Level evaluation
            level = Level.OK
            if val <= lims.hard_min or val >= lims.hard_max:
                level = Level.CRITICAL
                all_in_safe = False
            elif val < lims.safe_min or val > lims.safe_max:
                level = Level.WARNING
                all_in_safe = False
            elif strategy == "proactive":
                fc = forecasts.get(m)
                if (
                    fc is not None
                    and fc.confidence >= config.WATCH_CONFIDENCE
                    and fc.eta_safe_min is not None
                    and fc.eta_safe_min <= horizon_min
                ):
                    level = Level.WATCH

            node_levels[m] = level

            # Level transitions logging
            prev_lvl = self._prev_levels[node].get(m, Level.OK)
            if level != prev_lvl:
                self.store.record_level_change(node, m, prev_lvl, level, ts)
                if level == Level.WARNING:
                    self.store.log_event("warn", f"actuator:{node}", f"{m} elevated to WARNING: {val:.1f} {lims.unit}")
                elif level == Level.CRITICAL:
                    self.store.log_event("critical", f"actuator:{node}", f"{m} reached CRITICAL: {val:.1f} {lims.unit}")

            # Determine device requirements
            if m == "temperature":
                if level == Level.WATCH:
                    fc = forecasts[m]
                    eta = fc.eta_safe_min or 0.0
                    if eta > (horizon_min / 2.0):
                        if req_ranks["fan"] < 1:
                            req_ranks["fan"] = 1
                            reasons["fan"] = f"forecast: temperature leaves safe range in {eta:.1f} sim-min, confidence {fc.confidence:.2f}"
                            triggers["fan"] = "forecast"
                    else:
                        if req_ranks["fan"] < 2:
                            req_ranks["fan"] = 2
                            reasons["fan"] = f"forecast: temperature leaves safe range in {eta:.1f} sim-min, confidence {fc.confidence:.2f}"
                            triggers["fan"] = "forecast"
                        if req_ranks["hvac"] < 1:
                            req_ranks["hvac"] = 1
                            reasons["hvac"] = f"forecast: temperature leaves safe range in {eta:.1f} sim-min, confidence {fc.confidence:.2f}"
                            triggers["hvac"] = "forecast"
                elif level == Level.WARNING:
                    if req_ranks["fan"] < 2:
                        req_ranks["fan"] = 2
                        reasons["fan"] = f"threshold: temperature {val:.1f} {lims.unit} outside safe [{lims.safe_min}, {lims.safe_max}]"
                        triggers["fan"] = "threshold"
                    if req_ranks["hvac"] < 2:
                        req_ranks["hvac"] = 2
                        reasons["hvac"] = f"threshold: temperature {val:.1f} {lims.unit} outside safe [{lims.safe_min}, {lims.safe_max}]"
                        triggers["hvac"] = "threshold"
                elif level == Level.CRITICAL:
                    req_ranks["fan"] = 2
                    req_ranks["hvac"] = 2
                    reasons["fan"] = f"threshold: temperature {val:.1f} {lims.unit} at/beyond hard limit"
                    reasons["hvac"] = f"threshold: temperature {val:.1f} {lims.unit} at/beyond hard limit"
                    triggers["fan"] = "threshold"
                    triggers["hvac"] = "threshold"
                    requires_shutdown = True
                    shutdown_reason = f"critical temperature {val:.1f} {lims.unit}"

            elif m == "humidity":
                if level == Level.WATCH:
                    if req_ranks["dehumidifier"] < 1:
                        fc = forecasts[m]
                        req_ranks["dehumidifier"] = 1
                        reasons["dehumidifier"] = f"forecast: humidity leaves safe range in {fc.eta_safe_min:.1f} sim-min"
                        triggers["dehumidifier"] = "forecast"
                elif level == Level.WARNING:
                    if req_ranks["dehumidifier"] < 2:
                        req_ranks["dehumidifier"] = 2
                        reasons["dehumidifier"] = f"threshold: humidity {val:.1f} % outside safe range"
                        triggers["dehumidifier"] = "threshold"
                elif level == Level.CRITICAL:
                    req_ranks["dehumidifier"] = 2
                    reasons["dehumidifier"] = f"threshold: humidity {val:.1f} % at/beyond hard limit"
                    triggers["dehumidifier"] = "threshold"
                    requires_shutdown = True
                    shutdown_reason = f"critical humidity {val:.1f} %"

            elif m == "aqi":
                if level == Level.WATCH:
                    if req_ranks["purifier"] < 1:
                        fc = forecasts[m]
                        req_ranks["purifier"] = 1
                        reasons["purifier"] = f"forecast: AQI leaves safe range in {fc.eta_safe_min:.1f} sim-min"
                        triggers["purifier"] = "forecast"
                elif level == Level.WARNING:
                    if req_ranks["purifier"] < 2:
                        req_ranks["purifier"] = 2
                        reasons["purifier"] = f"threshold: AQI {val:.1f} outside safe range"
                        triggers["purifier"] = "threshold"
                elif level == Level.CRITICAL:
                    req_ranks["purifier"] = 2
                    reasons["purifier"] = f"threshold: AQI {val:.1f} at/beyond hard limit"
                    triggers["purifier"] = "threshold"
                    requires_shutdown = True
                    shutdown_reason = f"critical AQI {val:.1f}"

        self._prev_levels[node] = dict(node_levels)
        self.store.set_levels(node, node_levels)

        # Hysteresis and step down evaluation
        # Shrunk safe check helper
        def is_metric_in_shrunk_safe(metric: str) -> bool:
            v = cleaned.clean.get(metric)
            if v is None:
                return True
            l = self.store.limits(metric)
            margin = config.HYST_MARGIN * (l.safe_max - l.safe_min)
            return (l.safe_min + margin) <= v <= (l.safe_max - margin)

        dev_metrics = {
            "fan": ["temperature", "aqi"],
            "hvac": ["temperature", "humidity"],
            "dehumidifier": ["humidity"],
            "purifier": ["aqi"],
        }

        # Apply transitions per device
        for d in ("fan", "hvac", "dehumidifier", "purifier"):
            dev_info = self._devices[node][d]
            if dev_info["mode"] == "manual":
                continue

            current_rank = self.STATE_RANK[dev_info["state"]]
            target_rank = req_ranks[d]

            if target_rank > current_rank:
                # Immediate step up
                self._step_down_count[node][d] = 0
                new_state = self.RANK_STATE[target_rank]
                ev = ActuationEvent(
                    ts=ts,
                    node_id=node,
                    device=d,
                    from_state=dev_info["state"],
                    to_state=new_state,
                    trigger=triggers[d],
                    reason=reasons[d],
                )
                self._devices[node][d]["state"] = new_state
                self._turn_on_ts[node][d] = ts
                self.store.add_actuation(ev)
                events.append(ev)

            elif target_rank < current_rank:
                # Check hysteresis conditions for stepping down
                shrunk_safe = all(is_metric_in_shrunk_safe(m_name) for m_name in dev_metrics[d])
                on_duration = ts - self._turn_on_ts[node][d]

                if shrunk_safe:
                    self._step_down_count[node][d] += 1
                else:
                    self._step_down_count[node][d] = 0

                if (
                    self._step_down_count[node][d] >= config.HYST_READINGS
                    and on_duration >= config.MIN_ON_SECONDS
                ):
                    new_rank = current_rank - 1
                    new_state = self.RANK_STATE[new_rank]
                    self._step_down_count[node][d] = 0
                    ev = ActuationEvent(
                        ts=ts,
                        node_id=node,
                        device=d,
                        from_state=dev_info["state"],
                        to_state=new_state,
                        trigger="release",
                        reason="metrics recovered into shrunk safe range",
                    )
                    self._devices[node][d]["state"] = new_state
                    if new_rank == 0:
                        self._turn_on_ts[node][d] = 0.0
                    else:
                        self._turn_on_ts[node][d] = ts
                    self.store.add_actuation(ev)
                    events.append(ev)
            else:
                self._step_down_count[node][d] = 0

        # Shutdown state management
        if requires_shutdown:
            if not self._shutdown[node]:
                self._shutdown[node] = True
                self._shutdown_clear_count[node] = 0
                self.store.log_event("critical", f"actuator:{node}", f"Emergency soft shutdown: {shutdown_reason}")
                ev = ActuationEvent(
                    ts=ts,
                    node_id=node,
                    device="shutdown",
                    from_state="off",
                    to_state="on",
                    trigger="threshold",
                    reason=shutdown_reason,
                )
                self.store.add_actuation(ev)
                events.append(ev)
        else:
            if self._shutdown[node]:
                if all_in_safe:
                    self._shutdown_clear_count[node] += 1
                    if self._shutdown_clear_count[node] >= 3 * config.HYST_READINGS:
                        self._shutdown[node] = False
                        self._shutdown_clear_count[node] = 0
                        ev = ActuationEvent(
                            ts=ts,
                            node_id=node,
                            device="shutdown",
                            from_state="on",
                            to_state="off",
                            trigger="release",
                            reason="all metrics remained safe for 3x hysteresis window",
                        )
                        self.store.add_actuation(ev)
                        events.append(ev)
                else:
                    self._shutdown_clear_count[node] = 0

        self._sync_store_devices(node)
        return events

    def effects_for(self, node: str) -> dict[str, float]:
        """Calculate aggregate environmental feedback offsets produced by active actuators."""
        self._init_node(node)
        effects: dict[str, float] = {m: 0.0 for m in config.METRICS}

        for dev in ("fan", "hvac", "dehumidifier"):
            state = self._devices[node][dev]["state"]
            if state in ("low", "high"):
                dev_effs = config.EFFECTS[dev][state]
                for m, offset in dev_effs.items():
                    direction = self._last_direction[node].get(m, "high")
                    if direction == "low":
                        effects[m] += -offset
                    else:
                        effects[m] += offset

        # Purifier never flips sign
        pur_state = self._devices[node]["purifier"]["state"]
        if pur_state in ("low", "high"):
            for m, offset in config.EFFECTS["purifier"][pur_state].items():
                effects[m] += offset

        # Shutdown never flips sign
        if self._shutdown[node]:
            for m, offset in config.EFFECTS["shutdown"].items():
                effects[m] += offset

        return effects
