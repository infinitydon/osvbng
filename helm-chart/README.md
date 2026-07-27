# osvbng Helm chart

This chart deploys osvbng `v0.16.0` on Kubernetes with two VFIO/DPDK devices
allocated by the SR-IOV Network Device Plugin, native PBA CGNAT, active/standby
HA, BNG Blaster `0.9.37`, and an optional FreeRADIUS/PostgreSQL AAA profile.
The default profile is sized for 100 concurrent IPoE subscribers using QinQ
(S-VLAN 100 and C-VLANs 100-199).

## Tested environment

- Kubernetes 1.36.2
- workers `ebpf-bng-node-01` and `ebpf-bng-node-02`
- two QEMU VirtIO network devices bound to `vfio-pci` per worker
- 6 exclusive CPUs, 6 GiB memory, and 2 x 1 GiB hugepages
- BNG Blaster host devices `enp8s21` and `enp8s22`

The PCI devices are VirtIO NICs bound to `vfio-pci` in no-IOMMU mode. For a
production deployment, use a real IOMMU rather than no-IOMMU mode.

## Prerequisites

The cluster must provide:

1. Two QEMU VirtIO (`1af4:1000`) devices bound to `vfio-pci` on each BNG worker.
2. `/dev/vfio` available on those workers, with VFIO and IOMMU support.
3. At least 2 GiB of allocatable 1 GiB hugepages per BNG pod.
4. Multus and the `host-device` CNI for BNG Blaster.
5. A pull secret when either configured registry package is private.

This deployment intentionally does not use DRA. Install the pinned upstream
SR-IOV Network Device Plugin prerequisite. The selector uses vendor, device,
and driver—not PCI addresses—and advertises the single resource
`qemu-virtio-dpdk.dev/osvbng_vfio`:

```shell
kubectl label node ebpf-bng-node-01 \
  osvbng.infinitydon.com/dpdk-ha=true --overwrite
kubectl label node ebpf-bng-node-02 \
  osvbng.infinitydon.com/dpdk-ha=true --overwrite
kubectl apply -k helm-chart/sriov-device-plugin
kubectl rollout status -n kube-system \
  daemonset/osvbng-sriov-device-plugin
```

The osvbng pod requests quantity `2`. The entrypoint sorts the two allocated
BDFs and assigns the lower BDF to `access` and the higher BDF to `core`.

Create a GHCR pull secret when required:

```shell
kubectl create namespace osvbng
kubectl create secret docker-registry ghcr-pull \
  --namespace osvbng \
  --docker-server ghcr.io \
  --docker-username USER \
  --docker-password TOKEN
```

Create a private values file:

```yaml
osvbng:
  imagePullSecrets:
    - name: ghcr-pull
bngblaster:
  imagePullSecrets:
    - name: ghcr-pull
trafficTest:
  imagePullSecrets:
    - name: ghcr-pull
```

## Install

Review the worker, host devices, and VLANs in `values.yaml` before installation:

```shell
helm lint ./helm-chart
helm upgrade --install osvbng ./helm-chart \
  --namespace osvbng \
  --create-namespace \
  --values private-values.yaml \
  --wait \
  --timeout 10m
```

The chart supports in-place transitions between two modes:

```yaml
# One BNG replica
osvbng:
  standalone:
    coreAddress: 192.168.88.10/24
  ha:
    enabled: false
```

```yaml
# Two BNG replicas
osvbng:
  ha:
    enabled: true
    members:
      - nodeId: bng-a
        coreAddress: 192.168.88.10/24
        priority: 100
        preempt: false
      - nodeId: bng-b
        coreAddress: 192.168.88.11/24
        priority: 90
        preempt: false
```

`standalone.coreAddress` is required only when HA is disabled. HA member
addresses are explicit, independent, and do not need to be consecutive.
Use `examples/standalone-values.yaml` or `examples/ha-values.yaml`.

BNG Blaster runs as a post-install validation Job. It succeeds after all
configured sessions receive DHCP ACKs, then terminates and leaves its logs as
test evidence.

## Verification

```shell
kubectl get pods -n osvbng
kubectl wait -n osvbng --for=condition=complete \
  job/bngblaster-validation --timeout=5m
kubectl logs -n osvbng job/bngblaster-validation
kubectl logs -n osvbng osvbng-0 | grep "Session bound" | wc -l
kubectl exec -n osvbng osvbng-0 -- \
  vppctl -s /run/osvbng/cli.sock show interface
```

The expected default result is 100 DHCP ACKs, 100 bound IPoE sessions, and
VPP interfaces `access`, `access.100`, and `core` in the up state.

## Images

- osvbng uses the official upstream stable `v0.16.0` image.
- No local osvbng source patch is applied. Its LCP interfaces are created in
  the dedicated `dataplane` network namespace.
- BNG Blaster is built from the official `0.9.37` Ubuntu package using the
  reproducible Dockerfile in `images/bngblaster`.
- Runtime images are pinned by digest in `values.yaml`.

## Interactive subscriber test

The chart has an interactive traffic-test mode containing:

- `ue-test`, which runs a single BNG Blaster session with its per-session
  TUN feature plus a netshoot sidecar. The sidecar exposes the DHCP-assigned
  subscriber as Linux interface `bbl1`.
- Direct core egress from osvbng at `192.168.88.10/24` to gateway
  `192.168.88.1`. Subscriber traffic does not traverse Calico or a Kubernetes
  forwarding pod.

The access test interface is exclusive, so traffic-test mode and the
100-session BNG Blaster Job cannot run simultaneously. Helm rejects
that invalid combination. Switch from the scale test to interactive mode:

```shell
helm upgrade osvbng ./helm-chart \
  --namespace osvbng \
  --set bngblaster.enabled=false \
  --set trafficTest.enabled=true \
  --wait
```

Run real subscriber traffic:

```shell
kubectl exec -it -n osvbng deployment/ue-test -c ue -- sh
ip -4 address show bbl1
ping -I bbl1 -c 3 10.255.0.1
ping -I bbl1 -c 3 1.1.1.1
curl --interface bbl1 -I https://example.com
```

Confirm that osvbng, rather than the test edge, created the first translation:

```shell
kubectl exec -n osvbng deployment/ue-test -c ue -- \
  curl -sS "http://osvbng:8080/api/show/cgnat/sessions?inside-ip=10.255.0.2"
kubectl exec -n osvbng deployment/ue-test -c ue -- \
  curl -sS http://osvbng:8080/api/show/cgnat/mappings
```

The single-node profile reserves `192.168.88.10` for the BNG core and
`192.168.88.11-15` for CGNAT. VPP proxy ARP makes the translated
addresses reachable from the directly connected gateway without adding host
routes there. For production, prefer a routed public pool and set
`osvbng.cgnat.proxyArp: false`.

The tested lab egress path is:

```text
UE 10.255.0.2
  -> osvbng PBA CGNAT 192.168.88.11:1024-1535
  -> DPDK core 192.168.88.10/24
  -> gateway 192.168.88.1
  -> site Internet edge
```

Inspect the core, gateway, and BNG translation:

```shell
kubectl exec -n osvbng osvbng-0 -- \
  vppctl -s /run/osvbng/cli.sock show ip neighbors
kubectl exec -n osvbng osvbng-0 -- \
  vppctl -s /run/osvbng/cli.sock ping 192.168.88.1 source core repeat 3
kubectl exec -n osvbng deployment/ue-test -c ue -- \
  curl -sS http://osvbng:8080/api/show/cgnat/pools
kubectl exec -n osvbng deployment/ue-test -c ue -- \
  curl -sS "http://osvbng:8080/api/show/cgnat/sessions?inside-ip=10.255.0.2"
kubectl exec -n osvbng deployment/ue-test -c ue -- \
  curl -sS http://osvbng:8080/api/show/cgnat/mappings
```

The default test subscriber uses S-VLAN 100 and C-VLAN 100. Change
`trafficTest.outerVlan` and `trafficTest.innerVlan` when those identifiers
are already allocated.

## Two-node HA lab

The tested HA allocation is:

- BNG A identity: `osvbng-0`, core `192.168.88.10/24`
- BNG B identity: `osvbng-1`, core `192.168.88.11/24`
- shared PBA CGNAT pool: `192.168.88.12-15`
- virtual MAC: `02:00:5e:00:01:01`

Install one release containing a two-replica StatefulSet:

```shell
kubectl create namespace osvbng-ha
helm upgrade --install osvbng ./helm-chart -n osvbng-ha \
  -f helm-chart/examples/ha-values.yaml --wait --timeout 10m
```

The member list explicitly binds addresses to StatefulSet identity:

```yaml
members:
  - nodeId: bng-a
    coreAddress: 192.168.88.10/24
    priority: 100
    preempt: false
  - nodeId: bng-b
    coreAddress: 192.168.88.11/24
    priority: 90
    preempt: false
```

The addresses do not need to be consecutive. `osvbng-0` always selects member
zero and `osvbng-1` member one, even if the scheduler places them on different
eligible workers after a restart. Required hostname anti-affinity prevents
both replicas from sharing a worker. Stable peer addresses come from the
`osvbng-headless` Service.

The example includes one independent interactive UE Deployment; it is not
replicated with the StatefulSet. Copy `ghcr-pull` into `osvbng-ha` first when
the BNG Blaster image requires authentication.

Capture election, sync, subscriber, and NAT state:

```shell
kubectl exec -n osvbng-ha deployment/ue-test -c ue -- \
  curl -sS http://osvbng-0.osvbng-headless:8080/api/show/ha/status
kubectl exec -n osvbng-ha deployment/ue-test -c ue -- \
  curl -sS http://osvbng-1.osvbng-headless:8080/api/show/ha/status
kubectl exec -n osvbng-ha deployment/ue-test -c ue -- \
  curl -sS http://osvbng-0.osvbng-headless:8080/api/show/ha/sync
kubectl exec -n osvbng-ha deployment/ue-test -c ue -- \
  curl -sS http://osvbng-0.osvbng-headless:8080/api/show/subscriber/sessions
kubectl exec -n osvbng-ha deployment/ue-test -c ue -- \
  curl -sS http://osvbng-0.osvbng-headless:8080/api/show/cgnat/mappings
```

Trigger a graceful switchover on the active node:

```shell
kubectl exec -n osvbng-ha deployment/ue-test -c ue -- \
  curl -sS -X POST -H 'Content-Type: application/json' -d '{}' \
  http://osvbng-0.osvbng-headless:8080/api/exec/ha/switchover
```

The lab NAT and exit flow is:

```text
UE 10.255.0.2
  -> active osvbng SRG
  -> PBA CGNAT 192.168.88.12-15
  -> active worker's DPDK core (.10 on A or .11 on B)
  -> gateway 192.168.88.1
  -> worker/site upstream network
  -> Internet
```

In the v0.16.0 test, ping and curl survived graceful switchover and removal of
either BNG pod. After takeover, B continued forwarding the synchronized flow
while its `subscriber.sessions` and `cgnat.mappings` show handlers returned an
empty result. Use traffic probes and HA status in addition to those dumps when
validating this release.

## RADIUS

The optional `radius` profile deploys FreeRADIUS `3.2.7` and PostgreSQL
`17.10`. It connects directly to osvbng's native RADIUS provider; no HTTP
bridge is involved. The default remains simple: `radius.enabled: false`
renders neither component and osvbng uses its local allow-all provider.

Create one external Secret. Keep both values to single-line RADIUS-safe
strings:

```shell
kubectl create secret generic osvbng-radius -n osvbng-ha \
  --from-literal=shared-secret='REPLACE_WITH_RADIUS_SECRET' \
  --from-literal=postgres-password='REPLACE_WITH_DATABASE_PASSWORD'
```

Enable the profile with the supplied example:

```shell
helm upgrade --install osvbng ./helm-chart -n osvbng-ha \
  -f helm-chart/examples/ha-values.yaml \
  -f helm-chart/examples/radius-values.yaml \
  --wait --timeout 10m
```

`radius.existingSecret` is required only when the profile is enabled.
`radius.bootstrapUsers` provides small, declarative lab fixtures. The example
authorizes UE MAC `02:00:00:00:00:01`, returns `subscriber-pool`,
`Session-Timeout`, and `Acct-Interim-Interval`, and records authentication and
accounting in PostgreSQL. Provision `radcheck`, `radreply`, and related tables
through an OSS/BSS workflow instead of Helm values in a production system.

Check the result:

```shell
kubectl logs -n osvbng-ha deployment/osvbng-freeradius |
  grep 'Ready to process requests'
kubectl exec -n osvbng-ha osvbng-radius-postgresql-0 -- \
  psql -U radius -d radius -c \
  'SELECT username,reply,authdate FROM radpostauth ORDER BY id DESC LIMIT 5;'
kubectl exec -n osvbng-ha osvbng-radius-postgresql-0 -- \
  psql -U radius -d radius -c \
  'SELECT username,framedipaddress,acctstarttime,acctupdatetime,acctstoptime
   FROM radacct ORDER BY radacctid DESC LIMIT 5;'
```

Disable RADIUS by upgrading without the overlay:

```shell
helm upgrade osvbng ./helm-chart -n osvbng-ha \
  -f helm-chart/examples/ha-values.yaml --wait --timeout 10m
```

Helm removes the FreeRADIUS and PostgreSQL workloads and returns osvbng to
local authentication. The PostgreSQL PVC is intentionally retained to avoid
AAA data loss. Delete it explicitly only when the records are no longer
needed:

```shell
kubectl delete pvc -n osvbng-ha data-osvbng-radius-postgresql-0
```
