"""Configuration settings for IoT Environmental Monitoring system."""
from __future__ import annotations

import os
from typing import Any
from core.models import Limits

PORT = int(os.getenv("IOT_PORT", "5000"))
METRICS = ("temperature", "humidity", "aqi")

PHYSICAL = {
    "temperature": (-40.0, 125.0),
    "humidity": (0.0, 100.0),
    "aqi": (0.0, 500.0),
}

PROFILES: dict[str, dict[str, tuple[float, float, float, str]]] = {
    "data_center": {
        "temperature": (18.0, 27.0, 35.0, "°C"),
        "humidity": (40.0, 60.0, 80.0, "%"),
        "aqi": (0.0, 100.0, 200.0, "AQI"),
    },
    "cold_storage": {
        "temperature": (2.0, 8.0, 12.0, "°C"),
        "humidity": (30.0, 60.0, 80.0, "%"),
        "aqi": (0.0, 100.0, 200.0, "AQI"),
    },
    "greenhouse": {
        "temperature": (18.0, 30.0, 38.0, "°C"),
        "humidity": (50.0, 80.0, 95.0, "%"),
        "aqi": (0.0, 100.0, 200.0, "AQI"),
    },
}

DEFAULT_PROFILE = os.getenv("IOT_DEFAULT_PROFILE", "data_center")

# Pipeline
QUEUE_SIZE = int(os.getenv("IOT_QUEUE_SIZE", "500"))
WORKER_COUNT = int(os.getenv("IOT_WORKER_COUNT", "3"))
PROCESSING_DELAY_MS = float(os.getenv("IOT_PROCESSING_DELAY_MS", "5"))
PIPELINE_MODE = os.getenv("IOT_PIPELINE_MODE", "priority")
JUMP_LIMITS = {
    "temperature": float(os.getenv("IOT_JUMP_LIMIT_TEMPERATURE", "5.0")),
    "humidity": float(os.getenv("IOT_JUMP_LIMIT_HUMIDITY", "15.0")),
    "aqi": float(os.getenv("IOT_JUMP_LIMIT_AQI", "80.0")),
}
STUCK_REPEATS = int(os.getenv("IOT_STUCK_REPEATS", "10"))

# Wrangler
WINDOW = int(os.getenv("IOT_WINDOW", "30"))
EMA_ALPHA = float(os.getenv("IOT_EMA_ALPHA", "0.4"))
MAX_FFILL = int(os.getenv("IOT_MAX_FFILL", "5"))
MAD_K = float(os.getenv("IOT_MAD_K", "6.0"))
MAD_FLOOR = {
    "temperature": float(os.getenv("IOT_MAD_FLOOR_TEMPERATURE", "0.3")),
    "humidity": float(os.getenv("IOT_MAD_FLOOR_HUMIDITY", "1.0")),
    "aqi": float(os.getenv("IOT_MAD_FLOOR_AQI", "3.0")),
}
PERSIST_N = int(os.getenv("IOT_PERSIST_N", "3"))
DF_MAX_ROWS = int(os.getenv("IOT_DF_MAX_ROWS", "120"))

# Forecast
SIM_TIME_SCALE = float(os.getenv("IOT_SIM_TIME_SCALE", "60.0"))
HORIZON_MIN = float(os.getenv("IOT_HORIZON_MIN", "15.0"))
WINDOW_POINTS = int(os.getenv("IOT_WINDOW_POINTS", "60"))
MIN_POINTS = int(os.getenv("IOT_MIN_POINTS", "20"))
RETRAIN_EVERY = int(os.getenv("IOT_RETRAIN_EVERY", "5"))
STEP_MIN = float(os.getenv("IOT_STEP_MIN", "5.0"))
WATCH_CONFIDENCE = float(os.getenv("IOT_WATCH_CONFIDENCE", "0.6"))

# Actuator
HYST_MARGIN = float(os.getenv("IOT_HYST_MARGIN", "0.10"))
HYST_READINGS = int(os.getenv("IOT_HYST_READINGS", "10"))
MIN_ON_SECONDS = float(os.getenv("IOT_MIN_ON_SECONDS", "5.0"))
STRATEGY = os.getenv("IOT_STRATEGY", "proactive")
EFFECTS = {
    "fan": {"low": {"temperature": -2.0, "aqi": -10.0}, "high": {"temperature": -4.0, "aqi": -20.0}},
    "hvac": {"low": {"temperature": -5.0, "humidity": -5.0}, "high": {"temperature": -12.0, "humidity": -10.0}},
    "dehumidifier": {"low": {"humidity": -8.0}, "high": {"humidity": -18.0}},
    "purifier": {"low": {"aqi": -30.0}, "high": {"aqi": -70.0}},
    "shutdown": {"temperature": -15.0},
}

# Simulator
LAG_TAU_S = float(os.getenv("IOT_LAG_TAU_S", "20.0"))
DEFAULT_SEND_HZ = float(os.getenv("IOT_DEFAULT_SEND_HZ", "1.0"))
MAX_NODES = int(os.getenv("IOT_MAX_NODES", "8"))
DEFAULT_NODES = int(os.getenv("IOT_DEFAULT_NODES", "3"))
DEFAULT_NOISE = float(os.getenv("IOT_DEFAULT_NOISE", "0.2"))
FAULT_MODES = ("none", "dropout", "outlier", "stuck")
BASE_NOISE_SIGMA = {"temperature": 0.3, "humidity": 1.0, "aqi": 4.0}

# Sizes
HISTORY_LEN = int(os.getenv("IOT_HISTORY_LEN", "600"))
CHART_POINTS = int(os.getenv("IOT_CHART_POINTS", "120"))
EVENT_LOG_LEN = int(os.getenv("IOT_EVENT_LOG_LEN", "300"))
ACTUATION_LOG_LEN = int(os.getenv("IOT_ACTUATION_LOG_LEN", "200"))
LATENCY_SAMPLES = int(os.getenv("IOT_LATENCY_SAMPLES", "500"))
MAX_PAYLOAD_BYTES = int(os.getenv("IOT_MAX_PAYLOAD_BYTES", "2048"))


def derive_limits(profile_name: str, metric: str) -> Limits:
    """Calculate derived hard and slider limits for a metric in a profile."""
    prof = PROFILES.get(profile_name, PROFILES[DEFAULT_PROFILE])
    safe_min, safe_max, hard_max, unit = prof[metric]
    phys_min, phys_max = PHYSICAL[metric]

    hard_min = max(phys_min, safe_min - (hard_max - safe_max))
    span = hard_max - hard_min
    slider_min = max(phys_min, hard_min - 0.25 * span)
    slider_max = min(phys_max, hard_max + 0.25 * span)

    return Limits(
        safe_min=float(safe_min),
        safe_max=float(safe_max),
        hard_min=float(hard_min),
        hard_max=float(hard_max),
        slider_min=float(slider_min),
        slider_max=float(slider_max),
        unit=unit,
    )


def derive_all_limits(profile_name: str) -> dict[str, Limits]:
    """Calculate limits for all metrics in a profile."""
    return {m: derive_limits(profile_name, m) for m in METRICS}
