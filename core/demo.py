"""Interactive guided walkthrough demonstrating pipeline stages, anomaly handling, and closed-loop actuation."""
from __future__ import annotations

import threading
import time
from typing import Any


class DemoRunner:
    """Controls interactive guided demonstration scenario flow with live narrative captions."""

    DEFAULT_STEP_DURATIONS: dict[int, float] = {
        1: 15.0,
        2: 35.0,
        3: 20.0,
        4: 20.0,
        5: 30.0,
        6: 10.0,
    }

    CAPTIONS: dict[int, str] = {
        1: "Baseline normal: verifying pipeline stages (wrangle, forecast, actuate)",
        2: "Node-1 heat spike: observing forecast ETA, proactive fan, tiered HVAC",
        3: "Sensor fault: anomalies prioritized in queue, missing/outliers flagged and cleaned",
        4: "Burst traffic: queue depth rising, critical telemetry processed ahead of normal",
        5: "Humidity drift: predictive forecast triggering early dehumidifier automation",
        6: "Demo summary: loss rate, critical latency, forecast accuracy, prevented breaches",
    }

    def __init__(self, system: Any, step_durations: dict[int, float] | None = None) -> None:
        """Initialize demo runner with target system and configurable step durations."""
        self.system = system
        self._durations: dict[int, float] = dict(self.DEFAULT_STEP_DURATIONS)
        if step_durations:
            self._durations.update(step_durations)

        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False
        self._paused = False
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()

        self._current_step: int = 1
        self._remaining_s: float = 0.0
        self._caption: str = ""
        self._summary: dict[str, Any] | None = None

    def state(self) -> dict[str, Any]:
        """Return snapshot of current demo progress, step caption, and remaining time."""
        with self._lock:
            return {
                "running": self._running,
                "paused": self._paused,
                "step": self._current_step,
                "steps_total": 6,
                "caption": self._caption,
                "remaining_s": round(max(0.0, self._remaining_s), 2),
                "summary": self._summary,
            }

    def start(self) -> None:
        """Launch demonstration walkthrough in background daemon thread."""
        with self._lock:
            if self._running:
                return

            self._running = True
            self._paused = False
            self._stop_event.clear()
            self._pause_event.set()
            self._current_step = 1
            self._summary = None

            self._thread = threading.Thread(target=self._run_demo, daemon=True)
            self._thread.start()

    def pause(self) -> None:
        """Pause demo timer and step progression."""
        with self._lock:
            if self._running and not self._paused:
                self._paused = True
                self._pause_event.clear()

    def resume(self) -> None:
        """Resume active demo from paused state."""
        with self._lock:
            if self._running and self._paused:
                self._paused = False
                self._pause_event.set()

    def stop(self) -> None:
        """Stop demo immediately and restore normal system operational state."""
        with self._lock:
            if not self._running:
                return
            self._stop_event.set()
            self._pause_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

        with self._lock:
            self._running = False
            self._paused = False
            self._caption = "Demo stopped"

    def _run_demo(self) -> None:
        """Sequential execution of the 6 demonstration narrative stages."""
        saved_settings = self.system.get_settings()

        try:
            for step_num in range(1, 7):
                if self._stop_event.is_set():
                    break

                with self._lock:
                    self._current_step = step_num
                    self._caption = self.CAPTIONS.get(step_num, "")
                    duration = self._durations.get(step_num, 15.0)
                    self._remaining_s = duration

                # Execute step scenario actions
                if step_num == 1:
                    self.system.simulator.apply_scenario("normal")
                elif step_num == 2:
                    self.system.simulator.apply_scenario("heat_spike")
                elif step_num == 3:
                    self.system.simulator.apply_scenario("sensor_fault")
                elif step_num == 4:
                    self.system.simulator.apply_scenario("burst_traffic")
                elif step_num == 5:
                    self.system.simulator.apply_scenario("humidity_drift")
                elif step_num == 6:
                    metrics = self.system.metrics.compute()
                    latencies = metrics.get("latency", {})
                    with self._lock:
                        self._summary = {
                            "packet_loss_pct": metrics.get("packet_loss_pct", 0.0),
                            "critical_p95_latency_s": latencies.get("0", {}).get("p95", 0.0),
                            "forecast_error": metrics.get("forecast_error", {}),
                            "breaches_anticipated": metrics.get("breaches", {}).get("anticipated", 0),
                            "breaches_prevented": metrics.get("breaches", {}).get("prevented", 0),
                        }

                # High-frequency countdown loop honoring pause and stop
                dt_step = 0.02
                while True:
                    with self._lock:
                        if self._remaining_s <= 0 or self._stop_event.is_set():
                            break

                    self._pause_event.wait()
                    if self._stop_event.is_set():
                        break

                    time.sleep(dt_step)
                    if self._pause_event.is_set():
                        with self._lock:
                            self._remaining_s -= dt_step

        finally:
            self.system.simulator.apply_scenario("normal")
            self.system.apply_settings(saved_settings)
            with self._lock:
                self._running = False
                self._paused = False
                if not self._stop_event.is_set():
                    self._caption = "Demo completed"
