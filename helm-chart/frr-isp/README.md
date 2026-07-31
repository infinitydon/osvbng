# FRR ISP router

See [MIKROTIK.md](MIKROTIK.md) for the RouterOS 7.23.1 upstream BGP,
firewall, NAT, validation, and removal commands.

See [NODE-NETPLAN.md](NODE-NETPLAN.md) for the persistent node parent-link
configuration required by the Multus macvlan attachments.

This optional chart proves that the CGNAT pool does not need to share the
OSVBNG core subnet. It provides an FRR 10.7.0 ISP edge with VirtIO-backed
Multus macvlan interfaces.

The default HA profile uses eBGP ECMP rather than VRRP:

- FRR member core addresses: `172.31.255.4` and `.5`
- FRR member upstream addresses: `192.168.88.250` and `.251`
- routed lab CGNAT pool: `100.64.100.0/24`

The default routing profile uses eBGP:

- OSVBNG ASN: `65010`
- ISP FRR ASN: `65020`
- MikroTik ASN: `65030`
- every BNG peers with both ISP routers
- both ISP routers peer with the MikroTik at `192.168.88.1`
- the MikroTik advertises only the default route to the ISP routers
- the ISP routers advertise only the routed CGNAT prefix to the MikroTik
- only the BNG whose SRG is active originates the subscriber and CGNAT
  prefixes
- both ISP routers retain the learned route independently
- each BNG imports only `0.0.0.0/0` and installs both FRR next hops in VPP

Both FRR pods use required node affinity for
`osvbng.infinitydon.com/bng-frr-ha=true` and required hostname anti-affinity.
This is the same eligible worker pool used by the BNG StatefulSet. Both core
and upstream macvlan attachments use the single `enp8s19` parent; there is no
`nodeName` pinning.

There are no VRRP VIPs. MikroTik installs ECMP paths through `.250` and `.251`,
and each BNG installs ECMP defaults through `172.31.255.4` and `.5`.
The FRR macvlan interfaces use stable explicit MAC addresses so BGP can
re-establish immediately after a pod replacement. Proxy ARP is disabled; it
must remain disabled because core and uplink VirtIO interfaces share one L2.

OSVBNG 0.16 natively associates the prefixes under
`ha.srgs.default.networks` with SRG state. It originates those prefixes while
the SRG is `ACTIVE` or `ACTIVE_SOLO` and withdraws them in standby states.
The ISP routers do not query the Kubernetes or OSVBNG APIs.

The BNG profile applies `OSVBNG-IMPORT-DEFAULT` to accept only the default
route and `OSVBNG-EXPORT` to its eBGP neighbors. Production deployments should
also narrow the export policy to the exact subscriber and CGNAT allocations.
The integration profile sets `osvbng.bgp.advertiseSubscriberPrefix: false`,
so FRR learns only the routed CGNAT pool. Set it to `true` only when the private
subscriber pool must be reachable as a non-NAT routed service.

When `bgp.enabled` is `false`, the chart installs a static fallback route for
`cgnat.prefix` through `cgnat.staticNextHop`. Set the next hop to the core
address of the BNG that should receive traffic in non-BGP mode. This fallback
does not provide automatic BNG failover.

`bng-values.yaml` is an integration values file for the separate `bng` Helm
release. It moves the BNG core interfaces to the transit subnet, disables the
static default route, enables both BGP peers, disables proxy ARP, and keeps the
CGNAT addresses in the routed pool. The FRR chart cannot change another Helm
release's values.

## HA behavior in this Kubernetes lab

Keep `preempt` disabled with OSVBNG 0.16. A tracked-interface priority change
can move the SRG and BGP routes from `STANDBY` to `ACTIVE`, but v0.16 restores
synced subscriber sessions only when promotion starts in `STANDBY_ALONE`.

For a hard failure, prevent the StatefulSet from immediately recreating the
failed higher-priority member, wait for the survivor to reach
`STANDBY_ALONE`, and then call:

```shell
curl -sS -X POST \
  -H 'Content-Type: application/json' \
  -d '{"force":true,"srg_names":["default"]}' \
  http://osvbng-1.osvbng-headless:8080/api/exec/ha/switchover
```

The v0.16 forced-promotion path restores synchronized subscriber and CGNAT
state. End-to-end forwarding additionally depends on the upstream L2 domain
learning the SRG virtual MAC on the survivor. Validate that MAC movement on
the target VirtIO or physical switching platform before treating the profile
as production HA.

```shell
helm upgrade osvbng ./helm-chart/bng --namespace osvbng-ha \
  --reuse-values -f ./helm-chart/frr-isp/bng-values.yaml --wait

helm upgrade --install osvbng-frr-isp ./helm-chart/frr-isp \
  --namespace osvbng-ha --create-namespace --wait

kubectl get pods -n osvbng-ha -l app.kubernetes.io/name=osvbng-frr-isp -o wide
kubectl exec -n osvbng-ha deploy/osvbng-frr-isp-a -c frr -- ip address
kubectl exec -n osvbng-ha deploy/osvbng-frr-isp-a -c frr -- \
  vtysh -c "show bgp ipv4 unicast summary" \
        -c "show bgp ipv4 unicast 100.64.100.0/24" \
        -c "show ip route 100.64.100.0/24"

kubectl exec -n osvbng-ha osvbng-0 -c osvbng -- \
  vtysh -c "show bgp ipv4 unicast summary"
```

During a tested hard loss of the active BNG, OSVBNG 0.16 placed the remaining
member in `STANDBY_ALONE` rather than `ACTIVE`. The sidecar deliberately
withdraws the prefix in that state, preventing return traffic from being sent
to a standby dataplane. FRR-router failover is independently functional and
was validated with all 20 UE probes passing after one router was removed and
again after it rejoined. BNG automatic promotion requires an
upstream OSVBNG HA change or a supported witness/fencing design.
