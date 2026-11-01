/**
 * Configuration settings for IoT Environmental Monitoring system.
 */
import { Limits } from './models';

export const PORT = parseInt(process.env.IOT_PORT || '3000', 10);
export const METRICS = ['temperature', 'humidity', 'aqi'] as const;
export type Metric = (typeof METRICS)[number];

export const PHYSICAL: Record<Metric, [number, number]> = {
  temperature: [-40.0, 125.0],
  humidity: [0.0, 100.0],
  aqi: [0.0, 500.0],
};

export const PROFILES: Record<string, Record<Metric, [number, number, number, string]>> = {
  data_center: {
    temperature: [18.0, 27.0, 35.0, '°C'],
    humidity: [40.0, 60.0, 80.0, '%'],
    aqi: [0.0, 100.0, 200.0, 'AQI'],
  },
  cold_storage: {
    temperature: [2.0, 8.0, 12.0, '°C'],
    humidity: [30.0, 60.0, 80.0, '%'],
    aqi: [0.0, 100.0, 200.0, 'AQI'],
  },
  greenhouse: {
    temperature: [18.0, 30.0, 38.0, '°C'],
    humidity: [50.0, 80.0, 95.0, '%'],
    aqi: [0.0, 100.0, 200.0, 'AQI'],
  },
};

export const DEFAULT_PROFILE = process.env.IOT_DEFAULT_PROFILE || 'data_center';

// Pipeline
export const QUEUE_SIZE = parseInt(process.env.IOT_QUEUE_SIZE || '500', 10);
export const WORKER_COUNT = parseInt(process.env.IOT_WORKER_COUNT || '3', 10);
export const PROCESSING_DELAY_MS = parseFloat(process.env.IOT_PROCESSING_DELAY_MS || '5');
export const PIPELINE_MODE = process.env.IOT_PIPELINE_MODE || 'priority';
export const JUMP_LIMITS: Record<Metric, number> = {
  temperature: parseFloat(process.env.IOT_JUMP_LIMIT_TEMPERATURE || '5.0'),
  humidity: parseFloat(process.env.IOT_JUMP_LIMIT_HUMIDITY || '15.0'),
  aqi: parseFloat(process.env.IOT_JUMP_LIMIT_AQI || '80.0'),
};
export const STUCK_REPEATS = parseInt(process.env.IOT_STUCK_REPEATS || '10', 10);

// Wrangler
export const WINDOW = parseInt(process.env.IOT_WINDOW || '30', 10);
export const EMA_ALPHA = parseFloat(process.env.IOT_EMA_ALPHA || '0.4');
export const MAX_FFILL = parseInt(process.env.IOT_MAX_FFILL || '5', 10);
export const MAD_K = parseFloat(process.env.IOT_MAD_K || '6.0');
export const MAD_FLOOR: Record<Metric, number> = {
  temperature: parseFloat(process.env.IOT_MAD_FLOOR_TEMPERATURE || '0.3'),
  humidity: parseFloat(process.env.IOT_MAD_FLOOR_HUMIDITY || '1.0'),
  aqi: parseFloat(process.env.IOT_MAD_FLOOR_AQI || '3.0'),
};
export const PERSIST_N = parseInt(process.env.IOT_PERSIST_N || '3', 10);
export const DF_MAX_ROWS = parseInt(process.env.IOT_DF_MAX_ROWS || '120', 10);

// Forecast
export const SIM_TIME_SCALE = parseFloat(process.env.IOT_SIM_TIME_SCALE || '60.0');
export const HORIZON_MIN = parseFloat(process.env.IOT_HORIZON_MIN || '15.0');
export const WINDOW_POINTS = parseInt(process.env.IOT_WINDOW_POINTS || '60', 10);
export const MIN_POINTS = parseInt(process.env.IOT_MIN_POINTS || '20', 10);
export const RETRAIN_EVERY = parseInt(process.env.IOT_RETRAIN_EVERY || '5', 10);
export const STEP_MIN = parseFloat(process.env.IOT_STEP_MIN || '5.0');
export const WATCH_CONFIDENCE = parseFloat(process.env.IOT_WATCH_CONFIDENCE || '0.6');

// Actuator
export const HYST_MARGIN = parseFloat(process.env.IOT_HYST_MARGIN || '0.10');
export const HYST_READINGS = parseInt(process.env.IOT_HYST_READINGS || '10', 10);
export const MIN_ON_SECONDS = parseFloat(process.env.IOT_MIN_ON_SECONDS || '5.0');
export const STRATEGY = process.env.IOT_STRATEGY || 'proactive';
export const EFFECTS: Record<string, any> = {
  fan: { low: { temperature: -2.0, aqi: -10.0 }, high: { temperature: -4.0, aqi: -20.0 } },
  hvac: { low: { temperature: -5.0, humidity: -5.0 }, high: { temperature: -12.0, humidity: -10.0 } },
  dehumidifier: { low: { humidity: -8.0 }, high: { humidity: -18.0 } },
  purifier: { low: { aqi: -30.0 }, high: { aqi: -70.0 } },
  shutdown: { temperature: -15.0 },
};

// Simulator
export const LAG_TAU_S = parseFloat(process.env.IOT_LAG_TAU_S || '20.0');
export const DEFAULT_SEND_HZ = parseFloat(process.env.IOT_DEFAULT_SEND_HZ || '1.0');
export const MAX_NODES = parseInt(process.env.IOT_MAX_NODES || '8', 10);
export const DEFAULT_NODES = parseInt(process.env.IOT_DEFAULT_NODES || '3', 10);
export const DEFAULT_NOISE = parseFloat(process.env.IOT_DEFAULT_NOISE || '0.2');
export const FAULT_MODES = ['none', 'dropout', 'outlier', 'stuck'] as const;
export const BASE_NOISE_SIGMA: Record<Metric, number> = { temperature: 0.3, humidity: 1.0, aqi: 4.0 };

// Sizes
export const HISTORY_LEN = parseInt(process.env.IOT_HISTORY_LEN || '600', 10);
export const CHART_POINTS = parseInt(process.env.IOT_CHART_POINTS || '120', 10);
export const EVENT_LOG_LEN = parseInt(process.env.IOT_EVENT_LOG_LEN || '300', 10);
export const ACTUATION_LOG_LEN = parseInt(process.env.IOT_ACTUATION_LOG_LEN || '200', 10);
export const LATENCY_SAMPLES = parseInt(process.env.IOT_LATENCY_SAMPLES || '500', 10);
export const MAX_PAYLOAD_BYTES = parseInt(process.env.IOT_MAX_PAYLOAD_BYTES || '2048', 10);

export function deriveLimits(profileName: string, metric: Metric): Limits {
  const prof = PROFILES[profileName] || PROFILES[DEFAULT_PROFILE];
  const [safeMin, safeMax, hardMax, unit] = prof[metric];
  const [physMin, physMax] = PHYSICAL[metric];

  const hardMin = Math.max(physMin, safeMin - (hardMax - safeMax));
  const span = hardMax - hardMin;
  const sliderMin = Math.max(physMin, hardMin - 0.25 * span);
  const sliderMax = Math.min(physMax, hardMax + 0.25 * span);

  return {
    safe_min: safeMin,
    safe_max: safeMax,
    hard_min: hardMin,
    hard_max: hardMax,
    slider_min: sliderMin,
    slider_max: sliderMax,
    unit,
  };
}

export function deriveAllLimits(profileName: string): Record<Metric, Limits> {
  return {
    temperature: deriveLimits(profileName, 'temperature'),
    humidity: deriveLimits(profileName, 'humidity'),
    aqi: deriveLimits(profileName, 'aqi'),
  };
}
