"""Tests for data models and validation."""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import math
import pytest
from core.models import (
    ActuationEvent,
    CleanedReading,
    Forecast,
    Level,
    Limits,
    Priority,
    Reading,
)


def test_priority_and_level_enums():
    """Verify priority and level integer mapping and name serialization."""
    assert Priority.CRITICAL == 0
    assert Priority.ANOMALY == 1
    assert Priority.NORMAL == 2

    assert Level.OK == 0
    assert Level.WATCH == 1
    assert Level.WARNING == 2
    assert Level.CRITICAL == 3
    assert Level.OK.name == "OK"
    assert Level.CRITICAL.name == "CRITICAL"


def test_reading_from_dict_valid():
    """Verify Reading.from_dict with valid payloads."""
    payload = {
        "node_id": "node-1",
        "ts": 1700000000.0,
        "seq": 42,
        "temperature": 23.5,
        "humidity": 55.0,
        "aqi": 32.0,
        "status": "ok",
    }
    r = Reading.from_dict(payload)
    assert r.node_id == "node-1"
    assert r.ts == 1700000000.0
    assert r.seq == 42
    assert r.temperature == 23.5
    assert r.humidity == 55.0
    assert r.aqi == 32.0
    assert r.status == "ok"
    assert r.priority == Priority.NORMAL

    d = r.to_dict()
    assert d["node_id"] == "node-1"
    assert d["seq"] == 42
    assert d["priority"] == 2


def test_reading_from_dict_rejects_missing_keys():
    """Verify missing required keys raise ValueError."""
    with pytest.raises(ValueError, match="Missing required key"):
        Reading.from_dict({"ts": 100, "seq": 1})

    with pytest.raises(ValueError, match="Missing required key"):
        Reading.from_dict({"node_id": "n1", "seq": 1})

    with pytest.raises(ValueError, match="Missing required key"):
        Reading.from_dict({"node_id": "n1", "ts": 100})


def test_reading_from_dict_rejects_empty_node_id():
    """Verify blank node_id is rejected."""
    with pytest.raises(ValueError, match="node_id must be non-empty"):
        Reading.from_dict({"node_id": "   ", "ts": 100, "seq": 1})


def test_reading_from_dict_rejects_non_numeric():
    """Verify strings, booleans, and non-numerics raise ValueError."""
    # bool is instance of int in Python, must be explicitly rejected
    with pytest.raises(ValueError, match="ts must be a numeric"):
        Reading.from_dict({"node_id": "n1", "ts": True, "seq": 1})

    with pytest.raises(ValueError, match="seq must be an integer"):
        Reading.from_dict({"node_id": "n1", "ts": 100.0, "seq": False})

    with pytest.raises(ValueError, match="temperature must be numeric"):
        Reading.from_dict({"node_id": "n1", "ts": 100.0, "seq": 1, "temperature": "hot"})

    with pytest.raises(ValueError, match="humidity must be numeric"):
        Reading.from_dict({"node_id": "n1", "ts": 100.0, "seq": 1, "humidity": [50]})


def test_reading_from_dict_rejects_nan_and_inf():
    """Verify NaN and inf values in timestamp, sequence, and metrics raise ValueError."""
    with pytest.raises(ValueError, match="ts cannot be NaN or inf"):
        Reading.from_dict({"node_id": "n1", "ts": float("nan"), "seq": 1})

    with pytest.raises(ValueError, match="ts cannot be NaN or inf"):
        Reading.from_dict({"node_id": "n1", "ts": float("inf"), "seq": 1})

    with pytest.raises(ValueError, match="seq cannot be NaN or inf"):
        Reading.from_dict({"node_id": "n1", "ts": 100.0, "seq": float("nan")})

    with pytest.raises(ValueError, match="temperature cannot be NaN or inf"):
        Reading.from_dict({"node_id": "n1", "ts": 100.0, "seq": 1, "temperature": float("nan")})

    with pytest.raises(ValueError, match="aqi cannot be NaN or inf"):
        Reading.from_dict({"node_id": "n1", "ts": 100.0, "seq": 1, "aqi": float("-inf")})


def test_reading_from_dict_rejects_non_dict():
    """Verify non-dict payload raises ValueError."""
    with pytest.raises(ValueError, match="Reading payload must be a JSON object"):
        Reading.from_dict("invalid")


def test_limits_to_dict():
    """Verify Limits serialization."""
    limits = Limits(18.0, 27.0, 10.0, 35.0, 3.75, 41.25, "°C")
    d = limits.to_dict()
    assert d["safe_min"] == 18.0
    assert d["hard_max"] == 35.0
    assert d["unit"] == "°C"


def test_forecast_and_cleaned_reading_to_dict():
    """Verify Forecast and CleanedReading serialization."""
    cr = CleanedReading(
        node_id="n1",
        ts=100.0,
        seq=1,
        priority=Priority.NORMAL,
        raw={"temperature": 20.0},
        clean={"temperature": 20.0},
        smooth={"temperature": 20.0},
        flags={"temperature": []},
        rolling={"temperature": {"mean": 20.0, "std": 0.0, "min": 20.0, "max": 20.0, "slope_per_s": 0.0}},
        quality=1.0,
        status="ok",
        processed_at=100.05,
        received_at=100.01,
    )
    d = cr.to_dict()
    assert d["node_id"] == "n1"
    assert d["quality"] == 1.0

    fc = Forecast(
        node_id="n1",
        metric="temperature",
        made_at=100.0,
        horizon_min=15.0,
        points=[{"sim_min": 1.0, "value": 21.0}],
        lower=[20.5],
        upper=[21.5],
        predicted_end=22.0,
        eta_safe_min=None,
        eta_hard_min=12.5,
        confidence=0.85,
        model="linear",
    )
    fcd = fc.to_dict()
    assert fcd["model"] == "linear"
    assert fcd["confidence"] == 0.85
