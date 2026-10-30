"""Multi-stage worker pipeline with priority-based queuing, live scaling, and watchdog monitoring."""
from __future__ import annotations

import itertools
import queue
import threading
import time
from collections import deque
from typing import Any

import config
from core.models import Priority, Reading

_SENTINEL_PAYLOAD = object()


def classify(
    reading: Reading,
    last_reading: Reading | None,
    repeat_count: int,
    limits_dict: dict[str, Any],
) -> Priority:
    """Pure classification function returning CRITICAL, ANOMALY, or NORMAL priority."""
    # 1. Critical Check: any non-null metric at or beyond hard bounds
    for m in config.METRICS:
        val = getattr(reading, m)
        if val is not None:
            lim = limits_dict[m]
            if val <= lim.hard_min or val >= lim.hard_max:
                return Priority.CRITICAL

    # 2. Anomaly Checks
    # Any null metric
    for m in config.METRICS:
        if getattr(reading, m) is None:
            return Priority.ANOMALY

    # Status shutdown
    if reading.status == "shutdown":
        return Priority.ANOMALY

    # Stuck repeats
    if repeat_count >= config.STUCK_REPEATS:
        return Priority.ANOMALY

    # Jump limit exceeded
    if last_reading is not None:
        for m in config.METRICS:
            curr = getattr(reading, m)
            prev = getattr(last_reading, m)
            if curr is not None and prev is not None:
                if abs(curr - prev) > config.JUMP_LIMITS[m]:
                    return Priority.ANOMALY

    return Priority.NORMAL


class Pipeline:
    """Threaded pipeline managing telemetry ingestion, prioritization, and stage dispatch."""

    def __init__(self, store: Any, wrangler: Any, forecaster: Any, actuator: Any) -> None:
        """Initialize queue, workers container, and stage references."""
        self.store = store
        self.wrangler = wrangler
        self.forecaster = forecaster
        self.actuator = actuator

        settings = self.store.get_settings()
        queue_size = int(settings.get("queue_size", config.QUEUE_SIZE))
        self._queue: queue.PriorityQueue[Any] = queue.PriorityQueue(maxsize=queue_size)
        self._tie_breaker = itertools.count()

        self._state_lock = threading.Lock()
        self._last_readings: dict[str, Reading] = {}
        self._repeat_counts: dict[str, int] = {}
        self._last_drop_log_ts: float = 0.0

        # Workers
        self._workers: list[threading.Thread] = []
        self._stopped = False

        # Throughput tracking
        self._processed_timestamps: deque[float] = deque(maxlen=2000)
        self._throughput_lock = threading.Lock()

        # Rate-limiting logs
        self._last_crit_log_ts: float = 0.0
        self._last_anom_log_ts: float = 0.0

    def submit(self, reading: Reading) -> str:
        """Classify reading and enqueue according to priority mode."""
        now = time.time()
        reading.received_at = reading.received_at or now

        # Update per-node sequence and repeat counts under lock
        with self._state_lock:
            last = self._last_readings.get(reading.node_id)
            current_vals = (reading.temperature, reading.humidity, reading.aqi)
            prev_vals = (last.temperature, last.humidity, last.aqi) if last else None

            if prev_vals is not None and current_vals == prev_vals and None not in current_vals:
                rep_count = self._repeat_counts.get(reading.node_id, 1) + 1
            else:
                rep_count = 1

            lims = self.store.all_limits()
            prio = classify(reading, last, rep_count, lims)
            reading.priority = prio

            self._last_readings[reading.node_id] = reading
            self._repeat_counts[reading.node_id] = rep_count

        self.store.add_raw(reading)
        self.store.incr("received", 1, priority=prio)

        settings = self.store.get_settings()
        mode = settings.get("pipeline_mode", config.PIPELINE_MODE)
        prio_val = 2 if mode == "fifo" else int(prio)
        item = (prio_val, next(self._tie_breaker), reading)

        # Enqueue with timeout constraints
        if self._queue.full():
            if prio == Priority.NORMAL:
                self.store.incr("dropped", 1, priority=Priority.NORMAL)
                if now - self._last_drop_log_ts >= 5.0:
                    self.store.log_event("warn", "pipeline", "Queue full: dropped NORMAL reading")
                    self._last_drop_log_ts = now
                return "dropped"
            else:
                try:
                    self._queue.put(item, timeout=0.05)
                    return "accepted"
                except queue.Full:
                    self.store.incr("dropped", 1, priority=prio)
                    return "dropped"

        try:
            self._queue.put(item, timeout=0.05)
            return "accepted"
        except queue.Full:
            self.store.incr("dropped", 1, priority=prio)
            return "dropped"

    def _worker_loop(self) -> None:
        """Continuously consume and process readings from priority queue."""
        while not self._stopped:
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if not isinstance(item, tuple) or len(item) < 3 or item[2] is _SENTINEL_PAYLOAD:
                break

            _, _, reading = item
            self._process_reading(reading)

    def _process_reading(self, reading: Reading) -> None:
        """Execute processing stages for a single reading with error containment."""
        settings = self.store.get_settings()
        delay_ms = float(settings.get("processing_delay_ms", config.PROCESSING_DELAY_MS))
        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)

        try:
            cleaned = self.wrangler.process(reading)
            self.store.add_cleaned(cleaned)

            fcasts = self.forecaster.update(cleaned)
            if fcasts:
                self.store.set_forecasts(reading.node_id, fcasts)

            self.actuator.evaluate(cleaned, fcasts)

            now = time.time()
            self.store.incr("processed", 1, priority=reading.priority)
            latency = cleaned.processed_at - reading.received_at
            self.store.record_latency(reading.priority, latency)

            with self._throughput_lock:
                self._processed_timestamps.append(now)

            # Rate-limited alert events
            if reading.priority == Priority.CRITICAL:
                if now - self._last_crit_log_ts >= 1.0:
                    self.store.log_event("info", "pipeline", f"CRITICAL telemetry processed: {reading.node_id}")
                    self._last_crit_log_ts = now
            elif reading.priority == Priority.ANOMALY:
                if now - self._last_anom_log_ts >= 1.0:
                    self.store.log_event("warn", "pipeline", f"ANOMALY telemetry processed: {reading.node_id}")
                    self._last_anom_log_ts = now

        except Exception as exc:
            self.store.incr("errors", 1)
            self.store.log_event("critical", "pipeline", f"Pipeline stage error: {exc}")

    def start(self) -> None:
        """Start worker thread pool according to store settings."""
        self._stopped = False
        settings = self.store.get_settings()
        target_workers = max(1, min(8, int(settings.get("workers", config.WORKER_COUNT))))

        self._cleanup_dead_workers()
        while len(self._workers) < target_workers:
            t = threading.Thread(target=self._worker_loop, daemon=True)
            self._workers.append(t)
            t.start()

    def stop(self) -> None:
        """Signal all workers to terminate gracefully and wait for exit."""
        self._stopped = True
        self._cleanup_dead_workers()
        for _ in self._workers:
            self._queue.put((-1, next(self._tie_breaker), _SENTINEL_PAYLOAD))
        for t in self._workers:
            t.join(timeout=1.0)
        self._workers.clear()

    def is_alive(self) -> bool:
        """Check if at least one worker thread is actively running."""
        self._cleanup_dead_workers()
        return len(self._workers) > 0

    def _cleanup_dead_workers(self) -> None:
        """Prune terminated threads from tracking list."""
        self._workers = [w for w in self._workers if w.is_alive()]

    def apply_settings(self) -> None:
        """Dynamically resize worker pool and sync pipeline operational parameters."""
        settings = self.store.get_settings()
        target_workers = max(1, min(8, int(settings.get("workers", config.WORKER_COUNT))))

        self._cleanup_dead_workers()
        curr_count = len(self._workers)

        if not self._stopped:
            if curr_count < target_workers:
                for _ in range(target_workers - curr_count):
                    t = threading.Thread(target=self._worker_loop, daemon=True)
                    self._workers.append(t)
                    t.start()
            elif curr_count > target_workers:
                excess = curr_count - target_workers
                for _ in range(excess):
                    self._queue.put((-1, next(self._tie_breaker), _SENTINEL_PAYLOAD))
                # Sleep briefly to let sentinels be consumed
                time.sleep(0.05)
                self._cleanup_dead_workers()

    def _get_throughput(self) -> float:
        """Compute message processing throughput rate over 5-second window."""
        now = time.time()
        with self._throughput_lock:
            cutoff = now - 5.0
            while self._processed_timestamps and self._processed_timestamps[0] < cutoff:
                self._processed_timestamps.popleft()
            return len(self._processed_timestamps) / 5.0

    def snapshot(self) -> dict[str, Any]:
        """Generate pipeline performance and queue depth telemetry snapshot."""
        settings = self.store.get_settings()
        mode = settings.get("pipeline_mode", config.PIPELINE_MODE)
        delay_ms = float(settings.get("processing_delay_ms", config.PROCESSING_DELAY_MS))

        # Inspect priority breakdown inside queue
        q_list = list(self._queue.queue)
        depth_by_prio = {"0": 0, "1": 0, "2": 0}
        for item in q_list:
            if isinstance(item, tuple) and len(item) == 3 and item[2] is not _SENTINEL_PAYLOAD and item[2] is not None:
                r_prio = str(int(item[2].priority))
                if r_prio in depth_by_prio:
                    depth_by_prio[r_prio] += 1

        avg_wrangle = getattr(self.wrangler, "avg_ms", 0.0)

        return {
            "queue_depth": self._queue.qsize(),
            "depth_by_priority": depth_by_prio,
            "mode": mode,
            "workers": len([w for w in self._workers if w.is_alive()]),
            "processing_delay_ms": delay_ms,
            "latency": self.store.latency_stats(),
            "throughput": self._get_throughput(),
            "avg_wrangle_ms": avg_wrangle,
        }
