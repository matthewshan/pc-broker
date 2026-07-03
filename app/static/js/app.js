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
      if (action === 'on') refreshStatus();
    } else {
      msgEl.textContent = `Error: ${data.detail || resp.statusText}`;
    }
  } catch (err) {
    msgEl.textContent = `Network error: ${err.message}`;
  }
}

// ── Status / Chat gating ─────────────────────────────────────────────────
let currentState = null;
let modelsLoaded = false;

const STATE_LABELS = {
  offline: 'PC is offline.',
  waking: 'Waking the PC… this can take a minute.',
  timeout: 'Wake attempt timed out.',
  error: 'Broker error — check events.',
  host_up: 'PC is up, waiting for Ollama…',
  ollama_starting: 'PC is up, Ollama is starting…',
};

function updateChatUI(state) {
  const offlineEl = document.getElementById('chat-offline');
  const offlineText = document.getElementById('chat-offline-text');
  const wakeBtn = document.getElementById('chat-wake-btn');
  const input = document.getElementById('chat-input');
  const send = document.getElementById('chat-send');
  const select = document.getElementById('model-select');
  if (!offlineEl) return;

  const ready = state === 'ready';
  input.disabled = !ready;
  send.disabled = !ready;
  select.disabled = !ready;
  offlineEl.hidden = ready;
  if (!ready) {
    offlineText.textContent = STATE_LABELS[state] || `State: ${state}`;
    // Only offer the wake button when a wake would actually help.
    wakeBtn.hidden = !(state === 'offline' || state === 'timeout');
  }
}

async function loadModels() {
  try {
    const resp = await fetch('/api/llm/models');
    if (!resp.ok) return;
    const data = await resp.json();
    const select = document.getElementById('model-select');
    select.innerHTML = '';
    for (const m of data.models) {
      const opt = document.createElement('option');
      opt.value = m.name;
      opt.textContent = m.name;
      select.appendChild(opt);
    }
    const preferred = data.models.find((m) => m.name.startsWith('qwen3'));
    if (preferred) select.value = preferred.name;
    modelsLoaded = data.models.length > 0;
  } catch (err) {
    console.error('Model load failed', err);
  }
}

async function refreshStatus() {
  try {
    const resp = await fetch('/api/status');
    const data = await resp.json();

    const badge = document.querySelector('.state-badge');
    if (badge) {
      badge.className = `state-badge state-${data.state}`;
      badge.textContent = data.state.toUpperCase();
    }

    const ollamaStatus = document.getElementById('ollama-status');
    if (ollamaStatus) {
      const dot = ollamaStatus.querySelector('.dot');
      const text = ollamaStatus.querySelector('.ollama-text');
      dot.className = `dot ${data.ollama.reachable ? 'dot-green' : 'dot-red'}`;
      text.textContent = data.ollama.reachable ? 'Ready' : 'Unavailable';
    }

    if (data.state !== currentState) {
      currentState = data.state;
      updateChatUI(data.state);
    }
    if (data.state === 'ready' && !modelsLoaded) await loadModels();

    const msgEl = document.getElementById('power-msg');
    if (msgEl) msgEl.textContent = `Refreshed at ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    console.error('Refresh failed', err);
  }
}

// ── Chat ─────────────────────────────────────────────────────────────────
const chatHistory = [];
let currentAbort = null;

function addBubble(role, text) {
  const placeholder = document.getElementById('chat-placeholder');
  if (placeholder) placeholder.remove();
  const messages = document.getElementById('chat-messages');
  const bubble = document.createElement('div');
  bubble.className = `chat-bubble ${role}`;
  bubble.textContent = text;
  messages.appendChild(bubble);
  messages.scrollTop = messages.scrollHeight;
  return bubble;
}

function setStreaming(streaming) {
  document.getElementById('chat-send').hidden = streaming;
  document.getElementById('chat-stop').hidden = !streaming;
  document.getElementById('chat-input').disabled = streaming;
}

async function sendChat() {
  const input = document.getElementById('chat-input');
  const model = document.getElementById('model-select').value;
  const prompt = input.value.trim();
  if (!prompt || !model || currentAbort) return;

  input.value = '';
  chatHistory.push({ role: 'user', content: prompt });
  addBubble('user', prompt);
  const bubble = addBubble('assistant', '…');

  currentAbort = new AbortController();
  setStreaming(true);
  let assistantText = '';
  let sawDone = false;
  let thinking = false;

  try {
    const resp = await fetch('/api/llm/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model, messages: chatHistory }),
      signal: currentAbort.signal,
    });

    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      const detail = data.detail && (data.detail.detail || data.detail);
      bubble.textContent = `⚠ ${detail || resp.statusText}`;
      chatHistory.pop(); // keep history consistent with what the model saw
      if (resp.status === 503) refreshStatus(); // will surface the wake prompt
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop(); // keep the trailing partial line
      for (const line of lines) {
        if (!line.trim()) continue;
        const chunk = JSON.parse(line);
        if (chunk.message) {
          if (chunk.message.thinking && !chunk.message.content) {
            thinking = true;
            bubble.textContent = '🤔 thinking…';
          }
          if (chunk.message.content) {
            if (thinking) { thinking = false; assistantText = ''; }
            assistantText += chunk.message.content;
            bubble.textContent = assistantText;
            bubble.parentElement.scrollTop = bubble.parentElement.scrollHeight;
          }
        }
        if (chunk.done) sawDone = true;
        if (chunk.error) {
          bubble.textContent = `⚠ ${chunk.error}`;
          chatHistory.pop();
          return;
        }
      }
    }

    if (!sawDone && !assistantText) {
      bubble.textContent = '⚠ Stream ended unexpectedly.';
      chatHistory.pop();
      return;
    }
    if (!sawDone) assistantText += ' ⚠ (truncated)';
    bubble.textContent = assistantText;
    chatHistory.push({ role: 'assistant', content: assistantText });
  } catch (err) {
    if (err.name === 'AbortError') {
      bubble.textContent = assistantText ? `${assistantText} ⏹` : '⏹ Stopped.';
      if (assistantText) chatHistory.push({ role: 'assistant', content: assistantText });
      else chatHistory.pop();
    } else {
      bubble.textContent = `⚠ ${err.message}`;
      chatHistory.pop();
    }
  } finally {
    currentAbort = null;
    setStreaming(false);
    updateChatUI(currentState);
  }
}

function initChat() {
  const send = document.getElementById('chat-send');
  const stop = document.getElementById('chat-stop');
  const input = document.getElementById('chat-input');
  if (!send) return;
  send.addEventListener('click', sendChat);
  stop.addEventListener('click', () => currentAbort && currentAbort.abort());
  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && !ev.shiftKey) {
      ev.preventDefault();
      sendChat();
    }
  });
}

// ── Init / polling ───────────────────────────────────────────────────────
initChat();
refreshStatus();
// 15 s baseline; poll faster while a transition (waking/starting) is underway.
setInterval(() => {
  const transitional = currentState === 'waking' || currentState === 'ollama_starting' || currentState === 'host_up';
  if (!transitional) refreshStatus();
}, 15000);
setInterval(() => {
  const transitional = currentState === 'waking' || currentState === 'ollama_starting' || currentState === 'host_up';
  if (transitional) refreshStatus();
}, 5000);
