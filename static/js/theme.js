// Dark / light mode toggle. The initial theme is already applied by the
// inline snippet in base.html's <head> (before first paint, to avoid a
// flash of the wrong theme); this file only handles the toggle click and
// tells anything else on the page (e.g. charts) that the theme changed.
(function () {
  var KEY = 'lms-theme';

  function currentTheme() {
    return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
  }

  window.toggleTheme = function () {
    var next = currentTheme() === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem(KEY, next); } catch (e) { /* ignore */ }
    document.dispatchEvent(new CustomEvent('themechange', { detail: { theme: next } }));
  };
})();
