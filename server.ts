/**
 * Production Express server with full API routes and Vite middleware integration.
 */
import express, { Request, Response } from 'express';
import path from 'path';
import { fileURLToPath } from 'url';
import * as config from './server/config';
import { Priority, Reading } from './server/models';
import { System } from './server/system';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const PORT = parseInt(process.env.PORT || '3000', 10);
const HOST = '0.0.0.0';

const system = new System();
system.start(true);

const startTime = Date.now();

app.use(express.json({ limit: `${config.MAX_PAYLOAD_BYTES}b` }));

// Serve static assets directly
app.use('/static', express.static(path.resolve(__dirname, 'static')));

// ================= API Endpoints =================

// Health
app.get('/api/health', (_req: Request, res: Response) => {
  const uptime = Math.round((Date.now() - startTime) / 100) / 10;
  res.json({
    status: 'ok',
    uptime_s: uptime,
    pipeline_alive: system.pipeline.is_alive(),
    simulator_alive: true,
    queue_depth: system.state().pipeline.queue_depth,
  });
});

// State
app.get('/api/state', (_req: Request, res: Response) => {
  res.json(system.state());
});

// Metrics
app.get('/api/metrics', (_req: Request, res: Response) => {
  res.json(system.metrics.compute());
});

// Node history
app.get('/api/nodes/:node_id/history', (req: Request, res: Response) => {
  const nodeId = req.params.node_id;
  const limitArg = req.query.limit;
  const limit = limitArg ? parseInt(String(limitArg), 10) : config.CHART_POINTS;
  res.json(system.store.history(nodeId, isNaN(limit) ? config.CHART_POINTS : limit));
});

// Profile
app.get('/api/profile', (_req: Request, res: Response) => {
  res.json({
    profile: system.store.get_profile(),
    profiles: Object.keys(config.PROFILES),
    limits: system.store.all_limits(),
  });
});

app.post('/api/profile', (req: Request, res: Response) => {
  const profile = req.body?.profile;
  if (!profile) {
    res.status(400).json({ error: "Missing 'profile' field in payload" });
    return;
  }
  try {
    system.set_profile(profile);
    res.json({
      profile,
      limits: system.store.all_limits(),
    });
  } catch (err: any) {
    res.status(400).json({ error: err.message || String(err) });
  }
});

// Ingest telemetry reading
app.post('/api/ingest', (req: Request, res: Response) => {
  const body = req.body;
  if (!body || typeof body !== 'object') {
    res.status(400).json({ error: 'Malformed JSON payload' });
    return;
  }

  for (const k of ['node_id', 'ts', 'seq']) {
    if (body[k] === undefined || body[k] === null) {
      res.status(400).json({ error: `Missing required key: ${k}` });
      return;
    }
  }

  const reading: Reading = {
    node_id: String(body.node_id).trim(),
    ts: parseFloat(body.ts),
    seq: parseInt(body.seq, 10),
    temperature: body.temperature !== undefined && body.temperature !== null ? parseFloat(body.temperature) : null,
    humidity: body.humidity !== undefined && body.humidity !== null ? parseFloat(body.humidity) : null,
    aqi: body.aqi !== undefined && body.aqi !== null ? parseFloat(body.aqi) : null,
    status: body.status === 'shutdown' ? 'shutdown' : 'ok',
    received_at: body.received_at ? parseFloat(body.received_at) : Date.now() / 1000,
    priority: body.priority !== undefined ? parseInt(body.priority, 10) : Priority.NORMAL,
  };

  const result = system.pipeline.submit(reading);
  if (result === 'accepted') {
    const prioName = Priority[reading.priority] || 'NORMAL';
    res.status(202).json({ status: 'accepted', priority: prioName });
  } else {
    res.status(429).json({ error: 'Queue full, reading dropped' });
  }
});

// Simulator state & controls
app.get('/api/sim/state', (_req: Request, res: Response) => {
  res.json(system.simulator.get_state());
});

app.post('/api/sim/controls', (req: Request, res: Response) => {
  const data = req.body || {};
  const node_id = data.node_id || data.node;
  let controls = data.controls;
  if (!controls || typeof controls !== 'object') {
    controls = { ...data };
    delete controls.node_id;
    delete controls.node;
  }

  if (!node_id || !controls || Object.keys(controls).length === 0) {
    res.status(400).json({ error: 'node_id and controls dict required' });
    return;
  }

  try {
    system.simulator.set_controls(node_id, controls);
    const state = system.simulator.get_state();
    res.json(state.nodes[node_id] || {});
  } catch (err: any) {
    res.status(400).json({ error: err.message || String(err) });
  }
});

app.post('/api/sim/scenario', (req: Request, res: Response) => {
  const data = req.body || {};
  const name = data.name || data.scenario;
  const node_id = data.node_id || data.node;
  if (!name) {
    res.status(400).json({ error: "Missing scenario 'name'" });
    return;
  }

  try {
    system.simulator.apply_scenario(name, node_id);
    res.json({ scenario: name, node_id, status: 'applied' });
  } catch (err: any) {
    res.status(400).json({ error: err.message || String(err) });
  }
});

app.post('/api/sim/nodes', (req: Request, res: Response) => {
  const data = req.body || {};
  const action = data.action || 'add';
  const node_id = data.node_id;

  if (action !== 'add' && action !== 'remove') {
    res.status(400).json({ error: "action must be 'add' or 'remove'" });
    return;
  }

  try {
    let nid: string;
    if (action === 'add') {
      nid = system.simulator.add_node(node_id);
    } else {
      nid = system.simulator.remove_node(node_id);
    }
    res.json({
      action,
      node_id: nid,
      nodes: system.store.node_ids(),
    });
  } catch (err: any) {
    res.status(400).json({ error: err.message || String(err) });
  }
});

app.delete('/api/sim/nodes/:node_id', (req: Request, res: Response) => {
  const nodeId = req.params.node_id;
  try {
    const nid = system.simulator.remove_node(nodeId);
    res.json({
      action: 'remove',
      node_id: nid,
      nodes: system.store.node_ids(),
    });
  } catch (err: any) {
    res.status(400).json({ error: err.message || String(err) });
  }
});

// Pipeline config
app.post('/api/pipeline/config', (req: Request, res: Response) => {
  try {
    const updated = system.apply_settings(req.body || {});
    res.json(updated);
  } catch (err: any) {
    res.status(400).json({ error: err.message || String(err) });
  }
});

// Forecast config
app.post('/api/forecast/config', (req: Request, res: Response) => {
  try {
    const updated = system.apply_settings(req.body || {});
    res.json(updated);
  } catch (err: any) {
    res.status(400).json({ error: err.message || String(err) });
  }
});

// Strategy
app.post('/api/control/strategy', (req: Request, res: Response) => {
  const strategy = req.body?.strategy;
  if (!strategy) {
    res.status(400).json({ error: "Missing 'strategy' field" });
    return;
  }
  try {
    system.apply_settings({ strategy });
    res.json({ strategy: system.get_settings().strategy });
  } catch (err: any) {
    res.status(400).json({ error: err.message || String(err) });
  }
});

// Actuator overrides
app.post('/api/actuators/override', (req: Request, res: Response) => {
  const data = req.body || {};
  const node_id = data.node_id || data.node;
  const device = data.device;
  const action = data.action;

  if (!node_id || !device || !action) {
    res.status(400).json({ error: 'node_id, device, and action required' });
    return;
  }
  if (!['on', 'off', 'auto'].includes(action)) {
    res.status(400).json({ error: "action must be 'on', 'off', or 'auto'" });
    return;
  }

  system.actuator.override(node_id, device, action);
  res.json({ node_id, devices: system.store.devices(node_id) });
});

app.post('/api/actuators/restart', (req: Request, res: Response) => {
  const node_id = req.body?.node_id || req.body?.node;
  if (!node_id) {
    res.status(400).json({ error: "Missing 'node_id'" });
    return;
  }

  system.actuator.restart(node_id);
  res.json({
    node_id,
    status: 'restarted',
    shutdown: system.actuator.is_shutdown(node_id),
  });
});

// Benchmark
app.post('/api/benchmark/run', (req: Request, res: Response) => {
  const name = req.body?.name;
  const duration_s = req.body?.duration_s;
  if (!name) {
    res.status(400).json({ error: "Missing benchmark 'name'" });
    return;
  }
  try {
    system.benchmark.run(name, duration_s ? parseFloat(duration_s) : undefined);
    res.json(system.benchmark.status());
  } catch (err: any) {
    res.status(400).json({ error: err.message || String(err) });
  }
});

app.get('/api/benchmark/status', (_req: Request, res: Response) => {
  res.json(system.benchmark.status());
});

app.get('/api/benchmark/results', (_req: Request, res: Response) => {
  res.json(system.benchmark.results());
});

app.get('/api/benchmark/results.csv', (_req: Request, res: Response) => {
  res.setHeader('Content-Type', 'text/csv');
  res.send(system.benchmark.results_csv());
});

// Guided Demo
app.post('/api/demo/:action', (req: Request, res: Response) => {
  const action = req.params.action;
  if (action === 'start') {
    system.demo.start();
  } else if (action === 'pause') {
    system.demo.pause();
  } else if (action === 'resume') {
    system.demo.resume();
  } else if (action === 'stop') {
    system.demo.stop();
  } else {
    res.status(400).json({ error: `Invalid demo action: ${action}` });
    return;
  }
  res.json(system.demo.state());
});

// ================= Demonstration Playback Control (Pause, Resume, Reset) =================

app.post('/api/system/pause', (_req: Request, res: Response) => {
  system.pause();
  res.json({
    status: 'paused',
    paused: true,
    message: 'System simulation and ingestion paused.',
  });
});

app.post('/api/system/resume', (_req: Request, res: Response) => {
  system.resume();
  res.json({
    status: 'running',
    paused: false,
    message: 'System simulation and ingestion resumed.',
  });
});

app.post('/api/system/reset', (_req: Request, res: Response) => {
  system.reset_runtime();
  res.json({
    status: 'reset',
    paused: system.is_paused(),
    message: 'System runtime, telemetry buffers, and metrics reset to clean baseline.',
  });
});

app.get('/api/system/status', (_req: Request, res: Response) => {
  res.json({
    status: system.is_paused() ? 'paused' : 'running',
    paused: system.is_paused(),
  });
});

// ================= Frontend Static & Vite Integration =================

async function startServer() {
  if (process.env.NODE_ENV !== 'production') {
    const { createServer: createViteServer } = await import('vite');
    const vite = await createViteServer({
      server: {
        middlewareMode: true,
        hmr: false,
        watch: null,
      },
      appType: 'custom',
    });
    app.use(vite.middlewares);

    app.get('/', (_req: Request, res: Response) => {
      res.sendFile(path.resolve(__dirname, 'index.html'));
    });
    app.get('*', (_req: Request, res: Response) => {
      res.sendFile(path.resolve(__dirname, 'index.html'));
    });
  } else {
    app.use(express.static(path.resolve(__dirname, 'dist')));
    app.get('*', (_req: Request, res: Response) => {
      res.sendFile(path.resolve(__dirname, 'dist/index.html'));
    });
  }

  app.listen(PORT, HOST, () => {
    console.log(`[IoT Monitor] Express server running at http://${HOST}:${PORT}`);
  });
}

startServer().catch((err) => {
  console.error('Failed to start server:', err);
  process.exit(1);
});
