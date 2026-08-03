# OSVBNG CPE lab

This separate chart provides an end-to-end subscriber and TR-069 lab for an
existing OSVBNG release. It creates 20 BNG Blaster IPoE sessions, moves each
session interface into its own Linux network namespace, runs one cpe-labs
client per namespace, and registers the clients with GenieACS.

## Components

- BNG Blaster 0.9.37 creates the DHCP/QinQ subscriber sessions.
- `cpe-manager` creates `cpe1` through `cpe20`, moves `bbl1` through `bbl20`
  into them, and starts cpe-labs 0.2.1.
- `ue-api` exposes namespace-aware session, ping, and curl operations to the
  AIOps MCP server through the in-cluster `ue-test-api` Service.
- GenieACS 1.2.16 and MongoDB provide ACS management and persistence.

The access host device is exclusively moved into the subscriber-lab pod by
host-device CNI. Do not deploy another BNG Blaster or UE pod using that NIC.

## Network

- Subscriber pool: `10.255.0.0/24`
- CGNAT pool: `100.64.100.0/24`
- GenieACS management IP: `192.168.88.252/24`
- GenieACS route to CGNAT: `100.64.100.0/24 via 192.168.88.250` (FRR-A)
- CWMP URL: `http://192.168.88.252:7547/`
- GenieACS UI: `http://<kubernetes-node>:30748`

The direct FRR route is intentional. Sending ACS replies to the general
MikroTik default gateway can introduce an asymmetric CGNAT return path.

## Install

Install the BNG and optional RADIUS profile first. Ensure the RADIUS profile
has `autoProvisionSubscriberUsers: true` and `subscriberCapacity: 20`.

```shell
helm lint ./helm-chart/cpe-lab
helm upgrade --install osvbng-cpe-lab ./helm-chart/cpe-lab \
  --namespace osvbng-ha \
  --create-namespace \
  --wait --timeout 10m
```

The chart owns a MongoDB PVC. Remove it explicitly only when a clean loss of
ACS state is intended:

```shell
helm uninstall osvbng-cpe-lab -n osvbng-ha
kubectl delete pvc -n osvbng-ha data-mongodb-0
```

## Validate

```shell
kubectl get pods -n osvbng-ha \
  -l app.kubernetes.io/instance=osvbng-cpe-lab

POD=$(kubectl get pod -n osvbng-ha -l app=osvbng-cpe-lab \
  -o jsonpath='{.items[0].metadata.name}')

kubectl exec -n osvbng-ha "$POD" -c ue-api -- \
  wget -qO- http://127.0.0.1:8081/health

kubectl exec -n osvbng-ha "$POD" -c cpe-manager -- \
  ip netns exec cpe1 ping -c 3 8.8.8.8

kubectl exec -n osvbng-ha "$POD" -c cpe-manager -- \
  curl -sS 'http://genieacs:7557/devices/?projection=_id'
```

Expected results are 20 namespaces, 20 active subscriber sessions, successful
Internet traffic from each namespace, and 20 device records in GenieACS.

The source for the two locally built helper images is retained under `images/`
and excluded from the packaged chart by `.helmignore`.
