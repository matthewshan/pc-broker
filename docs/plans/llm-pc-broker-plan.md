# LLM PC Broker Plan

## Goal

Create an internal-only service that can:

- Wake a gaming PC on the local network
- Check whether the PC and local LLM runtime are available
- Provide a stable API for internal applications
- Provide a lightweight UI for:
  - chatting with the local LLM
  - checking PC / Ollama status
  - manually waking or shutting down the PC
- Integrate cleanly with the existing k3s homelab GitOps setup

This service will only be exposed internally through the internal gateway and Twingate.

---

## High-Level Architecture

### Components

#### 1. Gaming PC
Runs:
- Ollama
- optional local shutdown agent
- optional startup task(s) for model/runtime readiness

Responsibilities:
- Host GPU-backed local LLM inference
- Expose Ollama API on the local network
- Optionally accept authenticated shutdown or restart commands

#### 2. Broker Service
Runs in Kubernetes.

Responsibilities:
- Send Wake-on-LAN packets to the gaming PC
- Check whether the PC is reachable
- Check whether Ollama is healthy
- Expose a stable API for applications
- Optionally proxy requests to Ollama
- Expose a minimal admin/status UI

#### 3. Chat / User UI
Can be implemented in one of two ways:

##### Option A: Integrated into broker service
- simplest deployment
- one repo
- one service
- best for MVP

##### Option B: Open WebUI plus broker service
- Open WebUI provides the richer chat UX
- broker service provides power/state/control API
- better end-user chat experience
- more moving parts

Initial recommendation:
- Start with **broker service + lightweight built-in UI**
- Add Open WebUI later if richer chat features are needed

---

## Recommended Repository Strategy

### Preferred new repo name
`llm-pc-broker`

Reasons:
- clearly describes the core responsibility
- still works if the repo contains both API and lightweight UI
- avoids overcommitting to a large frontend architecture too early

### Alternative names
- `gpu-llm-broker`
- `homelab-llm-broker`
- `llm-power-gateway`
- `llm-pc-control-plane`

### Recommendation
Use **`llm-pc-broker`** unless the UI is expected to become a full standalone application.

---

## Scope

## In Scope

- Internal-only API
- Internal-only UI
- Wake-on-LAN support
- PC reachability checks
- Ollama readiness checks
- LLM request proxying
- Model listing
- Basic status dashboard
- Manual power controls
- Kubernetes deployment manifests
- GitOps-friendly configuration
- Authentication appropriate for internal access

## Out of Scope for MVP

- Public internet exposure
- Multi-user tenancy
- Billing / quotas
- Complex RBAC
- GPU scheduling inside Kubernetes
- Full observability stack beyond basic logs/health
- Advanced chat product features

---

## Primary Use Cases

### 1. Manual usage from browser
A user opens the internal UI and:
- sees whether the gaming PC is online
- wakes the PC if needed
- verifies Ollama readiness
- chats with the local model

### 2. Programmatic usage from internal applications
An internal app calls the broker API to:
- check availability
- wake the machine if necessary
- send chat or generation requests
- receive responses through a stable endpoint

### 3. Operational visibility
An operator can:
- check current state
- view recent events
- manually power on/off the PC
- verify model availability

---

## Proposed MVP Design

## API Endpoints

### Control / Status

#### `GET /api/status`
Returns a combined system status.

Example response:
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

#### `POST /api/power/on`
Sends Wake-on-LAN packet and returns accepted status.

#### `POST /api/power/off`
Requests graceful shutdown through a local authenticated agent on the PC.

#### `POST /api/power/restart`
Optional for later phase.

### LLM

#### `GET /api/llm/health`
Returns current LLM backend readiness.

#### `GET /api/llm/models`
Returns available Ollama models.

#### `POST /api/llm/chat`
Accepts chat-style requests and proxies them to Ollama.

#### `POST /api/llm/generate`
Optional endpoint if raw generate mode is wanted.

#### `POST /api/llm/embeddings`
Optional if embedding support is needed for internal apps.

### Events / Diagnostics

#### `GET /api/events`
Returns recent operational events such as:
- wake request sent
- PC became reachable
- Ollama became healthy
- shutdown requested
- timeout waiting for readiness

---

## Request Handling Behavior

### Wake-and-wait flow
When an LLM request arrives:

1. Check whether Ollama is reachable
2. If yes, proxy immediately
3. If not:
   - check whether the PC is reachable
   - if not reachable, send Wake-on-LAN
   - poll for host reachability
   - poll for Ollama readiness
4. If Ollama becomes ready within timeout:
   - proxy the request
5. If not:
   - return a structured timeout / warming-up response

### Suggested timeout strategy
- host reachability timeout: 2 to 5 minutes
- Ollama readiness timeout: 3 to 10 minutes
- configurable via environment variables

---

## UI Plan

## MVP UI Features

### Status dashboard
Display:
- overall state (`offline`, `waking`, `starting`, `ready`, `error`)
- PC reachability
- Ollama availability
- last wake request time
- available models
- recent events

### Power controls
Buttons:
- Wake PC
- Shut down PC
- Refresh status

### Chat panel
Simple chat interface:
- model selector
- prompt input
- response output
- loading / waking / ready status indicators

### Diagnostics section
Display:
- PC IP / hostname
- Ollama endpoint
- broker version
- last successful health check
- configured timeout values

## UI Implementation Recommendation

For MVP:
- serve a simple web UI directly from the broker service
- use server-rendered templates or a minimal SPA
- keep operational complexity low

Recommended choices:
- FastAPI + Jinja templates
- or FastAPI backend + very small frontend bundle
- avoid a separate frontend repo initially

---

## Security Plan

## Exposure model
Internal-only access via:
- internal gateway
- Twingate

No public exposure.

## Authentication / authorization
At minimum:
- require authentication at gateway layer
- require broker API token for programmatic access
- restrict sensitive actions such as shutdown

## Network rules
- only broker service should need to reach the gaming PC's Ollama API
- avoid exposing Ollama directly through shared ingress if possible
- prefer stable internal DNS / service naming

## Secrets
Store as Kubernetes secrets:
- gaming PC MAC address
- gaming PC IP / hostname
- Ollama endpoint
- API token(s)
- optional shutdown-agent shared secret

---

## Gaming PC Requirements

## BIOS / OS / Network
- Wake-on-LAN enabled in BIOS/UEFI
- Wake-on-LAN enabled in operating system / NIC settings
- static DHCP lease or reserved IP strongly recommended

## Ollama
- installed and configured to start reliably
- reachable from the cluster network
- model(s) pre-pulled to reduce first-use delays

## Optional shutdown agent
Needed only if graceful remote shutdown is desired.

Possible responsibilities:
- authenticate inbound broker request
- invoke OS shutdown command
- optionally restart Ollama service

---

## Kubernetes / GitOps Plan

## New application
Create a new application for the broker stack.

Suggested structure in homelab repo:
- app manifests
- deployment
- service
- ingress / gateway route
- secret references
- config map
- optional network policy

### Example logical structure
- `kubernetes/apps/llm-pc-broker/base/...`
- `kubernetes/apps/llm-pc-broker/overlays/home/...`

Adjust pathing to match the conventions already used in this repo.

## Configuration
Use environment variables for:
- PC MAC address
- broadcast IP
- PC host/IP
- Ollama base URL
- wake timeout
- readiness timeout
- API auth token
- polling interval

## Health checks
Broker service should expose:
- `/healthz`
- `/readyz`

---

## Suggested Technology Choices

## Broker service
Recommended:
- Python + FastAPI

Reasons:
- quick to implement
- easy HTTP proxying
- easy background polling / orchestration
- simple templated UI support if desired

## UI
Recommended MVP:
- lightweight built-in UI in same service

Future option:
- adopt Open WebUI if richer human chat workflows are needed

## Persistence
For MVP:
- none required
- recent events can be in-memory or log-based

Later:
- SQLite or lightweight store if event history becomes useful

---

## Operational States

Use a simple state model:

- `offline`
- `waking`
- `host_up`
- `ollama_starting`
- `ready`
- `timeout`
- `error`

These states should appear consistently in:
- API responses
- UI dashboard
- logs

---

## Logging and Observability

## Minimum logging
Log:
- wake requests
- readiness polling transitions
- proxy requests
- shutdown requests
- errors / timeouts

## Nice later additions
- Prometheus metrics
- request counters
- wake duration histogram
- model usage metrics

Not required for MVP.

---

## Failure Modes to Design For

- Wake-on-LAN packet sent but PC never wakes
- PC responds to ping but Ollama never becomes ready
- Ollama reachable but model load is slow
- PC shutdown agent unavailable
- multiple simultaneous wake requests
- stale DNS or changed IP
- gateway-authenticated user but missing broker API token

Mitigations:
- clear timeout responses
- idempotent wake behavior
- request coalescing for concurrent wake attempts
- explicit status states
- strong logging

---

## Future Enhancements

### Phase 2
- idle auto-shutdown after inactivity window
- request queue while waking
- richer chat UX
- model warmup endpoint
- restart Ollama action
- persistent event history

### Phase 3
- multiple GPU host support
- routing by model
- fallback to CPU-hosted small model
- webhooks / notifications when host becomes ready

---

## Open Questions

- Will the UI remain a lightweight admin + chat surface, or become a richer standalone app?
- Is remote shutdown actually required for MVP?
- Should the broker proxy all Ollama traffic, or only manage readiness and let clients connect directly after wake?
- Is Open WebUI desired from the start, or only after MVP validation?
- Should model loading/warmup be explicit or automatic on first request?

---

## Recommended Build Order

### Phase 1: Validate infrastructure
1. Enable and test Wake-on-LAN
2. Confirm cluster-to-PC network reachability
3. Confirm Ollama is reachable from the cluster
4. Decide whether shutdown support is needed now

### Phase 2: Build broker API
1. Implement `/api/status`
2. Implement `/api/power/on`
3. Implement `/api/llm/health`
4. Implement `/api/llm/models`
5. Implement `/api/llm/chat` proxy with wake-and-wait behavior

### Phase 3: Add UI
1. Create status dashboard
2. Add wake / shutdown actions
3. Add minimal chat panel
4. Add recent events display

### Phase 4: GitOps integration
1. Containerize broker
2. Add manifests to homelab repo
3. Add secrets/config
4. Expose via internal gateway
5. Validate Twingate access

### Phase 5: Hardening
1. Add auth controls
2. Improve logging
3. Add request coalescing
4. Add timeout tuning
5. Optionally add idle shutdown

---

## Final Recommendation

Build a single new repo named **`llm-pc-broker`** containing:
- broker API
- lightweight built-in UI
- Ollama proxy logic
- power control logic

Then deploy it from the homelab repo via GitOps.

This keeps the MVP small, understandable, and easy to evolve. If the UI grows significantly later, it can be split into a separate frontend without invalidating the overall architecture.
