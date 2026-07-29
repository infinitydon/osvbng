# FRR ISP router

This optional chart proves that the CGNAT pool does not need to share the
OSVBNG core subnet. It provides an FRR 10.7.0 ISP edge with VirtIO-backed
Multus macvlan interfaces.

The default HA profile uses unicast VRRP (Keepalived) because some virtual
switches suppress VRRP multicast between VirtIO ports:

- BNG-facing VIP: `172.31.255.1/29`
- FRR member core addresses: `172.31.255.4` and `.5`
- upstream VIP: `192.168.88.249/24`
- FRR member upstream addresses: `192.168.88.250` and `.251`
- routed CGNAT pool: `192.168.88.12/30`

The default routing profile uses eBGP:

- OSVBNG ASN: `65010`
- ISP FRR ASN: `65020`
- every BNG peers with both ISP routers
- only the BNG whose SRG state is `ACTIVE` originates `192.168.88.12/30`
- both ISP routers retain the learned route, independently of which router
  currently owns the VRRP VIP

OSVBNG 0.16 does not natively associate a CGNAT network statement with SRG
state. The BNG chart therefore includes a small `bgp-ha-advertiser` sidecar.
It uses the local HA API and local FRR VTY socket to originate or withdraw the
prefix. The ISP routers do not query the Kubernetes or OSVBNG APIs.

The profile disables FRR's generic `ebgp-requires-policy` guard because this
is a closed lab with exactly one permitted prefix. Production deployments
should replace that setting with explicit inbound and outbound prefix lists
and route maps.

`bng-values.yaml` is an integration values file for the separate `bng` Helm
release. It moves the BNG core interfaces to the transit subnet, enables the
BGP peers, disables proxy ARP, and keeps the CGNAT addresses in the routed
pool. The FRR chart cannot change another Helm release's values.

```shell
helm upgrade osvbng ./helm-chart/bng --namespace osvbng-ha \
  --reuse-values -f ./helm-chart/frr-isp/bng-values.yaml --wait

helm upgrade --install osvbng-frr-isp ./helm-chart/frr-isp \
  --namespace osvbng-ha --create-namespace --wait

kubectl get pods -n osvbng-ha -l app.kubernetes.io/name=osvbng-frr-isp -o wide
kubectl exec -n osvbng-ha deploy/osvbng-frr-isp-a -c frr -- ip address
kubectl exec -n osvbng-ha deploy/osvbng-frr-isp-a -c frr -- \
  vtysh -c "show bgp ipv4 unicast summary" \
        -c "show bgp ipv4 unicast 192.168.88.12/30" \
        -c "show ip route 192.168.88.12/30"

kubectl exec -n osvbng-ha osvbng-0 -c osvbng -- \
  vtysh -c "show bgp ipv4 unicast summary"
```

During a tested hard loss of the active BNG, OSVBNG 0.16 placed the remaining
member in `STANDBY_ALONE` rather than `ACTIVE`. The sidecar deliberately
withdraws the prefix in that state, preventing return traffic from being sent
to a standby dataplane. FRR-router failover is independently functional and
was validated without UE packet loss. BNG automatic promotion requires an
upstream OSVBNG HA change or a supported witness/fencing design.
