/**
 * System performance metrics, breach analytics, and early-warning effectiveness calculations.
 */
import * as config from './config';
import { Store } from './store';

export class MetricsEngine {
  private store: Store;

  constructor(store: Store) {
    this.store = store;
  }

  compute(): Record<string, any> {
    const counters = this.store.counters();
    const received = counters.received || 0;
    const dropped = counters.dropped || 0;
    const rejected = counters.rejected || 0;

    const total_ingress = received + rejected;
    const packet_loss_pct = total_ingress > 0 ? ((dropped + rejected) / total_ingress) * 100.0 : 0.0;

    const latency_stats = this.store.latency_stats();
    const forecast_error_stats = this.store.forecast_error_stats();

    // Breach analysis from historical level transitions
    const transitions = this.store.level_transitions();
    const state_track: Record<string, { watch_ts: number | null; in_breach: boolean }> = {};

    let anticipated = 0;
    let missed = 0;
    let prevented = 0;
    const lead_times: number[] = [];

    for (const trans of transitions) {
      const key = `${trans.node_id}:${trans.metric}`;
      if (!state_track[key]) {
        state_track[key] = { watch_ts: null, in_breach: false };
      }

      const node_st = state_track[key];
      const t_stamp = trans.ts;
      const old_lvl = trans.old;
      const new_lvl = trans.new;

      if (new_lvl === 'WATCH') {
        if (old_lvl === 'OK' || node_st.watch_ts === null) {
          node_st.watch_ts = t_stamp;
        }
        node_st.in_breach = false;
      } else if (new_lvl === 'WARNING' || new_lvl === 'CRITICAL') {
        if (old_lvl !== 'WARNING' && old_lvl !== 'CRITICAL') {
          // Transitioning into a breach
          if (node_st.watch_ts !== null) {
            anticipated += 1;
            lead_times.push(Math.max(0.0, t_stamp - node_st.watch_ts));
            node_st.watch_ts = null;
          } else {
            missed += 1;
          }
          node_st.in_breach = true;
        }
      } else if (new_lvl === 'OK') {
        if (node_st.watch_ts !== null && !node_st.in_breach) {
          prevented += 1;
        }
        node_st.watch_ts = null;
        node_st.in_breach = false;
      }
    }

    const total_breaches = anticipated + missed;
    const avg_lead_time =
      lead_times.length > 0 ? lead_times.reduce((a, b) => a + b, 0) / lead_times.length : 0.0;

    const exposure_data: Record<string, Record<string, any>> = {};
    for (const nid of this.store.node_ids()) {
      const exp = this.store.exposure(nid);
      exposure_data[nid] = {
        temperature: {
          peak: exp.temperature.peak !== -Infinity ? exp.temperature.peak : 0.0,
          seconds_outside: exp.temperature.seconds_outside_safe,
          seconds_above_hard: exp.temperature.seconds_above_hard,
        },
        humidity: {
          peak: exp.humidity.peak !== -Infinity ? exp.humidity.peak : 0.0,
          seconds_outside: exp.humidity.seconds_outside_safe,
          seconds_above_hard: exp.humidity.seconds_above_hard,
        },
        aqi: {
          peak: exp.aqi.peak !== -Infinity ? exp.aqi.peak : 0.0,
          seconds_outside: exp.aqi.seconds_outside_safe,
          seconds_above_hard: exp.aqi.seconds_above_hard,
        },
      };
    }

    return {
      packet_loss_pct: parseFloat(packet_loss_pct.toFixed(2)),
      latency: latency_stats,
      forecast_accuracy: forecast_error_stats,
      breaches: {
        prevented,
        missed,
        anticipated,
        total: total_breaches,
      },
      lead_time: {
        avg_s: parseFloat(avg_lead_time.toFixed(2)),
        count: lead_times.length,
      },
      exposure: exposure_data,
    };
  }
}
