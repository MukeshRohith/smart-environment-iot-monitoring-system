"""Tests for data wrangling pipeline and statistical anomaly detection."""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import time
import pytest
import config
from core.models import Reading
from core.store import Store
from core.wrangler import Wrangler


def test_missing_fill_and_stale():
    """Verify forward-fill up to MAX_FFILL then fallback to rolling mean flagged stale."""
    wrangler = Wrangler()
    node = "node-missing"

    # Base initial reading
    r0 = Reading(node_id=node, ts=100.0, seq=0, temperature=24.0, humidity=50.0, aqi=30.0)
    c0 = wrangler.process(r0)
    assert c0.clean["temperature"] == 24.0
    assert c0.flags["temperature"] == []

    # Send MAX_FFILL (5) consecutive nulls
    for i in range(1, config.MAX_FFILL + 1):
        r_null = Reading(node_id=node, ts=100.0 + i, seq=i, temperature=None, humidity=50.0, aqi=30.0)
        c_null = wrangler.process(r_null)
        assert c_null.clean["temperature"] == 24.0
        assert "missing_filled" in c_null.flags["temperature"]
        assert "stale" not in c_null.flags["temperature"]

    # 6th consecutive null (beyond MAX_FFILL) must be flagged "stale"
    r_stale = Reading(node_id=node, ts=100.0 + config.MAX_FFILL + 1, seq=config.MAX_FFILL + 1, temperature=None, humidity=50.0, aqi=30.0)
    c_stale = wrangler.process(r_stale)
    assert "stale" in c_stale.flags["temperature"]
    assert c_stale.clean["temperature"] == pytest.approx(24.0, abs=0.1)


def test_implausible_values():
    """Verify values beyond PHYSICAL bounds become missing and flagged implausible."""
    wrangler = Wrangler()
    node = "node-bounds"

    # Seed with plausible reading
    wrangler.process(Reading(node_id=node, ts=10.0, seq=0, temperature=22.0, humidity=45.0, aqi=50.0))

    # Send implausible values: temp=200 (>125), humidity=-5 (<0), aqi=600 (>500)
    r_bad = Reading(node_id=node, ts=11.0, seq=1, temperature=200.0, humidity=-5.0, aqi=600.0)
    c_bad = wrangler.process(r_bad)

    assert "implausible" in c_bad.flags["temperature"]
    assert "implausible" in c_bad.flags["humidity"]
    assert "implausible" in c_bad.flags["aqi"]

    # Implausible values become missing, so they get forward-filled
    assert c_bad.clean["temperature"] == 22.0
    assert c_bad.clean["humidity"] == 45.0
    assert c_bad.clean["aqi"] == 50.0


def test_outlier_rejection_and_persistence_shift():
    """Verify single outliers are replaced by median, but sustained shifts of PERSIST_N are accepted."""
    wrangler = Wrangler()
    node = "node-outlier"

    # Seed baseline with 10 stable readings around 20.0 °C
    for i in range(10):
        wrangler.process(Reading(node_id=node, ts=float(i), seq=i, temperature=20.0, humidity=50.0, aqi=20.0))

    # Isolated outlier spike (temp = 45.0 °C, far above MAD threshold)
    spike = Reading(node_id=node, ts=10.0, seq=10, temperature=45.0, humidity=50.0, aqi=20.0)
    c_spike = wrangler.process(spike)
    assert "outlier_replaced" in c_spike.flags["temperature"]
    assert c_spike.clean["temperature"] == pytest.approx(20.0, abs=0.5)

    # Recovery back to baseline
    wrangler.process(Reading(node_id=node, ts=11.0, seq=11, temperature=20.0, humidity=50.0, aqi=20.0))

    # Now test PERSISTENCE RULE: PERSIST_N (3) consecutive shifted readings at 35.0 °C
    shift_val = 35.0
    c_shift_1 = wrangler.process(Reading(node_id=node, ts=12.0, seq=12, temperature=shift_val, humidity=50.0, aqi=20.0))
    assert "outlier_replaced" in c_shift_1.flags["temperature"]

    c_shift_2 = wrangler.process(Reading(node_id=node, ts=13.0, seq=13, temperature=shift_val, humidity=50.0, aqi=20.0))
    assert "outlier_replaced" in c_shift_2.flags["temperature"]

    # 3rd consecutive reading in same direction reaches PERSIST_N=3 -> accepted!
    c_shift_3 = wrangler.process(Reading(node_id=node, ts=14.0, seq=14, temperature=shift_val, humidity=50.0, aqi=20.0))
    assert "outlier_replaced" not in c_shift_3.flags["temperature"]
    assert c_shift_3.clean["temperature"] == shift_val


def test_smoothing_and_slope_on_clean_ramp():
    """Verify EMA smoothing lag and linear slope calculation on a clean ramp."""
    wrangler = Wrangler()
    node = "node-ramp"

    # Ramp temperature up at +1.5 °C/s for 15 seconds
    slope_true = 1.5
    for i in range(15):
        t = float(i)
        temp = 20.0 + slope_true * t
        r = Reading(node_id=node, ts=t, seq=i, temperature=temp, humidity=50.0, aqi=20.0)
        c = wrangler.process(r)

    # In a rising ramp, EMA smoothed value should lag behind clean value
    assert c.smooth["temperature"] < c.clean["temperature"]
    # Slope calculation over the window should be very close to 1.5 °C/s
    slope_est = c.rolling["temperature"]["slope_per_s"]
    assert slope_est == pytest.approx(slope_true, abs=0.1)


def test_quality_score_drop():
    """Verify quality score starts at 1.0 and drops with imputed/replaced readings."""
    wrangler = Wrangler()
    node = "node-quality"

    # Completely clean readings -> Quality 1.0
    for i in range(5):
        c = wrangler.process(Reading(node_id=node, ts=float(i), seq=i, temperature=21.0, humidity=50.0, aqi=20.0))
    assert c.quality == 1.0

    # Introduce series of missing / implausible values
    for i in range(5, 10):
        c_bad = wrangler.process(Reading(node_id=node, ts=float(i), seq=i, temperature=None, humidity=200.0, aqi=None))
    assert c_bad.quality < 1.0
    assert c_bad.quality >= 0.0


def test_timing_under_3ms_average():
    """Verify that average processing time per reading is under 3 ms."""
    wrangler = Wrangler()
    node = "node-perf"

    # Warmup and process 150 readings with varied data
    for i in range(150):
        t = float(i)
        temp = 20.0 + (i % 5) * 0.5
        hum = 50.0 + (i % 3) * 1.0
        aqi = 25.0 + (i % 7) * 2.0
        r = Reading(node_id=node, ts=t, seq=i, temperature=temp, humidity=hum, aqi=aqi)
        wrangler.process(r)

    assert wrangler.avg_ms > 0.0
    assert wrangler.avg_ms < 3.0, f"Average processing time {wrangler.avg_ms:.2f} ms exceeds 3 ms limit"


def test_reset_clears_state():
    """Verify reset() empties cached histories and timing statistics."""
    wrangler = Wrangler()
    node = "node-reset"

    wrangler.process(Reading(node_id=node, ts=1.0, seq=1, temperature=22.0, humidity=50.0, aqi=20.0))
    assert wrangler.avg_ms > 0.0

    wrangler.reset()
    assert wrangler.avg_ms == 0.0
    assert len(wrangler._node_dfs) == 0
