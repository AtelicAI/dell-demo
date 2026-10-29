// Sign-in page: POST /api/login sets an HttpOnly session cookie; then go back to the page that asked.
(function () {
  const $ = (id) => document.getElementById(id);
  // Only same-site paths are followed after sign-in (never an absolute URL from the query string).
  const next = (() => {
    const n = new URLSearchParams(location.search).get('next') || '/';
    return n.startsWith('/') && !n.startsWith('//') && !n.startsWith('/login') ? n : '/';
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
