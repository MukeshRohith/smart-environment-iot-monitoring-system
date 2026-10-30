"""Multi-node environmental physics simulation engine with fault injection and closed-loop actuator feedback."""
from __future__ import annotations

import math
import random
import threading
import time
from typing import Any, Callable

import numpy as np
import requests

import config
from core.store import Store


class NodeSim:
    """State and physics model for a single simulated IoT node."""

    def __init__(self, node_id: str, initial_targets: dict[str, float]) -> None:
        """Initialize telemetry state, target bounds, and sequence tracking."""
        self.node_id = node_id
        self.target: dict[str, float] = dict(initial_targets)
        self.current: dict[str, float] = dict(initial_targets)
        self.noise: float = config.DEFAULT_NOISE
        self.send_rate_hz: float = config.DEFAULT_SEND_HZ
        self.fault_mode: str = "none"
        self.enabled: bool = True
        self.seq: int = 0
        self.last_emitted: dict[str, float | None] = dict(initial_targets)
        self.last_tick: float = time.time()

    def get_controls(self) -> dict[str, Any]:
        """Return full operational controls dictionary."""
        return {
            "target": dict(self.target),
            "current": dict(self.current),
            "noise": self.noise,
            "send_rate_hz": self.send_rate_hz,
            "fault_mode": self.fault_mode,
            "enabled": self.enabled,
        }


class SimulatorManager:
    """Manages worker threads, physics lag integration, and fault injection for all simulated nodes."""

    def __init__(
        self,
        store: Store,
        effects_provider: Callable[[str], dict[str, float]],
        shutdown_provider: Callable[[str], bool],
        transport: Callable[[dict[str, Any]], int] | None = None,
    ) -> None:
        """Initialize simulation manager with dependencies and default node ensemble."""
        self.store = store
        self.effects_provider = effects_provider
        self.shutdown_provider = shutdown_provider
        self._custom_transport = transport
        self.send_errors: int = 0

        self._session = requests.Session()
        self._lock = threading.RLock()
        self._nodes: dict[str, NodeSim] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._stop_events: dict[str, threading.Event] = {}
        self._running = False

        self._init_default_nodes()

    def _default_transport(self, payload: dict[str, Any]) -> int:
        """Post telemetry reading to local ingest API endpoint."""
        try:
            resp = self._session.post(
                f"http://127.0.0.1:{config.PORT}/api/ingest",
                json=payload,
                timeout=1.0,
            )
            return resp.status_code
        except Exception:
            self.send_errors += 1
            return 0

    def transport(self, payload: dict[str, Any]) -> int:
        """Route payload to custom or default network transport."""
        if self._custom_transport is not None:
            return self._custom_transport(payload)
        return self._default_transport(payload)

    def _get_midpoint_targets(self) -> dict[str, float]:
        """Calculate middle of safe range for all metrics under active profile."""
        lims = self.store.all_limits()
        return {m: (lims[m].safe_min + lims[m].safe_max) / 2.0 for m in config.METRICS}

    def _init_default_nodes(self) -> None:
        """Create and register initial default node ensemble."""
        targets = self._get_midpoint_targets()
        for i in range(1, config.DEFAULT_NODES + 1):
            node_id = f"node-{i}"
            node = NodeSim(node_id, targets)
            self._nodes[node_id] = node
            self._stop_events[node_id] = threading.Event()
            self.store.register_node(node_id, node.get_controls())

    def reset_to_profile(self) -> None:
        """Re-center all node targets to the active profile's safe mid-range."""
        with self._lock:
            targets = self._get_midpoint_targets()
            for node in self._nodes.values():
                node.target = dict(targets)
                self.store.set_node_controls(node.node_id, node.get_controls())

    def set_controls(self, node_id: str, partial: dict[str, Any]) -> None:
        """Validate bounds and update operational controls for a node."""
        with self._lock:
            if node_id not in self._nodes:
                raise ValueError(f"Node {node_id} does not exist")
            node = self._nodes[node_id]

            if "target" in partial:
                new_targets = dict(node.target)
                for m, val in partial["target"].items():
                    if m not in config.METRICS:
                        continue
                    lim = self.store.limits(m)
                    f_val = float(val)
                    if f_val < lim.slider_min or f_val > lim.slider_max:
                        raise ValueError(
                            f"Target {m}={f_val} outside allowable slider range [{lim.slider_min}, {lim.slider_max}]"
                        )
                    new_targets[m] = f_val
                node.target = new_targets

            if "noise" in partial:
                noise_val = float(partial["noise"])
                if noise_val < 0.0 or noise_val > 1.0:
                    raise ValueError("noise must be between 0.0 and 1.0")
                node.noise = noise_val

            if "send_rate_hz" in partial:
                rate = float(partial["send_rate_hz"])
                if rate < 0.2 or rate > 20.0:
                    raise ValueError("send_rate_hz must be between 0.2 and 20.0")
                node.send_rate_hz = rate

            if "fault_mode" in partial:
                fm = str(partial["fault_mode"])
                if fm not in config.FAULT_MODES:
                    raise ValueError(f"fault_mode must be one of {config.FAULT_MODES}")
                node.fault_mode = fm

            if "enabled" in partial:
                node.enabled = bool(partial["enabled"])

            self.store.set_node_controls(node_id, node.get_controls())

    def add_node(self, node_id: str | None = None) -> str:
        """Add a new simulated node up to MAX_NODES limit."""
        with self._lock:
            if len(self._nodes) >= config.MAX_NODES:
                raise ValueError(f"Maximum node count of {config.MAX_NODES} reached")

            if not node_id:
                idx = 1
                while f"node-{idx}" in self._nodes:
                    idx += 1
                node_id = f"node-{idx}"

            if node_id in self._nodes:
                raise ValueError(f"Node {node_id} already exists")

            targets = self._get_midpoint_targets()
            node = NodeSim(node_id, targets)
            self._nodes[node_id] = node
            self._stop_events[node_id] = threading.Event()
            self.store.register_node(node_id, node.get_controls())

            if self._running:
                t = threading.Thread(target=self._node_loop, args=(node_id,), daemon=True)
                self._threads[node_id] = t
                t.start()

            return node_id

    def remove_node(self, node_id: str | None = None) -> str:
        """Remove a simulated node down to minimum of 1 node."""
        with self._lock:
            if len(self._nodes) <= 1:
                raise ValueError("At least 1 node must remain active")

            if not node_id:
                node_id = sorted(list(self._nodes.keys()))[-1]

            if node_id not in self._nodes:
                raise ValueError(f"Node {node_id} does not exist")

            # Signal thread to stop
            stop_evt = self._stop_events.pop(node_id, None)
            if stop_evt:
                stop_evt.set()

            thread = self._threads.pop(node_id, None)
            if thread and thread.is_alive():
                thread.join(timeout=1.0)

            del self._nodes[node_id]
            self.store.unregister_node(node_id)
            return node_id

    def apply_scenario(self, name: str) -> None:
        """Apply pre-configured test scenarios across the node ensemble."""
        with self._lock:
            lims = self.store.all_limits()
            targets_mid = self._get_midpoint_targets()

            if name == "normal":
                for node in self._nodes.values():
                    node.target = dict(targets_mid)
                    node.noise = config.DEFAULT_NOISE
                    node.send_rate_hz = 1.0
                    node.fault_mode = "none"
                    node.enabled = True
                    self.store.set_node_controls(node.node_id, node.get_controls())

            elif name == "heat_spike":
                if "node-1" not in self._nodes:
                    self.add_node("node-1")
                t_lim = lims["temperature"]
                spike_target = t_lim.safe_max + 0.8 * (t_lim.hard_max - t_lim.safe_max)
                self.set_controls("node-1", {"target": {"temperature": spike_target}})

            elif name in ("humidity_drift", "humidity_leak"):
                if "node-2" not in self._nodes:
                    self.add_node("node-2")
                h_lim = lims["humidity"]
                drift_target = h_lim.safe_max + 0.8 * (h_lim.hard_max - h_lim.safe_max)
                self.set_controls("node-2", {"target": {"humidity": drift_target}})

            elif name in ("sensor_fault", "sensor_failure"):
                if "node-3" not in self._nodes:
                    self.add_node("node-3")
                self.set_controls("node-3", {"fault_mode": "outlier", "noise": 0.5})

            elif name == "burst_traffic":
                for node_id in self._nodes:
                    self.set_controls(node_id, {"send_rate_hz": 15.0})

            elif name == "cascade_failure":
                if "node-1" not in self._nodes:
                    self.add_node("node-1")
                if "node-2" not in self._nodes:
                    self.add_node("node-2")
                if "node-3" not in self._nodes:
                    self.add_node("node-3")
                t_lim = lims["temperature"]
                h_lim = lims["humidity"]
                self.set_controls("node-1", {"target": {"temperature": t_lim.hard_max + 1.0}})
                self.set_controls("node-2", {"target": {"humidity": h_lim.hard_max + 2.0}})
                self.set_controls("node-3", {"fault_mode": "outlier", "noise": 0.6, "send_rate_hz": 5.0})

            else:
                raise ValueError(f"Unknown scenario name: {name}")

    def _step_node(self, node: NodeSim, dt: float) -> dict[str, Any]:
        """Integrate 1 physics step and apply noise/fault generation."""
        eff_offset = self.effects_provider(node.node_id)
        values: dict[str, float | None] = {}

        for m in config.METRICS:
            phys_min, phys_max = config.PHYSICAL[m]
            offset = eff_offset.get(m, 0.0)
            eff_target = float(np.clip(node.target[m] + offset, phys_min, phys_max))

            # First-order thermal/environmental lag
            alpha = 1.0 - math.exp(-dt / config.LAG_TAU_S)
            node.current[m] += (eff_target - node.current[m]) * alpha

            # Base gaussian sensor noise
            sigma = node.noise * config.BASE_NOISE_SIGMA[m]
            noise_val = float(np.random.normal(0.0, sigma)) if sigma > 0 else 0.0
            values[m] = float(np.clip(node.current[m] + noise_val, phys_min, phys_max))

        # Fault Mode Injection
        if node.fault_mode == "dropout":
            for m in config.METRICS:
                if random.random() < 0.25:
                    values[m] = None
        elif node.fault_mode == "outlier":
            if random.random() < 0.08:
                m_chosen = random.choice(config.METRICS)
                sign = 1.0 if random.random() < 0.5 else -1.0
                spike_table = {"temperature": 15.0, "humidity": 40.0, "aqi": 200.0}
                if values[m_chosen] is not None:
                    values[m_chosen] += sign * spike_table[m_chosen]
        elif node.fault_mode == "stuck":
            values = dict(node.last_emitted)

        if node.fault_mode != "stuck":
            node.last_emitted = dict(values)

        node.seq += 1
        is_shut = self.shutdown_provider(node.node_id)
        status = "shutdown" if is_shut else "ok"

        return {
            "node_id": node.node_id,
            "ts": time.time(),
            "seq": node.seq,
            "temperature": values["temperature"],
            "humidity": values["humidity"],
            "aqi": values["aqi"],
            "status": status,
        }

    def _node_loop(self, node_id: str) -> None:
        """Worker loop for a single simulated node thread."""
        stop_evt = self._stop_events.get(node_id)
        while self._running and stop_evt and not stop_evt.is_set():
            with self._lock:
                node = self._nodes.get(node_id)
                if not node or not node.enabled:
                    time.sleep(0.1)
                    continue

                now = time.time()
                dt = max(0.001, now - node.last_tick)
                node.last_tick = now
                payload = self._step_node(node, dt)
                rate = node.send_rate_hz

            try:
                self.transport(payload)
            except Exception:
                self.send_errors += 1

            sleep_duration = 1.0 / max(0.2, rate)
            if stop_evt.wait(sleep_duration):
                break

    def start(self) -> None:
        """Start daemon simulation threads for all active nodes."""
        with self._lock:
            self._running = True
            for node_id in self._nodes:
                stop_evt = self._stop_events.get(node_id)
                if stop_evt:
                    stop_evt.clear()
                t = threading.Thread(target=self._node_loop, args=(node_id,), daemon=True)
                self._threads[node_id] = t
                t.start()

    def stop(self) -> None:
        """Signal and cleanly join all running simulation threads."""
        with self._lock:
            self._running = False
            for stop_evt in self._stop_events.values():
                stop_evt.set()

            for t in self._threads.values():
                if t.is_alive():
                    t.join(timeout=1.0)
            self._threads.clear()

    def get_state(self) -> dict[str, Any]:
        """Return operational state and controls for all nodes."""
        with self._lock:
            return {
                "nodes": {
                    node_id: {
                        "controls": node.get_controls(),
                        "current": dict(node.current),
                    }
                    for node_id, node in self._nodes.items()
                },
                "send_errors": self.send_errors,
            }
