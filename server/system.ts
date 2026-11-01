/**
 * Central System orchestrator unifying Store, Pipeline, Forecaster, Actuator, Simulator, and Analytics.
 */
import * as config from './config';
import { ActuatorEngine } from './actuator';
import { BenchmarkRunner } from './benchmark';
import { DemoRunner } from './demo';
import { Forecaster } from './forecaster';
import { MetricsEngine } from './metrics';
import { Pipeline } from './pipeline';
import { SimulatorManager } from './simulator';
import { Store } from './store';
import { Wrangler } from './wrangler';

export class System {
  store: Store;
  wrangler: Wrangler;
  forecaster: Forecaster;
  actuator: ActuatorEngine;
  pipeline: Pipeline;
  simulator: SimulatorManager;
  metrics: MetricsEngine;
  benchmark: BenchmarkRunner;
  demo: DemoRunner;

  private _running = false;
  private _paused = false;

  constructor(initial_profile?: string) {
    this.store = new Store(initial_profile || config.DEFAULT_PROFILE);
    this.wrangler = new Wrangler(this.store);
    this.forecaster = new Forecaster(this.store);
    this.actuator = new ActuatorEngine(this.store);

    this.pipeline = new Pipeline(this.store, this.wrangler, this.forecaster, this.actuator);

    this.simulator = new SimulatorManager(
      this.store,
      (nid) => this.actuator.effects_for(nid),
      (nid) => this.actuator.is_shutdown(nid),
      (reading) => this.pipeline.submit(reading)
    );

    this.metrics = new MetricsEngine(this.store);
    this.benchmark = new BenchmarkRunner(this);
    this.demo = new DemoRunner(this);
  }

  start(start_simulator = true): void {
    this._running = true;
    this._paused = false;
    this.pipeline.start();
    if (start_simulator) {
      this.simulator.start();
    }
  }

  stop(): void {
    this._running = false;
    this._paused = false;
    this.simulator.stop();
    this.pipeline.stop();
    this.demo.stop();
  }

  pause(): void {
    this._paused = true;
    this.pipeline.pause();
    this.simulator.pause();
    this.store.log_event('warn', 'system', 'Demonstration PAUSED: Telemetry ingestion and physics simulation frozen.');
  }

  resume(): void {
    this._paused = false;
    this.pipeline.resume();
    this.simulator.resume();
    this.store.log_event('info', 'system', 'Demonstration RESUMED: Telemetry ingestion and physics simulation active.');
  }

  is_paused(): boolean {
    return this._paused;
  }

  reset_runtime(): void {
    this.store.reset_runtime();
    this.wrangler.reset();
    this.forecaster.reset();
    this.actuator.reset();
    this.pipeline.reset();
    this.simulator.reset_to_profile();
    this.demo.stop();
    this.store.log_event('info', 'system', 'Demonstration RESET: All data streams, buffers, counters, and metrics cleared.');
  }

  get_settings(): Record<string, any> {
    return this.store.get_settings();
  }

  apply_settings(partial: Record<string, any>): Record<string, any> {
    const validated: Record<string, any> = {};

    if ('strategy' in partial) {
      const s = String(partial.strategy);
      if (s !== 'proactive' && s !== 'reactive') {
        throw new Error("strategy must be 'proactive' or 'reactive'");
      }
      validated.strategy = s;
    }

    if ('pipeline_mode' in partial) {
      const m = String(partial.pipeline_mode);
      if (m !== 'priority' && m !== 'fifo') {
        throw new Error("pipeline_mode must be 'priority' or 'fifo'");
      }
      validated.pipeline_mode = m;
    }

    if ('workers' in partial) {
      const w = parseInt(partial.workers, 10);
      if (isNaN(w) || w < 1 || w > 8) {
        throw new Error('workers must be between 1 and 8');
      }
      validated.workers = w;
    }

    if ('processing_delay_ms' in partial) {
      const d = parseFloat(partial.processing_delay_ms);
      if (isNaN(d) || d < 0 || d > 200) {
        throw new Error('processing_delay_ms must be between 0 and 200');
      }
      validated.processing_delay_ms = d;
    }

    if ('horizon_min' in partial) {
      const h = parseFloat(partial.horizon_min);
      if (isNaN(h) || h < 15 || h > 30) {
        throw new Error('horizon_min must be between 15 and 30');
      }
      validated.horizon_min = h;
    }

    if ('time_scale' in partial) {
      const ts = parseFloat(partial.time_scale);
      if (isNaN(ts) || ts < 10 || ts > 300) {
        throw new Error('time_scale must be between 10 and 300');
      }
      validated.time_scale = ts;
    }

    const updated = this.store.update_settings(validated);
    this.pipeline.apply_settings();
    return updated;
  }

  set_profile(name: string): void {
    if (!config.PROFILES[name]) {
      throw new Error(`Unknown profile '${name}'; available: ${Object.keys(config.PROFILES).join(', ')}`);
    }

    this.store.set_profile(name);
    this.simulator.reset_to_profile();
    this.forecaster.reset();
    this.actuator.reset();
  }

  state(): Record<string, any> {
    const snap = this.store.snapshot();
    snap.pipeline = this.pipeline.snapshot();
    snap.metrics = this.metrics.compute();
    snap.benchmark = this.benchmark.status();
    snap.demo = this.demo.state();
    snap.paused = this._paused;
    snap.system_status = this._paused ? 'paused' : 'running';
    return snap;
  }
}
