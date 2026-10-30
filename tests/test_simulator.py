"""Tests for environmental simulator, physics lag, fault modes, and scenario controls."""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
import config
from core.simulator import SimulatorManager
from core.store import Store


class FakeTransport:
    """Mock transport collecting emitted telemetry payloads."""

    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def __call__(self, payload: dict) -> int:
        self.payloads.append(payload)
        return 202


def test_smooth_ramp_towards_new_target():
    """Verify that current value approaches a new target smoothly via exponential lag without jumping."""
    store = Store("data_center")
    transport = FakeTransport()
    sim = SimulatorManager(
        store=store,
        effects_provider=lambda n: {},
        shutdown_provider=lambda n: False,
        transport=transport,
    )

    node = sim._nodes["node-1"]
    node.noise = 0.0  # Zero noise for deterministic physics
    node.target["temperature"] = 22.5
    node.current["temperature"] = 22.5

    # Change target to 30.0
    sim.set_controls("node-1", {"target": {"temperature": 30.0}, "noise": 0.0})

    # Step 1 second of simulation time
    payload = sim._step_node(node, dt=1.0)
    current_temp = node.current["temperature"]

    # Must have moved toward 30.0, but NOT jumped to 30.0
    # Expected increment: (30 - 22.5) * (1 - exp(-1/20)) ≈ 7.5 * 0.04877 ≈ 0.365 °C
    assert 22.7 < current_temp < 23.1
    assert payload["temperature"] == pytest.approx(current_temp, abs=0.01)


def test_actuator_effects_shift_effective_target():
    """Verify actuator negative/positive offsets shift the physics convergence target."""
    store = Store("data_center")
    transport = FakeTransport()
    # Actuator cools node-1 by -5.0 °C
    sim = SimulatorManager(
        store=store,
        effects_provider=lambda n: {"temperature": -5.0},
        shutdown_provider=lambda n: False,
        transport=transport,
    )

    node = sim._nodes["node-1"]
    node.noise = 0.0
    # Target is 25.0 -> with -5.0 effect, effective target is 20.0 °C!
    node.target["temperature"] = 25.0
    node.current["temperature"] = 20.0

    # Stepping forward should keep current steady around 20.0
    sim._step_node(node, dt=1.0)
    assert node.current["temperature"] == pytest.approx(20.0, abs=0.01)


def test_each_fault_mode_output_shape():
    """Verify dropout, outlier, and stuck fault mode injection behaviors."""
    store = Store("data_center")
    transport = FakeTransport()
    sim = SimulatorManager(
        store=store,
        effects_provider=lambda n: {},
        shutdown_provider=lambda n: False,
        transport=transport,
    )
    node = sim._nodes["node-1"]
    node.noise = 0.0

    # 1. Dropout: some readings become None
    sim.set_controls("node-1", {"fault_mode": "dropout"})
    has_null = False
    for _ in range(50):
        p = sim._step_node(node, dt=0.1)
        if p["temperature"] is None or p["humidity"] is None or p["aqi"] is None:
            has_null = True
            break
    assert has_null

    # 2. Outlier: injects sudden spikes (temperature 15, humidity 40, aqi 200 with p=0.08 per tick to one random field)
    sim.set_controls("node-1", {"fault_mode": "outlier", "noise": 0.0})
    node.current["temperature"] = 22.0
    node.current["humidity"] = 50.0
    node.current["aqi"] = 50.0
    has_outlier = False
    for _ in range(200):
        p = sim._step_node(node, dt=0.01)
        if (
            (p["temperature"] is not None and abs(p["temperature"] - 22.0) >= 10.0)
            or (p["humidity"] is not None and abs(p["humidity"] - 50.0) >= 20.0)
            or (p["aqi"] is not None and abs(p["aqi"] - 50.0) >= 100.0)
        ):
            has_outlier = True
            break
    assert has_outlier

    # 3. Stuck: identical values repeated
    sim.set_controls("node-1", {"fault_mode": "stuck", "noise": 0.0})
    node.target["temperature"] = 25.0
    p1 = sim._step_node(node, dt=1.0)
    p2 = sim._step_node(node, dt=1.0)
    p3 = sim._step_node(node, dt=1.0)
    assert p1["temperature"] == p2["temperature"] == p3["temperature"]


def test_scenarios_per_profile():
    """Verify test scenarios scale with active profile thresholds."""
    store = Store("data_center")
    transport = FakeTransport()
    sim = SimulatorManager(store, lambda n: {}, lambda n: False, transport)

    # 1. Data Center heat_spike: safe_max=27, hard_max=35 -> target = 27 + 0.8*8 = 33.4
    sim.apply_scenario("heat_spike")
    assert sim._nodes["node-1"].target["temperature"] == pytest.approx(33.4, abs=0.1)

    # 2. Switch to cold_storage: safe_max=8, hard_max=12 -> target = 8 + 0.8*4 = 11.2
    store.set_profile("cold_storage")
    sim.apply_scenario("heat_spike")
    assert sim._nodes["node-1"].target["temperature"] == pytest.approx(11.2, abs=0.1)

    # 3. Other scenarios
    sim.apply_scenario("humidity_drift")
    assert sim._nodes["node-2"].target["humidity"] > 60.0

    sim.apply_scenario("sensor_fault")
    assert sim._nodes["node-3"].fault_mode == "outlier"
    assert sim._nodes["node-3"].noise == 0.5

    sim.apply_scenario("burst_traffic")
    for n in sim._nodes.values():
        assert n.send_rate_hz == 15.0

    sim.apply_scenario("normal")
    assert sim._nodes["node-1"].fault_mode == "none"

    with pytest.raises(ValueError, match="Unknown scenario"):
        sim.apply_scenario("invalid_scenario")


def test_add_remove_limits():
    """Verify node pool bounds between 1 and MAX_NODES (8)."""
    store = Store("data_center")
    transport = FakeTransport()
    sim = SimulatorManager(store, lambda n: {}, lambda n: False, transport)

    # Initially 3 nodes
    assert len(sim._nodes) == 3

    # Add up to 8 nodes
    for i in range(4, 9):
        sim.add_node()
    assert len(sim._nodes) == 8

    # Adding 9th must raise ValueError
    with pytest.raises(ValueError, match="Maximum node count"):
        sim.add_node()

    # Remove down to 1 node
    while len(sim._nodes) > 1:
        sim.remove_node()
    assert len(sim._nodes) == 1

    # Removing the last node must raise ValueError
    with pytest.raises(ValueError, match="At least 1 node"):
        sim.remove_node()


def test_validation_errors():
    """Verify invalid control bounds are rejected."""
    store = Store("data_center")
    transport = FakeTransport()
    sim = SimulatorManager(store, lambda n: {}, lambda n: False, transport)

    lims = store.limits("temperature")

    # Target outside slider bounds
    with pytest.raises(ValueError, match="outside allowable slider range"):
        sim.set_controls("node-1", {"target": {"temperature": lims.slider_max + 10.0}})

    # Noise out of bounds
    with pytest.raises(ValueError, match="noise"):
        sim.set_controls("node-1", {"noise": 1.5})

    # Send rate out of bounds
    with pytest.raises(ValueError, match="send_rate_hz"):
        sim.set_controls("node-1", {"send_rate_hz": 25.0})

    # Invalid fault mode
    with pytest.raises(ValueError, match="fault_mode"):
        sim.set_controls("node-1", {"fault_mode": "exploding"})
