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

Agentgateway exposes stable, separate endpoints: `/mcp` for OSVBNG and
`/kubernetes/mcp` for Kubernetes. This preserves existing OSVBNG tool names.

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
    "bng_running_config",
    "cgnat_pools",
    "ha_status",
    "ue_sessions"
  ],
  "bng_health_error": false,
  "ha_status_error": false,
  "cgnat_pools_error": false,
  "switchover_blocked": true
}
```

This result proves that:

- the client reached the MCP endpoint through Agentgateway;
- the required OSVBNG tool definitions were returned without renaming;
- live BNG health, HA status, and CGNAT pool calls completed successfully; and
- the mutating HA switchover operation was denied by the default safety policy.

Validate the Kubernetes backend and its safety controls:

```powershell
kubectl get mcpserver kubernetes-ops -n osvbng-aiops
kubectl logs -n osvbng-aiops kubernetes-ops-0 --tail=50
```

Through `http://<node-ip>:30080/kubernetes/mcp`, call
`pods_list` to list pods across namespaces, `pods_list_in_namespace` with
`namespace=osvbng-ha`, and `events_list` with the same namespace. All must
succeed when `clusterWideReadOnly=true`. A `resources_get` call for `v1/Secret` must fail with
`resource not allowed`, and the tool list must contain no Kubernetes create,
update, delete, scale, exec, run, Helm install, or Helm uninstall tools.

Validate controller traversal with a current FRR pod name:

```text
What Deployment owns pod <frr-pod-name> in namespace osvbng-ha? Show the complete owner chain.
```

Expected calls: `pods_get`, followed by `resources_get` when the pod owner is a
ReplicaSet. Expected answer format:
`Pod/<pod> -> ReplicaSet/<replicaset> -> Deployment/<deployment>`. The agent
must not identify the ReplicaSet hash as the Deployment or call it a
StatefulSet.

An SDK message such as `Session termination failed: 202` can appear after the
JSON result. It concerns session cleanup and does not invalidate the successful
tool checks above.

Run the governed two-UE lifecycle and traffic validation:

```powershell
.\.venv\Scripts\python e2e_client.py `
  --url http://<node-ip>:30080/mcp `
  --lifecycle
```

This starts sessions 2 and 3 concurrently, reads session 3, runs ping and curl
through `bbl3`, then deletes both sessions. The expected lifecycle fields are
all `false`:

```json
{
  "ue_create_error": false,
  "ue_second_create_error": false,
  "ue_status_error": false,
  "ue_ping_error": false,
  "ue_curl_error": false,
  "ue_delete_error": false,
  "ue_second_delete_error": false
}
```

Useful AIOps prompts:

```text
Show current interactive UE sessions.
Create UE session 2.
Ping 1.1.1.1 from UE session 2.
Fetch http://example.com from UE session 2.
Delete UE session 2.
```

Running-configuration prompts:

```text
Dump the running configuration of both BNG members and show the differences.
```

Expected tool: `bng_running_configs`

```text
Show the running CGNAT configuration on BNG member 0.
```

Expected tool: `bng_running_config` with `member=0` and `section=cgnat`

The response must come from `/api/show/running-config`. Sensitive values must
already be represented as `<redacted>` in the tool result; prompt instructions
are not the security boundary.

Presentation contract:

- A **dump** request renders each requested member under a level-three heading
  containing its exact pod name, followed by a fenced `yaml` block. Do not
  replace the configuration with prose highlights.
- A **compare** or **differences** request renders only a compact Markdown table
  with `Path`, `osvbng-0`, and `osvbng-1`; values must be complete, never
  abbreviated with `...`.
- A request for both dump and comparison renders the diff table first, followed
  by the two YAML blocks.
- Do not add common-configuration highlights, recommendations, or an overall
  status section unless the user asks for analysis.
- End with one concise evidence line containing the tool name and timestamp.

The agent must request confirmation before create or delete and must name the
target session ID. Read-only status and traffic tests need no approval.

`ue_sessions` returns active sessions and capacity counters by default. This
compact result prevents an empty UI response caused by feeding all complete
inactive BNG Blaster records back into the model. Set `include_inactive=true`
only when the stopped-slot inventory is explicitly required.

For current mappings across numbered sessions, use `ue_session_range`; for
example, `ue_session_range(1, 10)`. Its response is a fresh, inclusive range.
The returned `session-id`, `ipv4-address`, and `linux-interface` (`bblN`) must
be reproduced without renumbering. The `interface` field is the BNG Blaster
access interface (`net1`), not the per-session Linux TUN. Earlier
`ue_session_create` responses are
historical evidence and must not be presented as current state.

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

## 6. Validate Ollama Cloud through Agentgateway

Verify that the backend, route, and strict client-auth policy are accepted:

```powershell
kubectl get agentgatewaybackend ollama-cloud -n osvbng-aiops
kubectl get httproute ollama-cloud -n osvbng-aiops
kubectl get agentgatewaypolicy ollama-cloud-client-auth -n osvbng-aiops
```

Expected status:

```text
agentgatewaybackend/ollama-cloud                    ACCEPTED=True
agentgatewaypolicy/ollama-cloud-client-auth         ACCEPTED=True ATTACHED=True
```

Set the test request without displaying either credential:

```powershell
$uri = 'http://<node-ip>:30080/v1/chat/completions'
$body = @{
  model = 'qwen3.5:cloud'
  messages = @(@{
    role = 'user'
    content = 'Reply with exactly: OLLAMA VIA AGENTGATEWAY OK'
  })
  stream = $false
} | ConvertTo-Json -Depth 5 -Compress
```

First send the request without a client credential:

```powershell
curl.exe -sS -o NUL -w '%{http_code}' `
  -H 'Content-Type: application/json' `
  -d $body $uri
```

Expected output:

```text
401
```

For an administrative validation, load the client key into memory and repeat
the request. Do not print `$clientKey` or store it in shell history:

```powershell
$encoded = kubectl get secret osvbng-agent-client-key `
  -n osvbng-aiops -o jsonpath='{.data.agent-runtime}'
$clientKey = [Text.Encoding]::UTF8.GetString(
  [Convert]::FromBase64String($encoded)
)

$response = Invoke-RestMethod -Uri $uri -Method Post `
  -ContentType 'application/json' `
  -Headers @{ Authorization = "Bearer $clientKey" } `
  -Body $body
$response.choices[0].message.content
Remove-Variable clientKey
```

Expected output:

```text
OLLAMA VIA AGENTGATEWAY OK
```

This proves that unauthenticated inference is rejected and authenticated
traffic follows `client -> Agentgateway -> Ollama Cloud`. The Ollama Cloud API
key remains confined to Agentgateway's backend configuration.

## 7. Validate the operations UI

```powershell
kubectl get pod osvbng-open-webui-0 -n osvbng-aiops
kubectl get pvc osvbng-open-webui -n osvbng-aiops
kubectl get service osvbng-open-webui -n osvbng-aiops
curl.exe http://<node-ip>:30081/health
curl.exe http://<node-ip>:30081/api/version
```

Expected results:

```text
osvbng-open-webui-0   1/1   Running
osvbng-open-webui     Bound
osvbng-open-webui     NodePort   80:30081/TCP
{"status":true}
{"version":"0.11.0"}
```

Confirm the UI is configured with an Agentgateway endpoint and a client key
without printing the credential:

```powershell
kubectl exec -n osvbng-aiops osvbng-open-webui-0 -- sh -c `
  'printf "base=%s key=%s\n" "$OPENAI_API_BASE_URL" "${OPENAI_API_KEY:+present}"'
```

Expected output:

```text
base=http://osvbng-mcp-gateway/v1 key=present
```

After creating the first administrator account, add the MCP connection as
described in the README and use the UI's connection verification. It must list
the nine tools documented in section 4.

The model selector should contain the curated entry:

```text
qwen3.5:cloud - OSVBNG Operations
```

Its base model is the upstream Ollama model `qwen3.5:cloud`. The raw
`qwen3.5:cloud` and `gpt-oss:120b-cloud` entries are also available through Ollama
Cloud. User-facing configuration uses these upstream names without a
connection prefix.

The curated model must have the `OSVBNG Operations` MCP server enabled in its
model settings. For cluster-wide Kubernetes questions, it must also have the
`Kubernetes Operations` server enabled. Open WebUI persists these attachments
as:

```text
server:mcp:osvbng-operations
server:mcp:kubernetes-operations
```

Its system prompt must route numbered UE ranges and current session-to-IP
mapping questions to `ue_session_range` immediately before answering. It must
treat create responses as historical and copy the fresh session IDs, addresses,
access interfaces, and Linux interfaces without renumbering.

Start a new chat after changing model tools because an open chat can retain its
earlier tool selection. Use this live validation prompt:

```text
Use the attached OSVBNG Operations MCP tools to report current overall platform
health. You must call a live tool and cite its returned evidence.
```

The activity row should show an OSVBNG Operations tool such as `bng_health`.
The answer must contain live evidence and must not use `list_knowledge_bases`
as a substitute or invent unsupported `show ...` commands.

Operational answers should use compact, conditional provenance. For example:

```text
Evidence: live MCP — bng_health, ha_status (observed 14:32 UTC)
```

The agent should mention cached, stale, unavailable, or knowledge-base data
only when it was actually used or affects confidence. Read-only answers should
not end with an approval request; approval is required only immediately before
a proposed mutating action, with its scope and impact stated.

### Short validated prompts

Use these in a new chat with `qwen3.5:cloud - OSVBNG Operations` selected. Do
not select the raw `qwen3.5:cloud` entry, which has no OSVBNG MCP attachment:

```text
Show current BNG health.
```

Expected tool: `bng_health`

```text
Check HA status for member 0.
```

Expected tool: `ha_status`

```text
Show CGNAT pools for member 0.
```

Expected tool: `cgnat_pools`

The three CGNAT tools select the ACTIVE HA member automatically when `member`
is omitted. An explicit `member` is the zero-based StatefulSet ordinal
(`0` = `osvbng-0`, `1` = `osvbng-1`) and should be used only when the user
requests that specific member. Never interpret a standby member's empty
runtime tables as the platform-wide CGNAT state.

Use `cgnat_mappings` once for subscriber-to-outside-IP and PBA port-block
questions. Use `cgnat_sessions` only for transport flows belonging to one
exact inside IP. An empty transport-flow result does not mean the PBA mapping
is absent, and ICMP ping traffic may not appear in that flow table.

```text
Call only the radius_servers tool. Do not call bng_health.
```

Expected tool: `radius_servers`

```text
Show current subscriber sessions.
```

Expected tool: `subscriber_sessions`

These prompts are read-only. The agent should return live evidence without
asking for approval.

For the current lab, the expected saved connection is:

```text
Name: OSVBNG Operations
Type: MCP
URL:  http://osvbng-mcp-gateway/mcp
Tool count: 9
```

Verify that public signup was closed after creating the first administrator.
A second request to `/api/v1/auths/signup` must return HTTP `403`.
