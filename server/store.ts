/**
 * Thread-safe bounded in-memory Store for IoT monitoring system.
 */
import * as config from './config';
import { Metric, deriveAllLimits, deriveLimits } from './config';
import {
  ActuationEvent,
  CleanedReading,
  Forecast,
  Level,
  Limits,
  Priority,
  Reading,
} from './models';

export class Store {
  private _profile: string;
  private _settings: Record<string, any>;
  private _registered_nodes: Set<string>;
  private _node_controls: Record<string, any>;

  // Telemetry storage
  private _raw_data: Record<string, Reading[]>;
  private _cleaned_data: Record<string, CleanedReading[]>;
  private _latest_cleaned: Record<string, CleanedReading>;
  private _latest_reading_time: Record<string, number>;
  private _latest_status: Record<string, string>;
  private _exposure: Record<
    string,
    Record<Metric, { peak: number; seconds_outside_safe: number; seconds_above_hard: number }>
  >;

  // Forecasts and errors
  private _forecasts: Record<string, Record<string, Forecast>>;
  private _forecast_errors: Record<Metric, number[]>;

  // Actuation and alert levels
  private _devices: Record<string, any>;
  private _device_on_seconds: Record<string, Record<string, number>>;
  private _device_state_since: Record<string, Record<string, number>>;
  private _levels: Record<string, Record<Metric, Level>>;
  private _level_transitions: Array<{
    ts: number;
    node_id: string;
    metric: string;
    old: string;
    new: string;
  }>;
  private _actuations: ActuationEvent[];

  // Events, counters, latencies
  private _events: Array<{ level: string; source: string; message: string; ts: number }>;
  private _counters: {
    received: number;
    processed: number;
    rejected: number;
    dropped: number;
    errors: number;
    by_priority: Record<string, number>;
  };
  private _latencies: Record<string, number[]>;

  constructor(initial_profile?: string) {
    this._profile = initial_profile || config.DEFAULT_PROFILE;
    this._settings = {
      strategy: config.STRATEGY,
      pipeline_mode: config.PIPELINE_MODE,
      workers: config.WORKER_COUNT,
      processing_delay_ms: config.PROCESSING_DELAY_MS,
      horizon_min: config.HORIZON_MIN,
      time_scale: config.SIM_TIME_SCALE,
    };

    this._registered_nodes = new Set();
    this._node_controls = {};

    this._raw_data = {};
    this._cleaned_data = {};
    this._latest_cleaned = {};
    this._latest_reading_time = {};
    this._latest_status = {};
    this._exposure = {};

    this._forecasts = {};
    this._forecast_errors = {
      temperature: [],
      humidity: [],
      aqi: [],
    };

    this._devices = {};
    this._device_on_seconds = {};
    this._device_state_since = {};
    this._levels = {};
    this._level_transitions = [];
    this._actuations = [];

    this._events = [];
    this._counters = {
      received: 0,
      processed: 0,
      rejected: 0,
      dropped: 0,
      errors: 0,
      by_priority: { '0': 0, '1': 0, '2': 0 },
    };
    this._latencies = {
      '0': [],
      '1': [],
      '2': [],
    };
  }

  // ================= Profile & Limits =================

  get_profile(): string {
    return this._profile;
  }

  set_profile(name: string): void {
    if (config.PROFILES[name]) {
      this._profile = name;
    }
  }

  limits(metric: Metric): Limits {
    return deriveLimits(this._profile, metric);
  }

  all_limits(): Record<Metric, Limits> {
    return deriveAllLimits(this._profile);
  }

  // ================= Settings =================

  get_settings(): Record<string, any> {
    return { ...this._settings };
  }

  update_settings(partial: Record<string, any>): Record<string, any> {
    this._settings = { ...this._settings, ...partial };
    return { ...this._settings };
  }

  // ================= Node Registration & Controls =================

  register_node(node_id: string, initial_controls?: Record<string, any>): void {
    this._registered_nodes.add(node_id);
    if (initial_controls) {
      this._node_controls[node_id] = { ...initial_controls };
    }
  }

  unregister_node(node_id: string): void {
    this._registered_nodes.delete(node_id);
    delete this._node_controls[node_id];
  }

  set_node_controls(node_id: string, controls: Record<string, any>): void {
    this._node_controls[node_id] = { ...controls };
  }

  get_node_controls(node_id: string): Record<string, any> | undefined {
    return this._node_controls[node_id];
  }

  node_ids(): string[] {
    return Array.from(this._registered_nodes).sort();
  }

  // ================= Telemetry Data & Exposure =================

  add_raw(reading: Reading): void {
    const node = reading.node_id;
    if (!this._raw_data[node]) {
      this._raw_data[node] = [];
    }
    this._raw_data[node].push(reading);
    if (this._raw_data[node].length > config.HISTORY_LEN) {
      this._raw_data[node].shift();
    }
    this._latest_reading_time[node] = reading.ts;
    this._latest_status[node] = reading.status;
  }

  add_cleaned(cleaned: CleanedReading): void {
    const node = cleaned.node_id;
    if (!this._cleaned_data[node]) {
      this._cleaned_data[node] = [];
    }

    const prevCleaned = this._latest_cleaned[node];
    const dt = prevCleaned ? Math.max(0.001, cleaned.ts - prevCleaned.ts) : 0.0;

    this._cleaned_data[node].push(cleaned);
    if (this._cleaned_data[node].length > config.HISTORY_LEN) {
      this._cleaned_data[node].shift();
    }
    this._latest_cleaned[node] = cleaned;
    this._latest_reading_time[node] = cleaned.ts;

    // Exposure tracking
    if (!this._exposure[node]) {
      this._exposure[node] = {
        temperature: { peak: -Infinity, seconds_outside_safe: 0, seconds_above_hard: 0 },
        humidity: { peak: -Infinity, seconds_outside_safe: 0, seconds_above_hard: 0 },
        aqi: { peak: -Infinity, seconds_outside_safe: 0, seconds_above_hard: 0 },
      };
    }

    for (const m of config.METRICS) {
      const val = cleaned.clean[m];
      if (val !== null && val !== undefined) {
        const lim = this.limits(m);
        const exp = this._exposure[node][m];
        if (val > exp.peak) {
          exp.peak = val;
        }
        if (dt > 0) {
          if (val < lim.safe_min || val > lim.safe_max) {
            exp.seconds_outside_safe += dt;
          }
          if (val >= lim.hard_max) {
            exp.seconds_above_hard += dt;
          }
        }
      }
    }
  }

  latest_cleaned(node: string): CleanedReading | null {
    return this._latest_cleaned[node] || null;
  }

  history(node: string, limit = config.CHART_POINTS): CleanedReading[] {
    const buf = this._cleaned_data[node] || [];
    return buf.slice(-limit);
  }

  exposure(node: string): Record<Metric, { peak: number; seconds_outside_safe: number; seconds_above_hard: number }> {
    if (!this._exposure[node]) {
      this._exposure[node] = {
        temperature: { peak: 0, seconds_outside_safe: 0, seconds_above_hard: 0 },
        humidity: { peak: 0, seconds_outside_safe: 0, seconds_above_hard: 0 },
        aqi: { peak: 0, seconds_outside_safe: 0, seconds_above_hard: 0 },
      };
    }
    return this._exposure[node];
  }

  // ================= Forecasts =================

  set_forecasts(node: string, forecasts: Record<string, Forecast>): void {
    this._forecasts[node] = { ...forecasts };
  }

  forecasts(node: string): Record<string, Forecast> {
    return this._forecasts[node] || {};
  }

  record_forecast_error(metric: Metric, _node: string, abs_err: number): void {
    if (!this._forecast_errors[metric]) {
      this._forecast_errors[metric] = [];
    }
    this._forecast_errors[metric].push(abs_err);
    if (this._forecast_errors[metric].length > 500) {
      this._forecast_errors[metric].shift();
    }
  }

  forecast_error_stats(): Record<Metric, { mae: number; rmse: number; n: number }> {
    const out: any = {};
    for (const m of config.METRICS) {
      const errs = this._forecast_errors[m] || [];
      if (errs.length === 0) {
        out[m] = { mae: 0.0, rmse: 0.0, n: 0 };
      } else {
        const sum = errs.reduce((a, b) => a + b, 0);
        const mae = sum / errs.length;
        const sse = errs.reduce((a, b) => a + b * b, 0);
        const rmse = Math.sqrt(sse / errs.length);
        out[m] = { mae, rmse, n: errs.length };
      }
    }
    return out;
  }

  // ================= Actuators & Levels =================

  set_devices(node: string, devices: Record<string, any>): void {
    const now = Date.now() / 1000;
    if (!this._device_on_seconds[node]) {
      this._device_on_seconds[node] = {};
      this._device_state_since[node] = {};
    }

    const prevDevices = this._devices[node] || {};
    for (const d of ['fan', 'hvac', 'dehumidifier', 'purifier']) {
      const oldState = prevDevices[d]?.state || 'off';
      const newState = devices[d]?.state || 'off';

      if (newState !== oldState) {
        if (oldState !== 'off') {
          const since = this._device_state_since[node][d] || now;
          const dt = Math.max(0, now - since);
          this._device_on_seconds[node][d] = (this._device_on_seconds[node][d] || 0) + dt;
        }
        this._device_state_since[node][d] = now;
      }
    }

    this._devices[node] = JSON.parse(JSON.stringify(devices));
  }

  devices(node: string): Record<string, any> {
    if (!this._devices[node]) {
      return {
        fan: { state: 'off', mode: 'auto' },
        hvac: { state: 'off', mode: 'auto' },
        dehumidifier: { state: 'off', mode: 'auto' },
        purifier: { state: 'off', mode: 'auto' },
        shutdown: false,
      };
    }
    return this._devices[node];
  }

  set_levels(node: string, levels: Record<Metric, Level>): void {
    this._levels[node] = { ...levels };
  }

  levels(node: string): Record<Metric, Level> {
    return (
      this._levels[node] || {
        temperature: Level.OK,
        humidity: Level.OK,
        aqi: Level.OK,
      }
    );
  }

  record_level_change(node: string, metric: string, oldLvl: Level, newLvl: Level, ts: number): void {
    this._level_transitions.push({
      ts,
      node_id: node,
      metric,
      old: Level[oldLvl],
      new: Level[newLvl],
    });
    if (this._level_transitions.length > config.EVENT_LOG_LEN) {
      this._level_transitions.shift();
    }
  }

  level_transitions(): Array<{ ts: number; node_id: string; metric: string; old: string; new: string }> {
    return [...this._level_transitions];
  }

  add_actuation(event: ActuationEvent): void {
    this._actuations.push(event);
    if (this._actuations.length > config.ACTUATION_LOG_LEN) {
      this._actuations.shift();
    }
    this.log_event(
      event.trigger === 'threshold' ? 'warn' : 'info',
      `actuator:${event.node_id}`,
      `${event.device.toUpperCase()} -> ${event.to_state.toUpperCase()} (${event.reason})`
    );
  }

  actuations(limit = 50): ActuationEvent[] {
    return this._actuations.slice(-limit);
  }

  // ================= Events & Logging =================

  log_event(level: string, source: string, message: string): void {
    this._events.push({
      level,
      source,
      message,
      ts: Date.now() / 1000,
    });
    if (this._events.length > config.EVENT_LOG_LEN) {
      this._events.shift();
    }
  }

  events(limit = 100): Array<{ level: string; source: string; message: string; ts: number }> {
    return this._events.slice(-limit);
  }

  // ================= Counters & Latency =================

  incr(name: 'received' | 'processed' | 'rejected' | 'dropped' | 'errors', n = 1, priority?: Priority): void {
    this._counters[name] = (this._counters[name] || 0) + n;
    if (priority !== undefined) {
      const pKey = String(priority);
      if (this._counters.by_priority[pKey] !== undefined) {
        this._counters.by_priority[pKey] += n;
      }
    }
  }

  counters() {
    return {
      ...this._counters,
      by_priority: { ...this._counters.by_priority },
    };
  }

  record_latency(priority: Priority | number, seconds: number): void {
    const key = String(priority);
    if (!this._latencies[key]) {
      this._latencies[key] = [];
    }
    this._latencies[key].push(seconds);
    if (this._latencies[key].length > config.LATENCY_SAMPLES) {
      this._latencies[key].shift();
    }
  }

  latency_stats(): Record<string, { avg: number; p50: number; p95: number; p99: number; n: number }> {
    const calc = (samples: number[]) => {
      if (samples.length === 0) {
        return { avg: 0, p50: 0, p95: 0, p99: 0, n: 0 };
      }
      const sorted = [...samples].sort((a, b) => a - b);
      const n = sorted.length;
      const sum = sorted.reduce((a, b) => a + b, 0);
      const percentile = (p: number) => {
        const idx = Math.min(n - 1, Math.floor((p / 100) * n));
        return sorted[idx] * 1000; // ms
      };
      return {
        avg: (sum / n) * 1000,
        p50: percentile(50),
        p95: percentile(95),
        p99: percentile(99),
        n,
      };
    };

    return {
      '0': calc(this._latencies['0'] || []),
      '1': calc(this._latencies['1'] || []),
      '2': calc(this._latencies['2'] || []),
    };
  }

  // ================= State Reset & Snapshot =================

  reset_runtime(): void {
    this._raw_data = {};
    this._cleaned_data = {};
    this._latest_cleaned = {};
    this._latest_reading_time = {};
    this._latest_status = {};
    this._exposure = {};
    this._forecasts = {};
    this._forecast_errors = { temperature: [], humidity: [], aqi: [] };
    this._devices = {};
    this._device_on_seconds = {};
    this._device_state_since = {};
    this._levels = {};
    this._level_transitions = [];
    this._actuations = [];
    this._events = [];
    this._counters = {
      received: 0,
      processed: 0,
      rejected: 0,
      dropped: 0,
      errors: 0,
      by_priority: { '0': 0, '1': 0, '2': 0 },
    };
    this._latencies = { '0': [], '1': [], '2': [] };
  }

  snapshot(): Record<string, any> {
    const now = Date.now() / 1000;
    const nodesOut: Record<string, any> = {};

    for (const nid of this.node_ids()) {
      const controls = this.get_node_controls(nid) || {};
      const latest = this.latest_cleaned(nid);
      const lastTs = this._latest_reading_time[nid] || 0;
      const isShutdown = this.devices(nid).shutdown;
      const status = isShutdown ? 'shutdown' : now - lastTs > 10 ? 'offline' : 'online';

      const lvls = this.levels(nid);
      const levelNames: Record<string, string> = {
        temperature: Level[lvls.temperature],
        humidity: Level[lvls.humidity],
        aqi: Level[lvls.aqi],
      };

      // Worst level
      const worstLevelVal = Math.max(lvls.temperature, lvls.humidity, lvls.aqi);
      const worstLevel = Level[worstLevelVal];

      // History formatted
      const hist = this.history(nid, config.CHART_POINTS);
      const tsArr: number[] = [];
      const rawMap: Record<string, Array<number | null>> = { temperature: [], humidity: [], aqi: [] };
      const smoothMap: Record<string, Array<number | null>> = { temperature: [], humidity: [], aqi: [] };

      for (const r of hist) {
        tsArr.push(r.ts);
        for (const m of config.METRICS) {
          rawMap[m].push(r.raw[m]);
          smoothMap[m].push(r.smooth[m]);
        }
      }

      nodesOut[nid] = {
        controls,
        status,
        latest,
        levels: levelNames,
        level: worstLevel,
        forecast: this.forecasts(nid),
        devices: this.devices(nid),
        history: {
          ts: tsArr,
          raw: rawMap,
          smooth: smoothMap,
        },
      };
    }

    return {
      ts: now,
      profile: this._profile,
      limits: this.all_limits(),
      settings: this.get_settings(),
      counters: this.counters(),
      nodes: nodesOut,
      events: this.events(100),
      actuations: this.actuations(50),
    };
  }
}
