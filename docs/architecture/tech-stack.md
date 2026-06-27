# Tech Stack

## Broker Service

### Language & Runtime

| Component | Choice | Version | Reason |
|---|---|---|---|
| Language | Python | 3.12 | Fast iteration, rich async/networking ecosystem |
| ASGI server | Uvicorn | 0.32 | Production-grade, standard for FastAPI |

### Framework & Libraries

| Library | Version | Purpose |
|---|---|---|
| [FastAPI](https://fastapi.tiangolo.com/) | 0.115 | HTTP framework — async, OpenAPI docs auto-generated |
| [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) | 2.6 | Type-safe env-var configuration |
| [httpx](https://www.python-httpx.org/) | 0.27 | Async HTTP client — used to proxy Ollama requests |
| [Jinja2](https://jinja.palletsprojects.com/) | 3.1 | Server-side HTML templating for the built-in UI |
| [python-multipart](https://github.com/Kludex/python-multipart) | 0.0.27 | Form data parsing (required by FastAPI) |

### Networking

| Concern | Approach |
|---|---|
| Wake-on-LAN | Raw UDP socket, UDP broadcast on port 9 |
| PC reachability | Async TCP connect (port 22) with configurable timeout |
| Ollama health | HTTP GET `/api/tags` via httpx |
| Ollama proxying | HTTP POST forwarded via httpx with long timeout |

### State Management

In-process state machine (`app/state.py`):

```
offline → waking → host_up → ollama_starting → ready
                                              ↘ timeout / error
```

- Single `BrokerState` singleton
- Background asyncio poll loop (configurable interval)
- In-memory event ring buffer (200 events, `app/events.py`)
- No external state store required for MVP

---

## User Interface

| Component | Choice | Reason |
|---|---|---|
| Rendering | Server-side Jinja2 + static files | Zero build step, zero JS framework, minimal ops complexity |
| Styling | Custom CSS (dark theme) | No dependency on external CDN |
| Interactivity | Vanilla JavaScript | Chat, power actions, 15s auto-refresh of status badge |

---

## Containerisation

| Component | Choice |
|---|---|
| Base image | `python:3.12-slim` |
| Registry | GitHub Container Registry (`ghcr.io/matthewshan/llm-pc-broker`) |
| Architectures | `linux/amd64`, `linux/arm64` |
| Build tool | Docker Buildx via GitHub Actions |

---

## CI/CD

| Workflow | Trigger | Action |
|---|---|---|
| `release.yaml` | Push of `v*.*.*` tag | Build multi-arch image → push to GHCR |
| `gh-pages.yaml` | Merge to `main` (changes in `docs/gh-pages-docs/`) | Deploy static docs to GitHub Pages |

---

## Kubernetes / GitOps

| Component | Choice | Notes |
|---|---|---|
| Cluster | k3s (single-node homelab) | Lightweight Kubernetes distribution |
| GitOps controller | ArgoCD | Deployed in the homelab cluster |
| App delivery | ArgoCD ApplicationSet | Picks up `applications/*/` directories automatically |
| Manifest format | Kustomize | No Helm chart required for this simplicity level |
| Routing | Gateway API (`HTTPRoute`) | Matches homelab convention; replaces Ingress |
| Secret management | `kubectl create secret` (manual) | Secrets are out-of-band; not stored in Git |

---

## Internal Access

| Layer | Tool |
|---|---|
| Internal network routing | Internal gateway (Gateway API) |
| Remote access | Twingate |
| Auth at perimeter | Gateway-level (handled by homelab infrastructure) |
| API-level auth | `API_TOKEN` env var (optional shared secret) |

---

## LLM Backend

| Component | Choice | Notes |
|---|---|---|
| Inference runtime | [Ollama](https://ollama.com/) | Runs on gaming PC with GPU |
| Model hosting | Pre-pulled on gaming PC | Reduces first-request latency |
| Access | HTTP API `:11434` | Broker proxies; clients never reach Ollama directly |

---

## Persistence

| Data | Storage |
|---|---|
| Operational state | In-memory (process lifetime) |
| Event history | In-memory ring buffer (max 200 events) |
| Configuration | Environment variables |
| Secrets | Kubernetes Secrets |

No database is required for the MVP. Persistent event history (SQLite) is a planned Phase 2 addition.

---

## Future Stack Additions (Planned)

| Feature | Technology |
|---|---|
| Persistent event history | SQLite (via `aiosqlite`) |
| Metrics | Prometheus client + `/metrics` endpoint |
| Richer chat UI | [Open WebUI](https://openwebui.com/) as a separate deployment |
| Idle auto-shutdown | Asyncio timer in broker |
| Request queuing while waking | asyncio `Queue` |
