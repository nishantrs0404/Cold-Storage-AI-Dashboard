/**
 * Cold Storage AI Dashboard -- Unified Frontend Logic
 * ====================================================
 * Single file: Chart.js setup + WebSocket + data fetching + UI updates.
 */

// ---- CONFIG ----
const API = window.location.origin.includes('file:')
    ? 'http://localhost:8000' : window.location.origin;
const WS_URL = API.replace('http', 'ws') + '/ws';
let selectedMinutes = 10;

// ---- CHART COLORS ----
const C = {
    temp:  { line: '#3b82f6', fill: 'rgba(59,130,246,.1)' },
    hum:   { line: '#10b981', fill: 'rgba(16,185,129,.1)' },
    mq2:   { line: '#f59e0b', fill: 'rgba(245,158,11,.1)' },
    mq135: { line: '#8b5cf6', fill: 'rgba(139,92,246,.1)' },
    pred:  { line: '#ec4899', fill: 'rgba(236,72,153,.1)' },
};

const CHART_OPTS = {
    responsive: true, maintainAspectRatio: false, animation: { duration: 300 },
    plugins: {
        legend: { display: false },
        tooltip: { backgroundColor: '#1e293b', titleColor: '#e2e8f0', bodyColor: '#94a3b8',
                   borderColor: '#334155', borderWidth: 1, padding: 10, cornerRadius: 8 },
    },
    scales: {
        x: { type: 'time', time: { unit: 'minute', displayFormats: { minute: 'HH:mm' } },
             grid: { color: 'rgba(255,255,255,.04)' },
             ticks: { color: '#64748b', maxTicksLimit: 8, font: { size: 10 } } },
        y: { grid: { color: 'rgba(255,255,255,.04)' },
             ticks: { color: '#64748b', font: { size: 10 } } },
    },
};

function ds(label, c) {
    return { label, data: [], borderColor: c.line, backgroundColor: c.fill,
             borderWidth: 2, pointRadius: 0, tension: 0.4, fill: true };
}

function mkChart(id, label, color, opts = {}) {
    return new Chart(document.getElementById(id), {
        type: 'line', data: { datasets: [ds(label, color)] },
        options: { ...CHART_OPTS, ...opts },
    });
}

function mkMini(id, color) {
    return new Chart(document.getElementById(id), {
        type: 'line',
        data: { datasets: [{ data: [], borderColor: color.line, backgroundColor: color.fill,
                             borderWidth: 1.5, pointRadius: 0, tension: 0.4, fill: true }] },
        options: { responsive: true, maintainAspectRatio: false, animation: { duration: 150 },
                   plugins: { legend: { display: false }, tooltip: { enabled: false } },
                   scales: { x: { display: false, type: 'time' }, y: { display: false } } },
    });
}

// ---- CREATE CHARTS ----
const charts = {
    combined: new Chart(document.getElementById('chartCombined'), {
        type: 'line',
        data: { datasets: [ds('Temperature', C.temp), ds('Humidity', C.hum),
                           ds('MQ-2', C.mq2), ds('MQ-135', C.mq135)] },
        options: { ...CHART_OPTS,
            plugins: { ...CHART_OPTS.plugins,
                legend: { display: true, labels: { color: '#94a3b8', padding: 16, usePointStyle: true } } } },
    }),
    temperature: mkChart('chartTemp', 'Temperature (C)', C.temp),
    humidity: mkChart('chartHum', 'Humidity (%)', C.hum),
    mq2: mkChart('chartMQ2', 'MQ-2 (ppm)', C.mq2),
    mq135: mkChart('chartMQ135', 'MQ-135 (ppm)', C.mq135),
    prediction: mkChart('chartPrediction', 'Prediction', C.pred),
};

const mini = {
    temperature: mkMini('miniTemp', C.temp),
    humidity: mkMini('miniHum', C.hum),
    mq2: mkMini('miniMQ2', C.mq2),
    mq135: mkMini('miniMQ135', C.mq135),
    prediction: mkMini('miniPred', C.pred),
};

// ---- UPDATE CHARTS ----
function updateCharts(data) {
    if (!data || !data.length) return;
    const keys = ['temperature', 'humidity', 'mq2', 'mq135'];
    const pts = data.map(d => ({ x: new Date(d.timestamp),
        temperature: d.temperature, humidity: d.humidity, mq2: d.mq2, mq135: d.mq135 }));

    keys.forEach((key, i) => {
        const mapped = pts.map(p => ({ x: p.x, y: p[key] }));
        charts[key].data.datasets[0].data = mapped;
        charts[key].update('none');
        mini[key].data.datasets[0].data = mapped.slice(-30);
        mini[key].update('none');
        charts.combined.data.datasets[i].data = mapped;
    });
    charts.combined.update('none');
}

function pushPrediction(prob) {
    const now = new Date();
    [charts.prediction, mini.prediction].forEach(c => {
        c.data.datasets[0].data.push({ x: now, y: prob || 0 });
        if (c.data.datasets[0].data.length > 200) c.data.datasets[0].data.shift();
        c.update('none');
    });
}

// ---- DOM ----
const $ = id => document.getElementById(id);

// ---- WEBSOCKET ----
function connectWS() {
    let ws;
    try { ws = new WebSocket(WS_URL); } catch {
        $('connText').textContent = 'Polling'; startPolling(); return;
    }
    ws.onopen = () => {
        $('connStatus').className = 'connection-status connected';
        $('connText').textContent = 'Live';
    };
    ws.onmessage = e => { try { handleData(JSON.parse(e.data)); } catch {} };
    ws.onclose = () => {
        $('connStatus').className = 'connection-status disconnected';
        $('connText').textContent = 'Reconnecting...';
        setTimeout(connectWS, 3000);
    };
    ws.onerror = () => ws.close();
}

function startPolling() {
    setInterval(async () => {
        try {
            const r = await (await fetch(`${API}/api/latest`)).json();
            if (r.sensors) handleData({ sensors: r.sensors, prediction: r.prediction, timestamp: r.timestamp });
        } catch {}
    }, 2000);
}

// ---- HANDLE DATA ----
function handleData({ sensors, prediction, timestamp }) {
    if (!sensors) return;
    $('lastUpdated').textContent = new Date(timestamp || Date.now()).toLocaleTimeString();

    // Sensor values
    $('valTemp').textContent = sensors.temperature?.toFixed(1) ?? '--';
    $('valHum').textContent = sensors.humidity?.toFixed(1) ?? '--';
    $('valMQ2').textContent = sensors.mq2?.toFixed(0) ?? '--';
    $('valMQ135').textContent = sensors.mq135?.toFixed(0) ?? '--';

    if (prediction) {
        const s = prediction.status || 'SAFE';
        const c = prediction.confidence || 0;
        $('valPred').textContent = s;
        $('predConf').textContent = `${c}% confidence`;
        $('statusBadge').textContent = s;
        $('statusBadge').className = `status-badge ${s.toLowerCase()}`;
        $('statusCard').className = `status-card ${s.toLowerCase()}`;
        $('confidence').textContent = `${c}%`;
        pushPrediction(prediction.probability);

        if (s === 'DANGER') {
            $('alertBar').style.display = 'flex';
            $('alertMsg').textContent = `DANGER! Confidence: ${c}% | Prob: ${(prediction.probability * 100).toFixed(1)}%`;
        } else {
            $('alertBar').style.display = 'none';
        }
    }
    fetchHistory();
}

// ---- FETCH HISTORY + ANALYTICS ----
async function fetchHistory() {
    try {
        const r = await (await fetch(`${API}/api/history?minutes=${selectedMinutes}`)).json();
        if (r.data) updateCharts(r.data);
        if (r.stats) updateStats(r.stats);
    } catch {}
}

function updateStats(stats) {
    [['Temp','temperature'],['Hum','humidity'],['MQ2','mq2'],['MQ135','mq135']].forEach(([ui,key]) => {
        const s = stats[key];
        if (!s) return;
        const a = $(` avg${ui}`), mn = $(`min${ui}`), mx = $(`max${ui}`);
        if ($(`avg${ui}`)) $(`avg${ui}`).textContent = s.mean;
        if ($(`min${ui}`)) $(`min${ui}`).textContent = s.min;
        if ($(`max${ui}`)) $(`max${ui}`).textContent = s.max;
    });
}

async function fetchAnalytics() {
    try {
        const r = await (await fetch(`${API}/api/analytics`)).json();
        if (r.risk_level) {
            $('riskLevel').textContent = r.risk_level.level;
            const s = r.risk_level.score || 0;
            $('riskBar').style.width = `${s}%`;
            $('riskBar').style.background = s > 50 ? '#ef4444' : s > 25 ? '#f59e0b' : '#10b981';
        }
        if (r.trends) {
            let html = '';
            [['Temperature','temperature'],['Humidity','humidity'],['MQ-2','mq2'],['MQ-135','mq135']].forEach(([n,k]) => {
                const t = r.trends[k] || 'stable';
                const arrow = t === 'increasing' ? '↑' : t === 'decreasing' ? '↓' : '→';
                const cls = t === 'increasing' ? 'trend-up' : t === 'decreasing' ? 'trend-down' : 'trend-stable';
                const sa = r.short_term?.[k]?.mean ?? '--';
                const la = r.long_term?.[k]?.mean ?? '--';
                html += `<tr><td>${n}</td><td class="${cls}">${arrow} ${t}</td><td>${sa}</td><td>${la}</td></tr>`;
            });
            $('trendBody').innerHTML = html;
        }
    } catch {}
}

// ---- TIME SELECTOR ----
document.querySelectorAll('.time-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.time-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        selectedMinutes = parseInt(btn.dataset.minutes);
        fetchHistory();
    });
});

// ---- INIT ----
document.addEventListener('DOMContentLoaded', () => {
    connectWS();
    setTimeout(fetchHistory, 1000);
    setTimeout(fetchAnalytics, 1500);
    setInterval(fetchAnalytics, 10000);
});
