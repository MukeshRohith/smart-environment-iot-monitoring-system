"""System performance metrics, breach analytics, and early-warning effectiveness calculations."""
from __future__ import annotations

from typing import Any

import numpy as np

import config
from core.store import Store


class MetricsEngine:
    """Computes runtime performance, packet loss, anticipatory lead time, and safety statistics."""

    def __init__(self, store: Store) -> None:
        """Initialize metrics engine bound to system Store."""
        self.store = store

    def compute(self) -> dict[str, Any]:
        """Aggregate store statistics into comprehensive performance dictionary."""
        counters = self.store.counters()
        received = counters.get("received", 0)
        dropped = counters.get("dropped", 0)
        rejected = counters.get("rejected", 0)

        total_ingress = received + rejected
        if total_ingress > 0:
            packet_loss_pct = float((dropped + rejected) / total_ingress * 100.0)
        else:
            packet_loss_pct = 0.0

        latency_stats = self.store.latency_stats()
        forecast_error_stats = self.store.forecast_error_stats()

        # Breach analysis from historical level transitions
        transitions = self.store.level_transitions()

        # Group transitions by (node, metric)
        state_track: dict[tuple[str, str], dict[str, Any]] = {}
        anticipated = 0
        missed = 0
        prevented = 0
        lead_times: list[float] = []

        for trans in transitions:
            key = (trans["node_id"], trans["metric"])
            if key not in state_track:
                state_track[key] = {"watch_ts": None, "in_breach": False}

            node_st = state_track[key]
            t_stamp = float(trans["ts"])
            old_lvl = trans["old"]
            new_lvl = trans["new"]

            if new_lvl == "WATCH":
                if old_lvl == "OK" or node_st["watch_ts"] is None:
                    node_st["watch_ts"] = t_stamp
                node_st["in_breach"] = False

            elif new_lvl in ("WARNING", "CRITICAL"):
                if old_lvl not in ("WARNING", "CRITICAL"):
                    # Transitioning into a breach
                    if node_st["watch_ts"] is not None:
                        anticipated += 1
                        lead_times.append(max(0.0, t_stamp - node_st["watch_ts"]))
                        node_st["watch_ts"] = None
                    else:
                        missed += 1
                    node_st["in_breach"] = True

            elif new_lvl == "OK":
                if node_st["watch_ts"] is not None and not node_st["in_breach"]:
                    prevented += 1
                node_st["watch_ts"] = None
                node_st["in_breach"] = False

        total_breaches = anticipated + missed
        avg_lead_time = float(np.mean(lead_times)) if lead_times else 0.0
        lead_time_data = {
            "avg_s": avg_lead_time,
            "count": len(lead_times),
        }

        breach_data = {
            "prevented": prevented,
            "missed": missed,
            "anticipated": anticipated,
            "total": total_breaches,
        }

        # Exposure formatting
        exposure_data: dict[str, dict[str, dict[str, float]]] = {}
        for node in self.store.node_ids():
            exp = self.store.exposure(node)
            exposure_data[node] = {
                m: {
                    "peak": exp[m]["peak"] if exp[m]["peak"] != float("-inf") else 0.0,
                    "seconds_outside": exp[m]["seconds_outside_safe"],
                    "seconds_above_hard": exp[m]["seconds_above_hard"],
                }
                for m in config.METRICS
            }

        # Cumulative device run time
        device_on_data: dict[str, float] = {}
        for node in self.store.node_ids():
            dev_secs = self.store.device_on_seconds(node)
            device_on_data[node] = float(sum(dev_secs.values()))

        return {
            "packet_loss_pct": packet_loss_pct,
            "counts": counters,
            "latency": latency_stats,
            "forecast_error": forecast_error_stats,
            "lead_time": lead_time_data,
            "breaches": breach_data,
            "exposure": exposure_data,
            "device_on_seconds": device_on_data,
        }
