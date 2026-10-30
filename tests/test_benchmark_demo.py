"""Tests for automated benchmarking runner and guided demo narrative engine."""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import time
import pytest
from core.benchmark import BenchmarkRunner
from core.demo import DemoRunner
from core.models import Level
from core.store import Store


class FakeSimulator:
    """Mock simulator for benchmark and demo harness."""

    def __init__(self) -> None:
        self.scenarios_applied: list[str] = []
        self.controls_set: list[tuple[str, dict]] = []

    def apply_scenario(self, name: str) -> None:
        self.scenarios_applied.append(name)

    def get_state(self) -> dict:
        return {
            "nodes": {
                "node-1": {"controls": {"target": {"temperature": 22.0}, "noise": 0.2}},
            },
            "send_errors": 0,
        }

    def set_controls(self, node: str, controls: dict) -> None:
        self.controls_set.append((node, controls))


class FakeMetrics:
    """Mock metrics engine returning structured performance stats."""

    def __init__(self, raise_on_compute: bool = False) -> None:
        self.raise_on_compute = raise_on_compute

    def compute(self) -> dict:
        if self.raise_on_compute:
            raise RuntimeError("Synthetic metrics compute failure")
        return {
            "packet_loss_pct": 0.5,
            "counts": {"received": 100, "dropped": 2},
            "latency": {
                "0": {"p95": 0.015},
                "2": {"p95": 0.045},
            },
            "forecast_error": {"temperature": {"mae": 0.35, "rmse": 0.42, "n": 20}},
            "lead_time": {"avg_s": 8.5, "count": 3},
            "breaches": {"prevented": 2, "missed": 0, "anticipated": 3, "total": 3},
            "exposure": {
                "node-1": {
                    "temperature": {"peak": 26.5, "seconds_outside": 5.0, "seconds_above_hard": 0.0}
                }
            },
            "device_on_seconds": {"node-1": 15.0},
        }


class FakeSystem:
    """Mock system interface conforming to SPEC.md."""

    def __init__(self, raise_on_metrics: bool = False) -> None:
        self.store = Store("data_center")
        self.simulator = FakeSimulator()
        self.metrics = FakeMetrics(raise_on_compute=raise_on_metrics)
        self._settings = {
            "strategy": "proactive",
            "pipeline_mode": "priority",
            "workers": 3,
            "processing_delay_ms": 5.0,
            "horizon_min": 15.0,
            "time_scale": 60.0,
        }
        self.applied_settings: list[dict] = []
        self.reset_count = 0

    def get_settings(self) -> dict:
        return dict(self._settings)

    def apply_settings(self, partial: dict) -> None:
        self._settings.update(partial)
        self.applied_settings.append(dict(partial))

    def reset_runtime(self) -> None:
        self.reset_count += 1
        self.store.reset_runtime()


def test_restore_in_finally_even_when_phase_raises():
    """Verify benchmark restores original settings and simulator controls even when an exception occurs."""
    system = FakeSystem(raise_on_metrics=True)
    initial_settings = system.get_settings()

    # Instant sleep function for tests
    bench = BenchmarkRunner(system, sleep_fn=lambda s: None)
    bench.run("strategy", duration_s=1.0)
    time.sleep(0.1)

    status = bench.status()
    assert status["state"] == "error"
    assert "Synthetic metrics compute failure" in status["error"]

    # Verify settings were restored
    current_settings = system.get_settings()
    assert current_settings["strategy"] == initial_settings["strategy"]
    assert len(system.simulator.controls_set) > 0


def test_benchmark_status_progression_and_csv_shape():
    """Verify benchmark status lifecycle (idle -> running -> done) and CSV export structure."""
    system = FakeSystem()
    bench = BenchmarkRunner(system, sleep_fn=lambda s: None)

    assert bench.status()["state"] == "idle"

    bench.run("strategy", duration_s=1.0)
    time.sleep(0.1)

    status = bench.status()
    assert status["state"] == "done"
    assert status["progress"] == 1.0

    res = bench.results()
    assert "strategy" in res
    assert "comparison" in res["strategy"]
    assert "reactive" in res["strategy"]["comparison"]
    assert "proactive" in res["strategy"]["comparison"]

    csv_data = bench.results_csv()
    lines = [l.strip() for l in csv_data.strip().split("\n") if l.strip()]
    assert lines[0] == "benchmark,variant,metric,value"
    assert len(lines) > 5
    assert any("strategy,reactive,peak_temperature" in l for l in lines)


def test_demo_pause_resume_stop_with_short_durations():
    """Verify demo progression, pause, resume, and stop controls using shortened step durations."""
    system = FakeSystem()
    # 0.8s duration per step gives ample time to test pause/resume
    short_durations = {i: 0.8 for i in range(1, 7)}
    demo = DemoRunner(system, step_durations=short_durations)

    assert not demo.state()["running"]

    # 1. Start demo
    demo.start()
    time.sleep(0.1)
    st = demo.state()
    assert st["running"]
    assert not st["paused"]
    assert st["step"] == 1

    # 2. Pause demo
    demo.pause()
    time.sleep(0.1)
    st_paused = demo.state()
    assert st_paused["paused"]
    rem_before = st_paused["remaining_s"]

    # While paused, remaining time must not decrease
    time.sleep(0.15)
    rem_after = demo.state()["remaining_s"]
    assert rem_before == pytest.approx(rem_after, abs=0.03)

    # 3. Resume demo
    demo.resume()
    time.sleep(0.1)
    assert not demo.state()["paused"]
    assert demo.state()["remaining_s"] < rem_before

    # 4. Stop demo
    demo.stop()
    assert not demo.state()["running"]
    # Normal scenario must be restored
    assert "normal" in system.simulator.scenarios_applied
