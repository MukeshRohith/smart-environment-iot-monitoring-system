"""Flask web application providing REST endpoints, simulation telemetry, and control APIs."""
from __future__ import annotations

import logging
import os
import time
from logging.handlers import RotatingFileHandler
from typing import Any

from flask import Flask, Response, jsonify, render_template, request

import config
from core.models import Reading
from core.system import System


def create_app(system: System | None = None) -> Flask:
    """Application factory configuring routes, error handlers, and logging."""
    app = Flask(__name__, template_folder="templates", static_folder="static")

    # Logging setup
    os.makedirs("logs", exist_ok=True)
    file_handler = RotatingFileHandler("logs/app.log", maxBytes=1_000_000, backupCount=3)
    formatter = logging.Formatter("[%(asctime)s] %(levelname)s in %(module)s: %(message)s")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)

    # Attach or create System context
    sys_instance = system or System()
    app.config["SYSTEM"] = sys_instance
    app.config["START_TIME"] = time.time()

    # ================= Global JSON Error Handlers =================

    @app.errorhandler(400)
    def handle_bad_request(err: Any) -> tuple[Response, int]:
        msg = err.description if hasattr(err, "description") else str(err)
        return jsonify({"error": msg}), 400

    @app.errorhandler(404)
    def handle_not_found(err: Any) -> tuple[Response, int]:
        return jsonify({"error": "Resource not found"}), 404

    @app.errorhandler(405)
    def handle_method_not_allowed(err: Any) -> tuple[Response, int]:
        return jsonify({"error": "Method not allowed"}), 405

    @app.errorhandler(413)
    def handle_payload_too_large(err: Any) -> tuple[Response, int]:
        return jsonify({"error": "Payload exceeds maximum allowed size"}), 413

    @app.errorhandler(500)
    def handle_internal_error(err: Any) -> tuple[Response, int]:
        app.logger.error(f"Internal server error: {err}")
        return jsonify({"error": "Internal server error"}), 500

    # ================= Core Web & Ingestion APIs =================

    @app.route("/", methods=["GET"])
    def index() -> Any:
        """Render main dashboard view."""
        index_path = os.path.join(app.template_folder, "index.html")
        if os.path.exists(index_path):
            return render_template("index.html")
        return "Smart IoT Environmental Monitoring & Predictive Automation System", 200

    @app.route("/api/health", methods=["GET"])
    def health() -> tuple[Response, int]:
        """Return service operational health, uptime, and thread liveness."""
        uptime = round(time.time() - app.config["START_TIME"], 1)
        data = {
            "status": "ok",
            "uptime_s": uptime,
            "pipeline_alive": sys_instance.pipeline.is_alive(),
            "simulator_alive": sys_instance.simulator._running,
            "queue_depth": sys_instance.pipeline._queue.qsize(),
        }
        return jsonify(data), 200

    @app.route("/api/state", methods=["GET"])
    def get_state() -> tuple[Response, int]:
        """Return complete state snapshot adhering to SPEC.md schema."""
        return jsonify(sys_instance.state()), 200

    @app.route("/api/metrics", methods=["GET"])
    def get_metrics() -> tuple[Response, int]:
        """Return runtime metrics and performance analytics."""
        return jsonify(sys_instance.metrics.compute()), 200

    @app.route("/api/nodes/<node_id>/history", methods=["GET"])
    def get_node_history(node_id: str) -> tuple[Response, int]:
        """Return historical cleaned telemetry up to requested limit."""
        limit_arg = request.args.get("limit", str(config.CHART_POINTS))
        try:
            limit = int(limit_arg)
        except ValueError:
            limit = config.CHART_POINTS
        return jsonify(sys_instance.store.history(node_id, limit)), 200

    @app.route("/api/profile", methods=["GET", "POST"])
    def handle_profile() -> tuple[Response, int]:
        """Inspect or switch current operational environment profile."""
        if request.method == "GET":
            return jsonify({
                "profile": sys_instance.store.get_profile(),
                "profiles": list(config.PROFILES.keys()),
                "limits": {m: sys_instance.store.limits(m).to_dict() for m in config.METRICS},
            }), 200

        data = request.get_json(silent=True) or {}
        profile_name = data.get("profile")
        if not profile_name:
            return jsonify({"error": "Missing 'profile' field in payload"}), 400

        try:
            sys_instance.set_profile(profile_name)
            return jsonify({
                "profile": profile_name,
                "limits": {m: sys_instance.store.limits(m).to_dict() for m in config.METRICS},
            }), 200
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/ingest", methods=["POST"])
    def ingest() -> tuple[Response, int]:
        """Ingest single node telemetry reading with payload verification."""
        content_length = request.content_length
        if content_length is not None and content_length > config.MAX_PAYLOAD_BYTES:
            return jsonify({"error": f"Payload exceeds {config.MAX_PAYLOAD_BYTES} bytes limit"}), 413

        if not request.is_json:
            return jsonify({"error": "Invalid content-type: application/json required"}), 400

        payload = request.get_json(silent=True)
        if payload is None:
            return jsonify({"error": "Malformed JSON payload"}), 400

        try:
            reading = Reading.from_dict(payload)
        except ValueError as exc:
            sys_instance.store.incr("rejected", 1)
            sys_instance.store.log_event("warn", "api:ingest", f"Rejected payload: {exc}")
            return jsonify({"error": str(exc)}), 400

        result = sys_instance.pipeline.submit(reading)
        if result == "accepted":
            prio_name = reading.priority.name if hasattr(reading.priority, "name") else str(reading.priority)
            return jsonify({"status": "accepted", "priority": prio_name}), 202
        else:
            return jsonify({"error": "Queue full, reading dropped"}), 429

    # ================= Simulator APIs =================

    @app.route("/api/sim/state", methods=["GET"])
    def get_sim_state() -> tuple[Response, int]:
        """Return simulator node controls and active values."""
        return jsonify(sys_instance.simulator.get_state()), 200

    @app.route("/api/sim/controls", methods=["POST"])
    def set_sim_controls() -> tuple[Response, int]:
        """Update physics targets and noise controls for a simulated node."""
        data = request.get_json(silent=True) or {}
        node_id = data.get("node_id") or data.get("node")
        controls = data.get("controls")
        if not node_id or not isinstance(controls, dict):
            return jsonify({"error": "node_id and controls dict required"}), 400

        try:
            sys_instance.simulator.set_controls(node_id, controls)
            state = sys_instance.simulator.get_state()
            return jsonify(state["nodes"].get(node_id, {})), 200
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/sim/scenario", methods=["POST"])
    def set_sim_scenario() -> tuple[Response, int]:
        """Activate preset simulation scenario."""
        data = request.get_json(silent=True) or {}
        name = data.get("name")
        if not name:
            return jsonify({"error": "Missing scenario 'name'"}), 400

        try:
            sys_instance.simulator.apply_scenario(name)
            return jsonify({"scenario": name, "status": "applied"}), 200
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/sim/nodes", methods=["POST"])
    def manage_sim_nodes() -> tuple[Response, int]:
        """Dynamically add or remove nodes from the simulation ensemble."""
        data = request.get_json(silent=True) or {}
        action = data.get("action")
        node_id = data.get("node_id")

        if action not in ("add", "remove"):
            return jsonify({"error": "action must be 'add' or 'remove'"}), 400

        try:
            if action == "add":
                nid = sys_instance.simulator.add_node(node_id)
            else:
                nid = sys_instance.simulator.remove_node(node_id)

            return jsonify({
                "action": action,
                "node_id": nid,
                "nodes": sys_instance.store.node_ids(),
            }), 200
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    # ================= Configuration & Control APIs =================

    @app.route("/api/pipeline/config", methods=["POST"])
    def config_pipeline() -> tuple[Response, int]:
        """Configure pipeline worker pool, processing delay, and queue mode."""
        data = request.get_json(silent=True) or {}
        try:
            updated = sys_instance.apply_settings(data)
            return jsonify(updated), 200
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/forecast/config", methods=["POST"])
    def config_forecast() -> tuple[Response, int]:
        """Configure forecast horizon and simulation time scale."""
        data = request.get_json(silent=True) or {}
        try:
            updated = sys_instance.apply_settings(data)
            return jsonify(updated), 200
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/control/strategy", methods=["POST"])
    def config_strategy() -> tuple[Response, int]:
        """Switch automation strategy between 'proactive' and 'reactive'."""
        data = request.get_json(silent=True) or {}
        strategy = data.get("strategy")
        if not strategy:
            return jsonify({"error": "Missing 'strategy' field"}), 400

        try:
            sys_instance.apply_settings({"strategy": strategy})
            return jsonify({"strategy": sys_instance.get_settings()["strategy"]}), 200
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/actuators/override", methods=["POST"])
    def override_actuator() -> tuple[Response, int]:
        """Apply manual state override to an environmental actuator."""
        data = request.get_json(silent=True) or {}
        node_id = data.get("node_id") or data.get("node")
        device = data.get("device")
        action = data.get("action")

        if not node_id or not device or not action:
            return jsonify({"error": "node_id, device, and action required"}), 400

        if action not in ("on", "off", "auto"):
            return jsonify({"error": "action must be 'on', 'off', or 'auto'"}), 400

        sys_instance.actuator.override(node_id, device, action)
        return jsonify({"node_id": node_id, "devices": sys_instance.store.devices(node_id)}), 200

    @app.route("/api/actuators/restart", methods=["POST"])
    def restart_actuator() -> tuple[Response, int]:
        """Manually clear emergency shutdown for a node."""
        data = request.get_json(silent=True) or {}
        node_id = data.get("node_id") or data.get("node")
        if not node_id:
            return jsonify({"error": "Missing 'node_id'"}), 400

        sys_instance.actuator.restart(node_id)
        return jsonify({
            "node_id": node_id,
            "status": "restarted",
            "shutdown": sys_instance.actuator.is_shutdown(node_id),
        }), 200

    # ================= Benchmark & Demo APIs =================

    @app.route("/api/benchmark/run", methods=["POST"])
    def run_benchmark() -> tuple[Response, int]:
        """Launch background benchmarking run."""
        data = request.get_json(silent=True) or {}
        name = data.get("name")
        duration_s = data.get("duration_s")

        if not name:
            return jsonify({"error": "Missing benchmark 'name'"}), 400

        try:
            sys_instance.benchmark.run(name, duration_s)
            return jsonify(sys_instance.benchmark.status()), 200
        except (ValueError, RuntimeError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/benchmark/status", methods=["GET"])
    def get_benchmark_status() -> tuple[Response, int]:
        """Return benchmark progress and execution state."""
        return jsonify(sys_instance.benchmark.status()), 200

    @app.route("/api/benchmark/results", methods=["GET"])
    def get_benchmark_results() -> tuple[Response, int]:
        """Return collected benchmark results."""
        return jsonify(sys_instance.benchmark.results()), 200

    @app.route("/api/benchmark/results.csv", methods=["GET"])
    def get_benchmark_results_csv() -> Response:
        """Download benchmark results in CSV format."""
        return Response(sys_instance.benchmark.results_csv(), mimetype="text/csv")

    @app.route("/api/demo/<action>", methods=["POST"])
    def handle_demo_action(action: str) -> tuple[Response, int]:
        """Control guided walkthrough demonstration."""
        if action == "start":
            sys_instance.demo.start()
        elif action == "pause":
            sys_instance.demo.pause()
        elif action == "resume":
            sys_instance.demo.resume()
        elif action == "stop":
            sys_instance.demo.stop()
        else:
            return jsonify({"error": f"Invalid demo action: {action}"}), 400

        return jsonify(sys_instance.demo.state()), 200

    return app
