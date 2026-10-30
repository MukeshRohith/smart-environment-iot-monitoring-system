"""Predictive forecasting engine using scikit-learn LinearRegression and Ridge polynomial regression."""
from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.metrics import r2_score
from sklearn.preprocessing import PolynomialFeatures

import config
from core.models import CleanedReading, Forecast


class Forecaster:
    """Predictive trajectory forecaster with model selection and error tracking."""

    def __init__(self, store: Any) -> None:
        """Initialize forecasting engine attached to a central Store."""
        self.store = store
        self.reset()

    def reset(self) -> None:
        """Clear all historical series, cached forecasts, and evaluation queues."""
        self._buffers: dict[tuple[str, str], list[tuple[float, float]]] = {}
        self._counters: dict[tuple[str, str], int] = {}
        self._cache: dict[tuple[str, str], Forecast] = {}
        self._pending_evals: dict[tuple[str, str], list[dict[str, float]]] = {}

    def _check_evaluations(self, node_id: str, ts: float, smooth: dict[str, float | None]) -> None:
        """Evaluate matured forecasts against actual cleaned smooth values."""
        for m in config.METRICS:
            actual = smooth.get(m)
            if actual is None:
                continue

            key = (node_id, m)
            pending = self._pending_evals.get(key, [])
            if not pending:
                continue

            remaining: list[dict[str, float]] = []
            for item in pending:
                if ts >= item["target_ts"]:
                    abs_err = abs(float(actual) - item["predicted_end"])
                    self.store.record_forecast_error(m, node_id, abs_err)
                else:
                    remaining.append(item)
            self._pending_evals[key] = remaining

    def update(self, cleaned: CleanedReading) -> dict[str, Forecast]:
        """Ingest CleanedReading, evaluate pending targets, and produce metric forecasts."""
        node_id = cleaned.node_id
        ts = cleaned.ts
        smooth_dict = cleaned.smooth

        # 1. Evaluate any forecasts whose horizon real timestamp has arrived
        self._check_evaluations(node_id, ts, smooth_dict)

        settings = self.store.get_settings()
        time_scale = float(settings.get("time_scale", config.SIM_TIME_SCALE))
        if time_scale <= 0:
            time_scale = 60.0
        horizon_min = float(settings.get("horizon_min", config.HORIZON_MIN))

        forecasts: dict[str, Forecast] = {}

        for m in config.METRICS:
            smooth_val = smooth_dict.get(m)
            if smooth_val is None:
                continue

            key = (node_id, m)
            if key not in self._buffers:
                self._buffers[key] = []
                self._counters[key] = 0

            self._buffers[key].append((ts, float(smooth_val)))
            if len(self._buffers[key]) > config.WINDOW_POINTS:
                self._buffers[key].pop(0)

            self._counters[key] += 1
            buf = self._buffers[key]

            # Require minimum points
            if len(buf) < config.MIN_POINTS:
                continue

            # Throttling check: if horizon changed, must refit to update points
            cached = self._cache.get(key)
            is_horizon_changed = cached is not None and cached.horizon_min != horizon_min
            if (
                cached is not None
                and not is_horizon_changed
                and (self._counters[key] % config.RETRAIN_EVERY != 0)
            ):
                forecasts[m] = cached
                continue

            # Build relative time axis in simulated minutes
            # Latest reading is at rel_sec = 0 -> sim_min = 0
            t_latest = buf[-1][0]
            ts_series = np.array([pt[0] for pt in buf], dtype=float)
            y = np.array([pt[1] for pt in buf], dtype=float)

            sim_min = (ts_series - t_latest) * (time_scale / 60.0)
            X = sim_min.reshape(-1, 1)

            # Holdout evaluation on most recent 20%
            n = len(X)
            n_holdout = max(2, int(round(n * 0.20)))
            n_train = n - n_holdout

            X_train, y_train = X[:n_train], y[:n_train]
            X_hold, y_hold = X[n_train:], y[n_train:]

            # Model 1: Linear Regression
            lin_model = LinearRegression()
            lin_model.fit(X_train, y_train)
            lin_pred_hold = lin_model.predict(X_hold)
            rmse_lin = float(np.sqrt(np.mean((y_hold - lin_pred_hold) ** 2)))

            # Model 2: Polynomial Degree 2 + Ridge
            poly = PolynomialFeatures(degree=2, include_bias=False)
            X_poly_train = poly.fit_transform(X_train)
            X_poly_hold = poly.transform(X_hold)

            ridge_model = Ridge(alpha=1.0)
            ridge_model.fit(X_poly_train, y_train)
            poly_pred_hold = ridge_model.predict(X_poly_hold)
            rmse_poly = float(np.sqrt(np.mean((y_hold - poly_pred_hold) ** 2)))

            # Model Selection
            if rmse_poly < rmse_lin:
                chosen_model_name = "poly2"
                final_poly = PolynomialFeatures(degree=2, include_bias=False)
                X_full_poly = final_poly.fit_transform(X)
                final_reg = Ridge(alpha=1.0)
                final_reg.fit(X_full_poly, y)

                def predict_fn(x_vals: np.ndarray) -> np.ndarray:
                    return final_reg.predict(final_poly.transform(x_vals.reshape(-1, 1)))

                y_hold_pred = poly_pred_hold
            else:
                chosen_model_name = "linear"
                final_reg = LinearRegression()
                final_reg.fit(X, y)

                def predict_fn(x_vals: np.ndarray) -> np.ndarray:
                    return final_reg.predict(x_vals.reshape(-1, 1))

                y_hold_pred = lin_pred_hold

            # Residual standard deviation & R2
            full_pred = predict_fn(sim_min)
            residuals = y - full_pred
            residual_std = float(np.std(residuals))

            r2_hold = float(r2_score(y_hold, y_hold_pred)) if len(y_hold) >= 2 else 0.0

            # Confidence calculation
            limits = self.store.limits(m)
            safe_span = limits.safe_max - limits.safe_min
            if safe_span <= 0:
                safe_span = 1.0
            noise_ratio = min(1.0, residual_std / safe_span)
            conf_val = 0.5 * max(r2_hold, 0.0) + 0.5 * (1.0 - noise_ratio)
            confidence = float(np.clip(conf_val, 0.0, 1.0))

            # Clamping bounds
            phys_min, phys_max = config.PHYSICAL[m]

            # Generate forecast points every STEP_MIN sim-minutes
            step = config.STEP_MIN
            grid_sim_min = np.arange(step, horizon_min + 1e-4, step)
            pred_grid = predict_fn(grid_sim_min)

            points: list[dict[str, float]] = []
            lower: list[float] = []
            upper: list[float] = []

            for s_min, p_val in zip(grid_sim_min, pred_grid):
                val_clamped = float(np.clip(p_val, phys_min, phys_max))
                low_clamped = float(np.clip(p_val - 1.96 * residual_std, phys_min, phys_max))
                up_clamped = float(np.clip(p_val + 1.96 * residual_std, phys_min, phys_max))
                points.append({"sim_min": float(round(s_min, 2)), "value": val_clamped})
                lower.append(low_clamped)
                upper.append(up_clamped)

            predicted_end = float(np.clip(predict_fn(np.array([horizon_min]))[0], phys_min, phys_max))

            # ETA evaluation at 1 sim-minute resolution
            eta_grid = np.arange(0, int(horizon_min) + 1, 1.0)
            eta_preds = np.clip(predict_fn(eta_grid), phys_min, phys_max)

            eta_safe_min: float | None = None
            eta_hard_min: float | None = None

            for i, p_val in enumerate(eta_preds):
                minute = float(i)
                is_safe_breach = p_val < limits.safe_min or p_val > limits.safe_max
                is_hard_breach = p_val < limits.hard_min or p_val > limits.hard_max

                if eta_safe_min is None and is_safe_breach:
                    eta_safe_min = minute
                if eta_hard_min is None and is_hard_breach:
                    eta_hard_min = minute

            f = Forecast(
                node_id=node_id,
                metric=m,
                made_at=ts,
                horizon_min=horizon_min,
                points=points,
                lower=lower,
                upper=upper,
                predicted_end=predicted_end,
                eta_safe_min=eta_safe_min,
                eta_hard_min=eta_hard_min,
                confidence=confidence,
                model=chosen_model_name,
            )

            self._cache[key] = f
            forecasts[m] = f

            # Schedule future evaluation at made_at + horizon in real seconds
            real_seconds = horizon_min * 60.0 / time_scale
            target_real_ts = ts + real_seconds
            if key not in self._pending_evals:
                self._pending_evals[key] = []
            self._pending_evals[key].append({
                "target_ts": target_real_ts,
                "predicted_end": predicted_end,
            })

        if forecasts:
            self.store.set_forecasts(node_id, forecasts)
        return forecasts
