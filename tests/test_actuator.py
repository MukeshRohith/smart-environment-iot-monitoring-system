"""Tests for automated closed-loop actuator engine, hysteresis, and overrides."""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
import config
from core.actuator import ActuatorEngine
from core.models import CleanedReading, Forecast, Level, Priority
from core.store import Store


def _make_cleaned(node_id: str, ts: float, temp: float, hum: float = 50.0, aqi: float = 30.0) -> CleanedReading:
    """Helper to construct CleanedReading for actuator evaluations."""
    return CleanedReading(
        node_id=node_id,
        ts=ts,
        seq=1,
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


def test_proactive_vs_reactive_watch():
    """Verify proactive strategy responds to predictive WATCH, while reactive ignores it."""
    store = Store("data_center")
    engine = ActuatorEngine(store)
    node = "node-strat"

    # Temp is safe at 22.0 °C. Forecast predicts breach in 10 sim-minutes with 0.85 confidence
    fc = Forecast(
        node_id=node, metric="temperature", made_at=100.0, horizon_min=15.0,
        points=[], lower=[], upper=[], predicted_end=28.0,
        eta_safe_min=10.0, eta_hard_min=None, confidence=0.85, model="linear",
    )
    cleaned = _make_cleaned(node, 100.0, 22.0)

    # 1. Proactive mode
    store.update_settings({"strategy": "proactive"})
    engine.evaluate(cleaned, {"temperature": fc})
    assert store.levels(node)["temperature"] == Level.WATCH
    assert store.devices(node)["fan"]["state"] == "low"

    # 2. Reactive mode
    engine.reset()
    store.update_settings({"strategy": "reactive"})
    engine.evaluate(cleaned, {"temperature": fc})
    assert store.levels(node)["temperature"] == Level.OK
    assert store.devices(node)["fan"]["state"] == "off"


def test_escalation_levels():
    """Verify device command progression through WATCH, WARNING, and CRITICAL levels."""
    store = Store("data_center")
    engine = ActuatorEngine(store)
    node = "node-levels"
    store.update_settings({"strategy": "proactive", "horizon_min": 15.0})

    # WATCH with imminent breach (eta 5.0 <= horizon/2) -> fan high + hvac low
    fc_imminent = Forecast(
        node_id=node, metric="temperature", made_at=10.0, horizon_min=15.0,
        points=[], lower=[], upper=[], predicted_end=28.0,
        eta_safe_min=5.0, eta_hard_min=None, confidence=0.85, model="linear",
    )
    engine.evaluate(_make_cleaned(node, 10.0, 22.0), {"temperature": fc_imminent})
    devs = store.devices(node)
    assert devs["fan"]["state"] == "high"
    assert devs["hvac"]["state"] == "low"
    assert not engine.is_shutdown(node)

    # WARNING (temp=28.0 > safe_max 27.0) -> fan high + hvac high
    engine.evaluate(_make_cleaned(node, 15.0, 28.0), {})
    devs = store.devices(node)
    assert devs["fan"]["state"] == "high"
    assert devs["hvac"]["state"] == "high"
    assert not engine.is_shutdown(node)

    # CRITICAL (temp=36.0 >= hard_max 35.0) -> fan high + hvac high + shutdown
    engine.evaluate(_make_cleaned(node, 20.0, 36.0), {})
    devs = store.devices(node)
    assert devs["fan"]["state"] == "high"
    assert devs["hvac"]["state"] == "high"
    assert engine.is_shutdown(node)


def test_hysteresis_no_flapping_and_step_down():
    """Verify hysteresis avoids boundary flapping and steps down only one notch at a time."""
    store = Store("data_center")
    engine = ActuatorEngine(store)
    node = "node-hyst"

    # Step up to WARNING at t=10.0 -> fan high
    engine.evaluate(_make_cleaned(node, 10.0, 28.0), {})
    assert store.devices(node)["fan"]["state"] == "high"

    # Safe range for DC temp is [18, 27]. Shrunk safe range is [18.9, 26.1].
    # Readings at 26.5 °C are inside safe [18, 27] but outside shrunk safe [18.9, 26.1].
    # Fan MUST NOT step down here even after 15 readings.
    for i in range(15):
        t = 20.0 + i
        engine.evaluate(_make_cleaned(node, t, 26.5), {})
        assert store.devices(node)["fan"]["state"] == "high"

    # Now drop to 22.0 °C (inside shrunk safe range [18.9, 26.1]).
    # First 9 readings: counter incrementing, but under HYST_READINGS (10) -> stays high
    for i in range(config.HYST_READINGS - 1):
        t = 40.0 + i
        engine.evaluate(_make_cleaned(node, t, 22.0), {})
        assert store.devices(node)["fan"]["state"] == "high"

    # 10th reading (at t=49.0 >= turn_on_ts + MIN_ON_SECONDS): steps down ONE notch to 'low'!
    engine.evaluate(_make_cleaned(node, 40.0 + config.HYST_READINGS, 22.0), {})
    assert store.devices(node)["fan"]["state"] == "low"

    # Another HYST_READINGS at 22.0 °C with MIN_ON_SECONDS: steps down from 'low' to 'off'
    for i in range(config.HYST_READINGS):
        t = 60.0 + i
        engine.evaluate(_make_cleaned(node, t, 22.0), {})
    assert store.devices(node)["fan"]["state"] == "off"


def test_manual_override_respected():
    """Verify manual override locks device state and ignores automated control."""
    store = Store("data_center")
    engine = ActuatorEngine(store)
    node = "node-override"

    # Manually turn fan off
    engine.override(node, "fan", "off")
    assert store.devices(node)["fan"]["state"] == "off"
    assert store.devices(node)["fan"]["mode"] == "manual"

    # Under CRITICAL conditions, fan must remain off due to manual lock
    engine.evaluate(_make_cleaned(node, 10.0, 36.0), {})
    assert store.devices(node)["fan"]["state"] == "off"

    # Revert back to auto mode
    engine.override(node, "fan", "auto")
    assert store.devices(node)["fan"]["mode"] == "auto"

    # Next evaluate responds to conditions
    engine.evaluate(_make_cleaned(node, 15.0, 36.0), {})
    assert store.devices(node)["fan"]["state"] == "high"


def test_shutdown_and_restart():
    """Verify emergency shutdown latch and restart/auto-recovery."""
    store = Store("data_center")
    engine = ActuatorEngine(store)
    node = "node-shut"

    # Critical reading triggers shutdown
    engine.evaluate(_make_cleaned(node, 10.0, 36.0), {})
    assert engine.is_shutdown(node)

    # Manual restart clears shutdown
    engine.restart(node)
    assert not engine.is_shutdown(node)

    # Re-trigger shutdown
    engine.evaluate(_make_cleaned(node, 20.0, 36.0), {})
    assert engine.is_shutdown(node)

    # 3 * HYST_READINGS (30) consecutive readings in safe range clears shutdown automatically
    for i in range(3 * config.HYST_READINGS):
        engine.evaluate(_make_cleaned(node, 30.0 + i, 22.0), {})
    assert not engine.is_shutdown(node)


def test_direction_aware_effects_and_sum():
    """Verify sign flip for 'low' direction and cumulative multi-device effects."""
    store = Store("data_center")
    engine = ActuatorEngine(store)
    node = "node-effects"

    # 1. High direction: temp=25.0 (> 22.5), aqi=70.0 (> 50.0) -> fan high cooling (-4.0, -20.0)
    engine.override(node, "fan", "on")  # fan high -> temp -4.0, aqi -20.0
    engine.evaluate(_make_cleaned(node, 10.0, 25.0, hum=50.0, aqi=70.0), {})
    eff_high = engine.effects_for(node)
    assert eff_high["temperature"] == -4.0
    assert eff_high["aqi"] == -20.0

    # 2. Low direction: temp=19.0 (< 22.5) -> sign of temp flips (+4.0), aqi remains high direction (70.0)
    engine.evaluate(_make_cleaned(node, 11.0, 19.0, hum=50.0, aqi=70.0), {})
    eff_low = engine.effects_for(node)
    assert eff_low["temperature"] == +4.0

    # 3. Purifier and shutdown never flip sign
    engine.override(node, "purifier", "on")  # purifier high = aqi -70.0
    eff_pur = engine.effects_for(node)
    assert eff_pur["aqi"] == -70.0 - 20.0  # fan -20 + purifier -70
