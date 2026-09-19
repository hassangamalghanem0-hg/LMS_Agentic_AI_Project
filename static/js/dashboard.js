// Sidebar-driven tabs: clicking a "data-tab" sidebar button switches the
// matching .tab-panel inside the .tab-panels wrapper that shares its
// data-tab-key. The active tab is remembered in localStorage per dashboard,
// and any form submitted from inside a tab panel re-selects that same tab
// after the page reloads (every dashboard action is a plain form POST).
(function () {
  function storeKey(tabKey) {
    return 'activeTab:' + tabKey;
  }

  function activate(tabKey, tabId) {
    const panelWrap = document.querySelector('.tab-panels[data-tab-key="' + tabKey + '"]');
    const panel = document.getElementById(tabId);
    if (!panelWrap || !panel) return false;
    document.querySelectorAll('[data-tab-key="' + tabKey + '"][data-tab]').forEach(b => b.classList.remove('active'));
    panelWrap.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    const btn = document.querySelector('[data-tab-key="' + tabKey + '"][data-tab="' + tabId + '"]');
    if (btn) btn.classList.add('active');
    panel.classList.add('active');
    return true;
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.tab-panels[data-tab-key]').forEach(panelWrap => {
      const key = panelWrap.dataset.tabKey;
      const saved = localStorage.getItem(storeKey(key));
      if (saved) activate(key, saved);
    });
  });

  document.addEventListener('click', function (e) {
    const btn = e.target.closest('[data-tab]');
    if (!btn) return;
    const key = btn.dataset.tabKey;
    if (!key) return;
    if (activate(key, btn.dataset.tab)) {
      localStorage.setItem(storeKey(key), btn.dataset.tab);
    }
  });

  document.addEventListener('submit', function (e) {
    const panel = e.target.closest('.tab-panel');
    if (!panel) return;
    const panelWrap = panel.closest('.tab-panels[data-tab-key]');
    if (panelWrap) {
      localStorage.setItem(storeKey(panelWrap.dataset.tabKey), panel.id);
    }
  });
})();

function toggleNotifs() {
  const dd = document.getElementById('notif-dropdown');
  if (!dd) return;
  dd.classList.toggle('hidden');
}
document.addEventListener('click', (e) => {
  const dd = document.getElementById('notif-dropdown');
  const bell = document.querySelector('.notif-bell');
  if (!dd || dd.classList.contains('hidden')) return;
  if (!dd.contains(e.target) && !bell.contains(e.target)) dd.classList.add('hidden');
});
