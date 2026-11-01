/**
 * Smart IoT Environmental Monitoring & Predictive Automation Dashboard
 * Production single-file vanilla JavaScript frontend.
 */

(function () {
  'use strict';

  // --- Global Application State ---
  const state = {
    activeNodeId: 'node-1',
    activeMetric: 'temperature',
    activeScenario: 'normal',
    scenarioScope: 'node',
    activeLogFilter: 'all',
    autoScroll: true,
    activeDragging: new Set(),
    lastSnapshot: null,
    debounceTimers: {},
    benchmarkTimer: null,
    chart: null,
    isEvalOpen: false,
    pollInterval: 1000,
    consecutiveFailures: 0
  };

  // --- API Helper ---
  async function api(path, options = {}) {
    const fetchOptions = {
      headers: {
        'Accept': 'application/json',
        'Content-Type': 'application/json'
      },
      ...options
    };
    if (fetchOptions.body && typeof fetchOptions.body === 'object') {
      fetchOptions.body = JSON.stringify(fetchOptions.body);
    }

    const t0 = performance.now();
    try {
      const res = await fetch(path, fetchOptions);
      const duration = performance.now() - t0;
      if (!res.ok) {
        let errData;
        try { errData = await res.json(); } catch (_) { errData = { error: res.statusText }; }
        throw new Error(errData.error || `HTTP ${res.status}`);
      }
      const data = await res.json();
      return { data, duration };
    } catch (err) {
      throw err;
    }
  }

  // --- Debounce Helper for User Inputs ---
  function debounce(key, delay, fn) {
    if (state.debounceTimers[key]) {
      clearTimeout(state.debounceTimers[key]);
    }
    state.debounceTimers[key] = setTimeout(() => {
      delete state.debounceTimers[key];
      fn();
    }, delay);
  }

  // --- DOM Elements Cache ---
  const dom = {};
  function cacheDom() {
    dom.connBanner = document.getElementById('conn-banner');
    dom.dismissBannerBtn = document.getElementById('dismiss-banner-btn');
    dom.connPill = document.getElementById('connection-pill');
    dom.profileSelect = document.getElementById('profile-select');
    dom.stratProactive = document.getElementById('strat-proactive');
    dom.stratReactive = document.getElementById('strat-reactive');
    dom.modePriority = document.getElementById('mode-priority');
    dom.modeFifo = document.getElementById('mode-fifo');
    dom.timescaleChip = document.getElementById('timescale-chip');
    dom.openEvalBtn = document.getElementById('open-eval-btn');
    dom.toggleDemoBtn = document.getElementById('toggle-demo-btn');
    dom.togglePauseBtn = document.getElementById('toggle-pause-btn');
    dom.pauseBtnIcon = document.getElementById('pause-btn-icon');
    dom.pauseBtnLabel = document.getElementById('pause-btn-label');
    dom.resetSystemBtn = document.getElementById('reset-system-btn');
    dom.systemPausedPill = document.getElementById('system-paused-pill');

    // KPI
    dom.kpiNodes = document.getElementById('kpi-nodes');
    dom.kpiNodesSub = document.getElementById('kpi-nodes-sub');
    dom.kpiThroughput = document.getElementById('kpi-throughput');
    dom.kpiProcessedSub = document.getElementById('kpi-processed-sub');
    dom.kpiQueue = document.getElementById('kpi-queue');
    dom.kpiQueueSub = document.getElementById('kpi-queue-sub');
    dom.kpiDropped = document.getElementById('kpi-dropped');
    dom.kpiLossSub = document.getElementById('kpi-loss-sub');
    dom.kpiAlerts = document.getElementById('kpi-alerts');
    dom.kpiAlertsSub = document.getElementById('kpi-alerts-sub');
    dom.kpiLatency = document.getElementById('kpi-latency');
    dom.kpiWrangleSub = document.getElementById('kpi-wrangle-sub');

    // Left controls
    dom.simNodeTabs = document.getElementById('sim-node-tabs');
    dom.addNodeBtn = document.getElementById('add-node-btn');
    dom.removeNodeBtn = document.getElementById('remove-node-btn');
    dom.sliderTemp = document.getElementById('slider-temp');
    dom.badgeTemp = document.getElementById('badge-temp');
    dom.boundTempMin = document.getElementById('bound-temp-min');
    dom.boundTempMax = document.getElementById('bound-temp-max');
    dom.sliderHum = document.getElementById('slider-hum');
    dom.badgeHum = document.getElementById('badge-hum');
    dom.boundHumMin = document.getElementById('bound-hum-min');
    dom.boundHumMax = document.getElementById('bound-hum-max');
    dom.sliderAqi = document.getElementById('slider-aqi');
    dom.badgeAqi = document.getElementById('badge-aqi');
    dom.boundAqiMin = document.getElementById('bound-aqi-min');
    dom.boundAqiMax = document.getElementById('bound-aqi-max');
    dom.sliderNoise = document.getElementById('slider-noise');
    dom.badgeNoise = document.getElementById('badge-noise');
    dom.sliderRate = document.getElementById('slider-rate');
    dom.badgeRate = document.getElementById('badge-rate');
    dom.faultModeGroup = document.getElementById('fault-mode-group');
    dom.nodeEnableToggle = document.getElementById('node-enable-toggle');
    dom.sliderWorkers = document.getElementById('slider-workers');
    dom.badgeWorkers = document.getElementById('badge-workers');
    dom.sliderDelay = document.getElementById('slider-delay');
    dom.badgeDelay = document.getElementById('badge-delay');
    dom.sliderHorizon = document.getElementById('slider-horizon');
    dom.badgeHorizon = document.getElementById('badge-horizon');

    // Center monitor
    dom.activeNodeName = document.getElementById('active-node-name');
    dom.chartCanvas = document.getElementById('telemetry-chart');
    dom.healthTilesGrid = document.getElementById('health-tiles-grid');

    // Internals
    dom.flowIngest = document.getElementById('flow-ingest');
    dom.flowQueue = document.getElementById('flow-queue');
    dom.qP0 = document.getElementById('q-p0');
    dom.qP1 = document.getElementById('q-p1');
    dom.qP2 = document.getElementById('q-p2');
    dom.flowWorkers = document.getElementById('flow-workers');
    dom.flowWrangle = document.getElementById('flow-wrangle');
    dom.flowForecast = document.getElementById('flow-forecast');
    dom.flowActuations = document.getElementById('flow-actuations');
    dom.latP0 = document.getElementById('lat-p0');
    dom.latP1 = document.getElementById('lat-p1');
    dom.latP2 = document.getElementById('lat-p2');
    dom.actuatorChipsContainer = document.getElementById('actuator-chips-container');
    dom.restartNodeBtn = document.getElementById('restart-node-btn');
    dom.pauseScrollBtn = document.getElementById('pause-scroll-btn');
    dom.eventLogList = document.getElementById('event-log-list');

    // Demo Bar and Scenario Pills
    dom.demoBar = document.getElementById('demo-bar');
    dom.demoStepBadge = document.getElementById('demo-step-badge');
    dom.demoCaption = document.getElementById('demo-caption');
    dom.demoCountdown = document.getElementById('demo-countdown');
    dom.demoPauseBtn = document.getElementById('demo-pause-btn');
    dom.demoStopBtn = document.getElementById('demo-stop-btn');
    dom.demoBarScenarioName = document.getElementById('demo-bar-scenario-name');
    dom.topbarDemoPill = document.getElementById('topbar-demo-pill');
    dom.topbarDemoScenario = document.getElementById('topbar-demo-scenario');
    dom.topbarDemoTimer = document.getElementById('topbar-demo-timer');
    dom.demoScenarioPill = document.getElementById('demo-scenario-pill');
    dom.demoPillTitle = document.getElementById('demo-pill-title');
    dom.demoPillTimer = document.getElementById('demo-pill-timer');
    dom.scopeNodeBtn = document.getElementById('scope-node-btn');
    dom.scopeAllBtn = document.getElementById('scope-all-btn');
    dom.scopeNodeLabel = document.getElementById('scope-node-label');

    // Evaluation Modal
    dom.evalModal = document.getElementById('eval-modal');
    dom.closeEvalBtn = document.getElementById('close-eval-btn');
    dom.exportCsvBtn = document.getElementById('export-csv-btn');
    dom.evalLoss = document.getElementById('eval-loss');
    dom.evalCritLat = document.getElementById('eval-crit-lat');
    dom.evalMae = document.getElementById('eval-mae');
    dom.evalAnticipated = document.getElementById('eval-anticipated');
    dom.evalPrevented = document.getElementById('eval-prevented');
    dom.evalLeadtime = document.getElementById('eval-leadtime');
    dom.runBenchStrategy = document.getElementById('run-bench-strategy');
    dom.runBenchPipeline = document.getElementById('run-bench-pipeline');
    dom.benchProgressContainer = document.getElementById('bench-progress-container');
    dom.benchPhaseLabel = document.getElementById('bench-phase-label');
    dom.benchPctLabel = document.getElementById('bench-pct-label');
    dom.benchProgressFill = document.getElementById('bench-progress-fill');
    dom.benchResultsContainer = document.getElementById('bench-results-container');
  }

  // --- Track Gradient Generator for Sliders ---
  function updateSliderTrackGradient(slider, limits) {
    if (!slider || !limits) return;
    const min = limits.slider_min;
    const max = limits.slider_max;
    const range = max - min;
    if (range <= 0) return;

    const p = val => Math.max(0, Math.min(100, ((val - min) / range) * 100));

    const pHardMin = p(limits.hard_min);
    const pSafeMin = p(limits.safe_min);
    const pSafeMax = p(limits.safe_max);
    const pHardMax = p(limits.hard_max);

    // Color zones:
    // min -> hard_min: critical (#ff5470)
    // hard_min -> safe_min: warning (#ff9f43)
    // safe_min -> safe_max: ok (#2dd4a0)
    // safe_max -> hard_max: warning (#ff9f43)
    // hard_max -> max: critical (#ff5470)
    const gradient = `linear-gradient(to right,
      #ff5470 0%, #ff5470 ${pHardMin}%,
      #ff9f43 ${pHardMin}%, #ff9f43 ${pSafeMin}%,
      #2dd4a0 ${pSafeMin}%, #2dd4a0 ${pSafeMax}%,
      #ff9f43 ${pSafeMax}%, #ff9f43 ${pHardMax}%,
      #ff5470 ${pHardMax}%, #ff5470 100%)`;

    slider.style.background = gradient;
  }

  // --- Chart.js Setup ---
  function initChart() {
    if (!window.Chart || !dom.chartCanvas) return;
    const ctx = dom.chartCanvas.getContext('2d');

    state.chart = new Chart(ctx, {
      type: 'line',
      data: {
        labels: [],
        datasets: [
          {
            label: 'Raw Telemetry',
            data: [],
            borderColor: 'rgba(76, 201, 240, 0.4)',
            backgroundColor: 'rgba(76, 201, 240, 0.4)',
            showLine: false,
            pointRadius: 2.5,
            pointHoverRadius: 4,
            order: 5
          },
          {
            label: 'EMA Smoothed',
            data: [],
            borderColor: '#4cc9f0',
            backgroundColor: 'transparent',
            borderWidth: 2,
            pointRadius: 0,
            tension: 0.1,
            order: 4
          },
          {
            label: 'Predictive Forecast',
            data: [],
            borderColor: '#7c5cff',
            backgroundColor: 'transparent',
            borderWidth: 2,
            borderDash: [5, 4],
            pointRadius: 0,
            tension: 0.15,
            order: 3
          },
          {
            label: 'Upper Confidence (95%)',
            data: [],
            borderColor: 'transparent',
            backgroundColor: 'rgba(124, 92, 255, 0.14)',
            fill: '+1',
            pointRadius: 0,
            order: 2
          },
          {
            label: 'Lower Confidence (95%)',
            data: [],
            borderColor: 'transparent',
            backgroundColor: 'transparent',
            pointRadius: 0,
            order: 1
          },
          {
            label: 'Safe Max',
            data: [],
            borderColor: 'rgba(45, 212, 160, 0.8)',
            borderWidth: 1.5,
            borderDash: [6, 4],
            pointRadius: 0,
            fill: false
          },
          {
            label: 'Safe Min',
            data: [],
            borderColor: 'rgba(45, 212, 160, 0.8)',
            borderWidth: 1.5,
            borderDash: [6, 4],
            pointRadius: 0,
            fill: false
          },
          {
            label: 'Hard Max',
            data: [],
            borderColor: 'rgba(255, 84, 112, 0.8)',
            borderWidth: 1.5,
            borderDash: [4, 4],
            pointRadius: 0,
            fill: false
          },
          {
            label: 'Hard Min',
            data: [],
            borderColor: 'rgba(255, 84, 112, 0.8)',
            borderWidth: 1.5,
            borderDash: [4, 4],
            pointRadius: 0,
            fill: false
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: {
          mode: 'index',
          intersect: false
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: '#182340',
            titleColor: '#e6eaf5',
            bodyColor: '#e6eaf5',
            borderColor: 'rgba(255,255,255,0.1)',
            borderWidth: 1,
            padding: 8,
            displayColors: true,
            filter: item => item.datasetIndex < 3
          }
        },
        scales: {
          x: {
            grid: { color: 'rgba(255, 255, 255, 0.04)' },
            ticks: {
              color: '#8b95b0',
              font: { size: 10 },
              maxRotation: 0,
              maxTicksLimit: 8
            }
          },
          y: {
            grid: { color: 'rgba(255, 255, 255, 0.05)' },
            ticks: {
              color: '#8b95b0',
              font: { size: 10 }
            }
          }
        }
      }
    });
  }

  // --- Update Chart with Snapshot Data ---
  function updateChart(snapshot) {
    if (!state.chart || !snapshot || !snapshot.nodes) return;
    const node = snapshot.nodes[state.activeNodeId];
    if (!node || !node.history) return;

    const metric = state.activeMetric;
    const history = node.history;
    const limits = snapshot.limits ? snapshot.limits[metric] : null;

    const rawList = (history.raw && history.raw[metric]) ? history.raw[metric] : [];
    const smoothList = (history.smooth && history.smooth[metric]) ? history.smooth[metric] : [];
    const tsList = history.ts || [];

    const formatTs = ts => {
      const d = new Date(ts * 1000);
      return d.toTimeString().split(' ')[0];
    };

    const labels = tsList.map(formatTs);
    const rawData = [...rawList];
    const smoothData = [...smoothList];

    // Forecast projection
    const forecast = (node.forecast && node.forecast[metric]) ? node.forecast[metric] : null;
    const fcData = new Array(tsList.length).fill(null);
    const upperData = new Array(tsList.length).fill(null);
    const lowerData = new Array(tsList.length).fill(null);

    if (forecast && forecast.points && forecast.points.length > 0) {
      const timeScale = (snapshot.settings && snapshot.settings.time_scale) ? snapshot.settings.time_scale : 60;
      const latestTs = tsList.length > 0 ? tsList[tsList.length - 1] : Date.now() / 1000;

      // Connect forecast starting point to last smoothed value
      if (smoothData.length > 0) {
        fcData[smoothData.length - 1] = smoothData[smoothData.length - 1];
        upperData[smoothData.length - 1] = smoothData[smoothData.length - 1];
        lowerData[smoothData.length - 1] = smoothData[smoothData.length - 1];
      }

      forecast.points.forEach((pt, idx) => {
        // real_seconds = sim_min * 60 / timeScale
        const futureRealSec = (pt.sim_min * 60.0) / timeScale;
        const futureTs = latestTs + futureRealSec;
        labels.push(formatTs(futureTs));

        rawData.push(null);
        smoothData.push(null);
        fcData.push(pt.value);
        upperData.push(forecast.upper[idx] !== undefined ? forecast.upper[idx] : pt.value);
        lowerData.push(forecast.lower[idx] !== undefined ? forecast.lower[idx] : pt.value);
      });
    }

    const totalLen = labels.length;
    const safeMaxData = limits ? new Array(totalLen).fill(limits.safe_max) : [];
    const safeMinData = limits ? new Array(totalLen).fill(limits.safe_min) : [];
    const hardMaxData = limits ? new Array(totalLen).fill(limits.hard_max) : [];
    const hardMinData = limits ? new Array(totalLen).fill(limits.hard_min) : [];

    // In-place chart update
    state.chart.data.labels = labels;
    state.chart.data.datasets[0].data = rawData;
    state.chart.data.datasets[1].data = smoothData;
    state.chart.data.datasets[2].data = fcData;
    state.chart.data.datasets[3].data = upperData;
    state.chart.data.datasets[4].data = lowerData;
    state.chart.data.datasets[5].data = safeMaxData;
    state.chart.data.datasets[6].data = safeMinData;
    state.chart.data.datasets[7].data = hardMaxData;
    state.chart.data.datasets[8].data = hardMinData;

    // Adjust Y-axis scale comfortably around limits
    if (limits && state.chart.options.scales.y) {
      state.chart.options.scales.y.min = Math.floor(limits.slider_min);
      state.chart.options.scales.y.max = Math.ceil(limits.slider_max);
    }

    state.chart.update('none');
  }

  // --- Render Node Health Tiles ---
  function renderHealthTiles(nodes, limits) {
    if (!dom.healthTilesGrid || !nodes) return;
    const nodeIds = Object.keys(nodes).sort();

    dom.healthTilesGrid.innerHTML = '';
    nodeIds.forEach(id => {
      const node = nodes[id];
      const tile = document.createElement('div');
      tile.className = `health-tile ${id === state.activeNodeId ? 'selected' : ''}`;
      tile.dataset.nodeId = id;

      const worstLevel = node.level || 'OK';
      const latest = node.latest;
      const clean = latest ? latest.clean : (node.controls ? node.controls.current : {});
      const devices = node.devices || {};

      const tempVal = clean && clean.temperature !== undefined && clean.temperature !== null ? clean.temperature.toFixed(1) : '--';
      const humVal = clean && clean.humidity !== undefined && clean.humidity !== null ? clean.humidity.toFixed(0) : '--';
      const aqiVal = clean && clean.aqi !== undefined && clean.aqi !== null ? clean.aqi.toFixed(0) : '--';

      tile.innerHTML = `
        <div class="health-tile-header">
          <span class="tile-node-name">${id}</span>
          <span class="tile-level-badge level-${worstLevel}">${worstLevel}</span>
        </div>
        <div class="tile-metrics-row">
          <div class="tile-metric-item">
            <span class="m-label">Temp</span>
            <span class="m-val">${tempVal}°C</span>
          </div>
          <div class="tile-metric-item">
            <span class="m-label">Hum</span>
            <span class="m-val">${humVal}%</span>
          </div>
          <div class="tile-metric-item">
            <span class="m-label">AQI</span>
            <span class="m-val">${aqiVal}</span>
          </div>
        </div>
        <div class="tile-devices-row">
          <span class="mini-dev-icon ${devices.fan && devices.fan.state !== 'off' ? 'active' : ''}" title="Fan: ${devices.fan ? devices.fan.state : 'off'}">FAN</span>
          <span class="mini-dev-icon ${devices.hvac && devices.hvac.state !== 'off' ? 'active' : ''}" title="HVAC: ${devices.hvac ? devices.hvac.state : 'off'}">HVAC</span>
          <span class="mini-dev-icon ${devices.dehumidifier && devices.dehumidifier.state !== 'off' ? 'active' : ''}" title="Dehumidifier: ${devices.dehumidifier ? devices.dehumidifier.state : 'off'}">DEHUM</span>
          <span class="mini-dev-icon ${devices.purifier && devices.purifier.state !== 'off' ? 'active' : ''}" title="Purifier: ${devices.purifier ? devices.purifier.state : 'off'}">PUR</span>
        </div>
      `;

      tile.addEventListener('click', () => {
        selectNode(id);
      });

      dom.healthTilesGrid.appendChild(tile);
    });
  }

  // --- Render Actuator Chips ---
  function renderActuators(node) {
    if (!dom.actuatorChipsContainer) return;
    if (!node || !node.devices) {
      dom.actuatorChipsContainer.innerHTML = '<div class="empty-state">No actuators available</div>';
      return;
    }

    const devices = node.devices;
    const devNames = ['fan', 'hvac', 'dehumidifier', 'purifier'];

    dom.actuatorChipsContainer.innerHTML = '';
    devNames.forEach(dev => {
      const devState = devices[dev] || { state: 'off', mode: 'auto' };
      const chip = document.createElement('div');
      chip.className = `actuator-chip active-${devState.state} ${devState.mode === 'manual' ? 'manual-mode' : ''}`;
      chip.dataset.device = dev;

      let stateColorClass = 'text-muted';
      if (devState.state === 'low') stateColorClass = 'text-cyan';
      if (devState.state === 'high') stateColorClass = 'text-warning';

      chip.innerHTML = `
        <div class="act-head">
          <span class="act-name">${dev}</span>
          <span class="act-mode-badge">${devState.mode}</span>
        </div>
        <div class="act-state-badge ${stateColorClass}">State: ${devState.state.toUpperCase()}</div>
      `;

      chip.addEventListener('click', async () => {
        // Toggle override logic:
        // If mode == 'auto', override to 'on' (or toggle off/on)
        // If mode == 'manual', release to 'auto'
        const nextAction = devState.mode === 'auto'
          ? (devState.state === 'off' ? 'on' : 'off')
          : 'auto';

        try {
          await api('/api/actuators/override', {
            method: 'POST',
            body: {
              node_id: state.activeNodeId,
              device: dev,
              action: nextAction
            }
          });
          refreshState();
        } catch (e) {
          console.error('Failed to toggle actuator override:', e);
        }
      });

      dom.actuatorChipsContainer.appendChild(chip);
    });

    // Shutdown restart button
    const isShutdown = (node.status === 'shutdown') || (devices.shutdown === true);
    if (dom.restartNodeBtn) {
      if (isShutdown) {
        dom.restartNodeBtn.classList.remove('hidden');
      } else {
        dom.restartNodeBtn.classList.add('hidden');
      }
    }
  }

  // --- Render Event Log ---
  function renderEventLog(events, actuations) {
    if (!dom.eventLogList) return;

    // Merge events and actuations, chronological sort, latest on top
    const merged = [];
    if (Array.isArray(events)) {
      events.forEach(e => {
        merged.push({
          ts: e.ts,
          type: 'event',
          level: e.level,
          source: e.source,
          message: e.message
        });
      });
    }
    if (Array.isArray(actuations)) {
      actuations.forEach(a => {
        merged.push({
          ts: a.ts,
          type: 'actuation',
          level: 'info',
          source: `${a.node_id}:${a.device}`,
          message: `${a.from_state} → ${a.to_state} (${a.trigger}: ${a.reason})`
        });
      });
    }

    merged.sort((a, b) => b.ts - a.ts);

    // Apply active filter
    const filter = state.activeLogFilter;
    const filtered = merged.filter(item => {
      if (filter === 'all') return true;
      if (filter === 'warn') return item.level === 'warn';
      if (filter === 'critical') return item.level === 'critical';
      if (filter === 'actuation') return item.type === 'actuation';
      return true;
    }).slice(0, 50);

    const formatTs = ts => {
      const d = new Date(ts * 1000);
      return d.toTimeString().split(' ')[0];
    };

    dom.eventLogList.innerHTML = '';
    if (filtered.length === 0) {
      dom.eventLogList.innerHTML = '<div style="color: var(--muted); padding: 8px; font-size: 11px;">No logs match filter</div>';
      return;
    }

    filtered.forEach(item => {
      const el = document.createElement('div');
      el.className = `log-entry level-${item.level} type-${item.type}`;
      el.innerHTML = `
        <span class="log-ts">${formatTs(item.ts)}</span>
        <span class="log-badge ${item.level === 'critical' ? 'text-critical' : (item.level === 'warn' ? 'text-warning' : 'text-cyan')}">${item.source}</span>
        <span class="log-msg">${item.message}</span>
      `;
      dom.eventLogList.appendChild(el);
    });

    if (state.autoScroll) {
      dom.eventLogList.scrollTop = 0;
    }
  }

  // --- Render Evaluation KPI & Comparisons ---
  function renderEvaluation(metrics, benchmark) {
    if (!metrics) return;

    if (dom.evalLoss) dom.evalLoss.textContent = `${(metrics.packet_loss_pct || 0).toFixed(1)}%`;

    if (dom.evalCritLat) {
      const latP0 = metrics.latency && metrics.latency['0'] ? metrics.latency['0'].p95 : null;
      dom.evalCritLat.textContent = latP0 !== null && latP0 !== undefined ? `${(latP0 * 1000).toFixed(1)} ms` : '-- ms';
    }

    if (dom.evalMae) {
      const fe = metrics.forecast_error && metrics.forecast_error[state.activeMetric];
      dom.evalMae.textContent = fe ? `${fe.mae.toFixed(2)}` : '--';
    }

    if (dom.evalAnticipated) {
      const b = metrics.breaches || {};
      dom.evalAnticipated.textContent = `${b.anticipated || 0} / ${b.total || 0}`;
    }

    if (dom.evalPrevented) {
      dom.evalPrevented.textContent = `${(metrics.breaches && metrics.breaches.prevented) || 0}`;
    }

    if (dom.evalLeadtime) {
      const lt = metrics.lead_time || {};
      dom.evalLeadtime.textContent = `${(lt.avg_s || 0).toFixed(1)} s (${lt.count || 0} samples)`;
    }

    // Benchmark status & comparisons
    if (benchmark && benchmark.history && benchmark.history.length > 0) {
      if (dom.benchResultsContainer) {
        dom.benchResultsContainer.innerHTML = '';
        benchmark.history.slice(-4).reverse().forEach(run => {
          const card = document.createElement('div');
          card.className = 'comparison-card';

          const title = run.type === 'strategy' ? 'Strategy: Reactive vs Proactive' : 'Pipeline: FIFO vs Priority';
          const p1Name = run.type === 'strategy' ? 'Reactive' : 'FIFO';
          const p2Name = run.type === 'strategy' ? 'Proactive' : 'Priority';

          let rowsHtml = '';
          if (run.type === 'strategy') {
            const p1 = run.reactive || {};
            const p2 = run.proactive || {};
            const delta = run.improvement_pct || {};

            rowsHtml = `
              <div class="comp-stat-row">
                <span class="comp-label">Peak Temperature:</span>
                <span class="comp-val">${p1.peak_temperature ? p1.peak_temperature.toFixed(1) : '--'}°C vs ${p2.peak_temperature ? p2.peak_temperature.toFixed(1) : '--'}°C</span>
              </div>
              <div class="comp-stat-row">
                <span class="comp-label">Time Above Safe:</span>
                <span class="comp-val">${p1.seconds_above_safe || 0}s vs ${p2.seconds_above_safe || 0}s</span>
                <span class="comp-delta ${(delta.time_above_safe_reduction || 0) >= 0 ? 'better' : 'worse'}">
                  ${(delta.time_above_safe_reduction || 0) >= 0 ? '↓' : '↑'} ${Math.abs(delta.time_above_safe_reduction || 0).toFixed(0)}%
                </span>
              </div>
              <div class="comp-stat-row">
                <span class="comp-label">Anticipated Breaches:</span>
                <span class="comp-val">${p1.anticipated_breaches || 0} vs ${p2.anticipated_breaches || 0}</span>
              </div>
            `;
          } else {
            const p1 = run.fifo || {};
            const p2 = run.priority || {};
            const delta = run.improvement_pct || {};

            rowsHtml = `
              <div class="comp-stat-row">
                <span class="comp-label">P0 (Critical) P95:</span>
                <span class="comp-val">${p1.critical_p95_ms ? p1.critical_p95_ms.toFixed(1) : '--'}ms vs ${p2.critical_p95_ms ? p2.critical_p95_ms.toFixed(1) : '--'}ms</span>
                <span class="comp-delta ${(delta.critical_latency_reduction || 0) >= 0 ? 'better' : 'worse'}">
                  ${(delta.critical_latency_reduction || 0) >= 0 ? '↓' : '↑'} ${Math.abs(delta.critical_latency_reduction || 0).toFixed(0)}%
                </span>
              </div>
              <div class="comp-stat-row">
                <span class="comp-label">P0 Loss %:</span>
                <span class="comp-val">${(p1.critical_loss_pct || 0).toFixed(1)}% vs ${(p2.critical_loss_pct || 0).toFixed(1)}%</span>
              </div>
            `;
          }

          card.innerHTML = `
            <div class="comp-header">
              <span class="comp-title">${title}</span>
              <span style="font-size: 10px; color: var(--muted);">${new Date(run.timestamp * 1000).toLocaleTimeString()}</span>
            </div>
            ${rowsHtml}
          `;
          dom.benchResultsContainer.appendChild(card);
        });
      }
    }
  }

  // --- Main Update Flow from Polling State ---
  function updateUI(snapshot) {
    if (!snapshot) return;
    state.lastSnapshot = snapshot;

    // 1. Connection status
    if (dom.connPill) {
      dom.connPill.className = 'pill-chip conn-chip connected';
      dom.connPill.innerHTML = '<span class="dot"></span> Online';
    }
    if (dom.connBanner) {
      dom.connBanner.classList.add('hidden');
    }

    // 2. Top bar
    if (dom.togglePauseBtn) {
      const isPaused = Boolean(snapshot.paused);
      if (isPaused) {
        dom.togglePauseBtn.classList.add('is-paused');
        if (dom.pauseBtnIcon) dom.pauseBtnIcon.textContent = '▶';
        if (dom.pauseBtnLabel) dom.pauseBtnLabel.textContent = 'Resume';
      } else {
        dom.togglePauseBtn.classList.remove('is-paused');
        if (dom.pauseBtnIcon) dom.pauseBtnIcon.textContent = '⏸';
        if (dom.pauseBtnLabel) dom.pauseBtnLabel.textContent = 'Pause';
      }
    }
    if (dom.systemPausedPill) {
      if (snapshot.paused) {
        dom.systemPausedPill.classList.remove('hidden');
      } else {
        dom.systemPausedPill.classList.add('hidden');
      }
    }

    if (dom.profileSelect && document.activeElement !== dom.profileSelect) {
      dom.profileSelect.value = snapshot.profile;
    }
    const settings = snapshot.settings || {};
    if (dom.stratProactive && dom.stratReactive) {
      if (settings.strategy === 'proactive') {
        dom.stratProactive.classList.add('active');
        dom.stratReactive.classList.remove('active');
      } else {
        dom.stratProactive.classList.remove('active');
        dom.stratReactive.classList.add('active');
      }
    }
    if (dom.modePriority && dom.modeFifo) {
      if (settings.pipeline_mode === 'priority') {
        dom.modePriority.classList.add('active');
        dom.modeFifo.classList.remove('active');
      } else {
        dom.modePriority.classList.remove('active');
        dom.modeFifo.classList.add('active');
      }
    }
    if (dom.timescaleChip && settings.time_scale) {
      dom.timescaleChip.textContent = `1 s = ${(settings.time_scale / 60).toFixed(1)} sim-min`;
    }

    // 3. KPI row
    const nodes = snapshot.nodes || {};
    const nodeIds = Object.keys(nodes);
    if (!nodeIds.includes(state.activeNodeId) && nodeIds.length > 0) {
      state.activeNodeId = nodeIds[0];
    }

    const onlineCount = nodeIds.filter(id => nodes[id].status === 'online').length;
    if (dom.kpiNodes) dom.kpiNodes.textContent = `${onlineCount} / ${nodeIds.length}`;
    if (dom.kpiNodesSub) dom.kpiNodesSub.textContent = `${nodeIds.length - onlineCount} offline`;

    const pipeline = snapshot.pipeline || {};
    const counters = snapshot.counters || {};

    if (dom.kpiThroughput) dom.kpiThroughput.innerHTML = `${(pipeline.throughput || 0).toFixed(1)} <small>msgs/s</small>`;
    if (dom.kpiProcessedSub) dom.kpiProcessedSub.textContent = `${counters.processed || 0} total`;

    if (dom.kpiQueue) dom.kpiQueue.textContent = `${pipeline.queue_depth || 0}`;
    const pBy = pipeline.depth_by_priority || {};
    if (dom.kpiQueueSub) dom.kpiQueueSub.textContent = `P0: ${pBy['0'] || 0} | P1: ${pBy['1'] || 0} | P2: ${pBy['2'] || 0}`;

    const dropped = counters.dropped || 0;
    const received = counters.received || 0;
    const rejected = counters.rejected || 0;
    const totalReceived = received + rejected;
    const lossPct = totalReceived > 0 ? ((dropped + rejected) / totalReceived) * 100 : 0;
    if (dom.kpiDropped) dom.kpiDropped.textContent = `${dropped}`;
    if (dom.kpiLossSub) dom.kpiLossSub.textContent = `Loss: ${lossPct.toFixed(1)}%`;

    const criticalNodes = nodeIds.filter(id => nodes[id].level === 'CRITICAL').length;
    if (dom.kpiAlerts) dom.kpiAlerts.textContent = `${criticalNodes}`;
    if (dom.kpiAlertsSub) dom.kpiAlertsSub.textContent = `${criticalNodes} nodes critical`;

    const p95Lat = pipeline.latency && pipeline.latency['0'] ? (pipeline.latency['0'].p95 * 1000).toFixed(0) : '0';
    if (dom.kpiLatency) dom.kpiLatency.innerHTML = `${p95Lat} <small>ms</small>`;
    if (dom.kpiWrangleSub) dom.kpiWrangleSub.textContent = `Wrangle: ${(pipeline.avg_wrangle_ms || 0).toFixed(1)} ms`;

    // 4. Node selector tabs
    if (dom.simNodeTabs) {
      dom.simNodeTabs.innerHTML = '';
      nodeIds.sort().forEach(id => {
        const btn = document.createElement('button');
        const node = nodes[id];
        const statusClass = node.level === 'CRITICAL' ? 'status-critical' : (node.status === 'shutdown' ? 'status-shutdown' : '');
        btn.className = `node-tab-btn ${id === state.activeNodeId ? 'active' : ''} ${statusClass}`;
        btn.textContent = id;
        btn.addEventListener('click', () => selectNode(id));
        dom.simNodeTabs.appendChild(btn);
      });
    }

    if (dom.activeNodeName) {
      dom.activeNodeName.textContent = state.activeNodeId.toUpperCase();
    }
    if (dom.scopeNodeLabel) {
      dom.scopeNodeLabel.textContent = state.activeNodeId.toUpperCase();
    }

    // 5. Sliders update (protect from overwrite while user is dragging)
    const activeNode = nodes[state.activeNodeId];
    const limits = snapshot.limits || {};

    if (activeNode && activeNode.controls) {
      const ctrl = activeNode.controls;
      const targets = ctrl.target || {};
      const current = ctrl.current || {};

      // Temperature Slider
      const tempLimits = limits.temperature;
      if (tempLimits) {
        if (!state.activeDragging.has('slider-temp')) {
          dom.sliderTemp.min = tempLimits.slider_min;
          dom.sliderTemp.max = tempLimits.slider_max;
          dom.sliderTemp.value = targets.temperature !== undefined ? targets.temperature : tempLimits.safe_min;
        }
        updateSliderTrackGradient(dom.sliderTemp, tempLimits);
        dom.boundTempMin.textContent = `${tempLimits.slider_min.toFixed(0)}°C`;
        dom.boundTempMax.textContent = `${tempLimits.slider_max.toFixed(0)}°C`;
      }
      const curTemp = current.temperature !== undefined ? current.temperature.toFixed(1) : '--';
      const tgtTemp = targets.temperature !== undefined ? targets.temperature.toFixed(1) : '--';
      dom.badgeTemp.textContent = `${curTemp} / ${tgtTemp} °C`;

      // Humidity Slider
      const humLimits = limits.humidity;
      if (humLimits) {
        if (!state.activeDragging.has('slider-hum')) {
          dom.sliderHum.min = humLimits.slider_min;
          dom.sliderHum.max = humLimits.slider_max;
          dom.sliderHum.value = targets.humidity !== undefined ? targets.humidity : humLimits.safe_min;
        }
        updateSliderTrackGradient(dom.sliderHum, humLimits);
        dom.boundHumMin.textContent = `${humLimits.slider_min.toFixed(0)}%`;
        dom.boundHumMax.textContent = `${humLimits.slider_max.toFixed(0)}%`;
      }
      const curHum = current.humidity !== undefined ? current.humidity.toFixed(0) : '--';
      const tgtHum = targets.humidity !== undefined ? targets.humidity.toFixed(0) : '--';
      dom.badgeHum.textContent = `${curHum} / ${tgtHum} %`;

      // AQI Slider
      const aqiLimits = limits.aqi;
      if (aqiLimits) {
        if (!state.activeDragging.has('slider-aqi')) {
          dom.sliderAqi.min = aqiLimits.slider_min;
          dom.sliderAqi.max = aqiLimits.slider_max;
          dom.sliderAqi.value = targets.aqi !== undefined ? targets.aqi : aqiLimits.safe_min;
        }
        updateSliderTrackGradient(dom.sliderAqi, aqiLimits);
        dom.boundAqiMin.textContent = `${aqiLimits.slider_min.toFixed(0)}`;
        dom.boundAqiMax.textContent = `${aqiLimits.slider_max.toFixed(0)}`;
      }
      const curAqi = current.aqi !== undefined ? current.aqi.toFixed(0) : '--';
      const tgtAqi = targets.aqi !== undefined ? targets.aqi.toFixed(0) : '--';
      dom.badgeAqi.textContent = `${curAqi} / ${tgtAqi} AQI`;

      // Noise & Rate
      if (!state.activeDragging.has('slider-noise')) {
        dom.sliderNoise.value = ctrl.noise !== undefined ? ctrl.noise : 0.2;
      }
      dom.badgeNoise.textContent = (Number(dom.sliderNoise.value)).toFixed(2);

      if (!state.activeDragging.has('slider-rate')) {
        dom.sliderRate.value = ctrl.send_rate_hz !== undefined ? ctrl.send_rate_hz : 1.0;
      }
      dom.badgeRate.textContent = `${(Number(dom.sliderRate.value)).toFixed(1)} Hz`;

      // Fault Mode
      const currentFault = ctrl.fault_mode || 'none';
      dom.faultModeGroup.querySelectorAll('.seg-btn').forEach(btn => {
        if (btn.dataset.fault === currentFault) {
          btn.classList.add('active');
        } else {
          btn.classList.remove('active');
        }
      });

      // Enable toggle
      if (document.activeElement !== dom.nodeEnableToggle) {
        dom.nodeEnableToggle.checked = ctrl.enabled !== false;
      }
    }

    // Pipeline engine sliders
    if (!state.activeDragging.has('slider-workers')) {
      dom.sliderWorkers.value = settings.workers || 3;
    }
    dom.badgeWorkers.textContent = dom.sliderWorkers.value;

    if (!state.activeDragging.has('slider-delay')) {
      dom.sliderDelay.value = settings.processing_delay_ms !== undefined ? settings.processing_delay_ms : 5;
    }
    dom.badgeDelay.textContent = `${dom.sliderDelay.value} ms`;

    if (!state.activeDragging.has('slider-horizon')) {
      dom.sliderHorizon.value = settings.horizon_min || 15;
    }
    dom.badgeHorizon.textContent = `${dom.sliderHorizon.value} min`;

    // 6. Chart update
    updateChart(snapshot);

    // 7. Health Tiles
    renderHealthTiles(nodes, limits);

    // 8. Internals flow & latency
    if (dom.flowIngest) dom.flowIngest.textContent = counters.received || 0;
    if (dom.flowQueue) dom.flowQueue.textContent = pipeline.queue_depth || 0;

    const totalQ = (pBy['0'] || 0) + (pBy['1'] || 0) + (pBy['2'] || 0);
    if (totalQ > 0) {
      if (dom.qP0) dom.qP0.style.width = `${((pBy['0'] || 0) / totalQ) * 100}%`;
      if (dom.qP1) dom.qP1.style.width = `${((pBy['1'] || 0) / totalQ) * 100}%`;
      if (dom.qP2) dom.qP2.style.width = `${((pBy['2'] || 0) / totalQ) * 100}%`;
    } else {
      if (dom.qP0) dom.qP0.style.width = '0%';
      if (dom.qP1) dom.qP1.style.width = '0%';
      if (dom.qP2) dom.qP2.style.width = '0%';
    }

    if (dom.flowWorkers) dom.flowWorkers.textContent = settings.workers || 3;
    if (dom.flowWrangle) dom.flowWrangle.textContent = `${(pipeline.avg_wrangle_ms || 0).toFixed(1)}ms`;
    if (dom.flowActuations) dom.flowActuations.textContent = (snapshot.actuations || []).length;

    const lat = pipeline.latency || {};
    if (dom.latP0) dom.latP0.textContent = lat['0'] ? `${(lat['0'].p95 * 1000).toFixed(1)} ms` : '-- ms';
    if (dom.latP1) dom.latP1.textContent = lat['1'] ? `${(lat['1'].p95 * 1000).toFixed(1)} ms` : '-- ms';
    if (dom.latP2) dom.latP2.textContent = lat['2'] ? `${(lat['2'].p95 * 1000).toFixed(1)} ms` : '-- ms';

    // 9. Actuator chips
    renderActuators(activeNode);

    // 10. Event log
    renderEventLog(snapshot.events, snapshot.actuations);

    // 11. Guided demo floating bar and scenario pills
    const demo = snapshot.demo || {};
    const isDemoRunning = Boolean(demo.running || demo.active);
    const stepNum = demo.step || 1;
    const totalSteps = demo.steps_total || demo.total_steps || 6;
    const remSec = Math.max(0, demo.remaining_s || 0);

    const scenarioTitles = {
      1: 'Baseline Normal',
      2: 'Heat Spike',
      3: 'Sensor Fault',
      4: 'Burst Traffic',
      5: 'Humidity Drift',
      6: 'Evaluation Summary'
    };
    const stepScenarioKeys = {
      1: 'normal',
      2: 'heat_spike',
      3: 'sensor_fault',
      4: 'burst_traffic',
      5: 'humidity_drift',
      6: 'normal'
    };

    const activeDemoScenarioName = demo.scenario_name || scenarioTitles[stepNum] || `Scenario ${stepNum}`;
    const activeDemoScenarioKey = demo.scenario || stepScenarioKeys[stepNum] || 'normal';

    if (isDemoRunning) {
      if (dom.demoBar) {
        dom.demoBar.classList.remove('hidden');
        if (dom.demoStepBadge) dom.demoStepBadge.textContent = `Step ${stepNum}/${totalSteps}`;
        if (dom.demoBarScenarioName) dom.demoBarScenarioName.textContent = activeDemoScenarioName;
        if (dom.demoCaption) dom.demoCaption.textContent = demo.caption || demo.description || 'Guided demonstration in progress...';
        if (dom.demoCountdown) dom.demoCountdown.textContent = `${remSec.toFixed(1)}s`;
        if (dom.demoPauseBtn) dom.demoPauseBtn.textContent = demo.paused ? 'Resume' : 'Pause';
      }
      if (dom.toggleDemoBtn) dom.toggleDemoBtn.textContent = '⏹ Stop Demo';

      if (dom.topbarDemoPill) {
        dom.topbarDemoPill.classList.remove('hidden');
        if (dom.topbarDemoScenario) dom.topbarDemoScenario.textContent = activeDemoScenarioName;
        if (dom.topbarDemoTimer) dom.topbarDemoTimer.textContent = `${remSec.toFixed(1)}s`;
      }
      if (dom.demoScenarioPill) {
        dom.demoScenarioPill.classList.remove('hidden');
        if (dom.demoPillTitle) dom.demoPillTitle.textContent = activeDemoScenarioName;
        if (dom.demoPillTimer) dom.demoPillTimer.textContent = `${remSec.toFixed(1)}s`;
      }

      updateScenarioButtons(activeDemoScenarioKey);
    } else {
      if (dom.demoBar) dom.demoBar.classList.add('hidden');
      if (dom.toggleDemoBtn) dom.toggleDemoBtn.textContent = '▶ Guided Demo';
      if (dom.topbarDemoPill) dom.topbarDemoPill.classList.add('hidden');
      if (dom.demoScenarioPill) dom.demoScenarioPill.classList.add('hidden');

      updateScenarioButtons(state.activeScenario);
    }

    // 12. Evaluation modal (if open)
    if (state.isEvalOpen) {
      renderEvaluation(snapshot.metrics, snapshot.benchmark);
    }
  }

  // --- Preset Scenario Buttons Selection ---
  function updateScenarioButtons(activeKey) {
    const key = activeKey || state.activeScenario || 'normal';
    document.querySelectorAll('[data-scenario]').forEach(btn => {
      if (btn.dataset.scenario === key) {
        btn.classList.add('active');
        btn.setAttribute('aria-pressed', 'true');
      } else {
        btn.classList.remove('active');
        btn.setAttribute('aria-pressed', 'false');
      }
    });
  }

  // --- Node Selection ---
  function selectNode(nodeId) {
    state.activeNodeId = nodeId;
    if (dom.scopeNodeLabel) {
      dom.scopeNodeLabel.textContent = nodeId.toUpperCase();
    }
    if (state.lastSnapshot) {
      updateUI(state.lastSnapshot);
    }
  }

  // --- Polling Routine ---
  async function pollState() {
    try {
      const { data, duration } = await api('/api/state');
      state.consecutiveFailures = 0;

      if (duration > 800 && dom.connPill) {
        dom.connPill.className = 'pill-chip conn-chip lagging';
        dom.connPill.innerHTML = '<span class="dot"></span> Lagging';
      }

      updateUI(data);
    } catch (err) {
      state.consecutiveFailures++;
      if (dom.connPill) {
        dom.connPill.className = 'pill-chip conn-chip disconnected';
        dom.connPill.innerHTML = '<span class="dot"></span> Offline';
      }
      if (dom.connBanner && state.consecutiveFailures >= 2) {
        dom.connBanner.classList.remove('hidden');
      }
    } finally {
      setTimeout(pollState, state.pollInterval);
    }
  }

  async function refreshState() {
    try {
      const { data } = await api('/api/state');
      updateUI(data);
    } catch (_) {}
  }

  // --- User Event Bindings ---
  function bindEvents() {
    // Dismiss disconnected banner
    if (dom.dismissBannerBtn) {
      dom.dismissBannerBtn.addEventListener('click', () => {
        dom.connBanner.classList.add('hidden');
      });
    }

    // Profile selection
    if (dom.profileSelect) {
      dom.profileSelect.addEventListener('change', async (e) => {
        try {
          await api('/api/profile', {
            method: 'POST',
            body: { profile: e.target.value }
          });
          refreshState();
        } catch (err) {
          console.error('Failed to change profile:', err);
        }
      });
    }

    // Strategy segmented toggle
    if (dom.stratProactive && dom.stratReactive) {
      dom.stratProactive.addEventListener('click', async () => {
        try {
          await api('/api/control/strategy', { method: 'POST', body: { strategy: 'proactive' } });
          refreshState();
        } catch (e) { console.error(e); }
      });
      dom.stratReactive.addEventListener('click', async () => {
        try {
          await api('/api/control/strategy', { method: 'POST', body: { strategy: 'reactive' } });
          refreshState();
        } catch (e) { console.error(e); }
      });
    }

    // Pipeline mode segmented toggle
    if (dom.modePriority && dom.modeFifo) {
      dom.modePriority.addEventListener('click', async () => {
        try {
          await api('/api/pipeline/config', { method: 'POST', body: { pipeline_mode: 'priority' } });
          refreshState();
        } catch (e) { console.error(e); }
      });
      dom.modeFifo.addEventListener('click', async () => {
        try {
          await api('/api/pipeline/config', { method: 'POST', body: { pipeline_mode: 'fifo' } });
          refreshState();
        } catch (e) { console.error(e); }
      });
    }

    // Scenario Target Scope Toggle
    if (dom.scopeNodeBtn && dom.scopeAllBtn) {
      dom.scopeNodeBtn.addEventListener('click', () => {
        state.scenarioScope = 'node';
        dom.scopeNodeBtn.classList.add('active');
        dom.scopeAllBtn.classList.remove('active');
      });
      dom.scopeAllBtn.addEventListener('click', () => {
        state.scenarioScope = 'all';
        dom.scopeAllBtn.classList.add('active');
        dom.scopeNodeBtn.classList.remove('active');
      });
    }

    // Scenarios buttons
    document.querySelectorAll('[data-scenario]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const scenario = btn.dataset.scenario;
        // If clicking already active non-normal scenario, toggle off smoothly back to normal
        const targetScenario = (state.activeScenario === scenario && scenario !== 'normal') ? 'normal' : scenario;
        state.activeScenario = targetScenario;
        updateScenarioButtons(targetScenario);

        const targetNodeId = state.scenarioScope === 'all' ? 'all' : state.activeNodeId;

        try {
          await api('/api/sim/scenario', {
            method: 'POST',
            body: {
              name: targetScenario,
              scenario: targetScenario,
              node_id: targetNodeId
            }
          });
          await refreshState();
        } catch (e) { console.error('Failed to apply scenario:', e); }
      });
    });

    // Metric tabs in Center Monitor
    document.querySelectorAll('.metric-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        document.querySelectorAll('.metric-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        state.activeMetric = tab.dataset.metric;
        if (state.lastSnapshot) {
          updateChart(state.lastSnapshot);
        }
      });
    });

    // Add / Remove node
    if (dom.addNodeBtn) {
      dom.addNodeBtn.addEventListener('click', async () => {
        try {
          const res = await api('/api/sim/nodes', {
            method: 'POST',
            body: { action: 'add' }
          });
          if (res.data && res.data.node_id) {
            state.activeNodeId = res.data.node_id;
          }
          await refreshState();
        } catch (e) { console.error('Add node failed:', e); }
      });
    }

    if (dom.removeNodeBtn) {
      dom.removeNodeBtn.addEventListener('click', async () => {
        try {
          const targetNode = state.activeNodeId;
          const res = await api('/api/sim/nodes', {
            method: 'POST',
            body: { action: 'remove', node_id: targetNode }
          });
          if (res.data && res.data.nodes && res.data.nodes.length > 0) {
            if (!res.data.nodes.includes(state.activeNodeId)) {
              state.activeNodeId = res.data.nodes[0];
            }
          }
          await refreshState();
        } catch (e) { console.error('Remove node failed:', e); }
      });
    }

    // Slider Drag & Debounced POST Handlers
    const registerSlider = (slider, id, onInput, onCommit) => {
      if (!slider) return;

      const start = () => state.activeDragging.add(id);
      const end = async () => {
        try {
          await onCommit();
        } finally {
          setTimeout(() => state.activeDragging.delete(id), 350);
        }
      };

      slider.addEventListener('mousedown', start);
      slider.addEventListener('touchstart', start, { passive: true });
      window.addEventListener('mouseup', () => {
        if (state.activeDragging.has(id)) {
          end();
        }
      });
      window.addEventListener('touchend', () => {
        if (state.activeDragging.has(id)) {
          end();
        }
      });

      slider.addEventListener('input', () => {
        state.activeDragging.add(id);
        onInput();
        debounce(id, 150, onCommit);
      });
    };

    // Temperature Target
    registerSlider(
      dom.sliderTemp,
      'slider-temp',
      () => {
        dom.badgeTemp.textContent = `Target: ${Number(dom.sliderTemp.value).toFixed(1)} °C`;
      },
      async () => {
        try {
          const val = parseFloat(dom.sliderTemp.value);
          await api('/api/sim/controls', {
            method: 'POST',
            body: {
              node_id: state.activeNodeId,
              controls: { target: { temperature: val } },
              target: { temperature: val }
            }
          });
        } catch (e) { console.error(e); }
      }
    );

    // Humidity Target
    registerSlider(
      dom.sliderHum,
      'slider-hum',
      () => {
        dom.badgeHum.textContent = `Target: ${Number(dom.sliderHum.value).toFixed(0)} %`;
      },
      async () => {
        try {
          const val = parseFloat(dom.sliderHum.value);
          await api('/api/sim/controls', {
            method: 'POST',
            body: {
              node_id: state.activeNodeId,
              controls: { target: { humidity: val } },
              target: { humidity: val }
            }
          });
        } catch (e) { console.error(e); }
      }
    );

    // AQI Target
    registerSlider(
      dom.sliderAqi,
      'slider-aqi',
      () => {
        dom.badgeAqi.textContent = `Target: ${Number(dom.sliderAqi.value).toFixed(0)} AQI`;
      },
      async () => {
        try {
          const val = parseFloat(dom.sliderAqi.value);
          await api('/api/sim/controls', {
            method: 'POST',
            body: {
              node_id: state.activeNodeId,
              controls: { target: { aqi: val } },
              target: { aqi: val }
            }
          });
        } catch (e) { console.error(e); }
      }
    );

    // Noise Slider
    registerSlider(
      dom.sliderNoise,
      'slider-noise',
      () => {
        dom.badgeNoise.textContent = Number(dom.sliderNoise.value).toFixed(2);
      },
      async () => {
        try {
          const val = parseFloat(dom.sliderNoise.value);
          await api('/api/sim/controls', {
            method: 'POST',
            body: {
              node_id: state.activeNodeId,
              controls: { noise: val },
              noise: val
            }
          });
        } catch (e) { console.error(e); }
      }
    );

    // Send Rate Slider
    registerSlider(
      dom.sliderRate,
      'slider-rate',
      () => {
        dom.badgeRate.textContent = `${Number(dom.sliderRate.value).toFixed(1)} Hz`;
      },
      async () => {
        try {
          const val = parseFloat(dom.sliderRate.value);
          await api('/api/sim/controls', {
            method: 'POST',
            body: {
              node_id: state.activeNodeId,
              controls: { send_rate_hz: val },
              send_rate_hz: val
            }
          });
        } catch (e) { console.error(e); }
      }
    );

    // Fault Mode buttons
    if (dom.faultModeGroup) {
      dom.faultModeGroup.querySelectorAll('.seg-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
          const fault = btn.dataset.fault;
          try {
            await api('/api/sim/controls', {
              method: 'POST',
              body: {
                node_id: state.activeNodeId,
                fault_mode: fault
              }
            });
            refreshState();
          } catch (e) { console.error(e); }
        });
      });
    }

    // Node Enable checkbox
    if (dom.nodeEnableToggle) {
      dom.nodeEnableToggle.addEventListener('change', async (e) => {
        try {
          await api('/api/sim/controls', {
            method: 'POST',
            body: {
              node_id: state.activeNodeId,
              enabled: e.target.checked
            }
          });
          refreshState();
        } catch (err) { console.error(err); }
      });
    }

    // Worker Threads Slider
    registerSlider(
      dom.sliderWorkers,
      'slider-workers',
      () => {
        dom.badgeWorkers.textContent = dom.sliderWorkers.value;
      },
      async () => {
        try {
          await api('/api/pipeline/config', {
            method: 'POST',
            body: { workers: parseInt(dom.sliderWorkers.value, 10) }
          });
        } catch (e) { console.error(e); }
      }
    );

    // Processing Delay Slider
    registerSlider(
      dom.sliderDelay,
      'slider-delay',
      () => {
        dom.badgeDelay.textContent = `${dom.sliderDelay.value} ms`;
      },
      async () => {
        try {
          await api('/api/pipeline/config', {
            method: 'POST',
            body: { processing_delay_ms: parseInt(dom.sliderDelay.value, 10) }
          });
        } catch (e) { console.error(e); }
      }
    );

    // Horizon Slider
    registerSlider(
      dom.sliderHorizon,
      'slider-horizon',
      () => {
        dom.badgeHorizon.textContent = `${dom.sliderHorizon.value} min`;
      },
      async () => {
        try {
          await api('/api/forecast/config', {
            method: 'POST',
            body: { horizon_min: parseInt(dom.sliderHorizon.value, 10) }
          });
        } catch (e) { console.error(e); }
      }
    );

    // Restart Node Button
    if (dom.restartNodeBtn) {
      dom.restartNodeBtn.addEventListener('click', async () => {
        try {
          await api('/api/actuators/restart', {
            method: 'POST',
            body: { node_id: state.activeNodeId }
          });
          refreshState();
        } catch (e) { console.error('Restart failed:', e); }
      });
    }

    // Log Filter buttons
    document.querySelectorAll('.filter-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        state.activeLogFilter = chip.dataset.filter;
        if (state.lastSnapshot) {
          renderEventLog(state.lastSnapshot.events, state.lastSnapshot.actuations);
        }
      });
    });

    // Auto-scroll pause toggle
    if (dom.pauseScrollBtn) {
      dom.pauseScrollBtn.addEventListener('click', () => {
        state.autoScroll = !state.autoScroll;
        dom.pauseScrollBtn.textContent = state.autoScroll ? 'Scroll: Auto' : 'Scroll: Paused';
      });
    }

    // System Demonstration Playback: Pause / Resume Entire System
    if (dom.togglePauseBtn) {
      dom.togglePauseBtn.addEventListener('click', async () => {
        const isPaused = Boolean(state.lastSnapshot && state.lastSnapshot.paused);
        try {
          if (isPaused) {
            await api('/api/system/resume', { method: 'POST' });
          } else {
            await api('/api/system/pause', { method: 'POST' });
          }
          refreshState();
        } catch (err) {
          console.error('Failed to toggle system pause:', err);
        }
      });
    }

    // System Demonstration Playback: Reset Entire System to Baseline
    if (dom.resetSystemBtn) {
      dom.resetSystemBtn.addEventListener('click', async () => {
        const originalHtml = dom.resetSystemBtn.innerHTML;
        dom.resetSystemBtn.innerHTML = '<span class="btn-icon">⏳</span> <span>Resetting...</span>';
        dom.resetSystemBtn.disabled = true;

        // Clear local UI dragging states and scenarios
        state.activeDragging.clear();
        state.activeScenario = 'normal';
        document.querySelectorAll('.chip-btn[data-scenario]').forEach(b => {
          b.classList.toggle('active', b.dataset.scenario === 'normal');
        });

        // Clear chart immediately for crisp visual feedback
        if (state.chart) {
          state.chart.data.labels = [];
          state.chart.data.datasets.forEach(ds => { ds.data = []; });
          state.chart.update('none');
        }

        try {
          await api('/api/system/reset', { method: 'POST' });
          await refreshState();
          dom.resetSystemBtn.innerHTML = '<span class="btn-icon">✓</span> <span>Reset Done</span>';
        } catch (err) {
          console.error('Failed to reset system:', err);
          dom.resetSystemBtn.innerHTML = '<span class="btn-icon">⚠️</span> <span>Error</span>';
        } finally {
          setTimeout(() => {
            dom.resetSystemBtn.innerHTML = originalHtml;
            dom.resetSystemBtn.disabled = false;
          }, 1000);
        }
      });
    }

    // Guided Demo button & controls
    if (dom.toggleDemoBtn) {
      dom.toggleDemoBtn.addEventListener('click', async () => {
        const isRunning = state.lastSnapshot && state.lastSnapshot.demo && state.lastSnapshot.demo.active;
        if (isRunning) {
          await api('/api/demo/stop', { method: 'POST' });
        } else {
          await api('/api/demo/start', { method: 'POST' });
        }
        refreshState();
      });
    }

    if (dom.demoPauseBtn) {
      dom.demoPauseBtn.addEventListener('click', async () => {
        const isPaused = state.lastSnapshot && state.lastSnapshot.demo && state.lastSnapshot.demo.paused;
        if (isPaused) {
          await api('/api/demo/resume', { method: 'POST' });
        } else {
          await api('/api/demo/pause', { method: 'POST' });
        }
        refreshState();
      });
    }

    if (dom.demoStopBtn) {
      dom.demoStopBtn.addEventListener('click', async () => {
        await api('/api/demo/stop', { method: 'POST' });
        refreshState();
      });
    }

    // Evaluation Modal open/close
    if (dom.openEvalBtn) {
      dom.openEvalBtn.addEventListener('click', () => {
        state.isEvalOpen = true;
        if (dom.evalModal) dom.evalModal.classList.remove('hidden');
        if (state.lastSnapshot) {
          renderEvaluation(state.lastSnapshot.metrics, state.lastSnapshot.benchmark);
        }
      });
    }

    if (dom.closeEvalBtn) {
      dom.closeEvalBtn.addEventListener('click', () => {
        state.isEvalOpen = false;
        if (dom.evalModal) dom.evalModal.classList.add('hidden');
      });
    }

    const modalBackdrop = document.querySelector('.modal-backdrop');
    if (modalBackdrop) {
      modalBackdrop.addEventListener('click', () => {
        state.isEvalOpen = false;
        if (dom.evalModal) dom.evalModal.classList.add('hidden');
      });
    }

    // Export CSV
    if (dom.exportCsvBtn) {
      dom.exportCsvBtn.addEventListener('click', () => {
        window.location.href = '/api/benchmark/results.csv';
      });
    }

    // Benchmark Run Handlers
    const startBenchmarkPolling = () => {
      if (dom.benchProgressContainer) dom.benchProgressContainer.classList.remove('hidden');
      if (dom.runBenchStrategy) dom.runBenchStrategy.disabled = true;
      if (dom.runBenchPipeline) dom.runBenchPipeline.disabled = true;

      if (state.benchmarkTimer) clearInterval(state.benchmarkTimer);
      state.benchmarkTimer = setInterval(async () => {
        try {
          const res = await api('/api/benchmark/status');
          const status = res.data;
          if (dom.benchPhaseLabel) dom.benchPhaseLabel.textContent = status.phase || 'Benchmarking...';
          const pct = Math.round((status.progress || 0) * 100);
          if (dom.benchPctLabel) dom.benchPctLabel.textContent = `${pct}%`;
          if (dom.benchProgressFill) dom.benchProgressFill.style.width = `${pct}%`;

          if (!status.running) {
            clearInterval(state.benchmarkTimer);
            state.benchmarkTimer = null;
            if (dom.runBenchStrategy) dom.runBenchStrategy.disabled = false;
            if (dom.runBenchPipeline) dom.runBenchPipeline.disabled = false;
            if (dom.benchPhaseLabel) dom.benchPhaseLabel.textContent = 'Complete';
            refreshState();
          }
        } catch (_) {}
      }, 500);
    };

    if (dom.runBenchStrategy) {
      dom.runBenchStrategy.addEventListener('click', async () => {
        try {
          await api('/api/benchmark/run', {
            method: 'POST',
            body: { benchmark_type: 'strategy', duration_s: 15 }
          });
          startBenchmarkPolling();
        } catch (e) {
          console.error('Benchmark error:', e);
          if (dom.benchPhaseLabel) dom.benchPhaseLabel.textContent = 'Error: ' + (e.message || e);
        }
      });
    }

    if (dom.runBenchPipeline) {
      dom.runBenchPipeline.addEventListener('click', async () => {
        try {
          await api('/api/benchmark/run', {
            method: 'POST',
            body: { benchmark_type: 'pipeline', duration_s: 15 }
          });
          startBenchmarkPolling();
        } catch (e) {
          console.error('Benchmark error:', e);
          if (dom.benchPhaseLabel) dom.benchPhaseLabel.textContent = 'Error: ' + (e.message || e);
        }
      });
    }
  }

  // --- Bootstrap on DOM Ready ---
  document.addEventListener('DOMContentLoaded', () => {
    cacheDom();
    initChart();
    bindEvents();
    pollState();
  });
})();
