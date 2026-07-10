# Kubernetes deployment (Helm)

The chart in `deploy/helm/pulseboard/` ports the compose stack 1:1 to a
home-lab cluster: three Deployments (pulseboard, Prometheus, Grafana), three
PVCs, ClusterIP Services only. The privacy posture is unchanged — **nothing
is exposed outside the cluster**; you reach everything with
`kubectl port-forward`, the k8s equivalent of the compose file's
`127.0.0.1` port bindings.

## Install

No public app image is published (this is personal health data — you run
your own build):

```bash
docker build -t pulseboard:0.1.0 .
# push to your registry, or for k3s/kind load it directly, e.g.:
#   docker save pulseboard:0.1.0 | k3s ctr images import -
#   kind load docker-image pulseboard:0.1.0

helm install pulseboard deploy/helm/pulseboard \
  --set grafana.adminPassword='pick-something'
```

Upgrade after changing values or chart files: `helm upgrade pulseboard
deploy/helm/pulseboard`. Remove with `helm uninstall pulseboard` (PVCs are
left behind by design — delete them explicitly to destroy data).

## Access

```bash
kubectl port-forward svc/pulseboard 8000:8000
kubectl port-forward svc/pulseboard-prometheus 9090:9090
kubectl port-forward svc/pulseboard-grafana 3000:3000
```

## Backfill inside the cluster

```bash
POD=$(kubectl get pod -l app.kubernetes.io/component=app -o name | head -1)
kubectl cp export.xml ${POD#pod/}:/tmp/export.xml
kubectl exec ${POD#pod/} -- python -m pulseboard.backfill /tmp/export.xml
```

## Design notes (the CKA-relevant bits)

- **One writer, Recreate strategy.** SQLite plus a ReadWriteOnce PVC means
  exactly one pulseboard pod; `strategy: Recreate` prevents an upgrade from
  briefly running two writers against the same file.
- **Grafana co-scheduling.** The SQLite history panels mount the pulseboard
  PVC read-only. With RWO storage both pods must sit on the same node, so
  the Grafana Deployment carries a required `podAffinity` to the app pod
  (`grafana.coScheduleWithApp`, on by default). On storage with
  ReadWriteMany you can turn it off.
- **Config rollouts.** The Prometheus scrape config and the Grafana
  provisioning live in ConfigMaps; the Deployments annotate the pod template
  with a `checksum/...` of those templates, so `helm upgrade` rolls the pods
  exactly when their config changed.
- **Probes.** pulseboard exposes `/health` (readiness + liveness),
  Prometheus `/-/ready` + `/-/healthy`, Grafana `/api/health`.
- **Datasource URLs are templated.** The chart re-renders the Grafana
  datasources ConfigMap (Prometheus URL = in-cluster Service DNS name); the
  dashboard JSON and alert rules are byte-identical copies of the compose
  provisioning in `deploy/helm/pulseboard/files/` — a unit test
  (`tests/test_helm_chart.py`) fails if the copies drift from `grafana/`.
- **Secrets.** Grafana admin credentials come from a chart-managed Secret,
  or set `grafana.existingSecret` to a pre-created one (keys `admin-user`,
  `admin-password`) and keep passwords out of your values files.
- **Plugin download.** Grafana installs `frser-sqlite-datasource` at startup
  via `GF_INSTALL_PLUGINS`, so the cluster needs egress to grafana.com on
  first start (afterwards it's cached on the Grafana PVC).

## Validation

CI runs `helm lint` and validates the rendered manifests with
`kubeconform -strict` on every push. The chart was developed against
`helm template` output; install it on your cluster and check
`kubectl get pods` → three `1/1 Running` pods, then walk the same
verification as the compose stack (POST → `/metrics` → Prometheus target UP
→ dashboard).
