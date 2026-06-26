# Copilot Context: LLM PC Broker

## Project Overview

`llm-pc-broker` is an internal-only Python/FastAPI service that:
- Wakes a gaming PC via Wake-on-LAN
- Checks PC and Ollama reachability
- Proxies LLM requests to the local Ollama instance
- Exposes a lightweight built-in chat + status UI
- Deploys to a k3s homelab cluster via GitOps

## Repository Layout

```
llm-pc-broker/
├── app/                        # Main FastAPI application
│   ├── main.py                 # App factory, lifespan, mounts
│   ├── config.py               # Settings via pydantic-settings
│   ├── state.py                # Global state machine (BrokerState)
│   ├── events.py               # In-memory event log
│   ├── routers/
│   │   ├── status.py           # GET /api/status
│   │   ├── power.py            # POST /api/power/on|off
│   │   ├── llm.py              # GET|POST /api/llm/*
│   │   ├── events.py           # GET /api/events
│   │   └── ui.py               # UI routes (Jinja2 templates)
│   ├── services/
│   │   ├── wol.py              # Wake-on-LAN packet sender
│   │   ├── ping.py             # ICMP/TCP reachability check
│   │   └── ollama.py           # Ollama health + proxy client
│   ├── templates/              # Jinja2 HTML templates
│   │   └── index.html
│   └── static/                 # CSS / JS assets
│       ├── css/style.css
│       └── js/app.js
├── tests/                      # pytest test suite
├── docs/
│   ├── plans/llm-pc-broker-plan.md
│   └── copilot-context.md      # This file
├── kubernetes/
│   └── apps/llm-pc-broker/base/  # Kubernetes manifests
├── Dockerfile
├── requirements.txt
└── .gitignore
```

## Technology Stack

| Layer | Choice | Reason |
|---|---|---|
| Language | Python 3.12 | Fast iteration, rich ecosystem |
| Framework | FastAPI | Async HTTP, OpenAPI docs, easy proxying |
| Config | pydantic-settings | Env-var driven, type-safe |
| Templates | Jinja2 | Built into FastAPI ecosystem |
| HTTP client | httpx | Async, used for Ollama proxy |
| Testing | pytest + httpx AsyncClient | Standard FastAPI testing pattern |
| Container | Docker (python:3.12-slim) | Small image |
| Orchestration | k3s (Kubernetes) | Existing homelab setup |

## Key Abstractions

### BrokerState (`app/state.py`)
A singleton that holds the current operational state and coordinates transitions.

States:
```
offline → waking → host_up → ollama_starting → ready
                                              ↘ timeout / error
```

The state machine is the single source of truth for:
- `GET /api/status` response
- UI dashboard display
- Wake-and-wait logic in LLM proxy

### Settings (`app/config.py`)
All configuration comes from environment variables (or `.env` file):
- `PC_MAC` – MAC address of gaming PC
- `PC_HOST` – IP or hostname of gaming PC
- `PC_BROADCAST` – Broadcast address for WoL
- `OLLAMA_BASE_URL` – e.g. `http://192.168.1.100:11434`
- `HOST_REACHABILITY_TIMEOUT` – seconds to wait for ping (default 300)
- `OLLAMA_READINESS_TIMEOUT` – seconds to wait for Ollama (default 600)
- `POLL_INTERVAL` – seconds between health checks (default 5)
- `API_TOKEN` – shared secret for programmatic API access (optional)
- `SHUTDOWN_AGENT_URL` – URL of optional shutdown agent on the PC
- `SHUTDOWN_AGENT_TOKEN` – auth token for the shutdown agent

### Event Log (`app/events.py`)
In-memory ring buffer (max 200 events) recording:
- wake requests
- state transitions
- proxy requests
- errors and timeouts

## API Contract

### `GET /api/status`
```json
{
  "state": "ready",
  "pc": { "reachable": true, "last_seen": "2026-05-17T12:00:00Z" },
  "ollama": { "reachable": true, "models": ["llama3"] }
}
```

### `POST /api/power/on`
Idempotent. Sends WoL magic packet. Returns `202 Accepted`.

### `POST /api/power/off`
Calls shutdown agent on PC. Returns `202 Accepted` or `503` if agent unavailable.

### `GET /api/llm/health`
Returns `{ "status": "ok" }` when Ollama is reachable.

### `GET /api/llm/models`
Proxies `GET /api/tags` from Ollama.

### `POST /api/llm/chat`
Wake-and-wait then proxy to `POST /api/chat` on Ollama.

### `GET /api/events`
Returns list of recent events (newest first).

### `GET /healthz`
Liveness probe – always 200 if process is alive.

### `GET /readyz`
Readiness probe – 200 when Ollama is reachable, 503 otherwise.

## Wake-and-Wait Flow

```
LLM request arrives
  └─ Ollama reachable? ──yes──► proxy immediately
       └─ no
           └─ PC reachable? ──no──► send WoL
               └─ poll PC reachability (timeout: HOST_REACHABILITY_TIMEOUT)
                   └─ poll Ollama readiness (timeout: OLLAMA_READINESS_TIMEOUT)
                       ├─ ready ──► proxy request
                       └─ timeout ──► 503 + waking-up body
```

## Running Locally

```bash
pip install -r requirements.txt
PC_MAC=aa:bb:cc:dd:ee:ff \
PC_HOST=192.168.1.100 \
OLLAMA_BASE_URL=http://192.168.1.100:11434 \
uvicorn app.main:app --reload
```

Open `http://localhost:8000` for the UI.
API docs available at `http://localhost:8000/docs`.

## Testing

```bash
pip install -r requirements.txt
pytest tests/ -v
```

## Building the Container

```bash
docker build -t llm-pc-broker:latest .
docker run -p 8000:8000 \
  -e PC_MAC=aa:bb:cc:dd:ee:ff \
  -e PC_HOST=192.168.1.100 \
  -e OLLAMA_BASE_URL=http://192.168.1.100:11434 \
  llm-pc-broker:latest
```

## Kubernetes Deployment

Manifests live in `kubernetes/apps/llm-pc-broker/base/`.

Required secrets (create manually or via sealed-secrets):
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: llm-pc-broker-secrets
  namespace: llm-pc-broker
stringData:
  PC_MAC: "aa:bb:cc:dd:ee:ff"
  API_TOKEN: "changeme"
  SHUTDOWN_AGENT_TOKEN: "changeme"
```

Required configmap values in `configmap.yaml`:
- `PC_HOST`
- `PC_BROADCAST`
- `OLLAMA_BASE_URL`
- `HOST_REACHABILITY_TIMEOUT`
- `OLLAMA_READINESS_TIMEOUT`
- `POLL_INTERVAL`

## Security Notes

- Expose only through internal gateway or Twingate
- Set `API_TOKEN` to restrict programmatic access
- The shutdown endpoint is the most sensitive; consider additional controls
- Never expose the Ollama port directly through a public ingress
- Store secrets as Kubernetes Secrets, not in ConfigMaps

## Future Enhancements

- Idle auto-shutdown timer
- Request queuing while waking
- Prometheus metrics (`/metrics`)
- Persistent event history (SQLite)
- Open WebUI integration
- Multi-host GPU routing
