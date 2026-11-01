/**
 * Multi-node environmental physics simulation engine with fault injection and closed-loop actuator feedback.
 */
import * as config from './config';
import { Metric } from './config';
import { Reading } from './models';
import { Store } from './store';

// Box-Muller Gaussian random generator
function randomGaussian(mean = 0.0, std = 1.0): number {
  let u = 0,
    v = 0;
  while (u === 0) u = Math.random();
  while (v === 0) v = Math.random();
  const num = Math.sqrt(-2.0 * Math.log(u)) * Math.cos(2.0 * Math.PI * v);
  return num * std + mean;
}

export class NodeSim {
  node_id: string;
  target: Record<Metric, number>;
  current: Record<Metric, number>;
  noise: number;
  send_rate_hz: number;
  fault_mode: string;
  enabled: boolean;
  seq: number;
  last_emitted: Record<Metric, number | null>;
  last_tick: number;

  constructor(node_id: string, initial_targets: Record<Metric, number>) {
    this.node_id = node_id;
    this.target = { ...initial_targets };
    this.current = { ...initial_targets };
    this.noise = config.DEFAULT_NOISE;
    this.send_rate_hz = config.DEFAULT_SEND_HZ;
    this.fault_mode = 'none';
    this.enabled = true;
    this.seq = 0;
    this.last_emitted = { ...initial_targets };
    this.last_tick = Date.now() / 1000;
  }

  get_controls(): Record<string, any> {
    return {
      target: { ...this.target },
      current: { ...this.current },
      noise: this.noise,
      send_rate_hz: this.send_rate_hz,
      fault_mode: this.fault_mode,
      enabled: this.enabled,
    };
  }
}

export class SimulatorManager {
  private store: Store;
  private effects_provider: (nodeId: string) => Record<Metric, number>;
  private shutdown_provider: (nodeId: string) => boolean;
  private transport_fn: (payload: Reading) => void;

  private _nodes: Record<string, NodeSim> = {};
  private _timers: Record<string, NodeJS.Timeout> = {};
  private _running = false;
  private _paused = false;
  send_errors = 0;

  constructor(
    store: Store,
    effects_provider: (nodeId: string) => Record<Metric, number>,
    shutdown_provider: (nodeId: string) => boolean,
    transport?: (payload: Reading) => void
  ) {
    this.store = store;
    this.effects_provider = effects_provider;
    this.shutdown_provider = shutdown_provider;
    this.transport_fn = transport || (() => {});
    this._init_default_nodes();
  }

  pause(): void {
    this._paused = true;
  }

  resume(): void {
    this._paused = false;
  }

  is_paused(): boolean {
    return this._paused;
  }

  set_transport(transport: (payload: Reading) => void): void {
    this.transport_fn = transport;
  }

  private _get_midpoint_targets(): Record<Metric, number> {
    const lims = this.store.all_limits();
    return {
      temperature: (lims.temperature.safe_min + lims.temperature.safe_max) / 2.0,
      humidity: (lims.humidity.safe_min + lims.humidity.safe_max) / 2.0,
      aqi: (lims.aqi.safe_min + lims.aqi.safe_max) / 2.0,
    };
  }

  private _init_default_nodes(): void {
    const targets = this._get_midpoint_targets();
    for (let i = 1; i <= config.DEFAULT_NODES; i++) {
      const nid = `node-${i}`;
      const node = new NodeSim(nid, targets);
      this._nodes[nid] = node;
      this.store.register_node(nid, node.get_controls());
    }
  }

  reset_to_profile(): void {
    const targets = this._get_midpoint_targets();
    const now = Date.now() / 1000;
    for (const node of Object.values(this._nodes)) {
      node.target = { ...targets };
      node.current = { ...targets };
      node.last_emitted = { ...targets };
      node.noise = config.DEFAULT_NOISE;
      node.send_rate_hz = config.DEFAULT_SEND_HZ;
      node.fault_mode = 'none';
      node.enabled = true;
      node.seq = 0;
      node.last_tick = now;
      this.store.set_node_controls(node.node_id, node.get_controls());
      this._restart_node_timer(node.node_id);
    }
  }

  set_controls(node_id: string, partial: Record<string, any>): void {
    const node = this._nodes[node_id];
    if (!node) {
      throw new Error(`Node ${node_id} does not exist`);
    }

    if (partial.target && typeof partial.target === 'object') {
      const lims = this.store.all_limits();
      for (const m of config.METRICS) {
        if (partial.target[m] !== undefined) {
          const val = parseFloat(partial.target[m]);
          const lim = lims[m];
          node.target[m] = Math.max(lim.slider_min, Math.min(lim.slider_max, val));
        }
      }
    }

    if (partial.noise !== undefined) {
      const n = parseFloat(partial.noise);
      node.noise = Math.max(0.0, Math.min(1.0, n));
    }

    if (partial.send_rate_hz !== undefined) {
      const r = parseFloat(partial.send_rate_hz);
      node.send_rate_hz = Math.max(0.2, Math.min(20.0, r));
      if (this._running) {
        this._restart_node_timer(node_id);
      }
    }

    if (partial.fault_mode !== undefined) {
      const fm = String(partial.fault_mode);
      if (['none', 'dropout', 'outlier', 'stuck'].includes(fm)) {
        node.fault_mode = fm;
      }
    }

    if (partial.enabled !== undefined) {
      node.enabled = Boolean(partial.enabled);
    }

    this.store.set_node_controls(node_id, node.get_controls());
  }

  add_node(node_id?: string): string {
    if (Object.keys(this._nodes).length >= config.MAX_NODES) {
      throw new Error(`Maximum node count of ${config.MAX_NODES} reached`);
    }

    if (!node_id) {
      let idx = 1;
      while (this._nodes[`node-${idx}`]) {
        idx++;
      }
      node_id = `node-${idx}`;
    }

    if (this._nodes[node_id]) {
      throw new Error(`Node ${node_id} already exists`);
    }

    const targets = this._get_midpoint_targets();
    const node = new NodeSim(node_id, targets);
    this._nodes[node_id] = node;
    this.store.register_node(node_id, node.get_controls());

    if (this._running) {
      this._start_node_timer(node_id);
    }

    return node_id;
  }

  remove_node(node_id?: string): string {
    if (Object.keys(this._nodes).length <= 1) {
      throw new Error('At least 1 node must remain active');
    }

    if (!node_id) {
      node_id = Object.keys(this._nodes).sort().pop()!;
    }

    if (!this._nodes[node_id]) {
      throw new Error(`Node ${node_id} does not exist`);
    }

    if (this._timers[node_id]) {
      clearTimeout(this._timers[node_id]);
      delete this._timers[node_id];
    }

    delete this._nodes[node_id];
    this.store.unregister_node(node_id);
    return node_id;
  }

  apply_scenario(name: string, node_id?: string): void {
    const lims = this.store.all_limits();
    const targets_mid = this._get_midpoint_targets();
    const is_all = node_id === 'all';

    if (name === 'normal') {
      if (node_id && !is_all && this._nodes[node_id]) {
        const node = this._nodes[node_id];
        node.target = { ...targets_mid };
        node.noise = config.DEFAULT_NOISE;
        node.send_rate_hz = 1.0;
        node.fault_mode = 'none';
        node.enabled = true;
        this.store.set_node_controls(node.node_id, node.get_controls());
        this._restart_node_timer(node.node_id);
      } else {
        for (const node of Object.values(this._nodes)) {
          node.target = { ...targets_mid };
          node.noise = config.DEFAULT_NOISE;
          node.send_rate_hz = 1.0;
          node.fault_mode = 'none';
          node.enabled = true;
          this.store.set_node_controls(node.node_id, node.get_controls());
          this._restart_node_timer(node.node_id);
        }
      }
    } else if (name === 'heat_spike') {
      const t_lim = lims.temperature;
      const spike_target = t_lim.safe_max + 0.8 * (t_lim.hard_max - t_lim.safe_max);
      if (is_all) {
        for (const nid of Object.keys(this._nodes)) {
          this.set_controls(nid, { target: { temperature: spike_target } });
        }
      } else {
        const target_node = node_id && this._nodes[node_id] ? node_id : 'node-1';
        if (!this._nodes[target_node]) this.add_node(target_node);
        this.set_controls(target_node, { target: { temperature: spike_target } });
      }
    } else if (name === 'humidity_drift' || name === 'humidity_leak') {
      const h_lim = lims.humidity;
      const drift_target = h_lim.safe_max + 0.8 * (h_lim.hard_max - h_lim.safe_max);
      if (is_all) {
        for (const nid of Object.keys(this._nodes)) {
          this.set_controls(nid, { target: { humidity: drift_target } });
        }
      } else {
        const target_node = node_id && this._nodes[node_id] ? node_id : 'node-2';
        if (!this._nodes[target_node]) this.add_node(target_node);
        this.set_controls(target_node, { target: { humidity: drift_target } });
      }
    } else if (name === 'sensor_fault' || name === 'sensor_failure') {
      if (is_all) {
        for (const nid of Object.keys(this._nodes)) {
          this.set_controls(nid, { fault_mode: 'dropout' });
        }
      } else {
        const target_node = node_id && this._nodes[node_id] ? node_id : 'node-3';
        if (!this._nodes[target_node]) this.add_node(target_node);
        this.set_controls(target_node, { fault_mode: 'dropout' });
      }
    } else if (name === 'burst_traffic') {
      if (is_all) {
        for (const nid of Object.keys(this._nodes)) {
          this.set_controls(nid, { send_rate_hz: 15.0 });
        }
      } else {
        const target_node = node_id && this._nodes[node_id] ? node_id : 'node-1';
        if (!this._nodes[target_node]) this.add_node(target_node);
        this.set_controls(target_node, { send_rate_hz: 15.0 });
      }
    } else {
      throw new Error(`Unknown scenario name: ${name}`);
    }
  }

  private _step_node(node: NodeSim, dt: number): Reading {
    const eff_offset = this.effects_provider(node.node_id);
    const values: Record<Metric, number | null> = { temperature: 0, humidity: 0, aqi: 0 };

    for (const m of config.METRICS) {
      const [phys_min, phys_max] = config.PHYSICAL[m];
      const offset = eff_offset[m] || 0.0;
      const eff_target = Math.max(phys_min, Math.min(phys_max, node.target[m] + offset));

      // Thermal/environmental lag
      const alpha = 1.0 - Math.exp(-dt / config.LAG_TAU_S);
      node.current[m] += (eff_target - node.current[m]) * alpha;

      // Noise
      const sigma = node.noise * config.BASE_NOISE_SIGMA[m];
      const noise_val = sigma > 0 ? randomGaussian(0.0, sigma) : 0.0;
      values[m] = Math.max(phys_min, Math.min(phys_max, node.current[m] + noise_val));
    }

    // Fault Mode Injection
    if (node.fault_mode === 'dropout') {
      for (const m of config.METRICS) {
        if (Math.random() < 0.25) {
          values[m] = null;
        }
      }
    } else if (node.fault_mode === 'outlier') {
      if (Math.random() < 0.08) {
        const m_chosen = config.METRICS[Math.floor(Math.random() * config.METRICS.length)];
        const sign = Math.random() < 0.5 ? 1.0 : -1.0;
        const spike_table: Record<Metric, number> = { temperature: 15.0, humidity: 40.0, aqi: 200.0 };
        if (values[m_chosen] !== null) {
          values[m_chosen] = values[m_chosen]! + sign * spike_table[m_chosen];
        }
      }
    } else if (node.fault_mode === 'stuck') {
      for (const m of config.METRICS) {
        values[m] = node.last_emitted[m];
      }
    }

    if (node.fault_mode !== 'stuck') {
      node.last_emitted = { ...values };
    }

    node.seq += 1;
    const is_shut = this.shutdown_provider(node.node_id);
    const status: 'ok' | 'shutdown' = is_shut ? 'shutdown' : 'ok';

    return {
      node_id: node.node_id,
      ts: Date.now() / 1000,
      seq: node.seq,
      temperature: values.temperature,
      humidity: values.humidity,
      aqi: values.aqi,
      status,
      received_at: 0.0,
      priority: 2,
    };
  }

  private _start_node_timer(node_id: string): void {
    if (this._timers[node_id]) {
      clearTimeout(this._timers[node_id]);
    }

    const tick = () => {
      if (!this._running) return;
      if (this._paused) {
        if (this._running) {
          this._timers[node_id] = setTimeout(tick, 200);
        }
        return;
      }
      const node = this._nodes[node_id];
      if (node && node.enabled) {
        const now = Date.now() / 1000;
        const dt = Math.max(0.001, now - node.last_tick);
        node.last_tick = now;

        const reading = this._step_node(node, dt);
        try {
          this.transport_fn(reading);
        } catch {
          this.send_errors++;
        }
      }

      const rate = node ? node.send_rate_hz : 1.0;
      const intervalMs = Math.max(50, 1000 / Math.max(0.2, rate));
      if (this._running) {
        this._timers[node_id] = setTimeout(tick, intervalMs);
      }
    };

    const node = this._nodes[node_id];
    const rate = node ? node.send_rate_hz : 1.0;
    this._timers[node_id] = setTimeout(tick, 1000 / Math.max(0.2, rate));
  }

  private _restart_node_timer(node_id: string): void {
    if (this._running) {
      this._start_node_timer(node_id);
    }
  }

  start(): void {
    this._running = true;
    for (const nid of Object.keys(this._nodes)) {
      this._start_node_timer(nid);
    }
  }

  stop(): void {
    this._running = false;
    for (const nid of Object.keys(this._timers)) {
      clearTimeout(this._timers[nid]);
    }
    this._timers = {};
  }

  get_state(): Record<string, any> {
    const nodesOut: Record<string, any> = {};
    for (const [nid, node] of Object.entries(this._nodes)) {
      nodesOut[nid] = {
        controls: node.get_controls(),
        current: { ...node.current },
      };
    }
    return {
      nodes: nodesOut,
      send_errors: this.send_errors,
    };
  }
}
