# osvbng Helm chart

This chart deploys osvbng `v0.16.0` on Kubernetes with two directly mounted
VFIO/DPDK devices, native PBA CGNAT, and BNG Blaster `0.9.37`. The default
profile is sized for 100 concurrent IPoE subscribers using QinQ (S-VLAN 100
and C-VLANs 100-199).

## Tested environment

- Kubernetes 1.36.2
- worker `ebpf-bng-node-01`
- access PCI device `0000:09:03.0`
- core PCI device `0000:09:04.0`
- 6 exclusive CPUs, 6 GiB memory, and 2 x 1 GiB hugepages
- BNG Blaster host devices `enp8s21` and `enp8s22`

The PCI devices are VirtIO NICs bound to `vfio-pci` in no-IOMMU mode. For a
production deployment, use a real IOMMU rather than no-IOMMU mode.

## Prerequisites

The cluster must provide:

1. Both configured PCI devices bound to `vfio-pci` on the dedicated worker.
2. `/dev/vfio` available on that worker, with VFIO and IOMMU support.
3. At least 2 GiB of allocatable 1 GiB hugepages.
4. Multus and the `host-device` CNI for BNG Blaster.
5. A pull secret when either configured registry package is private.

The chart intentionally does not use DRA or a device plugin. Kubernetes
therefore does not arbitrate the VFIO devices: deploy only one osvbng pod,
pin it to the dedicated worker, and do not assign the configured PCI
addresses to another workload.

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
kubectl get pods -n osvbng
kubectl exec -n osvbng deployment/bngblaster -- \
  grep -c "DHCP-ACK received" /tmp/bngblaster.log
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
- `ue-test-upstream`, which connects to the BNG core interface and provides
  routing toward its Kubernetes network. It only masquerades the CGNAT
  outside pool after osvbng has performed subscriber translation; it does not
  accept untranslated traffic from the subscriber pool.

The two physical interfaces use exclusive `host-device` CNI attachments, so
traffic-test mode and BNG Blaster cannot run simultaneously. Helm rejects
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
ping -I bbl1 -c 3 192.0.2.2
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

The lab uses `198.18.0.0/29` as its CGNAT outside pool. Because that benchmark
range is not globally routed, `ue-test-upstream` applies a second masquerade
from that pool to its Kubernetes `eth0` solely for public Internet testing.
In production, advertise a real public pool upstream and remove the test pod.

The default test subscriber uses S-VLAN 100 and C-VLAN 100. Change
`trafficTest.outerVlan` and `trafficTest.innerVlan` when those identifiers
are already allocated.

## RADIUS

osvbng `v0.16.0` has a native `subscriber.auth.radius` provider supporting
authentication, accounting, ordered server failover, CoA/Disconnect, VRF
binding, and attribute mappings. The chart currently defaults to local
allow-all authentication for repeatable baseline tests. A FreeRADIUS
component and opt-in RADIUS values can be added without an HTTP bridge.
