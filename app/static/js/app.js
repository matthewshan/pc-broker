/* LLM PC Broker – front-end logic */
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

    // Reload page to pick up full Jinja re-render on next manual refresh
    // For a lightweight SPA we just reload
    const msgEl = document.getElementById('power-msg');
    if (msgEl) msgEl.textContent = `Refreshed at ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    console.error('Refresh failed', err);
  }
}

// ── Chat ─────────────────────────────────────────────────────────────────
const chatMessages = document.getElementById('chat-messages');
const chatInput    = document.getElementById('chat-input');
const chatStatus   = document.getElementById('chat-status');
const modelSelect  = document.getElementById('model-select');

const conversationHistory = [];

function appendBubble(role, text) {
  const div = document.createElement('div');
  div.className = `chat-bubble ${role}`;
  div.textContent = text;
  chatMessages.appendChild(div);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  return div;
}

async function sendChat() {
  const model = modelSelect ? modelSelect.value : '';
  const content = chatInput ? chatInput.value.trim() : '';
  if (!content || !model) return;

  chatInput.value = '';
  appendBubble('user', content);
  conversationHistory.push({ role: 'user', content });

  chatStatus.textContent = 'Sending…';

  try {
    const resp = await fetch('/api/llm/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model, messages: conversationHistory }),
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      chatStatus.textContent = `Error: ${JSON.stringify(err.detail || err)}`;
      return;
    }

    const data = await resp.json();
    const assistantMsg = data?.message?.content ?? JSON.stringify(data);
    appendBubble('assistant', assistantMsg);
    conversationHistory.push({ role: 'assistant', content: assistantMsg });
    chatStatus.textContent = '';
  } catch (err) {
    chatStatus.textContent = `Network error: ${err.message}`;
  }
}

// Allow Ctrl+Enter / Cmd+Enter to send
if (chatInput) {
  chatInput.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      sendChat();
    }
  });
}

// ── Auto-refresh status badge every 15 s ─────────────────────────────────
setInterval(refreshStatus, 15000);
