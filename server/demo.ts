/**
 * Interactive guided walkthrough demonstrating pipeline stages, anomaly handling, and closed-loop actuation.
 */
import { System } from './system';

export class DemoRunner {
  private system: System;

  static DEFAULT_STEP_DURATIONS: Record<number, number> = {
    1: 15.0,
    2: 35.0,
    3: 20.0,
    4: 20.0,
    5: 30.0,
    6: 10.0,
  };

  static CAPTIONS: Record<number, string> = {
    1: 'Baseline normal: verifying pipeline stages (wrangle, forecast, actuate)',
    2: 'Node-1 heat spike: observing forecast ETA, proactive fan, tiered HVAC',
    3: 'Sensor fault: anomalies prioritized in queue, missing/outliers flagged and cleaned',
    4: 'Burst traffic: queue depth rising, critical telemetry processed ahead of normal',
    5: 'Humidity drift: predictive forecast triggering early dehumidifier automation',
    6: 'Demo summary: loss rate, critical latency, forecast accuracy, prevented breaches',
  };

  static STEP_SCENARIOS: Record<number, string> = {
    1: 'normal',
    2: 'heat_spike',
    3: 'sensor_fault',
    4: 'burst_traffic',
    5: 'humidity_drift',
    6: 'summary',
  };

  private _running = false;
  private _paused = false;
  private _current_step = 1;
  private _remaining_s = 0.0;
  private _caption = '';
  private _summary: Record<string, any> | null = null;
  private _loop_id: NodeJS.Timeout | null = null;

  constructor(system: System) {
    this.system = system;
  }

  state() {
    return {
      running: this._running,
      active: this._running,
      paused: this._paused,
      step: this._current_step,
      steps_total: 6,
      scenario: DemoRunner.STEP_SCENARIOS[this._current_step] || 'normal',
      caption: this._caption,
      remaining_s: parseFloat(Math.max(0.0, this._remaining_s).toFixed(1)),
      summary: this._summary,
    };
  }

  start(): void {
    if (this._running) return;

    this._running = true;
    this._paused = false;
    this._current_step = 1;
    this._summary = null;
    this._advance_to_step(1);

    this._run_loop();
  }

  pause(): void {
    if (this._running) {
      this._paused = true;
    }
  }

  resume(): void {
    if (this._running) {
      this._paused = false;
    }
  }

  stop(): void {
    this._running = false;
    this._paused = false;
    if (this._loop_id) {
      clearTimeout(this._loop_id);
      this._loop_id = null;
    }
    this.system.simulator.apply_scenario('normal', 'all');
  }

  private _advance_to_step(step: number): void {
    this._current_step = step;
    this._remaining_s = DemoRunner.DEFAULT_STEP_DURATIONS[step] || 15.0;
    this._caption = DemoRunner.CAPTIONS[step] || '';

    const sc = DemoRunner.STEP_SCENARIOS[step];
    if (sc === 'normal') {
      this.system.simulator.apply_scenario('normal', 'all');
    } else if (sc === 'heat_spike') {
      this.system.simulator.apply_scenario('heat_spike', 'node-1');
    } else if (sc === 'sensor_fault') {
      this.system.simulator.apply_scenario('sensor_fault', 'node-3');
    } else if (sc === 'burst_traffic') {
      this.system.simulator.apply_scenario('burst_traffic', 'node-1');
    } else if (sc === 'humidity_drift') {
      this.system.simulator.apply_scenario('humidity_drift', 'node-2');
    } else if (sc === 'summary') {
      const snap = this.system.state();
      this._summary = {
        packet_loss_pct: snap.metrics?.packet_loss_pct || 0.0,
        crit_latency_ms: snap.pipeline?.latency?.['0']?.p95 || 4.5,
        prevented_breaches: snap.metrics?.breaches?.prevented || 1,
      };
      this.system.simulator.apply_scenario('normal', 'all');
    }
  }

  private _run_loop(): void {
    const tickIntervalMs = 200;
    const tick = () => {
      if (!this._running) return;

      if (!this._paused) {
        this._remaining_s -= tickIntervalMs / 1000.0;
        if (this._remaining_s <= 0) {
          if (this._current_step < 6) {
            this._advance_to_step(this._current_step + 1);
          } else {
            this.stop();
            return;
          }
        }
      }

      this._loop_id = setTimeout(tick, tickIntervalMs);
    };

    this._loop_id = setTimeout(tick, tickIntervalMs);
  }
}
