"""Automated benchmarking runner comparing strategies and pipeline prioritization modes."""
from __future__ import annotations

import csv
import io
import threading
import time
from typing import Any, Callable


class BenchmarkRunner:
    """Orchestrates comparative benchmarking experiments with live status and metrics collection."""

    def __init__(self, system: Any, sleep_fn: Callable[[float], None] | None = None) -> None:
        """Initialize benchmark runner bound to system interface."""
        self.system = system
        self._sleep_fn = sleep_fn or time.sleep
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._status: dict[str, Any] = {
            "state": "idle",
            "progress": 0.0,
            "phase": "idle",
            "name": "",
            "error": None,
        }
        self._results: dict[str, Any] = {}

    def status(self) -> dict[str, Any]:
        """Return snapshot of current execution status and progress."""
        with self._lock:
            return dict(self._status)

    def results(self) -> dict[str, Any]:
        """Return dictionary of all completed benchmark results."""
        with self._lock:
            return dict(self._results)

    def results_csv(self) -> str:
        """Serialize benchmark results to CSV format with columns: benchmark,variant,metric,value."""
        with self._lock:
            output = io.StringIO()
            writer = csv.writer(output, lineterminator="\n")
            writer.writerow(["benchmark", "variant", "metric", "value"])

            for b_name, b_res in self._results.items():
                if "comparison" in b_res:
                    comp = b_res["comparison"]
                    for variant, metrics in comp.items():
                        if isinstance(metrics, dict):
                            for m_name, val in metrics.items():
                                writer.writerow([b_name, variant, m_name, val])

            return output.getvalue()

    def run(self, name: str, duration_s: float | None = None) -> None:
        """Launch benchmark run in background thread."""
        with self._lock:
            if self._status["state"] == "running":
                raise RuntimeError("Benchmark already running")
            if name not in ("strategy", "pipeline"):
                raise ValueError(f"Unknown benchmark name: {name}")

            self._status = {
                "state": "running",
                "progress": 0.0,
                "phase": f"starting {name}",
                "name": name,
                "error": None,
            }

            self._thread = threading.Thread(
                target=self._run_wrapper,
                args=(name, duration_s),
                daemon=True,
            )
            self._thread.start()

    def _run_wrapper(self, name: str, duration_s: float | None) -> None:
        """Execute benchmark workflow with guaranteed finally-block state restoration."""
        saved_settings = self.system.get_settings()
        saved_sim_state = {}
        if hasattr(self.system, "simulator") and hasattr(self.system.simulator, "get_state"):
            saved_sim_state = self.system.simulator.get_state()

        try:
            if name == "strategy":
                self._run_strategy_benchmark(duration_s or 60.0)
            elif name == "pipeline":
                self._run_pipeline_benchmark(duration_s or 45.0)

            with self._lock:
                self._status["state"] = "done"
                self._status["progress"] = 1.0
                self._status["phase"] = "completed"

        except Exception as exc:
            with self._lock:
                self._status["state"] = "error"
                self._status["error"] = str(exc)
                self._status["phase"] = "failed"
        finally:
            self.system.apply_settings(saved_settings)
            if "nodes" in saved_sim_state and hasattr(self.system, "simulator"):
                for nid, nstate in saved_sim_state["nodes"].items():
                    if "controls" in nstate:
                        self.system.simulator.set_controls(nid, nstate["controls"])

    def _run_strategy_benchmark(self, duration_s: float) -> None:
        """Benchmark reactive vs proactive automation strategies under a heat spike."""
        phases = ["reactive", "proactive"]
        data: dict[str, dict[str, Any]] = {}
        warmup = min(25.0, duration_s * 0.4)

        for i, strat in enumerate(phases):
            with self._lock:
                self._status["phase"] = f"{strat}: warming up"
                self._status["progress"] = (i * 0.5) + 0.05

            self.system.reset_runtime()
            self.system.apply_settings({"strategy": strat})
            self.system.simulator.apply_scenario("normal")
            self._sleep_fn(warmup)

            with self._lock:
                self._status["phase"] = f"{strat}: heat_spike evaluation"
                self._status["progress"] = (i * 0.5) + 0.25

            self.system.simulator.apply_scenario("heat_spike")
            self._sleep_fn(duration_s)

            metrics = self.system.metrics.compute()
            exposure_node1 = metrics["exposure"].get("node-1", {}).get("temperature", {})
            actuations = len(self.system.store.actuations(9999))
            dev_secs = metrics["device_on_seconds"].get("node-1", 0.0)

            data[strat] = {
                "peak_temperature": exposure_node1.get("peak", 0.0),
                "seconds_outside_safe": exposure_node1.get("seconds_outside", 0.0),
                "seconds_above_hard": exposure_node1.get("seconds_above_hard", 0.0),
                "actuations_count": actuations,
                "total_device_on_seconds": dev_secs,
                "anticipated_breaches": metrics["breaches"]["anticipated"],
                "missed_breaches": metrics["breaches"]["missed"],
            }

        # Percentage improvement
        reac = data["reactive"]["seconds_outside_safe"]
        pro = data["proactive"]["seconds_outside_safe"]
        improvement_pct = ((reac - pro) / reac * 100.0) if reac > 0 else 0.0

        with self._lock:
            self._results["strategy"] = {
                "comparison": data,
                "improvement_pct": improvement_pct,
            }

    def _run_pipeline_benchmark(self, duration_s: float) -> None:
        """Benchmark FIFO vs priority queue modes under burst traffic congestion."""
        self.system.apply_settings({"workers": 1, "processing_delay_ms": 40.0})
        modes = ["fifo", "priority"]
        data: dict[str, dict[str, Any]] = {}

        for i, mode in enumerate(modes):
            with self._lock:
                self._status["phase"] = f"{mode}: burst & heat spike"
                self._status["progress"] = (i * 0.5) + 0.1

            self.system.reset_runtime()
            self.system.apply_settings({"pipeline_mode": mode})
            self.system.simulator.apply_scenario("burst_traffic")
            self.system.simulator.apply_scenario("heat_spike")
            self._sleep_fn(duration_s)

            metrics = self.system.metrics.compute()
            latencies = metrics["latency"]
            crit_p95 = latencies.get("0", {}).get("p95", 0.0)
            norm_p95 = latencies.get("2", {}).get("p95", 0.0)
            dropped = self.system.store.counters().get("dropped", 0)
            loss_pct = metrics["packet_loss_pct"]

            data[mode] = {
                "critical_p95_latency_s": crit_p95,
                "normal_p95_latency_s": norm_p95,
                "dropped_count": dropped,
                "packet_loss_pct": loss_pct,
            }

        with self._lock:
            self._results["pipeline"] = {
                "comparison": data,
            }
