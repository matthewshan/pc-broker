# LLM PC Broker

An internal-only service that wakes a gaming PC on the local network, monitors the [Ollama](https://ollama.com/) LLM runtime, and proxies inference requests through a stable API — with a lightweight built-in chat UI.

Deployed to a k3s homelab cluster via ArgoCD GitOps.

---

## Overview

```
Browser / Internal App
        │
        ▼
  llm-pc-broker (k8s)
  ├─ GET  /api/status          ← combined PC + Ollama status
  ├─ POST /api/power/on        ← Wake-on-LAN
  ├─ POST /api/power/off       ← graceful shutdown via agent
  ├─ GET  /api/llm/models      ← available Ollama models
  ├─ POST /api/llm/chat        ← wake-and-wait, then proxy
  └─ GET  /                    ← built-in chat + status UI
        │
        ▼
  Gaming PC (LAN)
  └─ Ollama :11434
```

The broker implements a **wake-and-wait** flow: when an LLM request arrives and Ollama is offline, it automatically sends a WoL packet, polls for host reachability, then polls for Ollama readiness before proxying the request.

---

## Quick Start

### Prerequisites

- Python 3.12+
- Gaming PC with Wake-on-LAN enabled and Ollama running
- (Optional) a shutdown agent on the PC if you want remote power-off

### Run locally

```bash
pip install -r requirements.txt

PC_MAC=aa:bb:cc:dd:ee:ff \
PC_HOST=192.168.1.100 \
OLLAMA_BASE_URL=http://192.168.1.100:11434 \
uvicorn app.main:app --reload
```

Open http://localhost:8000 for the UI. OpenAPI docs at http://localhost:8000/docs.

### Run with Docker

```bash
docker build -t llm-pc-broker:latest .

docker run -p 8000:8000 \
  -e PC_MAC=aa:bb:cc:dd:ee:ff \
  -e PC_HOST=192.168.1.100 \
  -e OLLAMA_BASE_URL=http://192.168.1.100:11434 \
  llm-pc-broker:latest
```

---

## Configuration

All configuration is via environment variables (or a `.env` file).

| Variable | Default | Description |
|---|---|---|
| `PC_MAC` | `""` | MAC address of the gaming PC (required for WoL) |
| `PC_HOST` | `192.168.1.100` | IP or hostname of the gaming PC |
| `PC_BROADCAST` | `192.168.1.255` | Broadcast address for WoL packet |
| `OLLAMA_BASE_URL` | `http://192.168.1.100:11434` | Ollama API base URL |
| `HOST_REACHABILITY_TIMEOUT` | `300` | Seconds to wait for PC to become reachable |
| `OLLAMA_READINESS_TIMEOUT` | `600` | Seconds to wait for Ollama to become ready |
| `POLL_INTERVAL` | `5` | Seconds between health-check polls |
| `API_TOKEN` | `""` | Shared secret for programmatic access (optional) |
| `SHUTDOWN_AGENT_URL` | `""` | URL of optional shutdown agent on the PC |
| `SHUTDOWN_AGENT_TOKEN` | `""` | Auth token for the shutdown agent |

---

## API Reference

### Status

#### `GET /api/status`

Returns combined system status.

```json
{
  "state": "ready",
  "pc": {
    "reachable": true,
    "last_seen": "2026-05-17T12:00:00Z"
  },
  "ollama": {
    "reachable": true,
    "models": ["llama3", "deepseek-r1"]
  }
}
```

**States:** `offline` · `waking` · `host_up` · `ollama_starting` · `ready` · `timeout` · `error`

---

### Power

#### `POST /api/power/on`
Sends a Wake-on-LAN magic packet. Returns `202 Accepted`. Idempotent.

#### `POST /api/power/off`
Requests a graceful shutdown via the shutdown agent. Returns `202` or `503` if the agent is unconfigured/unreachable.

---

### LLM

#### `GET /api/llm/health`
Returns `{"status": "ok"}` when Ollama is reachable, `503` otherwise.

#### `GET /api/llm/models`
Returns the list of available Ollama models.

#### `POST /api/llm/chat`

Proxies a chat request to Ollama, waking the PC first if needed.

```json
{
  "model": "llama3",
  "messages": [{"role": "user", "content": "Hello!"}],
  "stream": false
}
```

#### `POST /api/llm/generate`
Raw generate proxy to Ollama.

#### `POST /api/llm/embeddings`
Embeddings proxy to Ollama.

---

### Events

#### `GET /api/events?limit=50`
Returns recent operational events (wake requests, state transitions, proxy requests, errors).

---

### Health probes

| Endpoint | Purpose |
|---|---|
| `GET /healthz` | Liveness — always 200 if process is alive |
| `GET /readyz` | Readiness — 200 when Ollama reachable, 503 otherwise |

---

## Operational States

The broker uses a simple state machine shared across API responses, UI, and logs:

```
offline → waking → host_up → ollama_starting → ready
                                              ↘ timeout / error
```

---

## Kubernetes Deployment

Manifests for the k3s homelab live in the [k3s-homelab](https://github.com/matthewshan/k3s-homelab) repo under `applications/llm-pc-broker/`.

The broker is deployed via ArgoCD with the `applications` ApplicationSet. See [`docs/k3s-homelab/`](docs/k3s-homelab/) for the manifest source.

Required secrets (managed out-of-band, e.g. via `kubectl create secret`):

```bash
kubectl create secret generic llm-pc-broker-secrets \
  --namespace llm-pc-broker \
  --from-literal=PC_MAC='aa:bb:cc:dd:ee:ff' \
  --from-literal=API_TOKEN='changeme' \
  --from-literal=SHUTDOWN_AGENT_TOKEN=''
```

---

## Development

### Install dev dependencies

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

### Run tests

```bash
pytest tests/ -v
```

### Project structure

```
app/
├── main.py          # FastAPI app factory + lifespan
├── config.py        # Settings (pydantic-settings)
├── state.py         # BrokerState state machine + background poll
├── events.py        # In-memory event ring buffer
├── routers/         # API route handlers
│   ├── status.py
│   ├── power.py
│   ├── llm.py
│   ├── events.py
│   └── ui.py
├── services/        # External service integrations
│   ├── wol.py       # Wake-on-LAN
│   ├── ping.py      # TCP reachability
│   └── ollama.py    # Ollama health + proxy
├── templates/       # Jinja2 HTML templates
└── static/          # CSS + JS assets
```

---

## Container Image

Images are published to the GitHub Container Registry on every release:

```
ghcr.io/matthewshan/llm-pc-broker:<tag>
ghcr.io/matthewshan/llm-pc-broker:latest
```

---

## Security Notes

- Expose only through an internal gateway or Twingate — never publicly
- Set `API_TOKEN` to restrict programmatic access
- The `/api/power/off` endpoint is the most sensitive; consider gateway-level auth
- Secrets are stored as Kubernetes Secrets, not ConfigMaps

---

## Documentation

- [Architecture & Tech Stack](docs/architecture/tech-stack.md)
- [Project Plan](docs/plans/llm-pc-broker-plan.md)
- [Copilot Context](docs/copilot-context.md)
- [Interactive Docs Site](https://matthewshan.github.io/llm-pc-broker/)
