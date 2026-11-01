/**
 * Automated closed-loop actuation and safety control engine.
 */
import * as config from './config';
import { Metric } from './config';
import { ActuationEvent, CleanedReading, Forecast, Level } from './models';
import { Store } from './store';

export class ActuatorEngine {
  private store: Store;
  private static STATE_RANK: Record<string, number> = { off: 0, low: 1, high: 2 };
  private static RANK_STATE: Record<number, string> = { 0: 'off', 1: 'low', 2: 'high' };

  private _devices: Record<string, Record<string, { state: string; mode: string }>> = {};
  private _shutdown: Record<string, boolean> = {};
  private _last_direction: Record<string, Record<Metric, string>> = {};
  private _step_down_count: Record<string, Record<string, number>> = {};
  private _turn_on_ts: Record<string, Record<string, number>> = {};
  private _prev_levels: Record<string, Record<Metric, Level>> = {};
  private _shutdown_clear_count: Record<string, number> = {};

  constructor(store: Store) {
    this.store = store;
    this.reset();
  }

  reset(): void {
    this._devices = {};
    this._shutdown = {};
    this._last_direction = {};
    this._step_down_count = {};
    this._turn_on_ts = {};
    this._prev_levels = {};
    this._shutdown_clear_count = {};
  }

  private _init_node(node: string): void {
    if (!this._devices[node]) {
      this._devices[node] = {
        fan: { state: 'off', mode: 'auto' },
        hvac: { state: 'off', mode: 'auto' },
        dehumidifier: { state: 'off', mode: 'auto' },
        purifier: { state: 'off', mode: 'auto' },
      };
      this._shutdown[node] = false;
      this._last_direction[node] = { temperature: 'high', humidity: 'high', aqi: 'high' };
      this._step_down_count[node] = { fan: 0, hvac: 0, dehumidifier: 0, purifier: 0 };
      this._turn_on_ts[node] = { fan: 0.0, hvac: 0.0, dehumidifier: 0.0, purifier: 0.0 };
      this._prev_levels[node] = {
        temperature: Level.OK,
        humidity: Level.OK,
        aqi: Level.OK,
      };
      this._shutdown_clear_count[node] = 0;
    }
  }

  is_shutdown(node: string): boolean {
    return !!this._shutdown[node];
  }

  restart(node: string): void {
    this._init_node(node);
    if (this._shutdown[node]) {
      this._shutdown[node] = false;
      this._shutdown_clear_count[node] = 0;
      const event: ActuationEvent = {
        ts: Date.now() / 1000,
        node_id: node,
        device: 'shutdown',
        from_state: 'on',
        to_state: 'off',
        trigger: 'manual',
        reason: 'manual restart',
      };
      this.store.add_actuation(event);
      this._sync_store_devices(node);
    }
  }

  override(node: string, device: string, action: string): void {
    this._init_node(node);
    if (!this._devices[node][device]) return;

    const old_state = this._devices[node][device].state;
    const old_mode = this._devices[node][device].mode;

    let new_state = old_state;
    let new_mode = old_mode;

    if (action === 'on') {
      new_state = 'high';
      new_mode = 'manual';
    } else if (action === 'off') {
      new_state = 'off';
      new_mode = 'manual';
    } else if (action === 'auto') {
      new_mode = 'auto';
    } else {
      return;
    }

    this._devices[node][device] = { state: new_state, mode: new_mode };

    if (new_state !== old_state || new_mode !== old_mode) {
      const event: ActuationEvent = {
        ts: Date.now() / 1000,
        node_id: node,
        device,
        from_state: old_state,
        to_state: new_state,
        trigger: 'manual',
        reason: `manual override: ${action}`,
      };
      this.store.add_actuation(event);
      this._sync_store_devices(node);
    }
  }

  private _sync_store_devices(node: string): void {
    const dev_copy: Record<string, any> = {
      fan: { ...this._devices[node].fan },
      hvac: { ...this._devices[node].hvac },
      dehumidifier: { ...this._devices[node].dehumidifier },
      purifier: { ...this._devices[node].purifier },
      shutdown: this._shutdown[node],
    };
    this.store.set_devices(node, dev_copy);
  }

  effects_for(node: string): Record<Metric, number> {
    this._init_node(node);
    const effects: Record<Metric, number> = { temperature: 0.0, humidity: 0.0, aqi: 0.0 };

    if (this._shutdown[node]) {
      effects.temperature += config.EFFECTS.shutdown.temperature;
    }

    const dev_state = this._devices[node];
    for (const d of ['fan', 'hvac', 'dehumidifier', 'purifier']) {
      const state = dev_state[d]?.state;
      if (state && (state === 'low' || state === 'high')) {
        const d_effects = config.EFFECTS[d]?.[state] || {};
        for (const [m, delta] of Object.entries(d_effects) as Array<[Metric, number]>) {
          let eff = delta;
          if (m === 'temperature' || m === 'humidity') {
            if (this._last_direction[node][m] === 'low' && d !== 'purifier') {
              eff = -eff;
            }
          }
          effects[m] += eff;
        }
      }
    }

    return effects;
  }

  evaluate(cleaned: CleanedReading, forecasts: Record<string, Forecast>): ActuationEvent[] {
    const node = cleaned.node_id;
    this._init_node(node);
    const ts = cleaned.ts;

    const settings = this.store.get_settings();
    const strategy = settings.strategy || config.STRATEGY;
    const horizon_min = parseFloat(settings.horizon_min || config.HORIZON_MIN);

    const events: ActuationEvent[] = [];
    const node_levels: Record<Metric, Level> = {
      temperature: Level.OK,
      humidity: Level.OK,
      aqi: Level.OK,
    };

    const req_ranks: Record<string, number> = { fan: 0, hvac: 0, dehumidifier: 0, purifier: 0 };
    const reasons: Record<string, string> = { fan: '', hvac: '', dehumidifier: '', purifier: '' };
    const triggers: Record<string, 'forecast' | 'threshold'> = {
      fan: 'threshold',
      hvac: 'threshold',
      dehumidifier: 'threshold',
      purifier: 'threshold',
    };
    let requires_shutdown = false;
    let shutdown_reason = '';
    let all_in_safe = true;

    for (const m of config.METRICS) {
      let val = cleaned.clean[m];
      const lims = this.store.limits(m);
      const midpoint = (lims.safe_min + lims.safe_max) / 2.0;

      if (val !== null && val !== undefined) {
        this._last_direction[node][m] = val >= midpoint ? 'high' : 'low';
      } else {
        val = midpoint;
      }

      let level = Level.OK;
      if (val <= lims.hard_min || val >= lims.hard_max) {
        level = Level.CRITICAL;
        all_in_safe = false;
      } else if (val < lims.safe_min || val > lims.safe_max) {
        level = Level.WARNING;
        all_in_safe = false;
      } else if (strategy === 'proactive') {
        const fc = forecasts[m];
        if (
          fc &&
          fc.confidence >= config.WATCH_CONFIDENCE &&
          fc.eta_safe_min !== null &&
          fc.eta_safe_min <= horizon_min
        ) {
          level = Level.WATCH;
        }
      }

      node_levels[m] = level;

      const prev_lvl = this._prev_levels[node][m] ?? Level.OK;
      if (level !== prev_lvl) {
        this.store.record_level_change(node, m, prev_lvl, level, ts);
        if (level === Level.WARNING) {
          this.store.log_event('warn', `actuator:${node}`, `${m} elevated to WARNING: ${val.toFixed(1)} ${lims.unit}`);
        } else if (level === Level.CRITICAL) {
          this.store.log_event(
            'critical',
            `actuator:${node}`,
            `${m} reached CRITICAL: ${val.toFixed(1)} ${lims.unit}`
          );
        }
      }

      // Device requirements mapping
      if (m === 'temperature') {
        if (level === Level.WATCH) {
          const fc = forecasts[m];
          const eta = fc?.eta_safe_min || 0.0;
          if (eta > horizon_min / 2.0) {
            if (req_ranks.fan < 1) {
              req_ranks.fan = 1;
              reasons.fan = `forecast: temperature leaves safe range in ${eta.toFixed(1)} sim-min, confidence ${fc?.confidence.toFixed(2)}`;
              triggers.fan = 'forecast';
            }
          } else {
            if (req_ranks.fan < 2) {
              req_ranks.fan = 2;
              reasons.fan = `forecast: temperature leaves safe range in ${eta.toFixed(1)} sim-min, confidence ${fc?.confidence.toFixed(2)}`;
              triggers.fan = 'forecast';
            }
            if (req_ranks.hvac < 1) {
              req_ranks.hvac = 1;
              reasons.hvac = `forecast: temperature leaves safe range in ${eta.toFixed(1)} sim-min, confidence ${fc?.confidence.toFixed(2)}`;
              triggers.hvac = 'forecast';
            }
          }
        } else if (level === Level.WARNING) {
          if (req_ranks.fan < 2) {
            req_ranks.fan = 2;
            reasons.fan = `threshold: temperature ${val.toFixed(1)} ${lims.unit} outside safe [${lims.safe_min}, ${lims.safe_max}]`;
            triggers.fan = 'threshold';
          }
          if (req_ranks.hvac < 2) {
            req_ranks.hvac = 2;
            reasons.hvac = `threshold: temperature ${val.toFixed(1)} ${lims.unit} outside safe [${lims.safe_min}, ${lims.safe_max}]`;
            triggers.hvac = 'threshold';
          }
        } else if (level === Level.CRITICAL) {
          req_ranks.fan = 2;
          req_ranks.hvac = 2;
          reasons.fan = `threshold: temperature ${val.toFixed(1)} ${lims.unit} at/beyond hard limit`;
          reasons.hvac = `threshold: temperature ${val.toFixed(1)} ${lims.unit} at/beyond hard limit`;
          triggers.fan = 'threshold';
          triggers.hvac = 'threshold';
          requires_shutdown = true;
          shutdown_reason = `critical temperature ${val.toFixed(1)} ${lims.unit}`;
        }
      } else if (m === 'humidity') {
        if (level === Level.WATCH) {
          if (req_ranks.dehumidifier < 1) {
            const fc = forecasts[m];
            req_ranks.dehumidifier = 1;
            reasons.dehumidifier = `forecast: humidity leaves safe range in ${fc?.eta_safe_min?.toFixed(1)} sim-min`;
            triggers.dehumidifier = 'forecast';
          }
        } else if (level === Level.WARNING) {
          if (req_ranks.dehumidifier < 2) {
            req_ranks.dehumidifier = 2;
            reasons.dehumidifier = `threshold: humidity ${val.toFixed(1)} % outside safe range`;
            triggers.dehumidifier = 'threshold';
          }
        } else if (level === Level.CRITICAL) {
          req_ranks.dehumidifier = 2;
          reasons.dehumidifier = `threshold: humidity ${val.toFixed(1)} % at/beyond hard limit`;
          triggers.dehumidifier = 'threshold';
          requires_shutdown = true;
          shutdown_reason = `critical humidity ${val.toFixed(1)} %`;
        }
      } else if (m === 'aqi') {
        if (level === Level.WATCH) {
          if (req_ranks.purifier < 1) {
            const fc = forecasts[m];
            req_ranks.purifier = 1;
            reasons.purifier = `forecast: AQI leaves safe range in ${fc?.eta_safe_min?.toFixed(1)} sim-min`;
            triggers.purifier = 'forecast';
          }
        } else if (level === Level.WARNING) {
          if (req_ranks.purifier < 2) {
            req_ranks.purifier = 2;
            reasons.purifier = `threshold: AQI ${val.toFixed(1)} outside safe range`;
            triggers.purifier = 'threshold';
          }
        } else if (level === Level.CRITICAL) {
          req_ranks.purifier = 2;
          reasons.purifier = `threshold: AQI ${val.toFixed(1)} at/beyond hard limit`;
          triggers.purifier = 'threshold';
          requires_shutdown = true;
          shutdown_reason = `critical AQI ${val.toFixed(1)}`;
        }
      }
    }

    this._prev_levels[node] = { ...node_levels };
    this.store.set_levels(node, node_levels);

    // Hysteresis & step down evaluation
    const is_metric_in_shrunk_safe = (metric: Metric) => {
      const v = cleaned.clean[metric];
      if (v === null || v === undefined) return true;
      const l = this.store.limits(metric);
      const margin = config.HYST_MARGIN * (l.safe_max - l.safe_min);
      return l.safe_min + margin <= v && v <= l.safe_max - margin;
    };

    const dev_metrics: Record<string, Metric[]> = {
      fan: ['temperature', 'aqi'],
      hvac: ['temperature', 'humidity'],
      dehumidifier: ['humidity'],
      purifier: ['aqi'],
    };

    for (const d of ['fan', 'hvac', 'dehumidifier', 'purifier']) {
      const dev_info = this._devices[node][d];
      if (dev_info.mode === 'manual') continue;

      const current_rank = ActuatorEngine.STATE_RANK[dev_info.state] || 0;
      const target_rank = req_ranks[d] || 0;

      if (target_rank > current_rank) {
        this._step_down_count[node][d] = 0;
        const new_state = ActuatorEngine.RANK_STATE[target_rank];
        const ev: ActuationEvent = {
          ts,
          node_id: node,
          device: d,
          from_state: dev_info.state,
          to_state: new_state,
          trigger: triggers[d],
          reason: reasons[d],
        };
        this._devices[node][d].state = new_state;
        this._turn_on_ts[node][d] = ts;
        this.store.add_actuation(ev);
        events.push(ev);
      } else if (target_rank < current_rank) {
        const shrunk_safe = dev_metrics[d].every((mName) => is_metric_in_shrunk_safe(mName));
        const on_duration = ts - (this._turn_on_ts[node][d] || 0);

        if (shrunk_safe) {
          this._step_down_count[node][d] += 1;
        } else {
          this._step_down_count[node][d] = 0;
        }

        if (this._step_down_count[node][d] >= config.HYST_READINGS && on_duration >= config.MIN_ON_SECONDS) {
          const new_rank = current_rank - 1;
          const new_state = ActuatorEngine.RANK_STATE[new_rank];
          this._step_down_count[node][d] = 0;
          const ev: ActuationEvent = {
            ts,
            node_id: node,
            device: d,
            from_state: dev_info.state,
            to_state: new_state,
            trigger: 'release',
            reason: 'metrics recovered into shrunk safe range',
          };
          this._devices[node][d].state = new_state;
          this._turn_on_ts[node][d] = new_rank === 0 ? 0.0 : ts;
          this.store.add_actuation(ev);
          events.push(ev);
        }
      } else {
        this._step_down_count[node][d] = 0;
      }
    }

    // Shutdown management
    if (requires_shutdown) {
      if (!this._shutdown[node]) {
        this._shutdown[node] = true;
        this._shutdown_clear_count[node] = 0;
        this.store.log_event('critical', `actuator:${node}`, `Emergency soft shutdown: ${shutdown_reason}`);
        const ev: ActuationEvent = {
          ts,
          node_id: node,
          device: 'shutdown',
          from_state: 'off',
          to_state: 'on',
          trigger: 'threshold',
          reason: shutdown_reason,
        };
        this.store.add_actuation(ev);
        events.push(ev);
      }
    } else {
      if (this._shutdown[node]) {
        if (all_in_safe) {
          this._shutdown_clear_count[node] += 1;
          if (this._shutdown_clear_count[node] >= config.HYST_READINGS) {
            this._shutdown[node] = false;
            this._shutdown_clear_count[node] = 0;
            this.store.log_event(
              'info',
              `actuator:${node}`,
              'Emergency shutdown cleared: metrics returned to safe range'
            );
            const ev: ActuationEvent = {
              ts,
              node_id: node,
              device: 'shutdown',
              from_state: 'on',
              to_state: 'off',
              trigger: 'release',
              reason: 'metrics returned to safe range',
            };
            this.store.add_actuation(ev);
            events.push(ev);
          }
        } else {
          this._shutdown_clear_count[node] = 0;
        }
      }
    }

    this._sync_store_devices(node);
    return events;
  }
}
