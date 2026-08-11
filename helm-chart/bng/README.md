# OSVBNG Helm chart

See [`../DEPLOYMENT.md`](../DEPLOYMENT.md) for the complete ordered platform
installation. This file is the BNG-specific reference.

This chart deploys upstream OSVBNG `v0.16.0` with direct VFIO/DPDK devices,
native PBA CGNAT, optional active/standby HA, BGP, and an optional
FreeRADIUS 3.2.7/PostgreSQL profile. Subscriber simulation now lives in the
separate `../cpe-lab` chart.

An experimental image built from upstream HA-fix PR #400 is available through
`examples/ha-fix-image-values.yaml`. Upstream test 37 passed 20/20, including
fresh bidirectional post-switchover traffic with no session flaps. The
Kubernetes/VFIO topology also restored all 20 sessions and CGNAT mappings, but
traffic took 244 seconds to recover while the virtual MAC moved between worker
VMs. The image therefore remains experimental until L2/FDB convergence is
made suitably fast for HA.

## Prerequisites

- Kubernetes 1.32 or newer, Multus, and hugepage support.
- Two QEMU VirtIO (`1af4:1000`) NICs bound to `vfio-pci` on each BNG worker.
- The workers labelled `osvbng.infinitydon.com/bng-frr-ha=true`.
- The pinned SR-IOV Network Device Plugin in `sriov-device-plugin/`.

This deployment does not use DRA. The plugin selects by vendor, device, and
driver rather than hard-coded PCI addresses, and advertises
`qemu-virtio-dpdk.dev/osvbng_vfio`. Each BNG pod requests two devices.

```shell
kubectl label node ebpf-bng-node-01 \
  osvbng.infinitydon.com/bng-frr-ha=true --overwrite
kubectl label node ebpf-bng-node-02 \
  osvbng.infinitydon.com/bng-frr-ha=true --overwrite
kubectl apply -k ./helm-chart/bng/sriov-device-plugin
kubectl rollout status -n kube-system daemonset/osvbng-sriov-device-plugin
```

## Install

Standalone mode uses `examples/standalone-values.yaml`. The current routed HA
lab uses `examples/ha-values.yaml` together with the FRR integration values.
Addresses are explicitly tied to StatefulSet identity; no IP arithmetic is
used.

```shell
helm lint ./helm-chart/bng
helm upgrade --install osvbng ./helm-chart/bng \
  --namespace osvbng-ha --create-namespace \
  --values ./helm-chart/frr-isp/bng-values.yaml \
  --values ./helm-chart/bng/examples/radius-values.yaml \
  --wait --timeout 10m
```

The routed HA profile uses:

- BNG core addresses `172.31.255.2/29` and `172.31.255.3/29`.
- FRR peers `172.31.255.4` and `172.31.255.5`.
- subscriber pool `10.255.0.0/24`.
- CGNAT pool `100.64.100.0/24`.
- eBGP rather than VRRP between BNG and FRR.

Only the active BNG advertises the CGNAT prefix. Advertising the private
subscriber prefix is conditional through `osvbng.bgp.advertiseSubscriberPrefix`
and is disabled for ordinary CGNAT Internet service.

## RADIUS

RADIUS is optional. When disabled, OSVBNG uses its local allow-all AAA
provider. To enable the lab profile:

```yaml
radius:
  enabled: true
  existingSecret: osvbng-radius
  autoProvisionSubscriberUsers: true
  subscriberCapacity: 20
```

Create the shared secret before installation:

```shell
kubectl create secret generic osvbng-radius -n osvbng-ha \
  --from-literal=shared-secret='replace-me'
```

## Validate

```shell
kubectl get pods -n osvbng-ha -l app.kubernetes.io/name=osvbng -o wide
kubectl exec -n osvbng-ha osvbng-0 -- \
  curl -sS http://127.0.0.1:8080/api/v1/ha
kubectl exec -n osvbng-ha osvbng-0 -- \
  vppctl -s /run/osvbng/cli.sock show interface
kubectl exec -n osvbng-ha osvbng-0 -- \
  vtysh -c 'show bgp ipv4 unicast summary'
```

Install `../cpe-lab` to validate DHCP, namespace traffic, CGNAT, and GenieACS
registration with 20 simulated subscriber CPEs.
