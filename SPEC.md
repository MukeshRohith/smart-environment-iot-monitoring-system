# Smart IoT Environmental Monitoring & Predictive Automation System (Python)

GOAL
Simulated IoT nodes stream temperature, humidity and AQI over HTTP to a Flask server. A priority queue with worker threads feeds a pipeline: wrangle (clean) -> forecast (15-30 sim-minutes ahead) -> actuate (fan / hvac / dehumidifier / purifier / soft shutdown). Actuators change the simulated readings (closed loop). A sleek dashboard controls the simulation with sliders and shows everything the system does.

WORKING RULES (save tokens)
- Build each module ONCE in final form. No prototypes, temporary code, stubs or placeholders. If a dependency is not built yet, code against its interface in this spec.
- Touch only the files named in the task. Never reprint unchanged files.
- Each task ends with a reply of at most 6 lines: files written, how to run its tests, assumptions.
- Each task includes a small pytest file; tests must pass before replying.
- No extra features, no TODOs. Type hints and one-line docstrings.
- Python 3.11+, Flask, pandas, numpy, scikit-learn, requests, pytest. Concurrency: threading + queue.PriorityQueue. No database: bounded in-memory deques. Frontend: one HTML, one CSS, one JS file, Chart.js from CDN, no frameworks or build tools.
- All shared state lives in Store (one RLock). No other global mutable state.

STRUCTURE
iot_monitor/: app.py run.py config.py requirements.txt README.md
core/: __init__ models store wrangler forecaster actuator pipeline simulator metrics benchmark demo system
templates/index.html  static/css/style.css  static/js/app.js  tests/  docs/esp32_example.ino

CONFIG (config.py; every value overridable via env var IOT_<NAME>)
PORT=5000; METRICS=("temperature","humidity","aqi")
PHYSICAL ranges: temperature -40..125, humidity 0..100, aqi 0..500
PROFILES (safe_min, safe_max, hard_max, unit): 
 data_center: temperature 18,27,35 "°C"; humidity 40,60,80 "%"; aqi 0,100,200 "AQI"
 cold_storage: temperature 2,8,12; humidity 30,60,80; aqi 0,100,200
 greenhouse: temperature 18,30,38; humidity 50,80,95; aqi 0,100,200
DEFAULT_PROFILE="data_center"
Derived limits: hard_min = safe_min-(hard_max-safe_max) floored at the physical min. span=hard_max-hard_min; slider_min=max(phys_min, hard_min-0.25*span); slider_max=min(phys_max, hard_max+0.25*span).
Pipeline: QUEUE_SIZE=500; WORKER_COUNT=3 (1-8); PROCESSING_DELAY_MS=5 (0-200); PIPELINE_MODE="priority" ("priority"|"fifo"); JUMP_LIMITS temperature 5, humidity 15, aqi 80; STUCK_REPEATS=10
Wrangler: WINDOW=30; EMA_ALPHA=0.4; MAX_FFILL=5; MAD_K=6; MAD_FLOOR temperature 0.3, humidity 1.0, aqi 3.0; PERSIST_N=3; DF_MAX_ROWS=120
Forecast: SIM_TIME_SCALE=60 (10-300; 1 real second = SCALE/60 sim-minutes); HORIZON_MIN=15 (15-30); WINDOW_POINTS=60; MIN_POINTS=20; RETRAIN_EVERY=5; STEP_MIN=5; WATCH_CONFIDENCE=0.6
 real_seconds = sim_minutes*60/SIM_TIME_SCALE
Actuator: HYST_MARGIN=0.10; HYST_READINGS=10; MIN_ON_SECONDS=5; STRATEGY="proactive" ("proactive"|"reactive")
 EFFECTS (offset added to the simulator's target for that node): 
  fan low {temperature:-2, aqi:-10}, high {temperature:-4, aqi:-20}
  hvac low {temperature:-5, humidity:-5}, high {temperature:-12, humidity:-10}
  dehumidifier low {humidity:-8}, high {humidity:-18}
  purifier low {aqi:-30}, high {aqi:-70}
  shutdown {temperature:-15}
 Sign is flipped for a metric whose last direction is "low" (below safe_min); purifier and shutdown never flip.
Simulator: LAG_TAU_S=20; DEFAULT_SEND_HZ=1 (0.2-20); MAX_NODES=8; DEFAULT_NODES=3; DEFAULT_NOISE=0.2 (0-1); FAULT_MODES=("none","dropout","outlier","stuck"); base noise sigma temperature 0.3, humidity 1.0, aqi 4.0
Sizes: HISTORY_LEN=600; CHART_POINTS=120; EVENT_LOG_LEN=300; ACTUATION_LOG_LEN=200; LATENCY_SAMPLES=500; MAX_PAYLOAD_BYTES=2048

MODELS (core/models.py; all have to_dict())
Priority(IntEnum): CRITICAL=0, ANOMALY=1, NORMAL=2.  Level(IntEnum): OK=0, WATCH=1, WARNING=2, CRITICAL=3 (serialized by name).
Reading: node_id, ts, seq, temperature, humidity, aqi (float|None), status "ok"|"shutdown", received_at=0.0, priority=NORMAL; from_dict() validates and raises ValueError (reject non-numeric, NaN, inf, missing node_id/ts/seq).
CleanedReading: node_id, ts, seq, priority, raw{m}, clean{m}, smooth{m}, flags{m:[str]}, rolling{m:{mean,std,min,max,slope_per_s}}, quality(0-1), status, processed_at, received_at.
Forecast: node_id, metric, made_at, horizon_min, points[{sim_min,value}], lower[], upper[], predicted_end, eta_safe_min|None, eta_hard_min|None, confidence, model "linear"|"poly2".
ActuationEvent: ts, node_id, device, from_state, to_state, trigger "forecast"|"threshold"|"manual"|"release", reason.
Limits: safe_min, safe_max, hard_min, hard_max, slider_min, slider_max, unit.

STORE (core/store.py), thread-safe, bounded
 profile: get_profile, set_profile(name), limits(metric)->Limits, all_limits()
 settings: get_settings/update_settings(partial) over {strategy, pipeline_mode, workers, processing_delay_ms, horizon_min, time_scale}
 nodes: register_node, unregister_node, set_node_controls(node, dict), node_ids()
 data: add_raw(reading), add_cleaned(cleaned) (also updates per-node/metric exposure: peak, seconds_outside_safe, seconds_above_hard, integrated using dt between cleaned readings), latest_cleaned(node), history(node, limit)
 forecasts: set_forecasts(node, {metric: Forecast}), forecasts(node); record_forecast_error(metric, node, abs_err), forecast_error_stats()->{metric:{mae,rmse,n}}
 actuation: set_devices(node, devices) (also accumulates device_on_seconds), devices(node), set_levels(node, {metric: Level}), levels(node), record_level_change(node, metric, old, new, ts), level_transitions(), add_actuation(event), actuations(limit)
 events: log_event(level "info"|"warn"|"critical", source, message), events(limit)
 counters: incr(name, n=1, priority=None) for received, processed, rejected, dropped, errors; counters(); record_latency(priority, seconds); latency_stats()->{priority:{avg,p50,p95,p99,n}}
 reset_runtime(): clear data, forecasts, devices, levels, transitions, exposure, events, counters, latencies; keep profile, settings, node registrations.
 snapshot() -> dict, schema below (store-owned parts only).

COMPONENT INTERFACES
 Wrangler(store): process(reading)->CleanedReading; reset(); avg_ms
 Forecaster(store): update(cleaned)->dict[metric, Forecast] (cached between retrains, {} until MIN_POINTS); reset()
 ActuatorEngine(store): evaluate(cleaned, forecasts)->list[ActuationEvent]; override(node, device, "on"|"off"|"auto"); restart(node); effects_for(node)->{metric: float}; is_shutdown(node); reset()
 Pipeline(store, wrangler, forecaster, actuator): start(); stop(); submit(reading)->"accepted"|"dropped"; apply_settings(); snapshot()
 SimulatorManager(store, effects_provider, shutdown_provider, transport=None): start(); stop(); set_controls(node, dict); add_node(); remove_node(node); apply_scenario(name); reset_to_profile(); get_state()
 MetricsEngine(store): compute()->dict
 BenchmarkRunner(system), DemoRunner(system)
 System (core/system.py) owns all components and exposes: store, wrangler, forecaster, actuator, pipeline, simulator, metrics, benchmark, demo, start(), stop(), reset_runtime(), get_settings(), apply_settings(partial), set_profile(name), state()->store.snapshot() plus keys "pipeline","metrics","benchmark","demo"

SNAPSHOT SCHEMA (/api/state)
{ts, profile, limits:{metric:Limits}, settings:{...}, counters:{received,processed,rejected,dropped,errors,by_priority:{"0","1","2"}},
 nodes:{id:{controls:{target:{m},current:{m},noise,send_rate_hz,fault_mode,enabled}, status:"online"|"offline"|"shutdown", latest:CleanedReading|null, levels:{m:"OK"|"WATCH"|"WARNING"|"CRITICAL"}, level:worst, forecast:{m:Forecast}, devices:{fan,hvac,dehumidifier,purifier:{state:"off"|"low"|"high",mode:"auto"|"manual"}, shutdown:bool}, history:{ts:[],raw:{m:[]},smooth:{m:[]}}}},
 events:[last 100], actuations:[last 50], pipeline:{queue_depth,depth_by_priority,mode,workers,processing_delay_ms,latency,throughput,avg_wrangle_ms}, metrics:{...}, benchmark:{...}, demo:{...}}

HTTP API
 GET / ; GET /api/health ; GET /api/state ; GET /api/metrics ; GET /api/nodes/<id>/history?limit=
 GET,POST /api/profile ; POST /api/ingest (202 accepted, 400 invalid, 413 too large, 429 dropped)
 GET /api/sim/state ; POST /api/sim/controls ; POST /api/sim/scenario ; POST /api/sim/nodes
 POST /api/pipeline/config ; POST /api/forecast/config ; POST /api/control/strategy
 POST /api/actuators/override ; POST /api/actuators/restart
 POST /api/benchmark/run ; GET /api/benchmark/status ; GET /api/benchmark/results ; GET /api/benchmark/results.csv
 POST /api/demo/start|pause|resume|stop
 All errors return JSON {"error": "..."}.
