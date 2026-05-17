# llm-pc-broker — k3s-homelab Manifests

This directory contains Kubernetes manifests formatted for the
[k3s-homelab](https://github.com/matthewshan/k3s-homelab) ArgoCD GitOps setup.

## What's here

```
applications/llm-pc-broker/
├── kustomization.yaml   # Kustomize root
├── ns.yaml              # Namespace
├── configmap.yaml       # Non-secret config (PC host, Ollama URL, timeouts)
├── deployment.yaml      # Deployment — references llm-pc-broker-secrets
├── service.yaml         # ClusterIP service
└── httproute.yaml       # Gateway API HTTPRoute (internal hostname)
```

## How to apply to k3s-homelab

1. Copy the `applications/llm-pc-broker/` directory into the
   `applications/` folder of the `k3s-homelab` repo.

2. Update `httproute.yaml` with your actual internal hostname.

3. Update `configmap.yaml` with your actual PC IP, broadcast address, and
   Ollama URL.

4. Create the required secret **manually** (not stored in Git):

   ```bash
   kubectl create secret generic llm-pc-broker-secrets \
     --namespace llm-pc-broker \
     --from-literal=PC_MAC='aa:bb:cc:dd:ee:ff' \
     --from-literal=API_TOKEN='changeme' \
     --from-literal=SHUTDOWN_AGENT_TOKEN='' \
     --from-literal=SHUTDOWN_AGENT_URL=''
   ```

5. Push to `main` — ArgoCD's `applications` ApplicationSet will pick up the
   new directory automatically.

## Differences from the original kubernetes/ manifests

| Original (`kubernetes/`) | k3s-homelab (`docs/k3s-homelab/`) |
|---|---|
| `networking.k8s.io/v1 Ingress` | `gateway.networking.k8s.io/v1 HTTPRoute` |
| `app: llm-pc-broker` labels | `app.kubernetes.io/name: llm-pc-broker` labels |
| Inline `secret.yaml` (placeholder) | Secret managed out-of-band |
| Separate `namespace.yaml` | `ns.yaml` (matches homelab convention) |
