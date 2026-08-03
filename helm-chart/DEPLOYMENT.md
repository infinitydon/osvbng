# Complete OSVBNG platform deployment

This is the authoritative installation, validation, upgrade, and removal
runbook for the Kubernetes OSVBNG platform. It covers the required BNG release
and every optional release in this repository.

## Deployment inventory

| Layer | Helm release | Namespace | Required | Purpose |
|---|---|---|---|---|
| BNG | `osvbng` | `osvbng-ha` | Yes | OSVBNG dataplane, CGNAT, HA and optional AAA |
| ISP edge | `osvbng-frr-isp` | `osvbng-ha` | HA routed profile | Dual FRR routers and eBGP |
| CPE lab | `osvbng-cpe-lab` | `osvbng-ha` | No | BNG Blaster, namespaces, cpe-labs and GenieACS |
| AIOps | `osvbng-aiops` | `osvbng-aiops` | No | Agentgateway, ToolHive, MCP, Open WebUI and Ollama Cloud |

The recommended full lab is dual-node BNG HA, dual FRR, optional
FreeRADIUS/PostgreSQL, the 20-session CPE lab, and the AIOps stack. Install in
the order used below.

## Tested topology and versions

| Component | Version/profile |
|---|---|
| Kubernetes | MicroK8s 1.36.2 |
| OSVBNG | 0.16.0 |
| SR-IOV Network Device Plugin | 3.11.0 |
| FRR | 10.7.0 |
| FreeRADIUS | 3.2.7 |
| PostgreSQL | 17.10 |
| BNG Blaster | 0.9.37 |
| cpe-labs | 0.2.1 |
| GenieACS | 1.2.16 |
| Agentgateway | 1.4.0 |
| ToolHive Operator | 0.40.1 |
| Open WebUI | 0.11.0, chart 15.2.0 |

The default routed lab uses these networks:

| Purpose | Addressing |
|---|---|
| BNG core | `172.31.255.2/29`, `172.31.255.3/29` |
| FRR core | `172.31.255.4/29`, `172.31.255.5/29` |
| FRR uplink | `192.168.88.250/24`, `192.168.88.251/24` |
| MikroTik | `192.168.88.1/24` |
| GenieACS management | `192.168.88.252/24` |
| Subscriber pool | `10.255.0.0/24` |
| CGNAT pool | `100.64.100.0/24` |

ASNs are OSVBNG `65010`, FRR `65020`, and MikroTik `65030`.

## 1. Prepare the client

Clone the required branch and select the cluster:

```shell
git clone https://github.com/infinitydon/osvbng.git
cd osvbng
git checkout k8s-implementation
export KUBECONFIG=/path/to/kubeconfig
kubectl cluster-info
helm version
```

Create the namespaces before creating their Secrets:

```shell
kubectl create namespace osvbng-ha --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace osvbng-aiops --dry-run=client -o yaml | kubectl apply -f -
```

## 2. Prepare the workers

The BNG and FRR workloads select workers through this common label and use
required anti-affinity to spread HA members. They do not use `nodeName`.

```shell
kubectl label node ebpf-bng-node-01 \
  osvbng.infinitydon.com/bng-frr-ha=true --overwrite
kubectl label node ebpf-bng-node-02 \
  osvbng.infinitydon.com/bng-frr-ha=true --overwrite
```

Each BNG worker needs:

- two QEMU VirtIO `1af4:1000` NICs bound to `vfio-pci`;
- a functional IOMMU (no-IOMMU is acceptable only for a lab);
- at least 2 GiB of allocatable 1 GiB hugepages;
- `/dev/vfio`, Multus, and the host-device and macvlan CNI plugins;
- the kernel-bound parent interfaces used by FRR and the CPE lab.

Use the consistent VirtIO naming described in
[`frr-isp/NODE-NETPLAN.md`](frr-isp/NODE-NETPLAN.md). In the tested profile,
FRR uses `enp8s19` and the subscriber lab uses `enp8s21`.

Validate every eligible worker:

```shell
ip -br link show
find /sys/kernel/mm/hugepages -name nr_hugepages -exec sh -c 'echo $1: $(cat $1)' _ {} \;
lspci -nnk -d 1af4:1000
ls -l /dev/vfio
```

### Install the VFIO device plugin

This is a standalone prerequisite, deliberately ignored by Helm. Its selector
matches vendor `1af4`, device `1000`, and driver `vfio-pci`; PCI addresses are
not hard-coded.

```shell
kubectl apply -k ./helm-chart/bng/sriov-device-plugin
kubectl rollout status -n kube-system \
  daemonset/osvbng-sriov-device-plugin --timeout=5m
kubectl get nodes \
  -o custom-columns='NAME:.metadata.name,VFIO:.status.allocatable.qemu-virtio-dpdk\.dev/osvbng_vfio'
```

Expected: each BNG worker advertises at least `2` devices under
`qemu-virtio-dpdk.dev/osvbng_vfio`.

## 3. Create BNG and registry Secrets

If GHCR packages are private, create the pull Secret in every namespace that
uses them:

```shell
kubectl create secret docker-registry ghcr-pull -n osvbng-ha \
  --docker-server=ghcr.io --docker-username="$GITHUB_USER" \
  --docker-password="$GITHUB_TOKEN"

kubectl create secret docker-registry ghcr-pull -n osvbng-aiops \
  --docker-server=ghcr.io --docker-username="$GITHUB_USER" \
  --docker-password="$GITHUB_TOKEN"
```

### Optional RADIUS/PostgreSQL Secret

Skip this subsection when `radius.enabled=false`. Generate a database password
and create one Secret containing both required keys:

```shell
RADIUS_SHARED_SECRET='replace-with-a-long-random-secret'
POSTGRES_PASSWORD='replace-with-a-long-random-password'

kubectl create secret generic osvbng-radius -n osvbng-ha \
  --from-literal=shared-secret="$RADIUS_SHARED_SECRET" \
  --from-literal=postgres-password="$POSTGRES_PASSWORD"
```

Do not commit either value to a values file.

## 4. Configure the upstream MikroTik (routed HA profile)

Skip this section for standalone direct-L2 operation. Apply the RouterOS
7.23.1 commands in [`frr-isp/MIKROTIK.md`](frr-isp/MIKROTIK.md). They:

- establish eBGP with `192.168.88.250` and `.251`;
- accept only `100.64.100.0/24` from FRR;
- advertise only the installed default route;
- allow TCP/179 from the FRRs; and
- masquerade the lab CGNAT prefix toward the WAN.

RouterOS saves CLI changes automatically. Create the documented export before
changing the router.

## 5. Deploy the BNG

Lint both supported profiles first:

```shell
helm lint ./helm-chart/bng
helm template osvbng ./helm-chart/bng -n osvbng-ha \
  -f ./helm-chart/bng/examples/standalone-values.yaml >/dev/null
helm template osvbng ./helm-chart/bng -n osvbng-ha \
  -f ./helm-chart/frr-isp/bng-values.yaml >/dev/null
```

### Option A: standalone BNG

This uses one BNG with direct-L2 core addressing and no FRR release:

```shell
helm upgrade --install osvbng ./helm-chart/bng \
  -n osvbng-ha \
  -f ./helm-chart/bng/examples/standalone-values.yaml \
  --wait --timeout 10m
```

To enable RADIUS in standalone mode, add:

```shell
-f ./helm-chart/bng/examples/radius-values.yaml
```

and ensure that the `osvbng-radius` Secret exists.

### Option B: routed two-member HA with FRR

The FRR integration values enable two explicit BNG members, eBGP, routed
CGNAT, and the optional RADIUS profile used by this lab:

```shell
helm upgrade --install osvbng ./helm-chart/bng \
  -n osvbng-ha \
  -f ./helm-chart/frr-isp/bng-values.yaml \
  --wait --timeout 10m
```

To run HA without RADIUS, create a private override:

```yaml
radius:
  enabled: false
```

and add `-f no-radius.yaml` after `bng-values.yaml`.

Validate the BNG release:

```shell
kubectl get pods -n osvbng-ha -l app=osvbng -o wide
kubectl get svc -n osvbng-ha
kubectl exec -n osvbng-ha osvbng-0 -c osvbng -- \
  vppctl -s /run/osvbng/cli.sock show interface
kubectl exec -n osvbng-ha osvbng-0 -c osvbng -- \
  vtysh -c 'show bgp ipv4 unicast summary'
```

When RADIUS is enabled:

```shell
kubectl rollout status -n osvbng-ha deploy/osvbng-freeradius --timeout=5m
kubectl rollout status -n osvbng-ha statefulset/osvbng-radius-postgresql --timeout=5m
kubectl logs -n osvbng-ha deploy/osvbng-freeradius --tail=50
```

## 6. Deploy the optional FRR ISP edge

Deploy this release only with the routed BNG profile:

```shell
helm lint ./helm-chart/frr-isp
helm upgrade --install osvbng-frr-isp ./helm-chart/frr-isp \
  -n osvbng-ha --wait --timeout 5m
```

Validate placement, BGP and routes:

```shell
kubectl get pods -n osvbng-ha \
  -l app.kubernetes.io/name=osvbng-frr-isp -o wide
kubectl exec -n osvbng-ha deploy/osvbng-frr-isp-a -c frr -- \
  vtysh -c 'show bgp ipv4 unicast summary' \
        -c 'show ip route 100.64.100.0/24' \
        -c 'show ip route 0.0.0.0/0'
kubectl exec -n osvbng-ha deploy/osvbng-frr-isp-b -c frr -- \
  vtysh -c 'show bgp ipv4 unicast summary'
```

On MikroTik, both FRR sessions should be established and the CGNAT prefix may
have two active ECMP next hops.

## 7. Deploy the optional 20-session CPE/ACS lab

This chart owns the access host-device NIC, so no other BNG Blaster or UE pod
may use `enp8s21` concurrently. It creates:

- 20 BNG Blaster IPoE/QinQ sessions;
- namespaces `cpe1` through `cpe20`;
- cpe-labs TR-069 simulators;
- the namespace-aware `ue-test-api` Service; and
- GenieACS plus a persistent MongoDB PVC.

Review `subscriberLab.nodeSelector`, `accessHostDevice`, the GenieACS address,
and the FRR return next hop in `cpe-lab/values.yaml`, then install:

```shell
helm lint ./helm-chart/cpe-lab
helm upgrade --install osvbng-cpe-lab ./helm-chart/cpe-lab \
  -n osvbng-ha --wait --timeout 10m
```

Validate sessions, traffic, and ACS registration:

```shell
POD=$(kubectl get pod -n osvbng-ha -l app=osvbng-cpe-lab \
  -o jsonpath='{.items[0].metadata.name}')

kubectl exec -n osvbng-ha "$POD" -c ue-api -- \
  curl -sS http://127.0.0.1:8081/health
kubectl exec -n osvbng-ha "$POD" -c cpe-manager -- ip netns list
kubectl exec -n osvbng-ha "$POD" -c cpe-manager -- \
  ip netns exec cpe1 ping -c 3 8.8.8.8
kubectl exec -n osvbng-ha "$POD" -c cpe-manager -- \
  curl -sS http://genieacs:7557/devices
```

Expected health is `capacity: 20`, `socket: true`, and `netns: 20`. The session
API should report `active_count: 20`, Internet probes should have no loss, and
GenieACS should contain 20 devices.

The GenieACS UI is:

```text
http://<node-ip>:30748
```

Only its UI is exposed by NodePort. See [`cpe-lab/README.md`](cpe-lab/README.md)
for the reported TR-069 parameters and ACS routing explanation.

## 8. Deploy the optional MCP/AIOps platform

### Install CRDs

From PowerShell, use the maintained prerequisite installer:

```powershell
$env:KUBECONFIG = 'C:\path\to\kubeconfig'
powershell -ExecutionPolicy Bypass -File `
  .\helm-chart\mcp-aiops\development\install-prerequisites.ps1 `
  -Namespace osvbng-aiops
```

This installs Gateway API 1.6.0, Agentgateway 1.4.0 CRDs, and ToolHive 0.40.1
CRDs. Do this before installing the application chart.

### Create AIOps Secrets

Generate separate random credentials for NOC, admin, and the internal agent:

```shell
NOC_MCP_KEY=$(openssl rand -hex 32)
ADMIN_MCP_KEY=$(openssl rand -hex 32)
AGENT_CLIENT_KEY=$(openssl rand -hex 32)
WEBUI_SECRET_KEY=$(openssl rand -hex 32)

kubectl create secret generic osvbng-mcp-noc-client-key -n osvbng-aiops \
  --from-literal=api-key="$NOC_MCP_KEY"
kubectl create secret generic osvbng-mcp-admin-client-key -n osvbng-aiops \
  --from-literal=api-key="$ADMIN_MCP_KEY"
kubectl create secret generic osvbng-agent-client-key -n osvbng-aiops \
  --from-literal=agent-runtime="$AGENT_CLIENT_KEY"
kubectl create secret generic osvbng-open-webui-secret -n osvbng-aiops \
  --from-literal=WEBUI_SECRET_KEY="$WEBUI_SECRET_KEY"
```

Create the upstream Ollama Cloud credential:

```shell
kubectl create secret generic ollama-cloud-api-key -n osvbng-aiops \
  --from-literal=OLLAMA_CLOUD_API_KEY="$OLLAMA_CLOUD_API_KEY"
```

Create Open WebUI bootstrap accounts using passwords supplied through the
environment:

```shell
kubectl create secret generic osvbng-open-webui-admin -n osvbng-aiops \
  --from-literal=email='admin@osvbng.local' \
  --from-literal=password="$OSVBNG_ADMIN_PASSWORD"
kubectl create secret generic osvbng-open-webui-noc -n osvbng-aiops \
  --from-literal=email='noc@osvbng.local' \
  --from-literal=password="$OSVBNG_NOC_PASSWORD"
```

### Install and validate AIOps

```shell
helm dependency build ./helm-chart/mcp-aiops
helm lint ./helm-chart/mcp-aiops
helm upgrade --install osvbng-aiops ./helm-chart/mcp-aiops \
  -n osvbng-aiops --wait --timeout 15m

kubectl get pods -n osvbng-aiops
kubectl get mcpserver -n osvbng-aiops
kubectl get gateway,httproute,agentgatewaybackend -n osvbng-aiops
kubectl get job -n osvbng-aiops
```

Open WebUI is the only user-facing NodePort:

```text
http://<node-ip>:30081
```

Agentgateway remains ClusterIP. Open WebUI sends inference and MCP traffic
through it. The managed models are:

- `qwen3.5:cloud - OSVBNG NOC Operations`
- `qwen3.5:cloud - OSVBNG Admin Operations`

The NOC model receives the Agentgateway-filtered read-only tool set. The admin
model receives the admin OSVBNG connection and read-only Kubernetes MCP
connection. See [`mcp-aiops/MCP-VALIDATION.md`](mcp-aiops/MCP-VALIDATION.md)
for authenticated tool-list and authorization tests.

For temporary gateway diagnostics only:

```shell
kubectl port-forward -n osvbng-aiops svc/osvbng-mcp-gateway 30080:80
curl -H "Authorization: Bearer $NOC_MCP_KEY" \
  http://127.0.0.1:30080/mcp/noc
```

## 9. Full-platform acceptance checklist

```shell
helm list -A
kubectl get nodes -o wide
kubectl get pods -n osvbng-ha -o wide
kubectl get pods -n osvbng-aiops -o wide
kubectl get pvc -A
```

Confirm all of the following:

- both BNG pods are Ready and distributed across eligible workers;
- one BNG is active and the peer is standby;
- both BNG-to-FRR and both FRR-to-MikroTik BGP sessions are established;
- MikroTik has the CGNAT route and FRRs have a default route;
- RADIUS authentication/accounting is healthy when enabled;
- all 20 CPE sessions are DHCP Bound and namespace traffic reaches Internet;
- GenieACS has 20 recently informed devices;
- Agentgateway, ToolHive MCP servers, Open WebUI, and bootstrap Job are healthy;
- NOC discovery omits protected admin tools; and
- the admin profile can retrieve BNG running configuration and routing state.

## 10. HA testing notes

FRR loss is safe to test by deleting one FRR pod and checking that traffic
continues through the other ECMP path. Wait for the replacement and BGP to
re-establish before testing the second member.

OSVBNG 0.16 hard failure has an upstream limitation: the survivor can remain
`STANDBY_ALONE`. Forced promotion after fencing/deleting the failed active
member restores synchronized state, but must not be automated without a
reliable fencing or witness mechanism. Follow the exact procedure and warnings
in [`frr-isp/README.md`](frr-isp/README.md).

## 11. Upgrade and rollback

Before every upgrade:

```shell
git rev-parse HEAD
helm list -A
helm get values osvbng -n osvbng-ha -o yaml > osvbng-values-backup.yaml
helm get values osvbng-frr-isp -n osvbng-ha -o yaml > frr-values-backup.yaml
helm get values osvbng-cpe-lab -n osvbng-ha -o yaml > cpe-values-backup.yaml
helm get values osvbng-aiops -n osvbng-aiops -o yaml > aiops-values-backup.yaml
```

Run `helm lint`, `helm template`, then `helm upgrade --install` with the same
ordered values files used initially. Roll back a failed release independently:

```shell
helm history RELEASE -n NAMESPACE
helm rollback RELEASE REVISION -n NAMESPACE --wait --timeout 10m
```

Do not roll back CRDs without checking controller compatibility. See
[`mcp-aiops/ROLLBACK.md`](mcp-aiops/ROLLBACK.md) for the AIOps-specific
procedure.

## 12. Removal

Uninstall in reverse dependency order:

```shell
helm uninstall osvbng-aiops -n osvbng-aiops
helm uninstall osvbng-cpe-lab -n osvbng-ha
helm uninstall osvbng-frr-isp -n osvbng-ha
helm uninstall osvbng -n osvbng-ha
```

Helm intentionally leaves PVCs. Delete them only when their data is no longer
needed:

```shell
kubectl get pvc -n osvbng-ha
kubectl get pvc -n osvbng-aiops
kubectl delete pvc -n osvbng-ha data-mongodb-0
kubectl delete pvc -n osvbng-ha data-osvbng-radius-postgresql-0
```

Identify the exact Open WebUI PVC with `kubectl get pvc -n osvbng-aiops` before
deleting it. Removing it deletes users, models, connections, and chat history.

Remove MikroTik objects only with the scoped commands in
[`frr-isp/MIKROTIK.md`](frr-isp/MIKROTIK.md). Remove shared CRDs or the VFIO
device plugin only when no other deployment depends on them.

## Troubleshooting quick reference

| Symptom | First checks |
|---|---|
| BNG Pending | allocatable VFIO resource, hugepages, node label and affinity |
| BNG CrashLoop | allocated BDFs, `/dev/vfio`, VPP and OSVBNG logs |
| BGP Idle | L2 reachability, TCP/179 firewall, ASN and import/export policy |
| CPE pod Pending | `enp8s21` already owned by another host-device pod |
| Sessions not Bound | BNG active state, VLAN range, RADIUS users and logs |
| Internet fails | CGNAT sessions, FRR routes, MikroTik route/NAT and return path |
| GenieACS empty | CWMP reachability to `.252:7547`, CGNAT return route via `.250` |
| MCP unavailable | CRDs, MCPServer status, ToolHive runner and HTTPRoute status |
| Ollama model missing | Ollama Secret, Agentgateway backend and Open WebUI bootstrap Job |

Component-specific diagnostics remain in each chart README. This file defines
the supported overall installation order and cross-chart dependencies.
