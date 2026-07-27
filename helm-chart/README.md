# osvbng Helm chart

This chart deploys osvbng `v0.2.0` on Kubernetes with two DRA-managed
VFIO/DPDK devices and BNG Blaster `0.9.37`. The default profile is sized for
100 concurrent IPoE subscribers using QinQ (S-VLAN 100 and C-VLANs 100-199).

## Tested environment

- Kubernetes 1.36.2
- `linux-net.dra.infinitydon.com` DRA driver
- worker `ebpf-bng-node-01`
- access PCI device `0000:09:03.0`
- core PCI device `0000:09:04.0`
- 6 exclusive CPUs, 6 GiB memory, and 2 x 1 GiB hugepages
- BNG Blaster host devices `enp8s21` and `enp8s22`

The PCI devices are VirtIO NICs bound to `vfio-pci` in no-IOMMU mode. For a
production deployment, use a real IOMMU rather than no-IOMMU mode.

## Prerequisites

The cluster must provide:

1. Kubernetes DRA (`resource.k8s.io/v1`) and the Linux network DRA driver.
2. A `linux-net-dpdk` DeviceClass exposing both configured PCI devices.
3. VFIO and IOMMU support on the target worker.
4. At least 2 GiB of allocatable 1 GiB hugepages.
5. Multus and the `host-device` CNI for BNG Blaster.
6. A pull secret when either configured registry package is private.

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
```

## Install

Review the worker, PCI addresses, host devices, and VLANs in `values.yaml`
before installation:

```shell
helm lint ./helm-chart
helm upgrade --install osvbng ./helm-chart \
  --namespace osvbng \
  --create-namespace \
  --values private-values.yaml \
  --wait \
  --timeout 10m
```

BNG Blaster starts automatically and establishes the configured sessions.

## Verification

```shell
kubectl get pods,resourceclaims -n osvbng
kubectl logs -n osvbng deployment/bngblaster | grep "ALL SESSIONS ESTABLISHED"
kubectl logs -n osvbng osvbng-0 | grep "Session bound" | wc -l
kubectl exec -n osvbng osvbng-0 -- \
  vppctl -s /run/osvbng/cli.sock show interface
```

The expected default result is 100 DHCP ACKs, 100 bound IPoE sessions, and
VPP interfaces `access`, `access.100`, and `core` in the up state.

## Images

- osvbng is based on upstream stable `v0.2.0`. Its stable image omitted
  `vpp-plugin-dpdk`; the chart image applies upstream commit `3cea1c7`.
- The small Kubernetes patch in `images/osvbng` prevents osvbng from trying
  to create an LCP TAP pair for the pod's existing `eth0` management veth.
- BNG Blaster is built from the official `0.9.37` Ubuntu package using the
  reproducible Dockerfile in `images/bngblaster`.
- Runtime images are pinned by digest in `values.yaml`.

## Interactive subscriber test

BNG Blaster is the scale and protocol test tool; it does not create a
UERANSIM-style Linux TUN interface for each subscriber. Use a separate,
single-subscriber Linux namespace or container with a DHCP/PPPoE client for
interactive `ping`, DNS, and `curl` tests. It must attach to an unused access
port or VLAN path; do not attach it to a host-device interface already owned
by the BNG Blaster pod.
