const tabs = [...document.querySelectorAll('.tab')];
const panels = [...document.querySelectorAll('.panel')];
const safetyCheck = document.querySelector('#safety-check');
const toast = document.querySelector('#toast');
const chartSymbol = document.querySelector('#chart-symbol');
const chartInterval = document.querySelector('#chart-interval');
const workspaceInstrument = document.querySelector('#workspace-instrument');
const timeframeChips = [...document.querySelectorAll('.timeframe-chip')];
const watchlistItems = [...document.querySelectorAll('.watchlist-item')];
const openChartWorkspace = document.querySelector('#open-chart-workspace');
const copySystemSummary = document.querySelector('#copy-system-summary');
const workspaceNote = document.querySelector('#workspace-note');
const loadChart = document.querySelector('#load-chart');
const unloadChart = document.querySelector('#unload-chart');
const chartFrame = document.querySelector('#external-chart');
const chartPlaceholder = document.querySelector('#chart-placeholder');
const chartStatus = document.querySelector('#chart-status');
const chartFrameTitle = document.querySelector('#chart-frame-title');
const controlPlaneStatus = document.querySelector('#control-plane-status');
const refreshControlPlane = document.querySelector('#refresh-control-plane');

const preferenceKey = 'trading-intelligence-display-preferences-v1';
const allowedSymbols = new Set([...chartSymbol.options].map((option) => option.value));
const allowedIntervals = new Set([...chartInterval.options].map((option) => option.value));
const symbolLabels = {
  'OANDA:XAUUSD': 'XAU/USD · Spot',
  'COMEX:GC1!': 'GC · Futures continuous',
  'FX:EURUSD': 'EUR/USD · Spot',
  'TVC:SPX': 'US500 · Cash index',
  'NASDAQ:AAPL': 'AAPL · Equity',
  'COINBASE:BTCUSD': 'BTC/USD · Crypto spot',
};
const intervalLabels = { 1: 'M1', 5: 'M5', 15: 'M15', 30: 'M30', 60: 'H1', 240: 'H4', D: 'D1' };

const state = { symbol: 'OANDA:XAUUSD', interval: '15', chartLoaded: false, controlPlane: 'UNREACHABLE' };
const controlPlanePath = '/api/v1/control-plane/status';
const integrationStates = new Set(['DISABLED', 'AWAITING_SECRET_BINDING', 'AWAITING_VERIFIED_CONTRACT', 'FORWARD_PAPER_READY', 'LIVE_BLOCKED', 'LIVE_GATE_READY']);

function showToast(message) {
  toast.textContent = message;
  toast.classList.add('show');
  window.clearTimeout(showToast.timeout);
  showToast.timeout = window.setTimeout(() => toast.classList.remove('show'), 4200);
}

async function refreshControlPlaneStatus() {
  if (!controlPlaneStatus || !refreshControlPlane) return;
  refreshControlPlane.disabled = true;
  controlPlaneStatus.textContent = 'جارٍ طلب حالة control plane الآمنة من نفس origin؛ لن يُرسل المتصفح أي API key أو secret.';
  try {
    const response = await fetch(controlPlanePath, {
      method: 'GET',
      credentials: 'omit',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) throw new Error(`status=${response.status}`);
    const payload = await response.json();
    const bingxState = payload?.bingx?.state;
    const geminiState = payload?.gemini?.state;
    if (
      payload?.operation_mode !== 'ANALYSIS_ONLY'
      || payload?.trade_dispatch !== 'UNAVAILABLE'
      || !integrationStates.has(bingxState)
      || !integrationStates.has(geminiState)
    ) {
      throw new Error('Unexpected control-plane status contract.');
    }
    state.controlPlane = bingxState;
    controlPlaneStatus.textContent = `Control plane وصل بأمان: mode=${payload.operation_mode} · dispatch=${payload.trade_dispatch} · BingX=${bingxState} · Gemini=${geminiState}. لا ينفذ هذا endpoint أمراً ولا يعرض مراجع/قيم الأسرار.`;
    showToast('تم تحديث حالة control plane الآمنة؛ ما زال تنفيذ التداول غير متاح.');
  } catch (_) {
    state.controlPlane = 'UNREACHABLE';
    controlPlaneStatus.textContent = 'تعذر الوصول إلى control plane بنفس origin. تبقى هذه الواجهة static ولا تفترض اتصال BingX أو بيانات حية أو تنفيذ.';
    showToast('لا يتوفر control plane في هذا النشر؛ لم يُرسل المتصفح بيانات اعتماد ولم تتغير حدود التداول.');
  } finally {
    refreshControlPlane.disabled = false;
  }
}

function loadPreferences() {
  try {
    const stored = JSON.parse(window.localStorage.getItem(preferenceKey) || '{}');
    if (allowedSymbols.has(stored.symbol)) state.symbol = stored.symbol;
    if (allowedIntervals.has(stored.interval)) state.interval = stored.interval;
  } catch (_) {
    // Preferences are optional and never contain credentials or market data.
  }
}

function savePreferences() {
  try {
    window.localStorage.setItem(preferenceKey, JSON.stringify({ symbol: state.symbol, interval: state.interval }));
  } catch (_) {
    // A blocked storage API must not change the control-room behaviour.
  }
}

function activateTab(tab, focus = false) {
  tabs.forEach((item) => {
    const active = item === tab;
    item.classList.toggle('active', active);
    item.setAttribute('aria-selected', String(active));
    item.tabIndex = active ? 0 : -1;
  });
  panels.forEach((panel) => {
    const active = panel.id === tab.dataset.panel;
    panel.classList.toggle('active', active);
    panel.hidden = !active;
  });
  if (focus) tab.focus();
}

function updateDisplayState() {
  const symbol = symbolLabels[state.symbol];
  const interval = intervalLabels[state.interval];
  chartSymbol.value = state.symbol;
  workspaceInstrument.value = state.symbol;
  chartInterval.value = state.interval;
  chartFrameTitle.textContent = `${symbol} · ${interval}`;
  workspaceNote.textContent = `إعداد العرض: ${symbol} · ${interval}. لا توجد لقطة بيانات أو إشارة أو أمر تداول في هذه الواجهة.`;
  timeframeChips.forEach((chip) => {
    const active = chip.dataset.timeframe === state.interval;
    chip.classList.toggle('active', active);
    chip.setAttribute('aria-pressed', String(active));
  });
  watchlistItems.forEach((item) => {
    const active = item.dataset.symbol === state.symbol;
    item.classList.toggle('active', active);
    item.setAttribute('aria-pressed', String(active));
  });
}

function chartUrlForCurrentSelection() {
  const chartUrl = new URL('chart.html', window.location.href);
  chartUrl.hash = new URLSearchParams({ symbol: state.symbol, interval: state.interval }).toString();
  return chartUrl;
}

function mountExternalChart() {
  const chartUrl = chartUrlForCurrentSelection();
  chartFrame.src = chartUrl.toString();
  chartFrame.hidden = false;
  chartPlaceholder.hidden = true;
  unloadChart.disabled = false;
  state.chartLoaded = true;
  chartStatus.textContent = `تم طلب مساحة عرض خارجية لـ ${symbolLabels[state.symbol]} · ${intervalLabels[state.interval]}. قد يتطلب ظهورها اتصالاً بطرف ثالث؛ ولا تُغذي هذه البيانات محركات القرار.`;
}

function setSymbol(symbol) {
  if (!allowedSymbols.has(symbol)) return;
  state.symbol = symbol;
  savePreferences();
  updateDisplayState();
  if (state.chartLoaded) mountExternalChart();
}

function setInterval(interval) {
  if (!allowedIntervals.has(interval)) return;
  state.interval = interval;
  savePreferences();
  updateDisplayState();
  if (state.chartLoaded) mountExternalChart();
}

tabs.forEach((tab, index) => {
  tab.addEventListener('click', () => activateTab(tab));
  tab.addEventListener('keydown', (event) => {
    if (!['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    let next = index;
    if (event.key === 'ArrowRight') next = (index - 1 + tabs.length) % tabs.length;
    if (event.key === 'ArrowLeft') next = (index + 1) % tabs.length;
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = tabs.length - 1;
    activateTab(tabs[next], true);
  });
});

safetyCheck.addEventListener('click', () => {
  showToast('فحص الأمان: لا يوجد مزود بيانات للنظام، ولا حساب وسيط، والتنفيذ الفعلي محظور. الرسم الخارجي لا يغيّر هذه الحدود.');
});
refreshControlPlane.addEventListener('click', refreshControlPlaneStatus);

chartSymbol.addEventListener('change', () => setSymbol(chartSymbol.value));
workspaceInstrument.addEventListener('change', () => setSymbol(workspaceInstrument.value));
chartInterval.addEventListener('change', () => setInterval(chartInterval.value));
timeframeChips.forEach((chip) => chip.addEventListener('click', () => setInterval(chip.dataset.timeframe)));
watchlistItems.forEach((item) => item.addEventListener('click', () => setSymbol(item.dataset.symbol)));

openChartWorkspace.addEventListener('click', () => {
  document.querySelector('#chart-lab').scrollIntoView({ behavior: 'smooth', block: 'start' });
  window.setTimeout(() => loadChart.focus(), 450);
});

copySystemSummary.addEventListener('click', async () => {
  const summary = [
    'Trading Intelligence Control Room',
    `mode=ANALYSIS_ONLY`,
    `decision=NO_TRADE`,
    `display_symbol=${state.symbol}`,
    `display_interval=${state.interval}`,
    'system_market_data=DISCONNECTED',
    `control_plane_bingx=${state.controlPlane}`,
    'broker_execution=DISABLED',
    'note=Display preferences are not an analysis snapshot or trading signal.',
  ].join('\n');
  try {
    await navigator.clipboard.writeText(summary);
    showToast('تم نسخ ملخص الحالة المحلية. لا يحتوي على بيانات سوق أو مفاتيح أو أمر تداول.');
  } catch (_) {
    showToast('تعذر النسخ من المتصفح. لا تزال حالة النظام المعروضة محلية وآمنة.');
  }
});

loadChart.addEventListener('click', mountExternalChart);

unloadChart.addEventListener('click', () => {
  chartFrame.src = 'about:blank';
  chartFrame.hidden = true;
  chartPlaceholder.hidden = false;
  unloadChart.disabled = true;
  state.chartLoaded = false;
  chartStatus.textContent = 'أزيل الرسم الخارجي. لا يوجد مصدر خارجي محمّل الآن.';
});

loadPreferences();
updateDisplayState();
