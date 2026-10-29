// Sign-in page: POST /api/login sets an HttpOnly session cookie; then go back to the page that asked.
(function () {
  const $ = (id) => document.getElementById(id);
  // Only same-origin pages are followed after sign-in; the value is resolved by the browser's own URL parser.
  const next = (() => {
    try {
      const u = new URL(new URLSearchParams(location.search).get('next') || '/', location.origin);
      // Navigate to the full checked URL so it is never re-parsed as a relative (e.g. protocol-relative) reference.
      return u.origin === location.origin && !u.pathname.startsWith('/login') ? u.href : '/';
    } catch (e) { return '/'; }
  })();
  $('f').addEventListener('submit', async (e) => {
    e.preventDefault();
    $('go').disabled = true;
    $('err').textContent = '';
    try {
      const r = await fetch('/api/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin',
        body: JSON.stringify({ username: $('u').value.trim(), password: $('p').value }),
      });
      if (!r.ok) throw new Error(r.status === 401 ? 'Wrong username or password.' : `Sign-in failed (${r.status}).`);
      location.replace(next);
    } catch (err) {
      $('err').textContent = err.message;
      $('p').value = '';
      $('p').focus();
      $('go').disabled = false;
    }
  });
})();
