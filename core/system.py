"""Central System orchestrator unifying Store, Pipeline, Forecaster, Actuator, Simulator, and Analytics."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

import config
from core.actuator import ActuatorEngine
from core.benchmark import BenchmarkRunner
from core.demo import DemoRunner
from core.forecaster import Forecaster
from core.metrics import MetricsEngine
from core.pipeline import Pipeline
from core.simulator import SimulatorManager
from core.store import Store
from core.wrangler import Wrangler


class System:
    """Root system context owning and synchronizing all component lifecycles."""

    def __init__(
        self,
        initial_profile: str | None = None,
        transport: Callable[[dict[str, Any]], int] | None = None,
    ) -> None:
        """Instantiate complete monitoring architecture and dependency injection graph."""
        self.store = Store(initial_profile or config.DEFAULT_PROFILE)
        self.wrangler = Wrangler(store=self.store)
        self.forecaster = Forecaster(store=self.store)
        self.actuator = ActuatorEngine(store=self.store)

        self.pipeline = Pipeline(
            store=self.store,
            wrangler=self.wrangler,
            forecaster=self.forecaster,
            actuator=self.actuator,
        )

        self.simulator = SimulatorManager(
            store=self.store,
            effects_provider=self.actuator.effects_for,
            shutdown_provider=self.actuator.is_shutdown,
            transport=transport,
        )

        self.metrics = MetricsEngine(store=self.store)
        self.benchmark = BenchmarkRunner(system=self)
        self.demo = DemoRunner(system=self)

        self._watchdog_thread: threading.Thread | None = None
        self._watchdog_stop = threading.Event()
        self._running = False

    def start(self, start_simulator: bool = True) -> None:
        """Start pipeline worker threads, simulator, and self-healing watchdog monitor."""
        self._running = True
        self.pipeline.start()

        if start_simulator:
            self.simulator.start()

        self._watchdog_stop.clear()
        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog_thread.start()

    def stop(self) -> None:
        """Gracefully terminate watchdog, simulator threads, and worker pipeline."""
        self._running = False
        self._watchdog_stop.set()
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            self._watchdog_thread.join(timeout=1.0)

        self.simulator.stop()
        self.pipeline.stop()
        self.demo.stop()

    def reset_runtime(self) -> None:
        """Reset operational transient buffers across all stateful subsystems."""
        self.store.reset_runtime()
        self.wrangler.reset()
        self.forecaster.reset()
        self.actuator.reset()

    def get_settings(self) -> dict[str, Any]:
        """Return currently configured operational settings."""
        return self.store.get_settings()

    def apply_settings(self, partial: dict[str, Any]) -> dict[str, Any]:
        """Validate parameter ranges and update live operational settings across components."""
        validated: dict[str, Any] = {}

        if "strategy" in partial:
            strat = str(partial["strategy"])
            if strat not in ("proactive", "reactive"):
                raise ValueError("strategy must be 'proactive' or 'reactive'")
            validated["strategy"] = strat

        if "pipeline_mode" in partial:
            mode = str(partial["pipeline_mode"])
            if mode not in ("priority", "fifo"):
                raise ValueError("pipeline_mode must be 'priority' or 'fifo'")
            validated["pipeline_mode"] = mode

        if "workers" in partial:
            w = int(partial["workers"])
            if w < 1 or w > 8:
                raise ValueError("workers must be between 1 and 8")
            validated["workers"] = w

        if "processing_delay_ms" in partial:
            delay = float(partial["processing_delay_ms"])
            if delay < 0.0 or delay > 200.0:
                raise ValueError("processing_delay_ms must be between 0 and 200")
            validated["processing_delay_ms"] = delay

        if "horizon_min" in partial:
            h = float(partial["horizon_min"])
            if h < 15.0 or h > 30.0:
                raise ValueError("horizon_min must be between 15 and 30")
            validated["horizon_min"] = h

        if "time_scale" in partial:
            scale = float(partial["time_scale"])
            if scale < 10.0 or scale > 300.0:
                raise ValueError("time_scale must be between 10 and 300")
            validated["time_scale"] = scale

        updated = self.store.update_settings(validated)
        self.pipeline.apply_settings()
        return updated

    def set_profile(self, name: str) -> None:
        """Switch active environment profile and reset downstream prediction and control baselines."""
        if name not in config.PROFILES:
            raise ValueError(f"Unknown profile '{name}'; available: {list(config.PROFILES.keys())}")

        self.store.set_profile(name)
        self.simulator.reset_to_profile()
        self.forecaster.reset()
        self.actuator.reset()

    def state(self) -> dict[str, Any]:
        """Aggregate store snapshot with pipeline, metrics, benchmark, and demo subsystems."""
        snap = self.store.snapshot()
        snap["pipeline"] = self.pipeline.snapshot()
        snap["metrics"] = self.metrics.compute()
        snap["benchmark"] = self.benchmark.status()
        snap["demo"] = self.demo.state()
        return snap

    def _watchdog_loop(self) -> None:
        """Monitor thread health and automatically recover terminated worker routines."""
        while not self._watchdog_stop.is_set():
            if self._running:
                # Check pipeline worker liveness
                if not self.pipeline.is_alive():
                    self.store.log_event("warn", "watchdog", "Pipeline workers dead; restarting pool")
                    self.pipeline.start()

                # Check simulator threads
                if self.simulator._running:
                    for node_id, node in self.simulator._nodes.items():
                        if node.enabled:
                            t = self.simulator._threads.get(node_id)
                            if t is None or not t.is_alive():
                                self.store.log_event("warn", "watchdog", f"Simulator thread for {node_id} dead; restarting")
                                self.simulator._threads[node_id] = threading.Thread(
                                    target=self.simulator._node_loop, args=(node_id,), daemon=True
                                )
                                self.simulator._threads[node_id].start()

            self._watchdog_stop.wait(5.0)
