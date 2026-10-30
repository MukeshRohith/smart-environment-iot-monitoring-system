"""Data wrangling pipeline: sanitization, imputation, outlier rejection, smoothing, and feature calculation."""
from __future__ import annotations

import time
from typing import Any

import numpy as np
import pandas as pd

import config
from core.models import CleanedReading, Reading


class Wrangler:
    """Incremental telemetry sanitization and rolling analytics engine."""

    def __init__(self, store: Any = None) -> None:
        """Initialize per-node state structures and metrics."""
        self.store = store
        self._total_time_s: float = 0.0
        self._total_count: int = 0
        self._cols: list[str] = ["ts"]
        for m in config.METRICS:
            self._cols.extend([f"{m}_raw", f"{m}_clean", f"{m}_smooth", f"{m}_imputed"])
        self._cols.append("quality")
        self.reset()

    @property
    def avg_ms(self) -> float:
        """Average reading processing time in milliseconds."""
        if self._total_count == 0:
            return 0.0
        return (self._total_time_s / self._total_count) * 1000.0

    def reset(self) -> None:
        """Clear all historical windows, filters, and state."""
        self._node_dfs: dict[str, pd.DataFrame] = {}
        self._node_arrs: dict[str, np.ndarray] = {}
        self._node_counts: dict[str, int] = {}
        self._consecutive_missing: dict[str, dict[str, int]] = {}
        self._last_clean: dict[str, dict[str, float]] = {}
        self._prev_smooth: dict[str, dict[str, float]] = {}
        self._deviating_count: dict[str, dict[str, int]] = {}
        self._deviating_dir: dict[str, dict[str, int]] = {}
        self._clean_buffers: dict[str, dict[str, list[float]]] = {}
        self._total_time_s = 0.0
        self._total_count = 0

    def _init_node(self, node_id: str) -> None:
        """Initialize node tracking containers if not present."""
        if node_id not in self._node_arrs:
            self._node_arrs[node_id] = np.zeros((config.DF_MAX_ROWS, len(self._cols)), dtype=float)
            self._node_counts[node_id] = 0
            self._node_dfs[node_id] = pd.DataFrame(columns=self._cols)
            self._consecutive_missing[node_id] = {m: 0 for m in config.METRICS}
            self._last_clean[node_id] = {}
            self._prev_smooth[node_id] = {}
            self._deviating_count[node_id] = {m: 0 for m in config.METRICS}
            self._deviating_dir[node_id] = {m: 0 for m in config.METRICS}
            self._clean_buffers[node_id] = {m: [] for m in config.METRICS}

    def process(self, reading: Reading) -> CleanedReading:
        """Process a raw Reading into a sanitized CleanedReading."""
        t_start = time.perf_counter()
        node_id = reading.node_id
        self._init_node(node_id)
        clean_bufs = self._clean_buffers[node_id]

        raw_dict: dict[str, float | None] = {}
        clean_dict: dict[str, float | None] = {}
        smooth_dict: dict[str, float | None] = {}
        flags_dict: dict[str, list[str]] = {m: [] for m in config.METRICS}
        imputed_flags: dict[str, bool] = {m: False for m in config.METRICS}
        rolling_dict: dict[str, dict[str, float]] = {}

        default_limits = (
            self.store.all_limits() if self.store else config.derive_all_limits(config.DEFAULT_PROFILE)
        )

        for m in config.METRICS:
            raw_val = getattr(reading, m)
            flags: list[str] = []
            is_imputed = False
            raw_dict[m] = raw_val

            # 1. Plausibility Check against config.PHYSICAL
            phys_min, phys_max = config.PHYSICAL[m]
            if raw_val is not None:
                if raw_val < phys_min or raw_val > phys_max:
                    raw_val = None
                    flags.append("implausible")
                    is_imputed = True

            # 2. Missing Values Imputation
            if raw_val is None:
                is_imputed = True
                self._consecutive_missing[node_id][m] += 1
                miss_count = self._consecutive_missing[node_id][m]

                if miss_count <= config.MAX_FFILL:
                    if m in self._last_clean[node_id]:
                        val = self._last_clean[node_id][m]
                    else:
                        lim = default_limits[m]
                        val = (lim.safe_min + lim.safe_max) / 2.0
                    flags.append("missing_filled")
                else:
                    buf = clean_bufs[m]
                    if len(buf) > 0:
                        val = float(np.mean(buf[-config.WINDOW:]))
                    elif m in self._last_clean[node_id]:
                        val = self._last_clean[node_id][m]
                    else:
                        lim = default_limits[m]
                        val = (lim.safe_min + lim.safe_max) / 2.0
                    flags.append("stale")
            else:
                self._consecutive_missing[node_id][m] = 0
                val = float(raw_val)

            # 3. Outlier Detection via Rolling Median and MAD
            buf = clean_bufs[m]
            if len(buf) >= 5:
                window_clean = np.array(buf[-config.WINDOW:], dtype=float)
                med = float(np.median(window_clean))
                mad = float(np.median(np.abs(window_clean - med)))
                mad_effective = max(mad, config.MAD_FLOOR[m])
                threshold = config.MAD_K * 1.4826 * mad_effective
                diff = val - med

                if abs(diff) > threshold:
                    current_dir = 1 if diff > 0 else -1
                    if current_dir == self._deviating_dir[node_id][m]:
                        self._deviating_count[node_id][m] += 1
                    else:
                        self._deviating_dir[node_id][m] = current_dir
                        self._deviating_count[node_id][m] = 1

                    if self._deviating_count[node_id][m] >= config.PERSIST_N:
                        # Genuine level shift accepted
                        self._deviating_count[node_id][m] = 0
                        self._deviating_dir[node_id][m] = 0
                    else:
                        val = med
                        flags.append("outlier_replaced")
                        is_imputed = True
                else:
                    self._deviating_count[node_id][m] = 0
                    self._deviating_dir[node_id][m] = 0

            clean_val = val
            clean_dict[m] = clean_val
            self._last_clean[node_id][m] = clean_val

            buf.append(clean_val)
            if len(buf) > config.DF_MAX_ROWS:
                del buf[:-config.DF_MAX_ROWS]

            # 4. Exponential Moving Average Smoothing
            prev_s = self._prev_smooth[node_id].get(m)
            if prev_s is None:
                smooth_val = clean_val
            else:
                smooth_val = config.EMA_ALPHA * clean_val + (1.0 - config.EMA_ALPHA) * prev_s
            self._prev_smooth[node_id][m] = smooth_val
            smooth_dict[m] = smooth_val

            flags_dict[m] = flags
            imputed_flags[m] = is_imputed

        # 5. Incremental Append to Window Buffer
        arr = self._node_arrs[node_id]
        count = self._node_counts[node_id]

        row_vals: list[float] = [float(reading.ts)]
        for m in config.METRICS:
            r_val = raw_dict[m]
            row_vals.extend([
                float(r_val) if r_val is not None else float("nan"),
                float(clean_dict[m]),
                float(smooth_dict[m]),
                1.0 if imputed_flags[m] else 0.0,
            ])
        row_vals.append(1.0)  # quality placeholder

        if count < config.DF_MAX_ROWS:
            arr[count] = row_vals
            count += 1
        else:
            arr[:-1] = arr[1:]
            arr[-1] = row_vals
        self._node_counts[node_id] = count

        # Expose incremental DataFrame bounded by DF_MAX_ROWS
        df = pd.DataFrame(arr[:count], columns=self._cols)
        self._node_dfs[node_id] = df

        # 6. Rolling Statistics & Slope per Second
        window_df = df.iloc[-config.WINDOW:]
        ts_arr = window_df["ts"].to_numpy(dtype=float)
        dt_span = ts_arr[-1] - ts_arr[0] if len(ts_arr) > 1 else 0.0

        for m in config.METRICS:
            vals = window_df[f"{m}_clean"].to_numpy(dtype=float)
            mean_v = float(np.mean(vals))
            std_v = float(np.std(vals))
            min_v = float(np.min(vals))
            max_v = float(np.max(vals))

            slope_per_s = 0.0
            if len(vals) >= 2 and dt_span > 1e-4:
                t_rel = ts_arr - ts_arr[0]
                slope_per_s = float(np.polyfit(t_rel, vals, 1)[0])

            rolling_dict[m] = {
                "mean": mean_v,
                "std": std_v,
                "min": min_v,
                "max": max_v,
                "slope_per_s": slope_per_s,
            }

        # 7. Quality Calculation
        imputed_cols = [f"{m}_imputed" for m in config.METRICS]
        total_evals = len(window_df) * len(config.METRICS)
        imputed_count = float(window_df[imputed_cols].sum().sum())
        penalty = imputed_count / total_evals if total_evals > 0 else 0.0
        quality = float(max(0.0, min(1.0, 1.0 - penalty)))

        arr[count - 1, -1] = quality
        df.iat[count - 1, -1] = quality

        processed_at = time.time()
        elapsed = time.perf_counter() - t_start
        self._total_time_s += elapsed
        self._total_count += 1

        return CleanedReading(
            node_id=node_id,
            ts=reading.ts,
            seq=reading.seq,
            priority=reading.priority,
            raw=raw_dict,
            clean=clean_dict,
            smooth=smooth_dict,
            flags=flags_dict,
            rolling=rolling_dict,
            quality=quality,
            status=reading.status,
            processed_at=processed_at,
            received_at=reading.received_at,
        )
