/* QuantDesk UI — QuantConsole 디자인 시안을 실제 API에 연결한 구현 */
'use strict';

const UP = '#f6465d', DOWN = '#3b82f6', CYAN = '#22d3ee';
const MODEL_LABELS = { rank_ensemble: 'Rank Ensemble', lstm_sequence: 'LSTM Sequence' };
const TYPE_COLORS = { LightGBM: ['#3d2b1e', '#fbbf24'], LSTM: ['#2b1e3d', '#c084fc'] };
const SCREEN_TITLES = {
  dashboard: ['대시보드', '자산 · 수익률 · 모델 추천 요약'],
  chart: ['종목 · 일봉 차트', '캔들 차트 · 이동평균 · 모델 시그널'],
  model: ['모델 예측', '모델 선택 후 즉시 예측 실행'],
  result: ['예측 결과', '모델별 일별 예측 기록 · DB 조회'],
  settings: ['자동매매 설정', '계좌 연동 · 전략 · 리스크 관리'],
};
const NAV_ITEMS = [
  { key: 'dashboard', label: '대시보드', icon: '◈' },
  { key: 'chart', label: '종목 · 차트', icon: '◧' },
  { key: 'model', label: '모델 예측', icon: '⬡' },
  { key: 'result', label: '예측 결과', icon: '▤' },
  { key: 'settings', label: '자동매매 설정', icon: '⚙' },
];

const state = {
  screen: 'dashboard',
  health: null, models: [], settings: null,
  stocks: [], selTicker: null, prices: null,
  overlays: { ma5: true, ma20: true, signals: true },
  selModel: 'rank_ensemble', running: false, runMsg: '',
  latest: [], resultDate: '', resultModel: '', resultDates: [],
  saved: false,
};

/* ---------- API ---------- */
async function api(path, opts = {}) {
  const res = await fetch('/api' + path, {
    headers: { 'Content-Type': 'application/json' }, ...opts,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `API 오류 (${res.status})`);
  }
  return res.json();
}

const fmt = (n) => n == null ? '—' : Math.round(n).toLocaleString('ko-KR');
const pct = (n, d = 2) => n == null ? '—' : `${n >= 0 ? '+' : ''}${n.toFixed(d)}%`;
const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* ---------- 내비게이션 ---------- */
function renderNav() {
  document.getElementById('nav').innerHTML = NAV_ITEMS.map((n) => `
    <button class="nav-btn ${state.screen === n.key ? 'active' : ''}" data-screen="${n.key}">
      <span class="nav-icon">${n.icon}</span><span>${n.label}</span>
    </button>`).join('');
  document.querySelectorAll('.nav-btn').forEach((b) =>
    b.addEventListener('click', () => switchScreen(b.dataset.screen)));
}

function switchScreen(key) {
  state.screen = key;
  const [title, sub] = SCREEN_TITLES[key];
  document.getElementById('screen-title').textContent = title;
  document.getElementById('screen-sub').textContent = sub;
  document.querySelectorAll('.screen').forEach((s) => s.classList.add('hidden'));
  document.getElementById(`screen-${key}`).classList.remove('hidden');
  renderNav();
  const renderers = { dashboard: renderDashboard, chart: renderChartScreen,
    model: renderModelScreen, result: renderResultScreen, settings: renderSettings };
  renderers[key]();
}

/* ---------- 상단바 ---------- */
function renderTopbar() {
  const h = state.health;
  document.getElementById('base-date').textContent = h?.panel_base_date ?? '—';
  const next = h?.scheduler?.next_run;
  document.getElementById('next-run').textContent =
    next ? next.slice(5, 16).replace('T', ' ') : '—';

  const on = !!state.settings?.auto_trade;
  const pill = document.getElementById('auto-pill');
  pill.classList.toggle('on', on);
  pill.classList.toggle('off', !on);
  document.getElementById('auto-text').textContent = `자동매매 ${on ? 'ON' : 'OFF'}`;
}

async function toggleAuto() {
  const next = !state.settings.auto_trade;
  const { settings } = await api('/settings', {
    method: 'PUT', body: JSON.stringify({ auto_trade: next }) });
  state.settings = settings;
  renderTopbar();
}

/* ---------- 대시보드 ---------- */
function recRow(p) {
  const isLstm = p.model_id === 'lstm_sequence';
  const scoreTxt = isLstm ? pct(p.score * 100, 1) : Number(p.score).toFixed(3);
  const dir = p.signal === 'BUY' ? '매수' : '관찰';
  const sub = isLstm ? `${p.horizon}일 후 초과수익 예측` : '랭크 신호';
  return `<div class="rec-item">
    <div class="rec-score"><span class="s num ${isLstm ? (p.score >= 0 ? 'up' : 'down') : 'cy'}">${scoreTxt}</span><span class="t">SCORE</span></div>
    <div class="rec-main"><div class="rec-name">${esc(p.name || p.ticker)}</div>
      <div class="rec-model">${MODEL_LABELS[p.model_id] || p.model_id} · ${sub}</div></div>
    <div class="rec-right"><div class="rec-dir ${p.signal === 'BUY' ? 'up' : ''}">${dir}</div>
      <div class="rec-sub num">${p.run_date}</div></div>
  </div>`;
}

function renderDashboard() {
  const el = document.getElementById('screen-dashboard');
  const broker = state.health?.broker;
  const byModel = {};
  state.latest.forEach((p) => (byModel[p.model_id] ??= []).push(p));
  const rankTop = (byModel.rank_ensemble || []).slice(0, 4);
  const lstmTop = (byModel.lstm_sequence || []).filter((p) => p.horizon === 7).slice(0, 3);
  const recs = [...rankTop, ...lstmTop];

  el.innerHTML = `
    <div class="grid-stats">
      <div class="stat"><div class="label">총 자산</div>
        <div class="value num">—</div><div class="sub">브로커 미연동 (${esc(broker?.provider || 'KIS')})</div></div>
      <div class="stat"><div class="label">데이터 기준일</div>
        <div class="value num">${state.health?.panel_base_date ?? '—'}</div><div class="sub">master_panel.parquet</div></div>
      <div class="stat"><div class="label">등록 모델</div>
        <div class="value num">${state.models.length}</div>
        <div class="sub">${state.models.filter((m) => m.ready).length}개 로드 가능</div></div>
      <div class="stat"><div class="label">저장된 예측</div>
        <div class="value num">${state.latest.length}</div>
        <div class="sub">최근 실행 기준 · daily_predictions</div></div>
    </div>
    <div class="dash-grid">
      <div class="dash-col">
        <div class="card">
          <div class="card-title">보유 종목 <span class="light">· 한국투자증권 연동 후 표시</span></div>
          <div class="empty">브로커(KIS) 미연동 상태입니다.<br>모델·UI 완성 후 자동매매 단계에서 연동됩니다.</div>
        </div>
        <div class="card">
          <div class="card-title">최근 실행 이력</div>
          <div id="dash-runs"></div>
        </div>
      </div>
      <div class="dash-col">
        <div class="card">
          <div class="card-title">오늘의 모델 추천 <span class="tag">AI</span></div>
          ${recs.length ? recs.map(recRow).join('')
            : '<div class="empty">저장된 예측이 없습니다.<br>[모델 예측] 화면에서 실행하거나 매일 08:00 자동 실행을 기다리세요.</div>'}
        </div>
        <div class="card">
          <div class="card-title">자동매매 체결 로그</div>
          <div class="empty">KIS 연동 전 — 체결 내역이 없습니다.</div>
        </div>
      </div>
    </div>`;
  loadRuns();
}

async function loadRuns() {
  const box = document.getElementById('dash-runs');
  if (!box) return;
  try {
    const { runs } = await api('/runs?limit=8');
    box.innerHTML = runs.length ? runs.map((r) => `
      <div class="rec-item">
        <span class="badge" style="background:${r.status === 'success' ? 'rgba(34,197,94,.15)' : r.status === 'running' ? 'rgba(240,185,11,.15)' : 'rgba(246,70,93,.15)'};color:${r.status === 'success' ? '#22c55e' : r.status === 'running' ? '#f0b90b' : '#f6465d'}">${r.status}</span>
        <div class="rec-main"><span style="font-size:12.5px;font-weight:500">${MODEL_LABELS[r.model_id] || r.model_id}</span>
          <span class="num" style="font-size:11.5px;color:#7d8ba0"> ${esc(r.message || '')}</span></div>
        <span class="num" style="font-size:11px;color:#5c6b80">${(r.started_at || '').slice(5, 16)}</span>
      </div>`).join('')
      : '<div class="empty">실행 이력이 없습니다.</div>';
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

/* ---------- 차트 화면 ---------- */
function renderChartScreen() {
  const el = document.getElementById('screen-chart');
  el.innerHTML = `
    <div class="chart-grid">
      <div class="card" style="padding:12px">
        <div class="search-box"><span style="color:#5c6b80;font-size:13px">⌕</span>
          <input id="stock-search" placeholder="종목 검색"></div>
        <div id="stock-list" class="stock-list"></div>
      </div>
      <div class="card">
        <div class="sel-head" id="sel-head"></div>
        <div class="overlay-chips" id="overlay-chips" style="margin-bottom:14px"></div>
        <div class="chart-wrap" id="chart-wrap">
          <canvas id="chart-main"></canvas><canvas id="chart-overlay"></canvas>
        </div>
        <div class="legend">
          <span><span class="sw" style="background:#f0b90b"></span>MA5</span>
          <span><span class="sw" style="background:#8b5cf6"></span>MA20</span>
          <span><span class="cy">▲</span> 모델 추천 시그널 (최근 예측)</span>
        </div>
      </div>
    </div>`;
  document.getElementById('stock-search').addEventListener('input', (e) =>
    renderStockList(e.target.value.trim()));
  renderStockList('');
  if (!state.selTicker && state.stocks.length) state.selTicker = state.stocks[0].ticker;
  if (state.selTicker) loadPrices(state.selTicker);
}

function renderStockList(query) {
  const box = document.getElementById('stock-list');
  if (!box) return;
  const list = state.stocks.filter((s) =>
    !query || s.name.includes(query) || s.ticker.includes(query)).slice(0, 120);
  box.innerHTML = list.map((s) => `
    <button class="stock-item ${s.ticker === state.selTicker ? 'active' : ''}" data-ticker="${s.ticker}">
      <div><div class="nm">${esc(s.name)}</div><div class="cd num">${s.ticker}</div></div>
      <div><div class="pr num">${fmt(s.close)}</div>
        <div class="pc num ${s.change_pct >= 0 ? 'up' : 'down'}">${pct(s.change_pct)}</div></div>
    </button>`).join('');
  box.querySelectorAll('.stock-item').forEach((b) =>
    b.addEventListener('click', () => { state.selTicker = b.dataset.ticker; renderChartScreen(); }));
}

async function loadPrices(ticker) {
  try {
    state.prices = await api(`/prices/${ticker}?days=120`);
    renderSelHead(); renderOverlayChips(); drawChart();
  } catch (e) {
    document.getElementById('sel-head').innerHTML =
      `<div class="empty">${esc(e.message)}</div>`;
  }
}

function renderSelHead() {
  const p = state.prices; if (!p) return;
  const c = p.candles, last = c[c.length - 1], prev = c[c.length - 2] || last;
  const chg = last.close - prev.close, chgPct = (chg / prev.close) * 100;
  const cls = chg >= 0 ? 'up' : 'down';
  document.getElementById('sel-head').innerHTML = `
    <div>
      <div style="display:flex;align-items:center;gap:10px">
        <span class="sel-name">${esc(p.name)}</span><span class="sel-code num">${p.ticker}</span>
        <span style="font-size:11px;color:#5c6b80">${esc(p.sector)}</span></div>
      <div style="display:flex;align-items:baseline;gap:10px;margin-top:3px">
        <span class="sel-price num ${cls}">₩${fmt(last.close)}</span>
        <span class="sel-chg num ${cls}">${chg >= 0 ? '+' : ''}${fmt(chg)} (${pct(chgPct)})</span></div>
    </div>`;
}

function renderOverlayChips() {
  const defs = [['ma5', 'MA5'], ['ma20', 'MA20'], ['signals', '모델 시그널']];
  const box = document.getElementById('overlay-chips');
  box.innerHTML = defs.map(([k, label]) =>
    `<button class="chip ${state.overlays[k] ? 'active' : ''}" data-k="${k}">${label}</button>`).join('');
  box.querySelectorAll('.chip').forEach((b) => b.addEventListener('click', () => {
    state.overlays[b.dataset.k] = !state.overlays[b.dataset.k];
    renderOverlayChips(); drawChart();
  }));
}

function sizeCanvas(cv) {
  const box = cv.parentElement.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  cv.width = box.width * dpr; cv.height = box.height * dpr;
  const ctx = cv.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, W: box.width, H: box.height };
}

const ma = (arr, w, i) => {
  if (i < w - 1) return null;
  let s = 0; for (let k = 0; k < w; k++) s += arr[i - k].close;
  return s / w;
};

let chartGeo = null;
function drawChart() {
  const cv = document.getElementById('chart-main');
  const ov = document.getElementById('chart-overlay');
  if (!cv || !state.prices) return;
  const { ctx, W, H } = sizeCanvas(cv); sizeCanvas(ov);
  ctx.clearRect(0, 0, W, H);

  const ohlc = state.prices.candles;
  const padL = 8, padR = 64, padT = 10, padB = 26, volH = 54;
  const priceH = H - padT - padB - volH, plotW = W - padL - padR;
  const step = plotW / ohlc.length, cw = Math.max(2, step * 0.62);

  let hi = -Infinity, lo = Infinity;
  ohlc.forEach((d, i) => {
    hi = Math.max(hi, d.high); lo = Math.min(lo, d.low);
    if (state.overlays.ma20) { const m = ma(ohlc, 20, i); if (m) { hi = Math.max(hi, m); lo = Math.min(lo, m); } }
  });
  const pad = (hi - lo) * 0.06; hi += pad; lo -= pad;

  const xAt = (i) => padL + step * i + step / 2;
  const yAt = (p) => padT + (hi - p) / (hi - lo) * priceH;
  const pAt = (y) => hi - (y - padT) / priceH * (hi - lo);
  let vmax = 0; ohlc.forEach((d) => vmax = Math.max(vmax, d.volume));
  const volTop = padT + priceH + 8;
  const vAt = (v) => volTop + volH - v / vmax * volH;

  ctx.font = "10px 'JetBrains Mono',monospace"; ctx.textBaseline = 'middle';
  for (let g = 0; g <= 4; g++) {
    const p = lo + (hi - lo) * g / 4, y = yAt(p);
    ctx.strokeStyle = 'rgba(255,255,255,0.04)';
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
    ctx.fillStyle = '#5c6b80'; ctx.textAlign = 'left'; ctx.fillText(fmt(p), W - padR + 6, y);
  }
  ctx.textAlign = 'center'; ctx.fillStyle = '#4d5869';
  for (let i = 0; i < ohlc.length; i += Math.ceil(ohlc.length / 6))
    ctx.fillText(ohlc[i].date.slice(5), xAt(i), H - 12);

  ohlc.forEach((d, i) => {
    const up = d.close >= d.open;
    ctx.fillStyle = up ? 'rgba(246,70,93,0.4)' : 'rgba(59,130,246,0.4)';
    ctx.fillRect(xAt(i) - cw / 2, vAt(d.volume), cw, volTop + volH - vAt(d.volume));
  });
  ohlc.forEach((d, i) => {
    const up = d.close >= d.open, col = up ? UP : DOWN, x = xAt(i);
    ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, yAt(d.high)); ctx.lineTo(x, yAt(d.low)); ctx.stroke();
    const yo = yAt(d.open), yc = yAt(d.close);
    ctx.fillRect(x - cw / 2, Math.min(yo, yc), cw, Math.max(1, Math.abs(yc - yo)));
  });

  const drawMA = (w, col) => {
    ctx.beginPath(); let started = false;
    ohlc.forEach((d, i) => {
      const m = ma(ohlc, w, i); if (m == null) return;
      if (!started) { ctx.moveTo(xAt(i), yAt(m)); started = true; }
      else ctx.lineTo(xAt(i), yAt(m));
    });
    ctx.strokeStyle = col; ctx.lineWidth = 1.5; ctx.stroke();
  };
  if (state.overlays.ma5) drawMA(5, '#f0b90b');
  if (state.overlays.ma20) drawMA(20, '#8b5cf6');

  // 모델 시그널: 최신 예측에 이 종목이 있으면 마지막 캔들 밑에 ▲
  if (state.overlays.signals) {
    const hit = state.latest.filter((p) => p.ticker === state.prices.ticker);
    if (hit.length) {
      const i = ohlc.length - 1, x = xAt(i), y = yAt(ohlc[i].low) + 14;
      ctx.fillStyle = CYAN;
      ctx.beginPath(); ctx.moveTo(x, y - 7); ctx.lineTo(x - 5, y + 2); ctx.lineTo(x + 5, y + 2);
      ctx.closePath(); ctx.fill();
      ctx.font = "10px 'JetBrains Mono',monospace"; ctx.textAlign = 'center';
      ctx.fillText(hit.map((p) => MODEL_LABELS[p.model_id]).join('·'), x, y + 14);
    }
  }

  chartGeo = { xAt, yAt, pAt, step, padL, padR, padT, priceH, W, H, ohlc };
  if (!ov._bound) {
    ov._bound = true;
    ov.addEventListener('mousemove', crosshair);
    ov.addEventListener('mouseleave', () => crosshair(null));
  }
}

function crosshair(e) {
  const ov = document.getElementById('chart-overlay');
  const g = chartGeo; if (!ov || !g) return;
  const ctx = ov.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, g.W, g.H);
  if (!e) return;
  const rect = ov.getBoundingClientRect();
  const mx = e.clientX - rect.left, my = e.clientY - rect.top;
  let idx = Math.round((mx - g.padL - g.step / 2) / g.step);
  idx = Math.max(0, Math.min(g.ohlc.length - 1, idx));
  const x = g.xAt(idx), d = g.ohlc[idx];

  ctx.setLineDash([4, 4]); ctx.strokeStyle = 'rgba(255,255,255,0.28)'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(x, g.padT); ctx.lineTo(x, g.padT + g.priceH); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(g.padL, my); ctx.lineTo(g.W - g.padR, my); ctx.stroke();
  ctx.setLineDash([]);

  ctx.fillStyle = CYAN; ctx.fillRect(g.W - g.padR, my - 9, g.padR, 18);
  ctx.fillStyle = '#04141a'; ctx.font = "10px 'JetBrains Mono',monospace";
  ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
  ctx.fillText(fmt(g.pAt(my)), g.W - g.padR + 6, my);
  ctx.textAlign = 'center';
  ctx.fillStyle = CYAN; ctx.fillRect(x - 24, g.H - 22, 48, 16);
  ctx.fillStyle = '#04141a'; ctx.fillText(d.date.slice(5), x, g.H - 14);

  const up = d.close >= d.open;
  ctx.textAlign = 'left'; ctx.font = "11px 'JetBrains Mono',monospace";
  ctx.fillStyle = 'rgba(8,11,17,0.85)'; ctx.fillRect(g.padL + 2, g.padT + 2, 210, 20);
  let cx = g.padL + 6;
  [['시', d.open], ['고', d.high], ['저', d.low], ['종', d.close]].forEach(([k, v]) => {
    ctx.fillStyle = '#7d8ba0'; ctx.fillText(k, cx, g.padT + 12); cx += 13;
    ctx.fillStyle = up ? UP : DOWN;
    const s = fmt(v); ctx.fillText(s, cx, g.padT + 12); cx += s.length * 6.6 + 9;
  });
}

/* ---------- 모델 화면 ---------- */
function renderModelScreen() {
  const el = document.getElementById('screen-model');
  const sel = state.models.find((m) => m.id === state.selModel) || state.models[0];
  if (sel) state.selModel = sel.id;

  el.innerHTML = `
    <div class="model-grid">
      <div>
        <div style="font-size:13px;color:#7d8ba0;margin-bottom:12px">
          model/ 폴더 · ${state.models.length}개 등록 · ${state.models.filter((m) => m.ready).length}개 로드 가능</div>
        <div class="model-cards">
          ${state.models.map((m) => {
            const [bg, fg] = TYPE_COLORS[m.type] || ['#1e2b3d', '#60a5fa'];
            return `<button class="model-card ${m.id === state.selModel ? 'active' : ''}" data-id="${m.id}">
              <div class="row"><span class="badge" style="background:${bg};color:${fg}">${m.type}</span>
                <span class="dot" style="background:${m.ready ? '#22c55e' : '#f6465d'}"></span></div>
              <div class="nm">${esc(m.name)}</div>
              <div class="fl num">${esc(m.file)}</div>
              <div class="desc">${esc(m.description)}</div>
              <div class="foot num" style="color:${m.ready ? '#5c6b80' : '#f6465d'}">${esc(m.detail)}</div>
            </button>`; }).join('')}
        </div>
      </div>
      <div class="run-panel">
        <div style="font-size:14px;font-weight:700;margin-bottom:16px">예측 실행</div>
        <div class="fld-label">선택된 모델</div>
        <div class="fld">${esc(sel ? sel.name : '—')}</div>
        <div class="fld-label">예측 대상</div>
        <div class="fld">KOSPI200 유니버스 전 종목 · 최신 거래일</div>
        <button id="run-btn" class="run-btn" ${state.running || !sel?.ready ? 'disabled' : ''}>
          ${state.running ? '예측 수행 중…' : sel?.ready ? '예측 수행하기' : '모델 파일 없음'}</button>
        <div id="run-result" class="run-result ${state.runMsg || state.latestForSel()?.length ? '' : 'hidden'}"></div>
        <div class="hint" style="margin-top:14px">서버 스케줄러가 매일 08:00에 전체 모델 예측을 자동 실행하여 DB(daily_predictions)에 저장합니다.</div>
      </div>
    </div>`;

  el.querySelectorAll('.model-card').forEach((b) =>
    b.addEventListener('click', () => { state.selModel = b.dataset.id; renderModelScreen(); }));
  const runBtn = document.getElementById('run-btn');
  if (runBtn) runBtn.addEventListener('click', runPredict);
  renderRunResult();
}

state.latestForSel = function () {
  return this.latest.filter((p) => p.model_id === this.selModel);
};

function renderRunResult() {
  const box = document.getElementById('run-result');
  if (!box) return;
  const rows = state.latestForSel();
  if (!state.runMsg && !rows.length) { box.classList.add('hidden'); return; }
  box.classList.remove('hidden');
  box.innerHTML = `
    ${state.runMsg ? `<div style="font-size:12px;color:#f0b90b;margin-bottom:10px">${esc(state.runMsg)}</div>` : ''}
    ${rows.length ? `
      <div style="display:flex;justify-content:space-between;margin-bottom:10px">
        <span style="font-size:12px;color:#7d8ba0">최근 저장된 예측</span>
        <span class="num" style="font-size:10.5px;color:#5c6b80">${rows[0].run_date}</span></div>
      <div class="result-list">${rows.slice(0, 15).map((p) => `
        <div class="rec-item" style="padding:8px 0">
          <span class="num" style="font-size:11px;color:#5c6b80;width:34px">${p.horizon ? p.horizon + 'd' : 'rank'}</span>
          <div class="rec-main"><span style="font-size:12.5px;font-weight:600">${esc(p.name || p.ticker)}</span></div>
          <span class="num" style="font-size:12px" class="${p.score >= 0 ? 'up' : 'down'}">${p.model_id === 'lstm_sequence' ? pct(p.score * 100, 2) : Number(p.score).toFixed(3)}</span>
          <span class="badge" style="margin-left:8px;background:${p.signal === 'BUY' ? 'rgba(246,70,93,.15)' : '#131a24'};color:${p.signal === 'BUY' ? '#f6465d' : '#7d8ba0'}">${p.signal}</span>
        </div>`).join('')}</div>` : ''}`;
}

async function runPredict() {
  if (state.running) return;
  state.running = true; state.runMsg = '';
  renderModelScreen();
  try {
    const res = await api('/predict', {
      method: 'POST', body: JSON.stringify({ model_id: state.selModel }) });
    if (res.status === 'running' && res.run_id) {
      state.runMsg = '예측 실행 중 — 패널 로드와 피처 생성에 수 분이 걸릴 수 있습니다.';
      renderRunResult();
      await pollRun(res.run_id);
    }
  } catch (e) {
    state.runMsg = `실행 실패: ${e.message}`;
  }
  state.running = false;
  await refreshLatest();
  renderModelScreen();
}

async function pollRun(runId) {
  for (let i = 0; i < 600; i++) {
    await new Promise((r) => setTimeout(r, 3000));
    const run = await api(`/runs/${runId}`);
    if (run.status === 'success') { state.runMsg = `완료 — ${run.message}`; return; }
    if (run.status === 'error') {
      state.runMsg = `오류: ${(run.message || '').split('\n')[0]}`; return; }
  }
  state.runMsg = '시간 초과 — [대시보드 > 실행 이력]에서 상태를 확인하세요.';
}

/* ---------- 결과 화면 ---------- */
async function renderResultScreen() {
  const el = document.getElementById('screen-result');
  try {
    const { dates } = await api('/predictions/dates');
    state.resultDates = dates;
    if (!state.resultDate && dates.length) state.resultDate = dates[0];
  } catch { state.resultDates = []; }

  const params = new URLSearchParams();
  if (state.resultModel) params.set('model_id', state.resultModel);
  if (state.resultDate) params.set('run_date', state.resultDate);
  let preds = [];
  try { preds = (await api(`/predictions?${params}`)).predictions; } catch { /* 표시만 */ }

  const counts = {};
  preds.forEach((p) => counts[p.model_id] = (counts[p.model_id] || 0) + 1);

  el.innerHTML = `
    <div class="filter-bar">
      <select id="res-date">
        ${state.resultDates.length
          ? state.resultDates.map((d) => `<option ${d === state.resultDate ? 'selected' : ''}>${d}</option>`).join('')
          : '<option value="">저장된 날짜 없음</option>'}
      </select>
      <button class="chip ${!state.resultModel ? 'active' : ''}" data-m="">전체</button>
      ${Object.entries(MODEL_LABELS).map(([id, label]) =>
        `<button class="chip ${state.resultModel === id ? 'active' : ''}" data-m="${id}">${label}</button>`).join('')}
      <span class="db-note num">DB · daily_predictions</span>
    </div>
    <div class="grid-stats" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr))">
      ${Object.entries(MODEL_LABELS).map(([id, label]) => `
        <div class="stat"><div class="label">${label}</div>
          <div class="value num cy">${counts[id] || 0}</div>
          <div class="sub">${state.resultDate || '—'} 저장 건수</div></div>`).join('')}
    </div>
    <div class="result-table">
      <div class="rt-head"><span>날짜</span><span>모델</span><span>종목</span><span>구분</span><span style="text-align:right">신호값</span><span style="text-align:right">기준 종가</span></div>
      ${preds.length ? preds.map((p) => `
        <div class="rt-row">
          <span class="num" style="color:#9fb0c6">${p.run_date}</span>
          <span>${MODEL_LABELS[p.model_id] || p.model_id}</span>
          <span style="font-weight:500">${esc(p.name || p.ticker)} <span class="num" style="color:#5c6b80;font-size:11px">${p.ticker}</span></span>
          <span class="${p.signal === 'BUY' ? 'up' : ''}" style="font-weight:700;font-size:12px">${p.signal}${p.horizon ? ` · ${p.horizon}d` : ''}</span>
          <span class="num" style="text-align:right">${p.model_id === 'lstm_sequence' ? pct(p.score * 100, 2) : Number(p.score).toFixed(4)}</span>
          <span class="num" style="text-align:right">${fmt(p.close)}</span>
        </div>`).join('')
      : '<div class="empty">조건에 맞는 예측 기록이 없습니다.</div>'}
    </div>`;

  document.getElementById('res-date').addEventListener('change', (e) => {
    state.resultDate = e.target.value; renderResultScreen(); });
  el.querySelectorAll('.filter-bar .chip').forEach((b) =>
    b.addEventListener('click', () => { state.resultModel = b.dataset.m; renderResultScreen(); }));
}

/* ---------- 설정 화면 ---------- */
function renderSettings() {
  const el = document.getElementById('screen-settings');
  const s = state.settings || {};
  const broker = state.health?.broker;
  el.innerHTML = `
    <div class="settings-col">
      <div class="card">
        <div class="set-title">계좌 연동 — 한국투자증권 (KIS OpenAPI)</div>
        <div class="set-sub">${esc(broker?.message || '미연동')} · 가상 계좌로 전략을 검증한 뒤 실계좌로 전환하세요.</div>
        <div class="mode-row">
          ${[['virtual', '가상 계좌', '모의 투자 · 리스크 없음'], ['real', '실제 계좌', '실거래 · 실제 자금 투입']]
            .map(([k, t, d]) => `<button class="mode-btn ${s.account_mode === k ? 'active' : ''}" data-mode="${k}">
              <div class="t">${t}</div><div class="d">${d}</div></button>`).join('')}
        </div>
        <div class="fld-grid">
          <div><div class="fld-label">증권사</div><div class="fld">한국투자증권 REST (미연동)</div></div>
          <div><div class="fld-label">APP KEY</div><div class="fld num" style="color:#7d8ba0">연동 시 환경변수로 설정</div></div>
        </div>
      </div>
      <div class="card">
        <div class="set-title" style="margin-bottom:16px">매매 전략</div>
        <div class="section-gap">
          <div>
            <div class="slider-row"><span>신뢰도 임계값</span><span class="slider-val num cy" id="v-conf">${s.conf_threshold}%</span></div>
            <input type="range" id="in-conf" min="50" max="95" value="${s.conf_threshold}" style="accent-color:#22d3ee">
            <div class="hint">모델 신호가 이 값 이상일 때만 자동 주문을 실행합니다. (자동매매 구현 시 적용)</div>
          </div>
          <div class="fld-grid" style="gap:16px">
            <div>
              <div class="slider-row"><span>익절 목표</span><span class="slider-val num up" id="v-tp">+${s.take_profit_pct}%</span></div>
              <input type="range" id="in-tp" min="2" max="30" value="${s.take_profit_pct}" style="accent-color:#f6465d">
            </div>
            <div>
              <div class="slider-row"><span>손절 한도</span><span class="slider-val num down" id="v-sl">-${s.stop_loss_pct}%</span></div>
              <input type="range" id="in-sl" min="2" max="20" value="${s.stop_loss_pct}" style="accent-color:#3b82f6">
            </div>
          </div>
          <div class="fld-grid">
            <div><div class="fld-label">종목당 최대 투자금</div><div class="fld num">₩ ${fmt(s.max_position_krw)}</div></div>
            <div><div class="fld-label">최대 동시 보유 종목</div><div class="fld num">${s.max_holdings} 종목</div></div>
          </div>
          <div>
            <div style="font-size:12.5px;color:#c3cede;margin-bottom:9px">사용 모델</div>
            <div style="display:flex;flex-wrap:wrap;gap:8px" id="model-chips">
              ${Object.entries(MODEL_LABELS).map(([id, label]) =>
                `<button class="chip ${(s.enabled_models || []).includes(id) ? 'active' : ''}" data-id="${id}">${label}</button>`).join('')}
            </div>
          </div>
        </div>
      </div>
      <div class="save-row">
        <button id="save-btn" class="save-btn">전략 저장</button>
        <span class="saved-msg ${state.saved ? '' : 'hidden'}">✓ 저장되었습니다</span>
      </div>
    </div>`;

  el.querySelectorAll('.mode-btn').forEach((b) => b.addEventListener('click', () => {
    state.settings = { ...state.settings, account_mode: b.dataset.mode };
    renderSettings();
  }));
  [['in-conf', 'v-conf', 'conf_threshold', (v) => `${v}%`],
   ['in-tp', 'v-tp', 'take_profit_pct', (v) => `+${v}%`],
   ['in-sl', 'v-sl', 'stop_loss_pct', (v) => `-${v}%`]].forEach(([inId, vId, key, fmtV]) => {
    document.getElementById(inId).addEventListener('input', (e) => {
      state.settings = { ...state.settings, [key]: +e.target.value };
      document.getElementById(vId).textContent = fmtV(e.target.value);
    });
  });
  document.getElementById('model-chips').querySelectorAll('.chip').forEach((b) =>
    b.addEventListener('click', () => {
      const cur = new Set(state.settings.enabled_models || []);
      cur.has(b.dataset.id) ? cur.delete(b.dataset.id) : cur.add(b.dataset.id);
      state.settings = { ...state.settings, enabled_models: [...cur] };
      renderSettings();
    }));
  document.getElementById('save-btn').addEventListener('click', async () => {
    const { settings } = await api('/settings', {
      method: 'PUT', body: JSON.stringify(state.settings) });
    state.settings = settings; state.saved = true;
    renderSettings(); renderTopbar();
    setTimeout(() => { state.saved = false;
      document.querySelector('.saved-msg')?.classList.add('hidden'); }, 2200);
  });
}

/* ---------- 부트스트랩 ---------- */
async function refreshLatest() {
  try { state.latest = (await api('/predictions/latest')).predictions; }
  catch { state.latest = []; }
}

async function boot() {
  renderNav();
  document.getElementById('auto-switch').addEventListener('click', toggleAuto);
  try {
    const [health, models, settings] = await Promise.all([
      api('/health'), api('/models'), api('/settings')]);
    state.health = health; state.models = models.models;
    state.settings = settings.settings;
  } catch (e) {
    document.getElementById('screen-dashboard').innerHTML =
      `<div class="empty">서버 연결 실패: ${esc(e.message)}</div>`;
    return;
  }
  await refreshLatest();
  try { state.stocks = (await api('/stocks')).stocks; } catch { state.stocks = []; }
  renderTopbar();
  switchScreen('dashboard');
  window.addEventListener('resize', () => {
    if (state.screen === 'chart' && state.prices) drawChart();
  });
}

boot();
