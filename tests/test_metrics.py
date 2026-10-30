"""Tests for MetricsEngine calculation of lead times, breach classifications, and loss rates."""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from core.metrics import MetricsEngine
from core.models import Level, Priority
from core.store import Store


def test_breach_classification_anticipated_missed_prevented():
    """Verify state transition processing for anticipated, missed, and prevented breach events."""
    store = Store("data_center")
    engine = MetricsEngine(store)
    node = "node-1"
    store.register_node(node)

    # 1. Anticipated Breach: OK -> WATCH (t=10.0) -> WARNING (t=16.5) -> return to OK (t=25.0)
    store.record_level_change(node, "temperature", Level.OK, Level.WATCH, 10.0)
    store.record_level_change(node, "temperature", Level.WATCH, Level.WARNING, 16.5)
    store.record_level_change(node, "temperature", Level.WARNING, Level.OK, 25.0)

    # 2. Missed Breach: OK -> CRITICAL (t=30.0) directly without WATCH -> return to OK (t=40.0)
    store.record_level_change(node, "temperature", Level.OK, Level.CRITICAL, 30.0)
    store.record_level_change(node, "temperature", Level.CRITICAL, Level.OK, 40.0)

    # 3. Prevented Breach: OK -> WATCH (t=50.0) -> return to OK (t=62.0) without breaching
    store.record_level_change(node, "temperature", Level.OK, Level.WATCH, 50.0)
    store.record_level_change(node, "temperature", Level.WATCH, Level.OK, 62.0)

    res = engine.compute()
    breaches = res["breaches"]
    lead = res["lead_time"]

    assert breaches["anticipated"] == 1
    assert breaches["missed"] == 1
    assert breaches["prevented"] == 1
    assert breaches["total"] == 2

    assert lead["count"] == 1
    assert lead["avg_s"] == pytest.approx(6.5, abs=0.01)


def test_packet_loss_calculation():
    """Verify packet loss percentage calculation with ingress and zero-divide guard."""
    store = Store("data_center")
    engine = MetricsEngine(store)

    # Initial zero case
    assert engine.compute()["packet_loss_pct"] == 0.0

    # Ingress: received 80, rejected 20 (total ingress = 100), dropped 10
    store.incr("received", 80, Priority.NORMAL)
    store.incr("rejected", 20, Priority.NORMAL)
    store.incr("dropped", 10, Priority.NORMAL)

    # Loss pct = (dropped 10 + rejected 20) / (received 80 + rejected 20) * 100 = 30%
    res = engine.compute()
    assert res["packet_loss_pct"] == pytest.approx(30.0, abs=0.01)
    assert res["counts"]["received"] == 80
    assert res["counts"]["dropped"] == 10


def test_exposure_and_device_metrics_present():
    """Verify that exposure and device operating time structures are correctly included."""
    store = Store("data_center")
    engine = MetricsEngine(store)
    node = "node-summary"
    store.register_node(node)

    res = engine.compute()
    assert node in res["exposure"]
    assert "temperature" in res["exposure"][node]
    assert "peak" in res["exposure"][node]["temperature"]
    assert "seconds_outside" in res["exposure"][node]["temperature"]
    assert node in res["device_on_seconds"]
    assert isinstance(res["device_on_seconds"][node], float)
