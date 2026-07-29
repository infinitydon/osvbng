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

The route watcher queries both OSVBNG HA APIs and points the pool route at the
member whose SRG is `ACTIVE`.

```shell
helm upgrade osvbng ./helm-chart/bng --namespace osvbng-ha \
  --reuse-values -f ./helm-chart/frr-isp/bng-values.yaml --wait

helm upgrade --install osvbng-frr-isp ./helm-chart/frr-isp \
  --namespace osvbng-ha --create-namespace --wait

kubectl get pods -n osvbng-ha -l app.kubernetes.io/name=osvbng-frr-isp -o wide
kubectl exec -n osvbng-ha deploy/osvbng-frr-isp-a -c frr -- ip address
kubectl exec -n osvbng-ha deploy/osvbng-frr-isp-a -c frr -- vtysh -c "show ip route"
```
