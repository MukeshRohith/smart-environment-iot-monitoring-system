/**
 * Data wrangling engine: sanitization, imputation, outlier rejection, smoothing, and feature calculation.
 */
import * as config from './config';
import { Metric } from './config';
import { CleanedReading, Reading } from './models';
import { Store } from './store';

export class Wrangler {
  private store: Store;
  private _total_time_s = 0.0;
  private _total_count = 0;

  private _consecutive_missing: Record<string, Record<Metric, number>> = {};
  private _last_clean: Record<string, Record<Metric, number>> = {};
  private _prev_smooth: Record<string, Record<Metric, number>> = {};
  private _deviating_count: Record<string, Record<Metric, number>> = {};
  private _deviating_dir: Record<string, Record<Metric, number>> = {};
  private _clean_buffers: Record<string, Record<Metric, number[]>> = {};
  private _ts_buffers: Record<string, number[]> = {};
  private _imputed_buffers: Record<string, Record<Metric, number[]>> = {};

  constructor(store: Store) {
    this.store = store;
    this.reset();
  }

  get avg_ms(): number {
    if (this._total_count === 0) return 0.0;
    return (this._total_time_s / this._total_count) * 1000.0;
  }

  reset(): void {
    this._consecutive_missing = {};
    this._last_clean = {};
    this._prev_smooth = {};
    this._deviating_count = {};
    this._deviating_dir = {};
    this._clean_buffers = {};
    this._ts_buffers = {};
    this._imputed_buffers = {};
    this._total_time_s = 0.0;
    this._total_count = 0;
  }

  private _init_node(node_id: string): void {
    if (!this._clean_buffers[node_id]) {
      this._consecutive_missing[node_id] = { temperature: 0, humidity: 0, aqi: 0 };
      this._last_clean[node_id] = {} as any;
      this._prev_smooth[node_id] = {} as any;
      this._deviating_count[node_id] = { temperature: 0, humidity: 0, aqi: 0 };
      this._deviating_dir[node_id] = { temperature: 0, humidity: 0, aqi: 0 };
      this._clean_buffers[node_id] = { temperature: [], humidity: [], aqi: [] };
      this._ts_buffers[node_id] = [];
      this._imputed_buffers[node_id] = { temperature: [], humidity: [], aqi: [] };
    }
  }

  process(reading: Reading): CleanedReading {
    const t_start = performance.now();
    const node_id = reading.node_id;
    this._init_node(node_id);

    const clean_bufs = this._clean_buffers[node_id];
    const ts_buf = this._ts_buffers[node_id];

    const raw_dict: Record<string, number | null> = {};
    const clean_dict: Record<string, number | null> = {};
    const smooth_dict: Record<string, number | null> = {};
    const flags_dict: Record<string, string[]> = { temperature: [], humidity: [], aqi: [] };
    const imputed_flags: Record<string, boolean> = { temperature: false, humidity: false, aqi: false };
    const rolling_dict: Record<string, { mean: number; std: number; min: number; max: number; slope_per_s: number }> =
      {};

    const default_limits = this.store ? this.store.all_limits() : config.deriveAllLimits(config.DEFAULT_PROFILE);

    for (const m of config.METRICS) {
      let raw_val = reading[m];
      const flags: string[] = [];
      let is_imputed = false;
      raw_dict[m] = raw_val;

      // 1. Plausibility Check against config.PHYSICAL
      const [phys_min, phys_max] = config.PHYSICAL[m];
      if (raw_val !== null) {
        if (raw_val < phys_min || raw_val > phys_max) {
          raw_val = null;
          flags.push('implausible');
          is_imputed = true;
        }
      }

      // 2. Missing Values Imputation
      let val: number;
      if (raw_val === null) {
        is_imputed = true;
        this._consecutive_missing[node_id][m] += 1;
        const miss_count = this._consecutive_missing[node_id][m];

        if (miss_count <= config.MAX_FFILL) {
          if (this._last_clean[node_id][m] !== undefined) {
            val = this._last_clean[node_id][m];
          } else {
            const lim = default_limits[m];
            val = (lim.safe_min + lim.safe_max) / 2.0;
          }
          flags.push('missing_filled');
        } else {
          const buf = clean_bufs[m];
          if (buf.length > 0) {
            const windowSlice = buf.slice(-config.WINDOW);
            val = windowSlice.reduce((a, b) => a + b, 0) / windowSlice.length;
          } else if (this._last_clean[node_id][m] !== undefined) {
            val = this._last_clean[node_id][m];
          } else {
            const lim = default_limits[m];
            val = (lim.safe_min + lim.safe_max) / 2.0;
          }
          flags.push('stale');
        }
      } else {
        this._consecutive_missing[node_id][m] = 0;
        val = raw_val;
      }

      // 3. Outlier Detection via Rolling Median and MAD
      const buf = clean_bufs[m];
      if (buf.length >= 5) {
        const windowClean = buf.slice(-config.WINDOW);
        const sorted = [...windowClean].sort((a, b) => a - b);
        const midIdx = Math.floor(sorted.length / 2);
        const med = sorted.length % 2 === 0 ? (sorted[midIdx - 1] + sorted[midIdx]) / 2 : sorted[midIdx];

        const diffs = windowClean.map((v) => Math.abs(v - med)).sort((a, b) => a - b);
        const mad = diffs.length % 2 === 0 ? (diffs[midIdx - 1] + diffs[midIdx]) / 2 : diffs[midIdx];

        const mad_effective = Math.max(mad, config.MAD_FLOOR[m]);
        const threshold = config.MAD_K * 1.4826 * mad_effective;
        const diff = val - med;

        if (Math.abs(diff) > threshold) {
          const current_dir = diff > 0 ? 1 : -1;
          if (current_dir === this._deviating_dir[node_id][m]) {
            this._deviating_count[node_id][m] += 1;
          } else {
            this._deviating_dir[node_id][m] = current_dir;
            this._deviating_count[node_id][m] = 1;
          }

          if (this._deviating_count[node_id][m] >= config.PERSIST_N) {
            // Genuine level shift accepted
            this._deviating_count[node_id][m] = 0;
            this._deviating_dir[node_id][m] = 0;
          } else {
            val = med;
            flags.push('outlier_replaced');
            is_imputed = true;
          }
        } else {
          this._deviating_count[node_id][m] = 0;
          this._deviating_dir[node_id][m] = 0;
        }
      }

      const clean_val = val;
      clean_dict[m] = clean_val;
      this._last_clean[node_id][m] = clean_val;

      buf.push(clean_val);
      if (buf.length > config.DF_MAX_ROWS) {
        buf.shift();
      }

      // 4. Exponential Moving Average Smoothing
      const prev_s = this._prev_smooth[node_id][m];
      let smooth_val: number;
      if (prev_s === undefined) {
        smooth_val = clean_val;
      } else {
        smooth_val = config.EMA_ALPHA * clean_val + (1.0 - config.EMA_ALPHA) * prev_s;
      }
      this._prev_smooth[node_id][m] = smooth_val;
      smooth_dict[m] = smooth_val;

      flags_dict[m] = flags;
      imputed_flags[m] = is_imputed;

      this._imputed_buffers[node_id][m].push(is_imputed ? 1 : 0);
      if (this._imputed_buffers[node_id][m].length > config.DF_MAX_ROWS) {
        this._imputed_buffers[node_id][m].shift();
      }
    }

    ts_buf.push(reading.ts);
    if (ts_buf.length > config.DF_MAX_ROWS) {
      ts_buf.shift();
    }

    // Rolling Statistics
    const windowLen = Math.min(ts_buf.length, config.WINDOW);
    const windowTs = ts_buf.slice(-windowLen);
    const dt_span = windowTs.length > 1 ? windowTs[windowTs.length - 1] - windowTs[0] : 0.0;

    for (const m of config.METRICS) {
      const windowClean = clean_bufs[m].slice(-windowLen);
      const n = windowClean.length;
      const sum = windowClean.reduce((a, b) => a + b, 0);
      const mean_v = n > 0 ? sum / n : 0;
      const variance = n > 0 ? windowClean.reduce((a, b) => a + (b - mean_v) ** 2, 0) / n : 0;
      const std_v = Math.sqrt(variance);
      const min_v = n > 0 ? Math.min(...windowClean) : 0;
      const max_v = n > 0 ? Math.max(...windowClean) : 0;

      let slope_per_s = 0.0;
      if (n >= 2 && dt_span > 1e-4) {
        const t0 = windowTs[0];
        let sumX = 0;
        let sumY = 0;
        let sumXY = 0;
        let sumXX = 0;
        for (let i = 0; i < n; i++) {
          const x = windowTs[i] - t0;
          const y = windowClean[i];
          sumX += x;
          sumY += y;
          sumXY += x * y;
          sumXX += x * x;
        }
        const denom = n * sumXX - sumX * sumX;
        slope_per_s = denom !== 0 ? (n * sumXY - sumX * sumY) / denom : 0.0;
      }

      rolling_dict[m] = {
        mean: mean_v,
        std: std_v,
        min: min_v,
        max: max_v,
        slope_per_s,
      };
    }

    // Quality Calculation
    let imputedCount = 0;
    for (const m of config.METRICS) {
      const impBuf = this._imputed_buffers[node_id][m].slice(-windowLen);
      imputedCount += impBuf.reduce((a, b) => a + b, 0);
    }
    const totalEvals = windowLen * config.METRICS.length;
    const penalty = totalEvals > 0 ? imputedCount / totalEvals : 0.0;
    const quality = Math.max(0.0, Math.min(1.0, 1.0 - penalty));

    const processed_at = Date.now() / 1000;
    const elapsed_s = (performance.now() - t_start) / 1000.0;
    this._total_time_s += elapsed_s;
    this._total_count += 1;

    return {
      node_id,
      ts: reading.ts,
      seq: reading.seq,
      priority: reading.priority,
      raw: raw_dict,
      clean: clean_dict,
      smooth: smooth_dict,
      flags: flags_dict,
      rolling: rolling_dict,
      quality,
      status: reading.status,
      processed_at,
      received_at: reading.received_at,
    };
  }
}
