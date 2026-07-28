# osvbng MCP/AIOps chart

This chart provides a framework-neutral MCP control plane for osvbng:

```text
MCP client
  -> Agentgateway
  -> ToolHive proxy
  -> osvbng MCP server

Agent runtime
  -> Agentgateway
  -> Ollama Cloud
  -> osvbng northbound APIs
```

Agentgateway `v1.4.0` supplies MCP routing and future authentication,
authorization, rate limiting, and observability. ToolHive `v0.40.1` manages
the custom MCP server and its isolated proxy. Both upstream charts are pinned
dependencies.

The MCP source, tests, build file, and prerequisite helper live under
`development/`. The chart's `.helmignore` excludes that entire directory from
the packaged Helm artifact.

## Tools

The first release exposes:

- `bng_health`
- `ha_status`
- `ha_sync`
- `subscriber_sessions`
- `cgnat_pools`
- `cgnat_mappings`
- `cgnat_sessions`
- `radius_servers`
- `ha_switchover`

All operational reads return structured JSON from the selected StatefulSet
member. `ha_switchover` is denied unless `osvbngMcp.allowMutations` is enabled
and the individual call includes `confirm: true`.

## Install

Set the kubeconfig, install the pinned CRDs, and create a pull secret when the
custom image is private:

```powershell
$env:KUBECONFIG = 'C:\path\to\kubeconfig'
powershell -ExecutionPolicy Bypass -File `
  .\helm-chart\mcp-aiops\development\install-prerequisites.ps1 `
  -Namespace osvbng-aiops

kubectl create secret docker-registry ghcr-pull `
  --namespace osvbng-aiops `
  --docker-server ghcr.io `
  --docker-username USER `
  --docker-password TOKEN
```

Ollama Cloud uses separate upstream and client credentials:

- `ollama-cloud-api-key` is read only by Agentgateway and attached to requests
  sent to Ollama Cloud.
- `osvbng-agent-client-key` authenticates the agent runtime to Agentgateway.

Create the upstream Secret from the Ollama key:

```powershell
kubectl create secret generic ollama-cloud-api-key `
  --namespace osvbng-aiops `
  --from-literal=OLLAMA_CLOUD_API_KEY="$env:OLLAMA_CLOUD_API_KEY"
```

Create `osvbng-agent-client-key` with a cryptographically random value before
installing the chart. Its data may contain one or more client entries; the
agent runtime must present the selected value as a Bearer token.

Install the application chart:

```powershell
helm dependency build .\helm-chart\mcp-aiops
helm upgrade --install osvbng-aiops .\helm-chart\mcp-aiops `
  --namespace osvbng-aiops `
  --create-namespace `
  --set 'osvbngMcp.imagePullSecrets[0].name=ghcr-pull'
```

The Gateway Service defaults to `NodePort` port `30080`. Access it through the
IP address of any reachable Kubernetes node:

```shell
curl http://<node-ip>:30080/mcp
```

The Streamable HTTP endpoint is `http://<node-ip>:30080/mcp`. Override
`gateway.nodePort` if port `30080` is unavailable.

The authenticated Ollama Cloud endpoint is:

```text
http://<node-ip>:30080/v1/chat/completions
```

The Ollama credential is not exposed to clients. Agentgateway validates the
client credential, removes it, and injects the upstream Ollama credential.

## Operations UI

The optional Open WebUI profile is enabled by default. It uses the latest
official Helm chart currently published (`15.2.0`) with the stable Open WebUI
application image overridden to `0.11.0`. It is available at:

```text
http://<node-ip>:30081
```

The UI connects to `http://osvbng-mcp-gateway/v1` with the internal
`osvbng-agent-client-key`. It has no direct Ollama endpoint or Ollama Cloud
credential. The allowed upstream Ollama Cloud models are `gpt-oss:20b` and
`gpt-oss:120b`; the curated UI model is named
`OSVBNG Operations - Ollama Cloud`.

Attach the `OSVBNG Operations` MCP connection to the curated model so live
operations prompts automatically receive the nine governed OSVBNG tools. Open
WebUI persists this attachment as `server:mcp:osvbng-operations`.
Knowledge-base tools are not a replacement for live BNG health, HA, RADIUS,
subscriber, or CGNAT queries.

Before installation, create a persistent encryption key used to protect UI
credentials:

```powershell
kubectl create secret generic osvbng-open-webui-secret `
  --namespace osvbng-aiops `
  --from-literal=WEBUI_SECRET_KEY="$env:WEBUI_SECRET_KEY"
```

The first account registered through the UI becomes the administrator. As that
administrator, add the OSVBNG MCP server under **Admin Settings -> External
Tools**:

```text
Type: MCP (Streamable HTTP)
URL:  http://osvbng-mcp-gateway/mcp
Name: OSVBNG Operations
```

The MCP connection remains inside the cluster and still traverses
Agentgateway. Do not configure `mcp-osvbng-ops-proxy` directly in the UI.

For the lab deployment, administrator credentials may be stored in the
non-chart Secret `osvbng-open-webui-admin`. Recover them locally without adding
them to shell history:

```powershell
$email = kubectl get secret osvbng-open-webui-admin -n osvbng-aiops `
  -o jsonpath='{.data.email}'
$password = kubectl get secret osvbng-open-webui-admin -n osvbng-aiops `
  -o jsonpath='{.data.password}'

[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($email))
[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($password))
```

Open WebUI automatically disables self-registration after the first
administrator is created. Create subsequent users from the administrator
interface rather than reopening public signup.

## Verify

See [MCP-VALIDATION.md](MCP-VALIDATION.md) for the complete Agentgateway and
MCP tool validation runbook, including representative expected output.

```shell
kubectl get mcpserver osvbng-ops -n osvbng-aiops
kubectl get gateway,httproute,agentgatewaybackend -n osvbng-aiops
kubectl get pods -n osvbng-aiops
```

ToolHive creates two workloads for the operations server:

- `osvbng-ops-0` is the custom operations MCP backend.
- `osvbng-ops-<hash>` is ToolHive's proxy runner in front of the backend.

Neither is an OSVBNG dataplane instance. The actual BNG pods remain `osvbng-0`
and `osvbng-1` in the BNG release namespace (for example, `osvbng-ha`).

Run unit tests:

```powershell
cd helm-chart\mcp-aiops\development
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m unittest -v test_server.py
```

Run the deterministic client through Agentgateway:

```powershell
.\.venv\Scripts\python e2e_client.py `
  --url http://127.0.0.1:8080/mcp
```

The test asserts all tool schemas, executes live health, HA, and CGNAT reads,
and proves that switchover is blocked.

## Build

Build and publish the image from `development/`:

```shell
buildah bud -t ghcr.io/infinitydon/osvbng-mcp:0.1.1 \
  helm-chart/mcp-aiops/development
buildah push ghcr.io/infinitydon/osvbng-mcp:0.1.1
```

Update `osvbngMcp.image` with the pushed manifest digest before deployment.

## Agent framework

No agent framework is selected yet. Keeping this layer standards-based lets a
future Ollama-compatible agent use the same governed endpoint. Framework
evaluation can focus on MCP support, human approval gates, durable workflows,
Kubernetes operation, and OpenAI-compatible model endpoints without changing
these tools.
