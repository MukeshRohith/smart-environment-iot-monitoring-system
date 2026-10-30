"""Tests for Store state, configuration derived limits, and concurrency."""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import threading
import time
import pytest
import config
from core.models import (
    ActuationEvent,
    CleanedReading,
    Forecast,
    Level,
    Priority,
    Reading,
)
from core.store import Store


def test_limits_derivation_all_profiles():
    """Verify safe, hard, and slider limits calculation for all 3 profiles."""
    store = Store("data_center")

    # Data Center
    t_dc = store.limits("temperature")
    assert t_dc.safe_min == 18.0
    assert t_dc.safe_max == 27.0
    assert t_dc.hard_max == 35.0
    assert t_dc.hard_min == 10.0
    assert t_dc.slider_min == 3.75
    assert t_dc.slider_max == 41.25

    h_dc = store.limits("humidity")
    assert h_dc.hard_min == 20.0
    assert h_dc.slider_min == 5.0
    assert h_dc.slider_max == 95.0

    a_dc = store.limits("aqi")
    assert a_dc.hard_min == 0.0
    assert a_dc.slider_min == 0.0
    assert a_dc.slider_max == 250.0

    # Cold Storage
    store.set_profile("cold_storage")
    t_cs = store.limits("temperature")
    assert t_cs.hard_min == -2.0
    assert t_cs.slider_min == -5.5
    assert t_cs.slider_max == 15.5

    h_cs = store.limits("humidity")
    assert h_cs.hard_min == 10.0
    assert h_cs.slider_min == 0.0  # floored at 0
    assert h_cs.slider_max == 97.5

    # Greenhouse
    store.set_profile("greenhouse")
    t_gh = store.limits("temperature")
    assert t_gh.hard_min == 10.0
    assert t_gh.slider_min == 3.0
    assert t_gh.slider_max == 45.0

    h_gh = store.limits("humidity")
    assert h_gh.hard_min == 35.0
    assert h_gh.slider_min == 20.0
    assert h_gh.slider_max == 100.0  # capped at phys max 100.0


def test_bounded_deques():
    """Verify that Store collections do not grow unbounded."""
    store = Store()
    node = "n_test"
    store.register_node(node)

    # Hammer raw data beyond history length
    for i in range(config.HISTORY_LEN + 50):
        store.add_raw(Reading(node_id=node, ts=float(i), seq=i, temperature=20.0))

    hist = store.history(node, limit=config.HISTORY_LEN + 100)
    assert len(hist["ts"]) == config.HISTORY_LEN

    # Verify event and actuation log bounding
    for i in range(config.EVENT_LOG_LEN + 20):
        store.log_event("info", "test", f"msg {i}")
    assert len(store.events(config.EVENT_LOG_LEN + 50)) == config.EVENT_LOG_LEN

    for i in range(config.ACTUATION_LOG_LEN + 20):
        store.add_actuation(ActuationEvent(
            ts=float(i),
            node_id=node,
            device="fan",
            from_state="off",
            to_state="low",
            trigger="threshold",
            reason="test",
        ))
    assert len(store.actuations(config.ACTUATION_LOG_LEN + 50)) == config.ACTUATION_LOG_LEN


def test_exposure_integration():
    """Verify cumulative exposure integration over time delta."""
    store = Store("data_center")
    node = "node_exp"
    store.register_node(node)

    # safe_min=18, safe_max=27, hard_max=35 for data_center temperature
    # Reading 1 at t=10.0, temp=22 (normal)
    cr1 = CleanedReading(
        node_id=node, ts=10.0, seq=1, priority=Priority.NORMAL,
        raw={"temperature": 22.0, "humidity": 50.0, "aqi": 30.0},
        clean={"temperature": 22.0, "humidity": 50.0, "aqi": 30.0},
        smooth={"temperature": 22.0, "humidity": 50.0, "aqi": 30.0},
        flags={"temperature": []},
        rolling={"temperature": {"mean": 22.0, "std": 0.0, "min": 22.0, "max": 22.0, "slope_per_s": 0.0}},
        quality=1.0, status="ok", processed_at=10.01, received_at=10.0,
    )
    store.add_cleaned(cr1)
    exp = store.exposure(node)["temperature"]
    assert exp["peak"] == 22.0
    assert exp["seconds_outside_safe"] == 0.0
    assert exp["seconds_above_hard"] == 0.0

    # Reading 2 at t=15.0 (dt=5.0), temp=30 (outside safe [18..27], below hard 35)
    cr2 = CleanedReading(
        node_id=node, ts=15.0, seq=2, priority=Priority.NORMAL,
        raw={"temperature": 30.0, "humidity": 50.0, "aqi": 30.0},
        clean={"temperature": 30.0, "humidity": 50.0, "aqi": 30.0},
        smooth={"temperature": 30.0, "humidity": 50.0, "aqi": 30.0},
        flags={"temperature": []},
        rolling={"temperature": {"mean": 26.0, "std": 4.0, "min": 22.0, "max": 30.0, "slope_per_s": 1.6}},
        quality=1.0, status="ok", processed_at=15.01, received_at=15.0,
    )
    store.add_cleaned(cr2)
    exp = store.exposure(node)["temperature"]
    assert exp["peak"] == 30.0
    assert exp["seconds_outside_safe"] == 5.0
    assert exp["seconds_above_hard"] == 0.0

    # Reading 3 at t=25.0 (dt=10.0), temp=38 (above hard_max 35)
    cr3 = CleanedReading(
        node_id=node, ts=25.0, seq=3, priority=Priority.CRITICAL,
        raw={"temperature": 38.0, "humidity": 50.0, "aqi": 30.0},
        clean={"temperature": 38.0, "humidity": 50.0, "aqi": 30.0},
        smooth={"temperature": 38.0, "humidity": 50.0, "aqi": 30.0},
        flags={"temperature": []},
        rolling={"temperature": {"mean": 30.0, "std": 6.5, "min": 22.0, "max": 38.0, "slope_per_s": 0.8}},
        quality=1.0, status="ok", processed_at=25.01, received_at=25.0,
    )
    store.add_cleaned(cr3)
    exp = store.exposure(node)["temperature"]
    assert exp["peak"] == 38.0
    assert exp["seconds_outside_safe"] == 15.0
    assert exp["seconds_above_hard"] == 10.0


def test_level_transition_recording():
    """Verify recording and retrieval of alert level transitions."""
    store = Store()
    store.register_node("n1")
    store.record_level_change("n1", "temperature", Level.OK, Level.WATCH, 100.0)
    store.record_level_change("n1", "temperature", Level.WATCH, Level.CRITICAL, 110.0)

    transitions = store.level_transitions()
    assert len(transitions) == 2
    assert transitions[0]["old"] == "OK"
    assert transitions[0]["new"] == "WATCH"
    assert transitions[1]["old"] == "WATCH"
    assert transitions[1]["new"] == "CRITICAL"


def test_reset_runtime_keeps_profile_and_settings():
    """Verify reset_runtime clears transient buffers while retaining static config."""
    store = Store("data_center")
    store.set_profile("greenhouse")
    store.update_settings({"workers": 7, "pipeline_mode": "fifo"})
    store.register_node("node_persist")

    store.incr("received", 10, Priority.CRITICAL)
    store.log_event("warn", "sensor", "anomaly")
    store.add_raw(Reading(node_id="node_persist", ts=time.time(), seq=1, temperature=25.0))

    assert store.counters()["received"] == 10
    assert len(store.events()) == 1

    store.reset_runtime()

    # Profile & settings & node registrations must remain
    assert store.get_profile() == "greenhouse"
    assert store.get_settings()["workers"] == 7
    assert store.get_settings()["pipeline_mode"] == "fifo"
    assert "node_persist" in store.node_ids()

    # Transient runtime state must be cleared
    assert store.counters()["received"] == 0
    assert store.counters()["by_priority"]["0"] == 0
    assert len(store.events()) == 0
    assert len(store.actuations()) == 0
    assert store.latest_cleaned("node_persist") is None


def test_thread_safety_8_threads():
    """Verify Store thread safety with 8 threads concurrently incrementing and adding readings."""
    store = Store()
    node = "thread_test_node"
    store.register_node(node)

    iterations_per_thread = 200
    threads = []

    def worker(worker_id: int):
        for i in range(iterations_per_thread):
            store.incr("received", 1, priority=Priority.NORMAL)
            reading = Reading(
                node_id=node,
                ts=time.time(),
                seq=worker_id * 1000 + i,
                temperature=20.0 + (i % 10),
            )
            store.add_raw(reading)
            if i % 10 == 0:
                store.log_event("info", f"worker-{worker_id}", f"step {i}")

    for tid in range(8):
        t = threading.Thread(target=worker, args=(tid,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    cnts = store.counters()
    assert cnts["received"] == 8 * iterations_per_thread
    assert cnts["by_priority"]["2"] == 8 * iterations_per_thread
    assert len(store.history(node)["ts"]) > 0


def test_snapshot_schema_and_node_status():
    """Verify snapshot schema structure and node online/offline/shutdown status."""
    store = Store("data_center")
    node_on = "node_online"
    node_off = "node_offline"
    node_down = "node_shutdown"

    now = time.time()
    store.register_node(node_on)
    store.register_node(node_off)
    store.register_node(node_down)

    # node_on: reading 1 second ago -> online
    store.add_raw(Reading(node_id=node_on, ts=now - 1.0, seq=1, temperature=22.0, received_at=now - 1.0))
    # node_off: reading 10 seconds ago -> offline
    store.add_raw(Reading(node_id=node_off, ts=now - 10.0, seq=1, temperature=22.0, received_at=now - 10.0))
    # node_down: status shutdown -> shutdown
    store.add_raw(Reading(node_id=node_down, ts=now - 1.0, seq=1, temperature=22.0, status="shutdown", received_at=now - 1.0))

    store.set_levels(node_on, {"temperature": Level.WARNING, "humidity": Level.OK, "aqi": Level.OK})

    snap = store.snapshot()
    assert "ts" in snap
    assert snap["profile"] == "data_center"
    assert "limits" in snap
    assert "settings" in snap
    assert "counters" in snap
    assert "nodes" in snap
    assert "events" in snap
    assert "actuations" in snap

    assert snap["nodes"][node_on]["status"] == "online"
    assert snap["nodes"][node_on]["level"] == "WARNING"
    assert snap["nodes"][node_off]["status"] == "offline"
    assert snap["nodes"][node_down]["status"] == "shutdown"
