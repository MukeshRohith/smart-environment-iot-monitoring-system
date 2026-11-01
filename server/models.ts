/**
 * Data models and enums for the IoT environmental monitoring system.
 */

export enum Priority {
  CRITICAL = 0,
  ANOMALY = 1,
  NORMAL = 2,
}

export enum Level {
  OK = 0,
  WATCH = 1,
  WARNING = 2,
  CRITICAL = 3,
}

export interface Limits {
  safe_min: number;
  safe_max: number;
  hard_min: number;
  hard_max: number;
  slider_min: number;
  slider_max: number;
  unit: string;
}

export interface Reading {
  node_id: string;
  ts: number;
  seq: number;
  temperature: number | null;
  humidity: number | null;
  aqi: number | null;
  status: 'ok' | 'shutdown';
  received_at: number;
  priority: Priority;
}

export interface CleanedReading {
  node_id: string;
  ts: number;
  seq: number;
  priority: Priority;
  raw: Record<string, number | null>;
  clean: Record<string, number | null>;
  smooth: Record<string, number | null>;
  flags: Record<string, string[]>;
  rolling: Record<string, {
    mean: number;
    std: number;
    min: number;
    max: number;
    slope_per_s: number;
  }>;
  quality: number;
  status: string;
  processed_at: number;
  received_at: number;
}

export interface ForecastPoint {
  sim_min: number;
  value: number;
}

export interface Forecast {
  node_id: string;
  metric: string;
  made_at: number;
  horizon_min: number;
  points: ForecastPoint[];
  lower: number[];
  upper: number[];
  predicted_end: number;
  eta_safe_min: number | null;
  eta_hard_min: number | null;
  confidence: number;
  model: 'linear' | 'poly2';
}

export interface ActuationEvent {
  ts: number;
  node_id: string;
  device: string;
  from_state: string;
  to_state: string;
  trigger: 'forecast' | 'threshold' | 'manual' | 'release';
  reason: string;
}
