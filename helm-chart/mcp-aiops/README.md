# osvbng MCP/AIOps chart

This chart provides a framework-neutral MCP control plane for osvbng:

```text
MCP client
  -> Agentgateway
  -> ToolHive proxy
  -> osvbng MCP server
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

Install the application chart:

```powershell
helm dependency build .\helm-chart\mcp-aiops
helm upgrade --install osvbng-aiops .\helm-chart\mcp-aiops `
  --namespace osvbng-aiops `
  --create-namespace `
  --set 'osvbngMcp.imagePullSecrets[0].name=ghcr-pull'
```

The default Gateway Service is `ClusterIP`. Use a port-forward for clients
outside the cluster:

```shell
kubectl port-forward -n osvbng-aiops service/osvbng-mcp-gateway 8080:80
```

The Streamable HTTP endpoint is `http://127.0.0.1:8080/mcp`.

## Verify

```shell
kubectl get mcpserver osvbng -n osvbng-aiops
kubectl get gateway,httproute,agentgatewaybackend -n osvbng-aiops
kubectl get pods -n osvbng-aiops
```

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
buildah bud -t ghcr.io/infinitydon/osvbng-mcp:0.1.0 \
  helm-chart/mcp-aiops/development
buildah push ghcr.io/infinitydon/osvbng-mcp:0.1.0
```

Update `osvbngMcp.image` with the pushed manifest digest before deployment.

## Agent framework

No agent framework is selected yet. Keeping this layer standards-based lets a
future Ollama-compatible agent use the same governed endpoint. Framework
evaluation can focus on MCP support, human approval gates, durable workflows,
Kubernetes operation, and OpenAI-compatible model endpoints without changing
these tools.
