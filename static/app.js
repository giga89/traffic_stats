/**
 * NetTracker — Dashboard JavaScript
 *
 * Security notes:
 * - All dynamic text is set via textContent (never innerHTML with user data).
 * - DOM nodes are built with createElement/appendChild, not raw HTML strings.
 * - WebSocket URL is constructed from window.location (same origin only).
 */

'use strict';

/* ══════════════════════════════════════════
   Constants & state
══════════════════════════════════════════ */
const WS_RECONNECT_DELAY_MS = 3000;
const MAX_HISTORY_POINTS    = 120;   // ~4 min at 2s interval
const BYTES_UNITS = ['B/s', 'KB/s', 'MB/s', 'GB/s', 'TB/s'];
const TOTAL_UNITS = ['B',   'KB',   'MB',   'GB',   'TB'];

// Chart.js color palette for containers
const PALETTE = [
  'hsl(210,100%,60%)', 'hsl(145,90%,48%)', 'hsl(35,100%,55%)',
  'hsl(265,90%,65%)', 'hsl(188,100%,50%)', 'hsl(355,90%,60%)',
  'hsl(55,100%,55%)',  'hsl(300,80%,65%)', 'hsl(175,85%,45%)',
];

let ws             = null;
let wsReconnect    = null;
let mainChart      = null;
let topChart       = null;
let selectedIface  = 'eth0';

// Rolling history: { label: [timestamps], rxSeries: [values], txSeries: [values] }
const ifaceHistory     = {};
const containerHistory = {};

/* ══════════════════════════════════════════
   Formatters (pure, no DOM)
══════════════════════════════════════════ */
function fmtRate(bytes) {
  let n = bytes;
  for (const unit of BYTES_UNITS) {
    if (Math.abs(n) < 1024) return `${n.toFixed(1)} ${unit}`;
    n /= 1024;
  }
  return `${n.toFixed(1)} TB/s`;
}

function fmtTotal(bytes) {
  let n = bytes;
  for (const unit of TOTAL_UNITS) {
    if (Math.abs(n) < 1024) return `${n.toFixed(1)} ${unit}`;
    n /= 1024;
  }
  return `${n.toFixed(1)} TB`;
}

function fmtTime(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

/* ══════════════════════════════════════════
   Chart initialisation
══════════════════════════════════════════ */
const CHART_DEFAULTS = {
  animation: { duration: 200 },
  responsive: true,
  maintainAspectRatio: false,
  plugins: {
    legend: {
      labels: { color: 'hsl(210,20%,65%)', font: { family: 'JetBrains Mono', size: 11 }, boxWidth: 12, padding: 16 },
    },
    tooltip: {
      backgroundColor: 'hsla(222,47%,8%,0.95)',
      titleColor:      'hsl(210,30%,96%)',
      bodyColor:       'hsl(210,20%,65%)',
      borderColor:     'hsla(210,60%,60%,0.2)',
      borderWidth:     1,
      callbacks: {
        label: ctx => ` ${ctx.dataset.label}: ${fmtRate(ctx.raw)}`,
      },
    },
  },
  scales: {
    x: {
      grid:  { color: 'hsla(210,20%,50%,0.08)' },
      ticks: { color: 'hsl(210,15%,40%)', font: { family: 'JetBrains Mono', size: 10 }, maxTicksLimit: 6 },
    },
    y: {
      grid:   { color: 'hsla(210,20%,50%,0.08)' },
      ticks:  { color: 'hsl(210,15%,40%)', font: { family: 'JetBrains Mono', size: 10 }, callback: v => fmtRate(v) },
      beginAtZero: true,
    },
  },
};

function initMainChart() {
  const ctx = document.getElementById('main-chart').getContext('2d');
  mainChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        {
          label: '↓ Download',
          data: [],
          borderColor:       'hsl(145,90%,48%)',
          backgroundColor:   'hsla(145,90%,48%,0.08)',
          borderWidth: 2,
          fill: true,
          tension: 0.4,
          pointRadius: 0,
        },
        {
          label: '↑ Upload',
          data: [],
          borderColor:       'hsl(355,90%,60%)',
          backgroundColor:   'hsla(355,90%,60%,0.08)',
          borderWidth: 2,
          fill: true,
          tension: 0.4,
          pointRadius: 0,
        },
      ],
    },
    options: CHART_DEFAULTS,
  });
}

function initTopChart() {
  const ctx = document.getElementById('top-chart').getContext('2d');
  topChart = new Chart(ctx, {
    type: 'bar',
    data: { labels: [], datasets: [] },
    options: {
      ...CHART_DEFAULTS,
      indexAxis: 'y',
      plugins: {
        ...CHART_DEFAULTS.plugins,
        legend: { display: false },
        tooltip: {
          ...CHART_DEFAULTS.plugins.tooltip,
          callbacks: { label: ctx => ` ${fmtRate(ctx.raw)}` },
        },
      },
      scales: {
        x: {
          ...CHART_DEFAULTS.scales.x,
          ticks: { ...CHART_DEFAULTS.scales.x.ticks, callback: v => fmtRate(v) },
          beginAtZero: true,
        },
        y: {
          ...CHART_DEFAULTS.scales.y,
          grid: { display: false },
          ticks: { color: 'hsl(210,20%,70%)', font: { family: 'Inter', size: 11 } },
        },
      },
    },
  });
}

/* ══════════════════════════════════════════
   Summary cards update
══════════════════════════════════════════ */
function updateSummaryCards(snapshot) {
  const ifaces     = snapshot.interfaces  || [];
  const containers = snapshot.containers  || [];

  // Total rates across all physical-ish interfaces (skip veth / docker bridges for summary)
  const physIfaces = ifaces.filter(r =>
    !r.iface.startsWith('veth') && !r.iface.startsWith('br-') && r.iface !== 'docker0'
  );

  const totalRx = physIfaces.reduce((s, r) => s + r.rx_rate, 0);
  const totalTx = physIfaces.reduce((s, r) => s + r.tx_rate, 0);

  // Secure DOM update: textContent only
  document.getElementById('total-rx').textContent       = fmtRate(totalRx);
  document.getElementById('total-tx').textContent       = fmtRate(totalTx);
  document.getElementById('ifaces-count').textContent   = ifaces.length;
  document.getElementById('containers-count').textContent = containers.length;
}

/* ══════════════════════════════════════════
   Interface selector & history chart
══════════════════════════════════════════ */
function updateIfaceSelector(ifaces) {
  const sel = document.getElementById('iface-select');
  const current = sel.value || selectedIface;
  const names = ifaces.map(r => r.iface);

  // Rebuild options safely using DOM API
  sel.replaceChildren();
  for (const name of names) {
    const opt = document.createElement('option');
    opt.value       = name;
    opt.textContent = name;
    if (name === current) opt.selected = true;
    sel.appendChild(opt);
  }

  if (!sel.value && names.length) sel.value = names[0];
  selectedIface = sel.value || selectedIface;
}

function pushMainChartData(ifaces, ts) {
  if (!mainChart) return;
  const row = ifaces.find(r => r.iface === selectedIface);
  if (!row) return;

  const label = fmtTime(ts);
  const ds    = mainChart.data;

  ds.labels.push(label);
  ds.datasets[0].data.push(row.rx_rate);
  ds.datasets[1].data.push(row.tx_rate);

  if (ds.labels.length > MAX_HISTORY_POINTS) {
    ds.labels.shift();
    ds.datasets[0].data.shift();
    ds.datasets[1].data.shift();
  }

  mainChart.update('none');
}

/* ══════════════════════════════════════════
   Top consumers bar chart
══════════════════════════════════════════ */
function updateTopChart(containers) {
  if (!topChart) return;
  const top = [...containers]
    .sort((a, b) => (b.rx_rate + b.tx_rate) - (a.rx_rate + a.tx_rate))
    .slice(0, 10);

  topChart.data.labels   = top.map(c => c.name);
  topChart.data.datasets = [
    {
      label: '↓ RX',
      data:  top.map(c => c.rx_rate),
      backgroundColor: top.map((_, i) => PALETTE[i % PALETTE.length] + 'bb'),
      borderColor:     top.map((_, i) => PALETTE[i % PALETTE.length]),
      borderWidth: 1,
      borderRadius: 4,
    },
  ];
  topChart.update('none');
}

/* ══════════════════════════════════════════
   Interface table
══════════════════════════════════════════ */
function updateIfaceTable(ifaces) {
  const tbody = document.getElementById('iface-tbody');
  tbody.replaceChildren();

  if (!ifaces.length) {
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = 5;
    td.className = 'loading-row';
    td.textContent = 'No interfaces detected';
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }

  for (const r of ifaces) {
    const tr = document.createElement('tr');

    // Interface name
    const tdIface = document.createElement('td');
    tdIface.className = 'cell-iface';
    tdIface.textContent = r.iface;

    // RX rate
    const tdRx = document.createElement('td');
    tdRx.className = 'cell-rx right';
    tdRx.textContent = fmtRate(r.rx_rate);

    // TX rate
    const tdTx = document.createElement('td');
    tdTx.className = 'cell-tx right';
    tdTx.textContent = fmtRate(r.tx_rate);

    // Total RX
    const tdTotalRx = document.createElement('td');
    tdTotalRx.className = 'cell-total right';
    tdTotalRx.textContent = fmtTotal(r.rx_bytes);

    // Total TX
    const tdTotalTx = document.createElement('td');
    tdTotalTx.className = 'cell-total right';
    tdTotalTx.textContent = fmtTotal(r.tx_bytes);

    tr.append(tdIface, tdRx, tdTx, tdTotalRx, tdTotalTx);
    tbody.appendChild(tr);
  }
}

/* ══════════════════════════════════════════
   Container table
══════════════════════════════════════════ */
function updateContainerTable(containers, dockerAvailable) {
  const tbody  = document.getElementById('container-tbody');
  const badge  = document.getElementById('container-docker-status');
  tbody.replaceChildren();

  // Update docker badge safely
  badge.textContent  = dockerAvailable ? '● Docker connected' : '○ Docker offline';
  badge.style.color  = dockerAvailable ? 'hsl(145,90%,48%)' : 'hsl(210,15%,40%)';

  if (!dockerAvailable) {
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan   = 8;
    td.className = 'loading-row';
    td.textContent = 'Docker is not available or the user is not in the docker group.';
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }

  if (!containers.length) {
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan   = 8;
    td.className = 'loading-row';
    td.textContent = 'No running containers found.';
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }

  // Find max rate for activity bar scaling
  const maxRate = Math.max(1, ...containers.map(c => c.rx_rate + c.tx_rate));

  containers.forEach((c, i) => {
    const tr = document.createElement('tr');
    const rank = i + 1;
    const totalRate = c.rx_rate + c.tx_rate;
    const barPct = Math.min(100, (totalRate / maxRate) * 100);

    // Rank
    const tdRank = document.createElement('td');
    tdRank.className = `cell-rank rank-${rank}`;
    tdRank.textContent = rank;

    // Name
    const tdName = document.createElement('td');
    tdName.className = 'cell-name';
    tdName.textContent = c.name;
    tdName.title = c.name;

    // Image
    const tdImage = document.createElement('td');
    tdImage.className = 'cell-image';
    tdImage.textContent = c.image;
    tdImage.title = c.image;

    // RX rate
    const tdRx = document.createElement('td');
    tdRx.className = 'cell-rx right';
    tdRx.textContent = fmtRate(c.rx_rate);

    // TX rate
    const tdTx = document.createElement('td');
    tdTx.className = 'cell-tx right';
    tdTx.textContent = fmtRate(c.tx_rate);

    // Total RX
    const tdTotalRx = document.createElement('td');
    tdTotalRx.className = 'cell-total right';
    tdTotalRx.textContent = fmtTotal(c.rx_bytes);

    // Total TX
    const tdTotalTx = document.createElement('td');
    tdTotalTx.className = 'cell-total right';
    tdTotalTx.textContent = fmtTotal(c.tx_bytes);

    // Activity bar
    const tdActivity = document.createElement('td');
    tdActivity.className = 'right';
    const barWrap = document.createElement('div');
    barWrap.className = 'activity-bar-wrap';
    const bar = document.createElement('div');
    bar.className = 'activity-bar';
    bar.style.width = `${barPct}%`;
    barWrap.appendChild(bar);
    tdActivity.appendChild(barWrap);

    tr.append(tdRank, tdName, tdImage, tdRx, tdTx, tdTotalRx, tdTotalTx, tdActivity);
    tbody.appendChild(tr);
  });
}

/* ══════════════════════════════════════════
   Snapshot Processing & Data Update
══════════════════════════════════════════ */
let isWsConnected = false;
let pollInterval  = null;

function processSnapshot(snap) {
  if (!snap) return;

  // Update last-update timestamp
  if (snap.ts) {
    document.getElementById('last-update').textContent = fmtTime(snap.ts);
  }

  // Docker badge
  const dockerBadge = document.getElementById('docker-badge');
  if (dockerBadge) {
    dockerBadge.textContent = snap.docker_available ? 'Docker ●' : 'Docker ○';
    dockerBadge.className   = `meta-badge ${snap.docker_available ? 'docker-online' : 'docker-offline'}`;
  }

  // Summary cards
  updateSummaryCards(snap);

  // Interface selector + main line chart
  if (snap.interfaces && snap.interfaces.length) {
    updateIfaceSelector(snap.interfaces);
    pushMainChartData(snap.interfaces, snap.ts || Date.now() / 1000);
    updateIfaceTable(snap.interfaces);
  }

  // Container table + top bar chart
  updateContainerTable(snap.containers || [], snap.docker_available);
  if (snap.containers && snap.containers.length) {
    updateTopChart(snap.containers);
  }
}

async function fetchSnapshot() {
  try {
    const res = await fetch('/api/snapshot');
    if (res.ok) {
      const snap = await res.json();
      processSnapshot(snap);
      if (!isWsConnected) {
        setWsStatus('connected', 'Live (HTTP)');
      }
    }
  } catch (err) {
    if (!isWsConnected) {
      setWsStatus('disconnected', 'Offline');
    }
  }
}

function startPollingFallback() {
  if (!pollInterval) {
    pollInterval = setInterval(() => {
      if (!isWsConnected) {
        fetchSnapshot();
      }
    }, 2000);
  }
}

/* ══════════════════════════════════════════
   WebSocket management
══════════════════════════════════════════ */
function setWsStatus(state, label) {
  const el = document.getElementById('ws-status');
  if (!el) return;
  el.className = `ws-indicator ws-${state}`;
  document.getElementById('ws-label').textContent = label;
}

function connectWs() {
  clearTimeout(wsReconnect);

  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const url   = `${proto}://${location.host}/ws`;

  setWsStatus('connecting', 'Connecting…');

  try {
    ws = new WebSocket(url);
  } catch (e) {
    isWsConnected = false;
    startPollingFallback();
    return;
  }

  ws.addEventListener('open', () => {
    isWsConnected = true;
    setWsStatus('connected', 'Live');
  });

  ws.addEventListener('message', evt => {
    let data;
    try {
      data = JSON.parse(evt.data);
    } catch {
      return;
    }

    if (data.type === 'ping') return;
    if (data.type !== 'update') return;

    isWsConnected = true;
    setWsStatus('connected', 'Live');
    processSnapshot(data);
  });

  ws.addEventListener('close', () => {
    isWsConnected = false;
    setWsStatus('connecting', 'Reconnecting…');
    startPollingFallback();
    wsReconnect = setTimeout(connectWs, WS_RECONNECT_DELAY_MS);
  });

  ws.addEventListener('error', () => {
    isWsConnected = false;
    startPollingFallback();
    try { ws.close(); } catch (e) {}
  });
}

/* ══════════════════════════════════════════
   Interface selector change handler
══════════════════════════════════════════ */
document.getElementById('iface-select').addEventListener('change', function () {
  selectedIface = this.value;
  if (mainChart) {
    mainChart.data.labels = [];
    mainChart.data.datasets[0].data = [];
    mainChart.data.datasets[1].data = [];
    mainChart.update();

    const titleEl = document.querySelector('#main-content .panel-title');
    if (titleEl) {
      const icon = titleEl.querySelector('svg');
      titleEl.replaceChildren();
      if (icon) titleEl.appendChild(icon);
      const txt = document.createTextNode(` Live Traffic — ${this.value}`);
      titleEl.appendChild(txt);
    }
  }
});

/* ══════════════════════════════════════════
   Initialise
══════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  initMainChart();
  initTopChart();
  fetchSnapshot();
  connectWs();
  startPollingFallback();
});

