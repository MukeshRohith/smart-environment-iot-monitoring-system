"""Tests for predictive forecasting and error tracking."""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
import config
from core.forecaster import Forecaster
from core.models import CleanedReading, Priority
from core.store import Store


def _make_cleaned(node_id: str, ts: float, seq: int, temp: float, hum: float = 50.0, aqi: float = 20.0) -> CleanedReading:
    """Helper to construct CleanedReading instances for tests."""
    return CleanedReading(
        node_id=node_id,
        ts=ts,
        seq=seq,
        priority=Priority.NORMAL,
        raw={"temperature": temp, "humidity": hum, "aqi": aqi},
        clean={"temperature": temp, "humidity": hum, "aqi": aqi},
        smooth={"temperature": temp, "humidity": hum, "aqi": aqi},
        flags={"temperature": [], "humidity": [], "aqi": []},
        rolling={"temperature": {"mean": temp, "std": 0.0, "min": temp, "max": temp, "slope_per_s": 0.0}},
        quality=1.0,
        status="ok",
        processed_at=ts + 0.01,
        received_at=ts,
    )


def test_linear_ramp_eta():
    """Verify that a rising temperature ramp predicts the correct future ETA."""
    store = Store("data_center")
    # safe_max is 27.0 °C
    forecaster = Forecaster(store)
    node = "node-ramp"

    # Scale 60: 1 real second = 1 sim-minute
    store.update_settings({"time_scale": 60.0, "horizon_min": 15.0})

    # Linear ramp rising by 0.5 °C per second (sim-minute).
    # Feed 20 readings from 12.5 °C up to 22.0 °C at ts=0..19.
    # At ts=19, current temp is 22.0 °C.
    # To reach safe_max (27.0 °C), it needs +5.0 °C -> exactly 10 sim-minutes!
    for i in range(20):
        t = float(i)
        temp = 12.5 + 0.5 * t
        r = _make_cleaned(node, t, i, temp)
        fcasts = forecaster.update(r)

    assert "temperature" in fcasts
    fc = fcasts["temperature"]
    assert fc.eta_safe_min is not None
    assert fc.eta_safe_min == pytest.approx(10.0, abs=1.0)


def test_flat_series_no_eta():
    """Verify that a steady temperature series inside safe bounds yields no ETA."""
    store = Store("data_center")
    forecaster = Forecaster(store)
    node = "node-flat"

    for i in range(25):
        r = _make_cleaned(node, float(i), i, 22.0)
        fcasts = forecaster.update(r)

    assert "temperature" in fcasts
    fc = fcasts["temperature"]
    assert fc.eta_safe_min is None
    assert fc.eta_hard_min is None


def test_horizon_change_extends_points():
    """Verify that updating horizon_min in store dynamically alters forecast points."""
    store = Store("data_center")
    forecaster = Forecaster(store)
    node = "node-horizon"

    store.update_settings({"horizon_min": 15.0})

    # Feed 20 readings to get initial forecast
    for i in range(20):
        r = _make_cleaned(node, float(i), i, 21.0)
        fc_15 = forecaster.update(r)

    # At horizon 15 and STEP_MIN=5: points at 5, 10, 15 (3 points)
    assert len(fc_15["temperature"].points) == 3
    assert fc_15["temperature"].horizon_min == 15.0

    # Increase horizon to 30.0 sim-minutes
    store.update_settings({"horizon_min": 30.0})

    # Next reading should detect horizon change and produce 6 points (5, 10, 15, 20, 25, 30)
    r_next = _make_cleaned(node, 20.0, 20, 21.0)
    fc_30 = forecaster.update(r_next)

    assert len(fc_30["temperature"].points) == 6
    assert fc_30["temperature"].horizon_min == 30.0


def test_throttling_returns_cached_object():
    """Verify that readings between RETRAIN_EVERY intervals return the cached Forecast instance."""
    store = Store("data_center")
    forecaster = Forecaster(store)
    node = "node-throttle"

    # Fill up to MIN_POINTS (20)
    for i in range(20):
        r = _make_cleaned(node, float(i), i, 21.0)
        fcasts = forecaster.update(r)

    fc_initial = fcasts["temperature"]

    # Next reading (reading 21, not divisible by RETRAIN_EVERY=5) must return cached object
    r_subsequent = _make_cleaned(node, 20.0, 21, 21.1)
    fc_cached = forecaster.update(r_subsequent)

    assert fc_cached["temperature"] is fc_initial


def test_error_recording_populates_mae_synthetic():
    """Verify that when synthetic timestamp passes forecast horizon target, error is logged."""
    store = Store("data_center")
    forecaster = Forecaster(store)
    node = "node-error"

    # time_scale=60 -> 1 real second = 1 sim minute
    # horizon_min=10 -> real seconds = 10.0 s
    store.update_settings({"time_scale": 60.0, "horizon_min": 10.0})

    for i in range(20):
        t = float(i)
        r = _make_cleaned(node, t, i, 20.0)
        fcasts = forecaster.update(r)

    # Initial error count is 0
    stats_before = store.forecast_error_stats()["temperature"]
    assert stats_before["n"] == 0

    # The last forecast was made at ts=19.0 with horizon 10 real seconds -> target_ts = 29.0
    # Send reading at synthetic timestamp 29.5 (target passed) with actual temperature 22.0
    r_future = _make_cleaned(node, 29.5, 21, 22.0)
    forecaster.update(r_future)

    stats_after = store.forecast_error_stats()["temperature"]
    assert stats_after["n"] >= 1
    assert stats_after["mae"] > 0.0
