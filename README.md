# pc-broker

An internal-only service that **wakes and shuts down a gaming PC** on the local
network, controllable from a phone over Twingate. Deployed to a k3s homelab
cluster via ArgoCD GitOps.

This is the power-only build. The architecture is deliberately kept so an LLM
runtime (Ollama proxy + chat) can be layered back on later — see
[`docs/plans/llm-pc-broker-plan.md`](docs/plans/llm-pc-broker-plan.md).

---

## Overview

```
Phone / browser (on Twingate)
        │  https://pc.mattshan.dev  → gateway-internal (192.168.1.194)
        ▼
  pc-broker (k8s, hostNetwork)
  ├─ GET  /api/status      ← PC reachability + state
  ├─ POST /api/power/on    ← Wake-on-LAN magic packet (UDP broadcast)
  ├─ POST /api/power/off   ← graceful shutdown via the agent on the PC
  ├─ GET  /api/events      ← recent operational events
  └─ GET  /                ← built-in status + power UI
        │
        ▼
  Gaming PC (LAN)
  └─ shutdown agent :8001  (agent/ — handles power-off)
```

Power-*on* is a Wake-on-LAN broadcast; because a pod on the Cilium overlay can't
broadcast onto the LAN, the Deployment runs with `hostNetwork: true`. Power-*off*
is delegated to a tiny [shutdown agent](agent/README.md) running on the PC.

---

## Run locally

```bash
pip install -r requirements.txt

PC_MAC=aa:bb:cc:dd:ee:ff \
PC_HOST=192.168.1.100 \
PC_BROADCAST=192.168.1.255 \
SHUTDOWN_AGENT_URL=http://192.168.1.100:8001 \
uvicorn app.main:app --reload
```

Open http://localhost:8000 for the UI; OpenAPI docs at http://localhost:8000/docs.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `PC_MAC` | `""` | MAC of the gaming PC (required for WoL) |
| `PC_HOST` | `192.168.1.100` | IP/hostname of the gaming PC |
| `PC_BROADCAST` | `192.168.1.255` | Broadcast address for the WoL packet |
| `HOST_REACHABILITY_TIMEOUT` | `300` | Seconds to wait for the PC to come up |
| `POLL_INTERVAL` | `5` | Seconds between reachability polls |
| `API_TOKEN` | `""` | If set, `/api/power/off` requires `Authorization: Bearer <token>` |
| `SHUTDOWN_AGENT_URL` | `""` | URL of the shutdown agent on the PC |
| `SHUTDOWN_AGENT_TOKEN` | `""` | Bearer token the broker sends to the agent |

---

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/status` | `state`, `pc.reachable`, `pc.last_seen`, `last_wake_request` |
| `POST` | `/api/power/on` | Send Wake-on-LAN. `202`. Idempotent. |
| `POST` | `/api/power/off` | Graceful shutdown via the agent. `202`, or `503` if unconfigured. |
| `GET` | `/api/events?limit=50` | Recent operational events |
| `GET` | `/healthz` | Liveness |
| `GET` | `/readyz` | Readiness (process up; no PC dependency) |

States: `offline → waking → host_up` (`timeout` / `error` on failure).

---

## Components

- **Broker** (`app/`) — FastAPI service in this repo; container published to
  `ghcr.io/matthewshan/pc-broker` on a `v*.*.*` tag.
- **Shutdown agent** (`agent/`) — stdlib HTTP service that runs on the Windows
  PC to perform shutdowns. See [`agent/README.md`](agent/README.md), which also
  lists the one-time **Wake-on-LAN setup** required on the PC.
- **k8s manifests** — live in the `k3s-homelab` repo under
  `applications/pc-broker/` (ArgoCD auto-discovers them).

## Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/ -v
```

## Security

- Exposed only via the internal gateway / Twingate — never publicly.
- `/api/power/off` is the sensitive action; set `API_TOKEN` to require a token.
- Secrets (`PC_MAC`, `SHUTDOWN_AGENT_TOKEN`, `API_TOKEN`) live in a k8s Secret,
  created out-of-band, not in Git.
