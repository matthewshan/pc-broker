# Task: Add llm-pc-broker manifests to k3s-homelab

## Context

The [llm-pc-broker](https://github.com/matthewshan/llm-pc-broker) service needs to be
deployed to the k3s homelab cluster via the existing ArgoCD GitOps setup.

The `applications` ApplicationSet (`applications/applications-appset.yaml`) already
scans `applications/*` for Kustomize directories — adding a new folder is all that is
needed for ArgoCD to pick it up automatically.

The pattern to follow is `applications/html-emailer/` (look at that directory for
style reference).

---

## Goal

Create a new branch, add `applications/llm-pc-broker/` with the files below, then
open a **draft pull request** targeting `main`.

---

## Files to create

### `applications/llm-pc-broker/kustomization.yaml`

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

namespace: llm-pc-broker

resources:
  - ns.yaml
  - configmap.yaml
  - deployment.yaml
  - service.yaml
  - httproute.yaml
```

### `applications/llm-pc-broker/ns.yaml`

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: llm-pc-broker
```

### `applications/llm-pc-broker/configmap.yaml`

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: llm-pc-broker-config
  namespace: llm-pc-broker
data:
  PC_HOST: "192.168.1.100"
  PC_BROADCAST: "192.168.1.255"
  OLLAMA_BASE_URL: "http://192.168.1.100:11434"
  HOST_REACHABILITY_TIMEOUT: "300"
  OLLAMA_READINESS_TIMEOUT: "600"
  POLL_INTERVAL: "5"
```

### `applications/llm-pc-broker/deployment.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: llm-pc-broker
  namespace: llm-pc-broker
  labels:
    app.kubernetes.io/name: llm-pc-broker
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: llm-pc-broker
  strategy:
    type: RollingUpdate
  template:
    metadata:
      labels:
        app.kubernetes.io/name: llm-pc-broker
    spec:
      containers:
        - name: broker
          image: ghcr.io/matthewshan/llm-pc-broker:latest
          ports:
            - name: http
              containerPort: 8000
          envFrom:
            - configMapRef:
                name: llm-pc-broker-config
            - secretRef:
                name: llm-pc-broker-secrets
          livenessProbe:
            httpGet:
              path: /healthz
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 15
          readinessProbe:
            httpGet:
              path: /readyz
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 15
            failureThreshold: 3
          resources:
            requests:
              cpu: "50m"
              memory: "128Mi"
            limits:
              cpu: "500m"
              memory: "256Mi"
```

### `applications/llm-pc-broker/service.yaml`

```yaml
apiVersion: v1
kind: Service
metadata:
  name: llm-pc-broker-service
  namespace: llm-pc-broker
spec:
  ports:
    - port: 8000
      targetPort: 8000
      protocol: TCP
  selector:
    app.kubernetes.io/name: llm-pc-broker
```

### `applications/llm-pc-broker/httproute.yaml`

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: llm-pc-broker-route
  namespace: llm-pc-broker
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: gateway-external
      namespace: gateway
      sectionName: http
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: gateway-external
      namespace: gateway
      sectionName: https
  hostnames:
    - "llm-broker.mattshan.dev"
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - group: ''
          kind: Service
          name: llm-pc-broker-service
          port: 8000
          weight: 1
```

---

## Steps

- [ ] Create branch `feat/add-llm-pc-broker` from `main`
- [ ] Create `applications/llm-pc-broker/kustomization.yaml`
- [ ] Create `applications/llm-pc-broker/ns.yaml`
- [ ] Create `applications/llm-pc-broker/configmap.yaml`
- [ ] Create `applications/llm-pc-broker/deployment.yaml`
- [ ] Create `applications/llm-pc-broker/service.yaml`
- [ ] Create `applications/llm-pc-broker/httproute.yaml`
- [ ] Open a draft PR: `feat: add llm-pc-broker application manifests`

## PR description to use

```
Adds Kustomize manifests for the llm-pc-broker service so ArgoCD's
`applications` ApplicationSet picks it up automatically.

The secret `llm-pc-broker-secrets` must be created out-of-band before ArgoCD
syncs:

    kubectl create secret generic llm-pc-broker-secrets \
      --namespace llm-pc-broker \
      --from-literal=PC_MAC='aa:bb:cc:dd:ee:ff' \
      --from-literal=API_TOKEN='changeme' \
      --from-literal=SHUTDOWN_AGENT_TOKEN='' \
      --from-literal=SHUTDOWN_AGENT_URL=''

Source repo: https://github.com/matthewshan/llm-pc-broker
Image: ghcr.io/matthewshan/llm-pc-broker:latest
```

---

## Notes

- Do **not** commit the secret into Git — it is managed out-of-band.
- The `httproute.yaml` uses `gateway-external` in namespace `gateway` — this
  matches the pattern in `applications/html-emailer/httproute.yaml`.
- No changes to `applications/applications-appset.yaml` are needed; the
  directory path `applications/llm-pc-broker` is automatically discovered.
