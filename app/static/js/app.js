/* PC Broker - front-end logic */
'use strict';

// ── Power Actions ────────────────────────────────────────────────────────
async function powerAction(action) {
  const msgEl = document.getElementById('power-msg');
  msgEl.textContent = action === 'on' ? 'Sending Wake-on-LAN…' : 'Requesting shutdown…';
  try {
    const resp = await fetch(`/api/power/${action}`, { method: 'POST' });
    const data = await resp.json();
    if (resp.ok) {
      msgEl.textContent = data.message || 'Done.';
    } else {
      msgEl.textContent = `Error: ${data.detail || resp.statusText}`;
    }
  } catch (err) {
    msgEl.textContent = `Network error: ${err.message}`;
  }
}

// ── Refresh Status ───────────────────────────────────────────────────────
async function refreshStatus() {
  try {
    const resp = await fetch('/api/status');
    const data = await resp.json();

    // Update state badge
    const badge = document.querySelector('.state-badge');
    if (badge) {
      badge.className = `state-badge state-${data.state}`;
      badge.textContent = data.state.toUpperCase();
    }

    const msgEl = document.getElementById('power-msg');
    if (msgEl) msgEl.textContent = `Refreshed at ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    console.error('Refresh failed', err);
  }
}

// ── Auto-refresh status badge every 15 s ─────────────────────────────────
setInterval(refreshStatus, 15000);
