# pc-broker

An internal-only service that **wakes and shuts down a gaming PC** on the local
network and provides an **AI chat UI backed by Ollama** running on that PC,
controllable from a phone over Twingate. Deployed to a k3s homelab cluster via
ArgoCD GitOps. (Original design doc:
[`docs/plans/llm-pc-broker-plan.md`](docs/plans/llm-pc-broker-plan.md).)

---

## Overview

```
Phone / browser (on Twingate)
        │  https://pc.mattshan.dev  → gateway-internal (192.168.1.194)
        ▼
  pc-broker (k8s, hostNetwork)
  ├─ GET  /api/status      ← PC reachability + Ollama readiness + idle state
  ├─ POST /api/power/on    ← Wake-on-LAN magic packet (UDP broadcast)
  ├─ POST /api/power/off   ← graceful shutdown via the agent on the PC
  ├─ POST /api/llm/chat    ← streaming chat proxy to Ollama on the PC
  ├─ POST /api/chat        ← Ollama-compatible alias (LiteLLM & co. point here)
  ├─ GET  /api/events      ← recent operational events
  └─ GET  /                ← built-in status + power + chat UI
        │
        ▼
  Gaming PC (LAN)
  ├─ shutdown agent :8001  (agent/ — power-off + /activity for idle shutdown)
  └─ Ollama :11434         (headless via agent/install-ollama.ps1)
```

Power-*on* is a Wake-on-LAN broadcast; because a pod on the Cilium overlay can't
broadcast onto the LAN, the Deployment runs with `hostNetwork: true`. Power-*off*
is delegated to a tiny [shutdown agent](agent/README.md) running on the PC. When
idle auto-shutdown is enabled, the broker also powers the PC off after sustained
inactivity — but never while someone is using it (see `IDLE_*` below; every
ambiguous signal counts as "in use").

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
| `PC_REACHABILITY_PORT` | `8001` | TCP port probed to decide if the PC is up (the agent's port) |
| `HOST_REACHABILITY_TIMEOUT` | `300` | Seconds to wait for the PC to come up |
| `POLL_INTERVAL` | `5` | Seconds between reachability polls |
| `API_TOKEN` | `""` | If set, `/api/power/off` requires `Authorization: Bearer <token>` |
| `SHUTDOWN_AGENT_URL` | `""` | URL of the shutdown agent on the PC |
| `SHUTDOWN_AGENT_TOKEN` | `""` | Bearer token the broker sends to the agent |
| `OLLAMA_URL` | `""` | Ollama base URL; derived as `http://{PC_HOST}:{OLLAMA_PORT}` when empty |
| `OLLAMA_PORT` | `11434` | Ollama port used when deriving the URL |
| `OLLAMA_HEALTH_TIMEOUT` | `3.0` | Seconds per Ollama health probe |
| `IDLE_SHUTDOWN_ENABLED` | `false` | Auto-shutdown the PC when both chat and the local user are idle |
| `IDLE_SHUTDOWN_MINUTES` | `30` | Minimum minutes since last LLM activity |
| `IDLE_USER_THRESHOLD_MINUTES` | `20` | Minimum minutes since last local user input |
| `IDLE_POST_WAKE_GRACE_MINUTES` | `15` | Never auto-shutdown within this window after a wake |
| `IDLE_ACTIVITY_POLL_INTERVAL` | `60` | Seconds between agent `/activity` polls |
| `IDLE_GPU_UTIL_THRESHOLD` | `15` | GPU % above which the PC counts as in use (`0` disables) |
| `IDLE_CONSECUTIVE_CHECKS` | `2` | Idle verdicts in a row required before shutdown |

In the homelab deployment these are set on the Deployment in the `k3s-homelab`
repo (`applications/pc-broker/`) — non-secret values as plain env, secrets via
ESO/Infisical. Enable idle shutdown there only after a dry-run pass (agent
installed with `-DryRun`).

---

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/status` | `state`, `pc.*`, `ollama.*`, `idle.*`, `last_wake_request` |
| `POST` | `/api/power/on` | Send Wake-on-LAN. `202`. Idempotent. |
| `POST` | `/api/power/off` | Graceful shutdown via the agent. `202`, or `503` if unconfigured. |
| `GET` | `/api/llm/health` | Live Ollama reachability + broker state |
| `GET` | `/api/llm/models` | Available Ollama models (`503` unless `ready`) |
| `POST` | `/api/llm/chat` | Streaming NDJSON chat proxy to Ollama (`503` unless `ready`) |
| `POST` | `/api/chat` | Ollama-compatible alias of the chat proxy — passes `tools`/`format` through and honors `stream: false`, so `OLLAMA_API_BASE` can point at the broker (`503` unless `ready`) |
| `POST` | `/api/idle/keep_awake` | `{"enabled": bool}` — pause/resume idle auto-shutdown |
| `GET` | `/api/events?limit=50` | Recent operational events |
| `GET` | `/healthz` | Liveness |
| `GET` | `/readyz` | Readiness (process up; no PC dependency) |

States: `offline → waking → ollama_starting → ready` (`timeout` / `error` on failure).

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
- `/api/llm/*` and `/api/idle/keep_awake` are intentionally unauthenticated
  (same trust model as `/api/power/on`): a network-local actor can wake the PC,
  run chats, or pin it awake, but cannot force a shutdown — the idle logic
  requires real inactivity reported by the agent, and fails toward "keep on".
  Chat `options` are whitelisted/capped and request bodies limited to 1 MB.
- Ollama itself has no auth; its firewall rule is scoped to the broker/k3s
  host only (`install-ollama.ps1 -AllowFrom`), not the whole LAN.
- Secrets (`PC_MAC`, `SHUTDOWN_AGENT_TOKEN`, `API_TOKEN`) live in a k8s Secret,
  created out-of-band, not in Git.
