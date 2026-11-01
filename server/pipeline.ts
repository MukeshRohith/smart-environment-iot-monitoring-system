/**
 * Multi-stage pipeline with priority-based queuing, live scaling, and latency tracking.
 */
import * as config from './config';
import { ActuatorEngine } from './actuator';
import { Forecaster } from './forecaster';
import { Priority, Reading } from './models';
import { Store } from './store';
import { Wrangler } from './wrangler';

export function classify(
  reading: Reading,
  last_reading: Reading | null,
  repeat_count: number,
  limits_dict: Record<string, any>
): Priority {
  // 1. Critical Check: any non-null metric at or beyond hard bounds
  for (const m of config.METRICS) {
    const val = reading[m];
    if (val !== null && val !== undefined) {
      const lim = limits_dict[m];
      if (val <= lim.hard_min || val >= lim.hard_max) {
        return Priority.CRITICAL;
      }
    }
  }

  // 2. Anomaly Checks
  // Null metric
  for (const m of config.METRICS) {
    if (reading[m] === null || reading[m] === undefined) {
      return Priority.ANOMALY;
    }
  }

  // Status shutdown
  if (reading.status === 'shutdown') {
    return Priority.ANOMALY;
  }

  // Stuck repeats
  if (repeat_count >= config.STUCK_REPEATS) {
    return Priority.ANOMALY;
  }

  // Jump limit exceeded
  if (last_reading) {
    for (const m of config.METRICS) {
      const curr = reading[m];
      const prev = last_reading[m];
      if (curr !== null && curr !== undefined && prev !== null && prev !== undefined) {
        if (Math.abs(curr - prev) > config.JUMP_LIMITS[m]) {
          return Priority.ANOMALY;
        }
      }
    }
  }

  return Priority.NORMAL;
}

interface QueueItem {
  reading: Reading;
  seq: number;
}

export class Pipeline {
  private store: Store;
  private wrangler: Wrangler;
  private forecaster: Forecaster;
  private actuator: ActuatorEngine;

  private _queue: QueueItem[] = [];
  private _tie_breaker = 0;
  private _last_readings: Record<string, Reading> = {};
  private _repeat_counts: Record<string, number> = {};

  private _workers_count = config.WORKER_COUNT;
  private _active_workers = 0;
  private _running = false;
  private _paused = false;

  private _processed_timestamps: number[] = [];

  constructor(store: Store, wrangler: Wrangler, forecaster: Forecaster, actuator: ActuatorEngine) {
    this.store = store;
    this.wrangler = wrangler;
    this.forecaster = forecaster;
    this.actuator = actuator;
    this.apply_settings();
  }

  apply_settings(): void {
    const settings = this.store.get_settings();
    this._workers_count = parseInt(settings.workers || config.WORKER_COUNT, 10);
  }

  start(): void {
    this._running = true;
    this._spawn_workers();
  }

  stop(): void {
    this._running = false;
  }

  pause(): void {
    this._paused = true;
  }

  resume(): void {
    this._paused = false;
    this._trigger_work();
  }

  is_paused(): boolean {
    return this._paused;
  }

  reset(): void {
    this._queue = [];
    this._last_readings = {};
    this._repeat_counts = {};
    this._processed_timestamps = [];
  }

  is_alive(): boolean {
    return this._running;
  }

  submit(reading: Reading): 'accepted' | 'dropped' {
    const now = Date.now() / 1000;
    reading.received_at = reading.received_at || now;

    const limits = this.store.all_limits();
    const last = this._last_readings[reading.node_id] || null;

    let repeat_count = 0;
    if (
      last &&
      last.temperature === reading.temperature &&
      last.humidity === reading.humidity &&
      last.aqi === reading.aqi
    ) {
      repeat_count = (this._repeat_counts[reading.node_id] || 0) + 1;
    }
    this._repeat_counts[reading.node_id] = repeat_count;
    this._last_readings[reading.node_id] = reading;

    reading.priority = classify(reading, last, repeat_count, limits);

    const settings = this.store.get_settings();
    const queue_max = parseInt(settings.queue_size || config.QUEUE_SIZE, 10);
    const mode = settings.pipeline_mode || config.PIPELINE_MODE;

    if (this._queue.length >= queue_max) {
      this.store.incr('dropped', 1, reading.priority);
      return 'dropped';
    }

    this.store.add_raw(reading);
    this.store.incr('received', 1, reading.priority);

    const item: QueueItem = {
      reading,
      seq: this._tie_breaker++,
    };

    if (mode === 'priority') {
      // PriorityQueue ordering: priority asc (0, 1, 2), then seq asc
      let inserted = false;
      for (let i = 0; i < this._queue.length; i++) {
        const q = this._queue[i];
        if (
          item.reading.priority < q.reading.priority ||
          (item.reading.priority === q.reading.priority && item.seq < q.seq)
        ) {
          this._queue.splice(i, 0, item);
          inserted = true;
          break;
        }
      }
      if (!inserted) {
        this._queue.push(item);
      }
    } else {
      this._queue.push(item);
    }

    this._trigger_work();
    return 'accepted';
  }

  private _spawn_workers(): void {
    while (this._active_workers < this._workers_count && this._running) {
      this._active_workers++;
      this._worker_loop().finally(() => {
        this._active_workers--;
      });
    }
  }

  private _trigger_work(): void {
    if (this._running && this._active_workers < this._workers_count) {
      this._spawn_workers();
    }
  }

  private async _worker_loop(): Promise<void> {
    while (this._running) {
      if (this._paused) {
        await new Promise((r) => setTimeout(r, 100));
        continue;
      }

      if (this._queue.length === 0) {
        // Idle wait
        await new Promise((r) => setTimeout(r, 20));
        if (this._queue.length === 0) break;
      }

      const item = this._queue.shift();
      if (!item) continue;

      const reading = item.reading;
      const settings = this.store.get_settings();
      const delayMs = parseFloat(settings.processing_delay_ms ?? config.PROCESSING_DELAY_MS);

      if (delayMs > 0) {
        await new Promise((r) => setTimeout(r, delayMs));
      }

      try {
        const cleaned = this.wrangler.process(reading);
        this.store.add_cleaned(cleaned);

        const forecasts = this.forecaster.update(cleaned);
        this.actuator.evaluate(cleaned, forecasts);

        const now = Date.now() / 1000;
        const latency_s = Math.max(0.0001, now - reading.received_at);
        this.store.record_latency(reading.priority, latency_s);
        this.store.incr('processed', 1, reading.priority);

        this._processed_timestamps.push(now);
        if (this._processed_timestamps.length > 2000) {
          this._processed_timestamps.shift();
        }
      } catch (err) {
        this.store.incr('errors', 1);
        this.store.log_event('critical', 'pipeline:worker', `Processing error: ${err}`);
      }
    }
  }

  snapshot(): Record<string, any> {
    const depth_by_priority: Record<string, number> = { '0': 0, '1': 0, '2': 0 };
    for (const item of this._queue) {
      const p = String(item.reading.priority);
      depth_by_priority[p] = (depth_by_priority[p] || 0) + 1;
    }

    const settings = this.store.get_settings();
    const now = Date.now() / 1000;
    const windowStart = now - 5.0;
    const recentCount = this._processed_timestamps.filter((t) => t >= windowStart).length;
    const throughput = parseFloat((recentCount / 5.0).toFixed(1));

    return {
      queue_depth: this._queue.length,
      depth_by_priority,
      mode: settings.pipeline_mode || config.PIPELINE_MODE,
      workers: this._workers_count,
      processing_delay_ms: parseFloat(settings.processing_delay_ms || config.PROCESSING_DELAY_MS),
      latency: this.store.latency_stats(),
      throughput,
      avg_wrangle_ms: parseFloat(this.wrangler.avg_ms.toFixed(2)),
    };
  }
}
