/**
 * Predictive forecasting engine using Linear and Polynomial (Degree 2) Ridge regression.
 */
import * as config from './config';
import { Metric } from './config';
import { CleanedReading, Forecast, ForecastPoint } from './models';
import { Store } from './store';

// Helper: Solve 3x3 linear system A * w = v via Cramer's rule
function solve3x3(A: number[][], v: number[]): [number, number, number] {
  function det3(M: number[][]): number {
    return (
      M[0][0] * (M[1][1] * M[2][2] - M[1][2] * M[2][1]) -
      M[0][1] * (M[1][0] * M[2][2] - M[1][2] * M[2][0]) +
      M[0][2] * (M[1][0] * M[2][1] - M[1][1] * M[2][0])
    );
  }

  const d = det3(A);
  if (Math.abs(d) < 1e-12) {
    return [0, 0, 0];
  }

  const A0 = [
    [v[0], A[0][1], A[0][2]],
    [v[1], A[1][1], A[1][2]],
    [v[2], A[2][1], A[2][2]],
  ];
  const A1 = [
    [A[0][0], v[0], A[0][2]],
    [A[1][0], v[1], A[1][2]],
    [A[2][0], v[2], A[2][2]],
  ];
  const A2 = [
    [A[0][0], A[0][1], v[0]],
    [A[1][0], A[1][1], v[1]],
    [A[2][0], A[2][1], v[2]],
  ];

  return [det3(A0) / d, det3(A1) / d, det3(A2) / d];
}

export class Forecaster {
  private store: Store;
  private _buffers: Record<string, Array<[number, number]>> = {};
  private _counters: Record<string, number> = {};
  private _cache: Record<string, Forecast> = {};
  private _pending_evals: Record<string, Array<{ target_ts: number; predicted_end: number }>> = {};

  constructor(store: Store) {
    this.store = store;
    this.reset();
  }

  reset(): void {
    this._buffers = {};
    this._counters = {};
    this._cache = {};
    this._pending_evals = {};
  }

  private _check_evaluations(node_id: string, ts: number, smooth: Record<string, number | null>): void {
    for (const m of config.METRICS) {
      const actual = smooth[m];
      if (actual === null || actual === undefined) continue;

      const key = `${node_id}:${m}`;
      const pending = this._pending_evals[key];
      if (!pending || pending.length === 0) continue;

      const remaining: Array<{ target_ts: number; predicted_end: number }> = [];
      for (const item of pending) {
        if (ts >= item.target_ts) {
          const abs_err = Math.abs(actual - item.predicted_end);
          this.store.record_forecast_error(m, node_id, abs_err);
        } else {
          remaining.push(item);
        }
      }
      this._pending_evals[key] = remaining;
    }
  }

  update(cleaned: CleanedReading): Record<string, Forecast> {
    const node_id = cleaned.node_id;
    const ts = cleaned.ts;
    const smooth_dict = cleaned.smooth;

    this._check_evaluations(node_id, ts, smooth_dict);

    const settings = this.store.get_settings();
    let time_scale = parseFloat(settings.time_scale || config.SIM_TIME_SCALE);
    if (time_scale <= 0) time_scale = 60.0;
    const horizon_min = parseFloat(settings.horizon_min || config.HORIZON_MIN);

    const forecasts: Record<string, Forecast> = {};

    for (const m of config.METRICS) {
      const smooth_val = smooth_dict[m];
      if (smooth_val === null || smooth_val === undefined) continue;

      const key = `${node_id}:${m}`;
      if (!this._buffers[key]) {
        this._buffers[key] = [];
        this._counters[key] = 0;
      }

      this._buffers[key].push([ts, smooth_val]);
      if (this._buffers[key].length > config.WINDOW_POINTS) {
        this._buffers[key].shift();
      }

      this._counters[key] += 1;
      const buf = this._buffers[key];

      if (buf.length < config.MIN_POINTS) continue;

      const cached = this._cache[key];
      const is_horizon_changed = cached && cached.horizon_min !== horizon_min;
      if (cached && !is_horizon_changed && this._counters[key] % config.RETRAIN_EVERY !== 0) {
        forecasts[m] = cached;
        continue;
      }

      const t_latest = buf[buf.length - 1][0];
      const X_all = buf.map(([t]) => (t - t_latest) * (time_scale / 60.0));
      const y_all = buf.map(([, v]) => v);

      const n = X_all.length;
      const n_holdout = Math.max(2, Math.round(n * 0.2));
      const n_train = n - n_holdout;

      const X_train = X_all.slice(0, n_train);
      const y_train = y_all.slice(0, n_train);
      const X_hold = X_all.slice(n_train);
      const y_hold = y_all.slice(n_train);

      // 1. Linear regression on train
      let sumX = 0,
        sumY = 0,
        sumXY = 0,
        sumXX = 0;
      for (let i = 0; i < n_train; i++) {
        const x = X_train[i];
        const y = y_train[i];
        sumX += x;
        sumY += y;
        sumXY += x * y;
        sumXX += x * x;
      }
      const denomLin = n_train * sumXX - sumX * sumX;
      const slopeLin = denomLin !== 0 ? (n_train * sumXY - sumX * sumY) / denomLin : 0;
      const interceptLin = (sumY - slopeLin * sumX) / n_train;

      const linHoldPred = X_hold.map((x) => slopeLin * x + interceptLin);
      const sseLin = linHoldPred.reduce((acc, p, i) => acc + (p - y_hold[i]) ** 2, 0);
      const rmseLin = Math.sqrt(sseLin / n_holdout);

      // 2. Polynomial Degree 2 + Ridge (alpha = 1.0) on train: y = a * x^2 + b * x + c
      // Form X^T * X + alpha * I
      let sX4 = 0,
        sX3 = 0,
        sX2 = 0,
        sX1 = 0,
        sX0 = n_train;
      let syX2 = 0,
        syX1 = 0,
        syX0 = 0;
      for (let i = 0; i < n_train; i++) {
        const x = X_train[i];
        const y = y_train[i];
        const x2 = x * x;
        sX4 += x2 * x2;
        sX3 += x2 * x;
        sX2 += x2;
        sX1 += x;
        syX2 += y * x2;
        syX1 += y * x;
        syX0 += y;
      }
      const alpha = 1.0;
      const A_train = [
        [sX4 + alpha, sX3, sX2],
        [sX3, sX2 + alpha, sX1],
        [sX2, sX1, sX0 + alpha],
      ];
      const v_train = [syX2, syX1, syX0];
      const [w2_tr, w1_tr, w0_tr] = solve3x3(A_train, v_train);

      const polyHoldPred = X_hold.map((x) => w2_tr * x * x + w1_tr * x + w0_tr);
      const ssePoly = polyHoldPred.reduce((acc, p, i) => acc + (p - y_hold[i]) ** 2, 0);
      const rmsePoly = Math.sqrt(ssePoly / n_holdout);

      // Model Selection
      let chosen_model_name: 'linear' | 'poly2';
      let predict_fn: (x: number) => number;
      let y_hold_pred: number[];

      if (rmsePoly < rmseLin) {
        chosen_model_name = 'poly2';
        let fsX4 = 0,
          fsX3 = 0,
          fsX2 = 0,
          fsX1 = 0,
          fsX0 = n;
        let fsyX2 = 0,
          fsyX1 = 0,
          fsyX0 = 0;
        for (let i = 0; i < n; i++) {
          const x = X_all[i];
          const y = y_all[i];
          const x2 = x * x;
          fsX4 += x2 * x2;
          fsX3 += x2 * x;
          fsX2 += x2;
          fsX1 += x;
          fsyX2 += y * x2;
          fsyX1 += y * x;
          fsyX0 += y;
        }
        const A_full = [
          [fsX4 + alpha, fsX3, fsX2],
          [fsX3, fsX2 + alpha, fsX1],
          [fsX2, fsX1, fsX0 + alpha],
        ];
        const v_full = [fsyX2, fsyX1, fsyX0];
        const [w2_f, w1_f, w0_f] = solve3x3(A_full, v_full);
        predict_fn = (x: number) => w2_f * x * x + w1_f * x + w0_f;
        y_hold_pred = polyHoldPred;
      } else {
        chosen_model_name = 'linear';
        let fSumX = 0,
          fSumY = 0,
          fSumXY = 0,
          fSumXX = 0;
        for (let i = 0; i < n; i++) {
          const x = X_all[i];
          const y = y_all[i];
          fSumX += x;
          fSumY += y;
          fSumXY += x * y;
          fSumXX += x * x;
        }
        const fDenom = n * fSumXX - fSumX * fSumX;
        const fSlope = fDenom !== 0 ? (n * fSumXY - fSumX * fSumY) / fDenom : 0;
        const fIntercept = (fSumY - fSlope * fSumX) / n;
        predict_fn = (x: number) => fSlope * x + fIntercept;
        y_hold_pred = linHoldPred;
      }

      // Residual std & R2
      const residuals = X_all.map((x, i) => y_all[i] - predict_fn(x));
      const resMean = residuals.reduce((a, b) => a + b, 0) / n;
      const resVar = residuals.reduce((a, b) => a + (b - resMean) ** 2, 0) / n;
      const residual_std = Math.sqrt(resVar);

      const meanHold = y_hold.reduce((a, b) => a + b, 0) / n_holdout;
      const ssTot = y_hold.reduce((a, b) => a + (b - meanHold) ** 2, 0);
      const ssRes = y_hold.reduce((a, b, i) => a + (b - y_hold_pred[i]) ** 2, 0);
      const r2_hold = ssTot > 0 ? 1.0 - ssRes / ssTot : 0.0;

      // Confidence
      const limits = this.store.limits(m);
      const safe_span = Math.max(1.0, limits.safe_max - limits.safe_min);
      const noise_ratio = Math.min(1.0, residual_std / safe_span);
      const conf_val = 0.5 * Math.max(0.0, r2_hold) + 0.5 * (1.0 - noise_ratio);
      const confidence = Math.max(0.0, Math.min(1.0, conf_val));

      const [phys_min, phys_max] = config.PHYSICAL[m];

      // Points every STEP_MIN sim-minutes
      const points: ForecastPoint[] = [];
      const lower: number[] = [];
      const upper: number[] = [];

      for (let s_min = config.STEP_MIN; s_min <= horizon_min + 1e-4; s_min += config.STEP_MIN) {
        const raw_pred = predict_fn(s_min);
        const val_clamped = Math.max(phys_min, Math.min(phys_max, raw_pred));
        const low_clamped = Math.max(phys_min, Math.min(phys_max, raw_pred - 1.96 * residual_std));
        const up_clamped = Math.max(phys_min, Math.min(phys_max, raw_pred + 1.96 * residual_std));

        points.push({ sim_min: parseFloat(s_min.toFixed(2)), value: val_clamped });
        lower.push(low_clamped);
        upper.push(up_clamped);
      }

      const predicted_end = Math.max(phys_min, Math.min(phys_max, predict_fn(horizon_min)));

      // ETA evaluation at 1 sim-minute resolution
      let eta_safe_min: number | null = null;
      let eta_hard_min: number | null = null;

      for (let minute = 0; minute <= Math.floor(horizon_min); minute++) {
        const p_val = Math.max(phys_min, Math.min(phys_max, predict_fn(minute)));
        const is_safe_breach = p_val < limits.safe_min || p_val > limits.safe_max;
        const is_hard_breach = p_val < limits.hard_min || p_val > limits.hard_max;

        if (eta_safe_min === null && is_safe_breach) {
          eta_safe_min = minute;
        }
        if (eta_hard_min === null && is_hard_breach) {
          eta_hard_min = minute;
        }
      }

      const f: Forecast = {
        node_id,
        metric: m,
        made_at: ts,
        horizon_min,
        points,
        lower,
        upper,
        predicted_end,
        eta_safe_min,
        eta_hard_min,
        confidence,
        model: chosen_model_name,
      };

      this._cache[key] = f;
      forecasts[m] = f;

      const real_seconds = (horizon_min * 60.0) / time_scale;
      const target_real_ts = ts + real_seconds;
      if (!this._pending_evals[key]) {
        this._pending_evals[key] = [];
      }
      this._pending_evals[key].push({
        target_ts: target_real_ts,
        predicted_end,
      });
    }

    if (Object.keys(forecasts).length > 0) {
      this.store.set_forecasts(node_id, forecasts);
    }
    return forecasts;
  }
}
