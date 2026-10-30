"""Comprehensive API and end-to-end integration tests using in-process transport."""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import time
import pytest
from app import create_app
from core.models import Level
from core.system import System


@pytest.fixture
def client_and_system():
    """Create Flask test client and connected System with in-process transport."""
    sys_instance = System(initial_profile="data_center")
    app = create_app(system=sys_instance)
    client = app.test_client()

    # Route simulator transport directly into in-process test client
    sys_instance.simulator._custom_transport = lambda p: client.post("/api/ingest", json=p).status_code

    sys_instance.start(start_simulator=False)
    yield client, sys_instance
    sys_instance.stop()


def test_every_route_returns_documented_shape_and_status(client_and_system):
    """Verify all routes return documented JSON structures and status codes."""
    client, system = client_and_system

    # GET /
    r = client.get("/")
    assert r.status_code == 200

    # GET /api/health
    r = client.get("/api/health")
    assert r.status_code == 200
    h_data = r.get_json()
    for k in ("status", "uptime_s", "pipeline_alive", "simulator_alive", "queue_depth"):
        assert k in h_data

    # GET /api/state (snapshot schema)
    r = client.get("/api/state")
    assert r.status_code == 200
    s_data = r.get_json()
    for k in ("ts", "profile", "limits", "settings", "counters", "nodes", "events", "actuations", "pipeline", "metrics", "benchmark", "demo"):
        assert k in s_data, f"Missing key in state snapshot: {k}"

    # GET /api/metrics
    r = client.get("/api/metrics")
    assert r.status_code == 200
    m_data = r.get_json()
    for k in ("packet_loss_pct", "counts", "latency", "forecast_error", "lead_time", "breaches", "exposure", "device_on_seconds"):
        assert k in m_data

    # GET /api/nodes/<id>/history
    r = client.get("/api/nodes/node-1/history?limit=10")
    assert r.status_code == 200
    hist = r.get_json()
    assert "ts" in hist and "raw" in hist and "smooth" in hist

    # GET /api/profile
    r = client.get("/api/profile")
    assert r.status_code == 200
    assert r.get_json()["profile"] == "data_center"

    # POST /api/profile
    r = client.post("/api/profile", json={"profile": "greenhouse"})
    assert r.status_code == 200
    assert r.get_json()["profile"] == "greenhouse"

    # Revert back to data_center
    client.post("/api/profile", json={"profile": "data_center"})

    # POST /api/ingest (valid 202)
    valid_payload = {"node_id": "node-1", "ts": time.time(), "seq": 1, "temperature": 22.0, "humidity": 50.0, "aqi": 30.0}
    r = client.post("/api/ingest", json=valid_payload)
    assert r.status_code == 202
    assert r.get_json()["status"] == "accepted"

    # POST /api/ingest (invalid 400)
    r = client.post("/api/ingest", json={"node_id": "node-1"})
    assert r.status_code == 400
    assert "error" in r.get_json()

    # GET /api/sim/state
    r = client.get("/api/sim/state")
    assert r.status_code == 200
    assert "nodes" in r.get_json()

    # POST /api/sim/controls
    r = client.post("/api/sim/controls", json={"node_id": "node-1", "controls": {"noise": 0.1}})
    assert r.status_code == 200

    # POST /api/sim/scenario
    r = client.post("/api/sim/scenario", json={"name": "normal"})
    assert r.status_code == 200

    # POST /api/sim/nodes
    r = client.post("/api/sim/nodes", json={"action": "add"})
    assert r.status_code == 200
    nid = r.get_json()["node_id"]
    r = client.post("/api/sim/nodes", json={"action": "remove", "node_id": nid})
    assert r.status_code == 200

    # POST /api/pipeline/config
    r = client.post("/api/pipeline/config", json={"workers": 2, "processing_delay_ms": 0.0})
    assert r.status_code == 200

    # POST /api/forecast/config
    r = client.post("/api/forecast/config", json={"horizon_min": 20.0})
    assert r.status_code == 200

    # POST /api/control/strategy
    r = client.post("/api/control/strategy", json={"strategy": "proactive"})
    assert r.status_code == 200
    assert r.get_json()["strategy"] == "proactive"

    # POST /api/actuators/override
    r = client.post("/api/actuators/override", json={"node_id": "node-1", "device": "fan", "action": "on"})
    assert r.status_code == 200

    # POST /api/actuators/restart
    r = client.post("/api/actuators/restart", json={"node_id": "node-1"})
    assert r.status_code == 200

    # POST /api/benchmark/run
    r = client.post("/api/benchmark/run", json={"name": "strategy", "duration_s": 0.2})
    assert r.status_code == 200

    # GET /api/benchmark/status
    r = client.get("/api/benchmark/status")
    assert r.status_code == 200
    assert "state" in r.get_json()

    # GET /api/benchmark/results
    r = client.get("/api/benchmark/results")
    assert r.status_code == 200

    # GET /api/benchmark/results.csv
    r = client.get("/api/benchmark/results.csv")
    assert r.status_code == 200
    assert "text/csv" in r.mimetype

    # POST /api/demo/start|pause|resume|stop
    r = client.post("/api/demo/start")
    assert r.status_code == 200
    r = client.post("/api/demo/pause")
    assert r.status_code == 200
    r = client.post("/api/demo/resume")
    assert r.status_code == 200
    r = client.post("/api/demo/stop")
    assert r.status_code == 200


def test_end_to_end_rising_ramp_proactive_vs_reactive(client_and_system):
    """End-to-end test: feed rising ramp, verify ETA, WATCH before WARNING in proactive, and actuations logged."""
    client, system = client_and_system
    node = "node-1"

    # 1. Proactive Mode
    system.apply_settings({"strategy": "proactive", "processing_delay_ms": 0.0, "time_scale": 60.0, "horizon_min": 15.0})
    system.reset_runtime()

    # Data center safe_max is 27.0 °C.
    # Feed ramp from 18.0 °C to 26.5 °C rising by 0.35 °C per reading (25 readings).
    # All readings are inside safe range [18, 27], but rising rapidly toward breach.
    for i in range(25):
        t = 100.0 + i
        temp = 18.0 + 0.35 * i
        payload = {
            "node_id": node,
            "ts": t,
            "seq": i + 1,
            "temperature": temp,
            "humidity": 50.0,
            "aqi": 30.0,
        }
        res = client.post("/api/ingest", json=payload)
        assert res.status_code == 202

    # Give worker pool a moment to process queue
    time.sleep(0.3)

    # Verify forecast with ETA appeared
    fcasts = system.store.forecasts(node)
    assert "temperature" in fcasts
    fc = fcasts["temperature"]
    assert fc.eta_safe_min is not None
    assert fc.eta_safe_min <= 15.0

    # Verify level was elevated to WATCH before any WARNING occurred
    levels = system.store.levels(node)
    assert levels["temperature"] == Level.WATCH

    # Verify actuations were logged
    actuations = system.store.actuations()
    assert len(actuations) > 0
    assert any(a["device"] == "fan" and a["to_state"] != "off" for a in actuations)

    # 2. Reactive Mode: feed same rising ramp under reactive strategy
    system.apply_settings({"strategy": "reactive"})
    system.reset_runtime()

    for i in range(25):
        t = 200.0 + i
        temp = 18.0 + 0.35 * i
        payload = {
            "node_id": node,
            "ts": t,
            "seq": 100 + i,
            "temperature": temp,
            "humidity": 50.0,
            "aqi": 30.0,
        }
        client.post("/api/ingest", json=payload)

    time.sleep(0.3)

    # In reactive mode, since temp (26.4) is <= safe_max (27.0), level MUST be OK (NOT WATCH)
    reactive_levels = system.store.levels(node)
    assert reactive_levels["temperature"] == Level.OK
