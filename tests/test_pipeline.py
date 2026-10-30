"""Tests for priority queuing, classification, fault tolerance, and worker scaling."""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import time
import pytest
import config
from core.models import CleanedReading, Priority, Reading
from core.pipeline import Pipeline, classify
from core.store import Store


class FakeWrangler:
    """Mock wrangler stage for pipeline testing."""

    def __init__(self, fail_first: bool = False) -> None:
        self.fail_first = fail_first
        self.calls: list[Reading] = []
        self.avg_ms = 0.5

    def process(self, reading: Reading) -> CleanedReading:
        self.calls.append(reading)
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError("Wrangler synthetic failure")
        return CleanedReading(
            node_id=reading.node_id,
            ts=reading.ts,
            seq=reading.seq,
            priority=reading.priority,
            raw={"temperature": reading.temperature, "humidity": reading.humidity, "aqi": reading.aqi},
            clean={"temperature": reading.temperature, "humidity": reading.humidity, "aqi": reading.aqi},
            smooth={"temperature": reading.temperature, "humidity": reading.humidity, "aqi": reading.aqi},
            flags={"temperature": [], "humidity": [], "aqi": []},
            rolling={"temperature": {"mean": 20.0, "std": 0.0, "min": 20.0, "max": 20.0, "slope_per_s": 0.0}},
            quality=1.0,
            status=reading.status,
            processed_at=reading.ts + 0.01,
            received_at=reading.received_at,
        )


class FakeForecaster:
    """Mock forecaster stage for pipeline testing."""

    def __init__(self) -> None:
        self.calls: list[CleanedReading] = []

    def update(self, cleaned: CleanedReading) -> dict:
        self.calls.append(cleaned)
        return {}


class FakeActuator:
    """Mock actuator stage for pipeline testing."""

    def __init__(self) -> None:
        self.calls: list[CleanedReading] = []

    def evaluate(self, cleaned: CleanedReading, forecasts: dict) -> list:
        self.calls.append(cleaned)
        return []


def test_classify_all_cases():
    """Verify pure classification function across critical, anomaly, and normal conditions."""
    store = Store("data_center")
    lims = store.all_limits()

    # 1. Normal
    r_norm = Reading(node_id="n1", ts=1.0, seq=1, temperature=22.0, humidity=50.0, aqi=30.0)
    assert classify(r_norm, None, 1, lims) == Priority.NORMAL

    # 2. Critical (temp 36.0 >= hard_max 35.0)
    r_crit = Reading(node_id="n1", ts=2.0, seq=2, temperature=36.0, humidity=50.0, aqi=30.0)
    assert classify(r_crit, r_norm, 1, lims) == Priority.CRITICAL

    # 3. Anomaly: null metric
    r_null = Reading(node_id="n1", ts=3.0, seq=3, temperature=None, humidity=50.0, aqi=30.0)
    assert classify(r_null, r_norm, 1, lims) == Priority.ANOMALY

    # 4. Anomaly: status shutdown
    r_shut = Reading(node_id="n1", ts=4.0, seq=4, temperature=22.0, humidity=50.0, aqi=30.0, status="shutdown")
    assert classify(r_shut, r_norm, 1, lims) == Priority.ANOMALY

    # 5. Anomaly: jump limit exceeded (jump 6.0 > config.JUMP_LIMITS['temperature'] 5.0)
    r_jump = Reading(node_id="n1", ts=5.0, seq=5, temperature=28.0, humidity=50.0, aqi=30.0)
    assert classify(r_jump, r_norm, 1, lims) == Priority.ANOMALY

    # 6. Anomaly: stuck repeat readings (repeat_count >= STUCK_REPEATS)
    assert classify(r_norm, r_norm, config.STUCK_REPEATS, lims) == Priority.ANOMALY


def test_priority_vs_fifo_ordering():
    """Verify PriorityQueue pops CRITICAL before NORMAL in priority mode, but respects FIFO in fifo mode."""
    # 1. Priority Mode: submit NORMAL then CRITICAL -> pop CRITICAL first
    store_prio = Store("data_center")
    store_prio.update_settings({"pipeline_mode": "priority"})
    pipe_prio = Pipeline(store_prio, FakeWrangler(), FakeForecaster(), FakeActuator())

    r_norm = Reading(node_id="n1", ts=10.0, seq=1, temperature=22.0, humidity=50.0, aqi=30.0)
    r_crit = Reading(node_id="n1", ts=11.0, seq=2, temperature=38.0, humidity=50.0, aqi=30.0)

    pipe_prio.submit(r_norm)
    pipe_prio.submit(r_crit)

    item1 = pipe_prio._queue.get_nowait()
    item2 = pipe_prio._queue.get_nowait()

    assert item1[2].priority == Priority.CRITICAL
    assert item2[2].priority == Priority.NORMAL

    # 2. FIFO Mode: submit NORMAL then CRITICAL -> pop NORMAL first
    store_fifo = Store("data_center")
    store_fifo.update_settings({"pipeline_mode": "fifo"})
    pipe_fifo = Pipeline(store_fifo, FakeWrangler(), FakeForecaster(), FakeActuator())

    pipe_fifo.submit(r_norm)
    pipe_fifo.submit(r_crit)

    f_item1 = pipe_fifo._queue.get_nowait()
    f_item2 = pipe_fifo._queue.get_nowait()

    assert f_item1[2].priority == Priority.NORMAL
    assert f_item2[2].priority == Priority.CRITICAL


def test_overflow_drops_normal_first():
    """Verify full queue rejects NORMAL immediately while allowing critical priority attempt."""
    store = Store("data_center")
    pipe = Pipeline(store, FakeWrangler(), FakeForecaster(), FakeActuator())
    # Artificially cap queue maxsize to 2
    pipe._queue.maxsize = 2

    # Fill queue to capacity
    r1 = Reading(node_id="n1", ts=1.0, seq=1, temperature=22.0, humidity=50.0, aqi=30.0)
    r2 = Reading(node_id="n1", ts=2.0, seq=2, temperature=22.1, humidity=50.0, aqi=30.0)
    assert pipe.submit(r1) == "accepted"
    assert pipe.submit(r2) == "accepted"
    assert pipe._queue.full()

    # Normal reading must be dropped immediately
    r_normal = Reading(node_id="n1", ts=3.0, seq=3, temperature=22.2, humidity=50.0, aqi=30.0)
    res = pipe.submit(r_normal)
    assert res == "dropped"
    assert store.counters()["dropped"] == 1


def test_stage_exception_does_not_kill_worker():
    """Verify that an unhandled stage exception logs an error and keeps the worker thread alive."""
    store = Store("data_center")
    store.update_settings({"processing_delay_ms": 0.0, "workers": 1})
    wrangler = FakeWrangler(fail_first=True)
    pipe = Pipeline(store, wrangler, FakeForecaster(), FakeActuator())

    pipe.start()
    assert pipe.is_alive()

    # Reading 1 will trigger RuntimeError in FakeWrangler
    r1 = Reading(node_id="n1", ts=10.0, seq=1, temperature=22.0, humidity=50.0, aqi=30.0)
    pipe.submit(r1)

    # Wait for reading 1 to fail
    time.sleep(0.1)
    assert store.counters()["errors"] == 1
    assert pipe.is_alive()  # Worker thread MUST remain running!

    # Reading 2 will succeed
    r2 = Reading(node_id="n1", ts=11.0, seq=2, temperature=23.0, humidity=50.0, aqi=30.0)
    pipe.submit(r2)
    time.sleep(0.1)

    assert store.counters()["processed"] == 1
    pipe.stop()


def test_live_resize():
    """Verify that apply_settings() dynamically scales worker pool count up and down."""
    store = Store("data_center")
    store.update_settings({"workers": 2, "processing_delay_ms": 0.0})
    pipe = Pipeline(store, FakeWrangler(), FakeForecaster(), FakeActuator())

    pipe.start()
    assert len(pipe._workers) == 2

    # Scale up to 4 workers
    store.update_settings({"workers": 4})
    pipe.apply_settings()
    assert len(pipe._workers) == 4

    # Scale down to 1 worker
    store.update_settings({"workers": 1})
    pipe.apply_settings()
    time.sleep(0.1)
    pipe._cleanup_dead_workers()
    assert len(pipe._workers) == 1

    snap = pipe.snapshot()
    assert snap["workers"] == 1
    assert snap["queue_depth"] == 0

    pipe.stop()
    assert len(pipe._workers) == 0
