"""Application entrypoint starting system orchestrator, HTTP server, and polling startup."""
from __future__ import annotations

import atexit
import signal
import sys
import threading
import time
import webbrowser

import requests

import config
from app import create_app
from core.system import System


def _wait_for_server_and_start_sim(system: System, port: int) -> None:
    """Poll health endpoint until server is listening, then start simulation and browser."""
    url = f"http://127.0.0.1:{port}/api/health"
    start_time = time.time()
    while time.time() - start_time < 15.0:
        try:
            resp = requests.get(url, timeout=0.5)
            if resp.status_code == 200:
                system.simulator.start()
                try:
                    webbrowser.open(f"http://127.0.0.1:{port}")
                except Exception:
                    pass
                return
        except Exception:
            pass
        time.sleep(0.2)


def main() -> None:
    """Configure system, register shutdown traps, and launch server."""
    system = System()
    app = create_app(system=system)

    # Start pipeline and watchdog; simulator starts after health check
    system.start(start_simulator=False)

    def shutdown_handler(*args: Any) -> None:
        """Ensure clean shutdown with zero orphan threads."""
        system.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)
    atexit.register(system.stop)

    # Polling thread to start simulator once server answers
    poll_thread = threading.Thread(
        target=_wait_for_server_and_start_sim,
        args=(system, config.PORT),
        daemon=True,
    )
    poll_thread.start()

    app.run(host="0.0.0.0", port=config.PORT, threaded=True)


if __name__ == "__main__":
    main()
