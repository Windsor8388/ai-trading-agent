const tabs = [...document.querySelectorAll('.tab')];
const panels = [...document.querySelectorAll('.panel')];
const safetyCheck = document.querySelector('#safety-check');
const toast = document.querySelector('#toast');
const chartSymbol = document.querySelector('#chart-symbol');
const loadChart = document.querySelector('#load-chart');
const unloadChart = document.querySelector('#unload-chart');
const chartFrame = document.querySelector('#external-chart');
const chartPlaceholder = document.querySelector('#chart-placeholder');
const chartStatus = document.querySelector('#chart-status');

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
  toast.textContent = 'فحص الأمان: لا يوجد مزود بيانات للنظام، ولا حساب وسيط، والتنفيذ الفعلي محظور.';
  toast.classList.add('show');
  window.setTimeout(() => toast.classList.remove('show'), 4200);
});

function selectedChartLabel() {
  return chartSymbol.options[chartSymbol.selectedIndex].text;
}

loadChart.addEventListener('click', () => {
  const chartUrl = new URL('chart.html', window.location.href);
  chartUrl.hash = encodeURIComponent(chartSymbol.value);
  chartFrame.src = chartUrl.toString();
  chartFrame.hidden = false;
  chartPlaceholder.hidden = true;
  unloadChart.disabled = false;
  chartStatus.textContent = `تم طلب عرض خارجي لـ ${selectedChartLabel()}. هذا العرض لا يُغذي محركات القرار.`;
});

unloadChart.addEventListener('click', () => {
  chartFrame.src = 'about:blank';
  chartFrame.hidden = true;
  chartPlaceholder.hidden = false;
  unloadChart.disabled = true;
  chartStatus.textContent = 'أزيل الرسم الخارجي. لا يوجد مصدر خارجي محمّل الآن.';
});
