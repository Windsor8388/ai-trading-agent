const tabs = document.querySelectorAll('.tab');
const panels = document.querySelectorAll('.panel');
const safetyCheck = document.querySelector('#safety-check');
const toast = document.querySelector('#toast');

function activateTab(tab) {
  tabs.forEach((item) => {
    const active = item === tab;
    item.classList.toggle('active', active);
    item.setAttribute('aria-selected', String(active));
  });
  panels.forEach((panel) => {
    const active = panel.id === tab.dataset.panel;
    panel.classList.toggle('active', active);
    panel.hidden = !active;
  });
}

tabs.forEach((tab) => tab.addEventListener('click', () => activateTab(tab)));

safetyCheck.addEventListener('click', () => {
  toast.textContent = 'فحص الأمان: لا يوجد مزود بيانات حي، ولا حساب وسيط، والتنفيذ الفعلي محظور.';
  toast.classList.add('show');
  window.setTimeout(() => toast.classList.remove('show'), 4200);
});
