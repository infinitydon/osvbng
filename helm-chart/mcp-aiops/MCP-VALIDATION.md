# Agentgateway and MCP validation

This runbook validates the complete request path:

```text
MCP client -> Agentgateway -> ToolHive proxy -> OSVBNG operations MCP server
```

The examples assume the Helm release and namespace are both `osvbng-aiops`.
Set the kubeconfig before running them:

```powershell
$env:KUBECONFIG = 'C:\path\to\kubeconfig'
```

## 1. Validate the workloads

```powershell
kubectl get pods -n osvbng-aiops
```

Expected result (pod hashes will differ):

```text
NAME                                            READY   STATUS    RESTARTS
osvbng-aiops-agentgateway-597f974468-nt9rn      1/1     Running   0
osvbng-mcp-gateway-5755f9879d-zw57m             1/1     Running   0
osvbng-ops-0                                    1/1     Running   0
osvbng-ops-5d4c8cfd69-gvxxw                     1/1     Running   0
toolhive-operator-78dbbb5987-fs2kf              1/1     Running   0
```

`osvbng-ops-0` is the operations MCP backend. `osvbng-ops-<hash>` is its
ToolHive proxy. They are not BNG dataplane pods.

## 2. Validate ToolHive

```powershell
kubectl get mcpserver osvbng-ops -n osvbng-aiops
```

Expected result:

```text
NAME          STATUS   READY   REPLICAS   URL
osvbng-ops    Ready    True    1          http://mcp-osvbng-ops-proxy.osvbng-aiops.svc.cluster.local:8080/mcp
```

Confirm that ToolHive's backend health checks receive HTTP 200:

```powershell
kubectl logs -n osvbng-aiops osvbng-ops-0 --since=2m |
  Select-String 'GET / HTTP'
```

Expected lines:

```text
"GET / HTTP/1.1" 200 OK
```

Repeated `404 Not Found` responses here indicate that the deployed MCP image
does not provide the root health endpoint.

## 3. Validate Agentgateway resources

```powershell
kubectl get gateway,httproute,agentgatewaybackend -n osvbng-aiops
```

Expected status:

```text
NAME                                                   CLASS          ADDRESS          PROGRAMMED
gateway.gateway.networking.k8s.io/osvbng-mcp-gateway   agentgateway   <cluster-ip>     True

NAME
httproute.gateway.networking.k8s.io/osvbng-mcp

NAME                                              ACCEPTED
agentgatewaybackend.agentgateway.dev/osvbng-mcp   True
```

The two required success conditions are:

- Gateway `PROGRAMMED=True`
- AgentgatewayBackend `ACCEPTED=True`

Confirm that the Agentgateway backend resolves to the ToolHive proxy:

```powershell
kubectl get agentgatewaybackend osvbng-mcp -n osvbng-aiops `
  -o custom-columns='SERVICE:.spec.mcp.targets[0].static.backendRef.name,PORT:.spec.mcp.targets[0].static.port'
```

Expected output:

```text
SERVICE                PORT
mcp-osvbng-ops-proxy   8080
```

## 4. List and exercise the MCP tools

Find a reachable worker-node address and confirm the allocated NodePort:

```powershell
kubectl get nodes -o wide
kubectl get service osvbng-mcp-gateway -n osvbng-aiops
```

Expected output:

```text
NAME                  TYPE       CLUSTER-IP       EXTERNAL-IP   PORT(S)
osvbng-mcp-gateway    NodePort   <cluster-ip>     <none>        80:30080/TCP
```

Install the client dependencies if needed and run the deterministic validation
client. Replace `<node-ip>` with a reachable address from `kubectl get nodes
-o wide`:

```powershell
cd helm-chart\mcp-aiops\development
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python e2e_client.py `
  --url http://<node-ip>:30080/mcp
```

Expected output:

```json
{
  "tools": [
    "bng_health",
    "cgnat_mappings",
    "cgnat_pools",
    "cgnat_sessions",
    "ha_status",
    "ha_switchover",
    "ha_sync",
    "radius_servers",
    "subscriber_sessions"
  ],
  "bng_health_error": false,
  "ha_status_error": false,
  "cgnat_pools_error": false,
  "switchover_blocked": true
}
```

This result proves that:

- the client reached the MCP endpoint through Agentgateway;
- all nine expected tool definitions were returned;
- live BNG health, HA status, and CGNAT pool calls completed successfully; and
- the mutating HA switchover operation was denied by the default safety policy.

An SDK message such as `Session termination failed: 202` can appear after the
JSON result. It concerns session cleanup and does not invalidate the successful
tool checks above.

## 5. In-cluster validation without port-forwarding

The same test can be run from the MCP backend pod:

```powershell
kubectl cp .\development\e2e_client.py `
  osvbng-aiops/osvbng-ops-0:/dev/shm/e2e_client.py

kubectl exec -n osvbng-aiops osvbng-ops-0 -- `
  python /dev/shm/e2e_client.py `
  --url http://osvbng-mcp-gateway/mcp
```

The expected JSON is identical to the output in the preceding section.
