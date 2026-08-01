# osvbng MCP/AIOps chart

This chart provides a framework-neutral MCP control plane for osvbng:

```text
MCP client
  -> Agentgateway
  -> ToolHive proxies
     -> osvbng MCP server
     -> Kubernetes MCP server

Agent runtime
  -> Agentgateway
  -> Ollama Cloud
  -> osvbng northbound APIs
```

Agentgateway `v1.4.0` supplies MCP routing and future authentication,
authorization, rate limiting, and observability. ToolHive `v0.40.1` manages
the custom MCP server and its isolated proxy. Both upstream charts are pinned
dependencies.

The optional Kubernetes operations backend uses upstream
`containers/kubernetes-mcp-server` `v0.0.65`. It defaults to the `core`
toolset in read-only mode. Secret and ServiceAccount resources are denied in
the server configuration, and no Kubernetes mutation tools are published.
Its ServiceAccount is additionally bound to read-only Roles in the namespaces
listed by `kubernetesMcp.targetNamespaces`.

The lab API server currently permits service-account operations beyond those
RoleBindings, so the server-side `read_only` and `denied_resources` controls
are the effective safety boundary. Correct the cluster authorization mode
before enabling any Kubernetes mutation or Helm toolset.

The MCP source, tests, build file, and prerequisite helper live under
`development/`. The chart's `.helmignore` excludes that entire directory from
the packaged Helm artifact.

## Tools

The OSVBNG server is published as two isolated ToolHive profiles. The NOC
profile is an explicit allowlist; the admin profile exposes every approved
OSVBNG tool. Kubernetes remains a separate read-only backend.

- `bng_health`
- `bng_running_config`
- `bng_running_configs`
- `ha_status`
- `ha_sync`
- `subscriber_sessions`
- `cgnat_pools`
- `cgnat_mappings`
- `cgnat_sessions`
- `radius_servers`
- `ha_switchover`
- `ue_sessions`
- `ue_session_status`
- `ue_session_range`
- `ue_session_create`
- `ue_session_delete`
- `ue_ping`
- `ue_curl`

CGNAT tools query the current ACTIVE HA member automatically unless a
zero-based StatefulSet member ordinal is supplied explicitly.

`bng_running_config` dumps one member's authoritative live configuration.
`bng_running_configs` dumps every member and returns leaf-level differences.
Both tools redact passwords, shared secrets, tokens, credentials, API keys,
private keys, and authorization values inside the MCP server before returning
data to the model. Use the optional dotted `section` argument to limit output,
for example `cgnat`, `ha`, `interfaces.core`, or
`plugins.subscriber.auth.radius`.

All operational reads return structured JSON from the selected StatefulSet
member. `ha_switchover` exists only in the admin profile and every call still
requires `confirm: true`.

UE lifecycle is backed by the private `ue-test-api` service in the BNG
namespace. Create and delete activate or stop preallocated BNG Blaster slots;
they exist only in the admin profile and require `confirm: true`. Status, ping,
and curl remain available to the NOC profile.

Routing diagnostics are available from both profiles. They cover BGP
summaries, FRR RIB/BGP routes,
neighbor advertised/received routes, BNG VPP FIB detail, and a combined
`routing_overview` across both BNGs and both ISP FRRs. Kubernetes pod exec is
used only as a transport for predefined `show ... json` and `show ip fib`
commands; the MCP API accepts no arbitrary command text.

The Kubernetes backend provides pod inventory/details/logs, events, resource
inventory, and resource reads. The Helm toolset is supported upstream but is
disabled here; upstream currently provides install, list, and uninstall but
not upgrade or rollback.

`kubernetesMcp.clusterWideReadOnly=true` permits inventory and diagnostics in
all namespaces. It still publishes no mutation tools and server-side resource
denials block Secrets and ServiceAccounts. Set it to `false` to restrict RBAC
to `kubernetesMcp.targetNamespaces`.

Controller ownership must be read from `metadata.ownerReferences`, never from
name patterns. For a Deployment pod, the expected chain is
`Pod -> ReplicaSet -> Deployment`; StatefulSets and DaemonSets normally own
their pods directly. The MCP server embeds this rule in both its server
instructions and the `pods_get`/`resources_get` tool descriptions.

## Install

Set the kubeconfig, install the pinned CRDs, and create a pull secret when the
custom image is private:

Create independent client keys before installing or upgrading the chart. Do
not put their values in `values.yaml`:

```powershell
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
function New-McpKey {
  $bytes = New-Object byte[] 32
  $rng.GetBytes($bytes)
  ([BitConverter]::ToString($bytes) -replace '-', '').ToLowerInvariant()
}
$nocKey = New-McpKey
$adminKey = New-McpKey
kubectl create secret generic osvbng-mcp-noc-client-key -n osvbng-aiops `
  --from-literal=api-key=$nocKey
kubectl create secret generic osvbng-mcp-admin-client-key -n osvbng-aiops `
  --from-literal=api-key=$adminKey
```

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
curl -H "Authorization: Bearer <noc-key>" http://<node-ip>:30080/mcp/noc
```

The authenticated endpoints are `/mcp/noc` and `/mcp/admin`; exact `/mcp`
aliases NOC. Override `gateway.nodePort` if port `30080` is unavailable.

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
credential. The allowed upstream Ollama Cloud models are `qwen3.5:cloud` and
`gpt-oss:120b-cloud`; the curated UI model is named
`qwen3.5:cloud - OSVBNG Operations` and uses the upstream `qwen3.5:cloud` model
name as its base model. Open WebUI is configured to retain the upstream Ollama
names without adding a connection prefix.

Attach the `OSVBNG Operations` MCP connection to the curated model so live
operations prompts automatically receive the governed OSVBNG tools. Open
WebUI persists this attachment as `server:mcp:osvbng-operations`.
Knowledge-base tools are not a replacement for live BNG health, HA, RADIUS,
subscriber, or CGNAT queries.

Live answers should identify the MCP tools used in one concise evidence line.
They should discuss cached or knowledge-base data only when it was actually
used, and request approval only before a proposed change—not after ordinary
read-only diagnostics.

Before installation, create a persistent encryption key used to protect UI
credentials:

```powershell
kubectl create secret generic osvbng-open-webui-secret `
  --namespace osvbng-aiops `
  --from-literal=WEBUI_SECRET_KEY="$env:WEBUI_SECRET_KEY"
```

Create the administrator and NOC credential Secrets before installation. The
Helm-managed `osvbng-open-webui-bootstrap` post-install/post-upgrade Job signs
in with these credentials (or creates the first administrator on a new Open
WebUI database) and reconciles the group, users, connections, and models:

```powershell
kubectl create secret generic osvbng-open-webui-admin -n osvbng-aiops `
  --from-literal=email='admin@osvbng.local' `
  --from-literal=password="$env:OSVBNG_ADMIN_PASSWORD"

kubectl create secret generic osvbng-open-webui-noc -n osvbng-aiops `
  --from-literal=email='noc@osvbng.local' `
  --from-literal=password="$env:OSVBNG_NOC_PASSWORD"
```

The Job configures these authenticated MCP connections:

```text
Type: MCP (Streamable HTTP)
URL:  http://osvbng-mcp-gateway/mcp/noc
Authentication: Bearer
Key: value from osvbng-mcp-noc-client-key
Name: OSVBNG NOC Operations

Type: MCP (Streamable HTTP)
URL:  http://osvbng-mcp-gateway/mcp/admin
Authentication: Bearer
Key: value from osvbng-mcp-admin-client-key
Name: OSVBNG Admin Operations
```

The NOC connection and curated model are granted to the `OSVBNG NOC` group.
The admin connection and model have no grants, which keeps them
administrator-only. The MCP connections remain inside the cluster and still
traverse Agentgateway.

The curated NOC model also uses the configurable system policy at
`openWebUIBootstrap.models.noc.systemPrompt`. This includes the deployment's
chosen response for unavailable or disallowed operations; no denial wording is
embedded in the bootstrap program.

This wording policy improves the response but is not the authorization
boundary. The NOC tool list in `osvbngMcp.profiles.noc.tools` is rendered into
both the ToolHive `MCPToolConfig` allowlist and an Agentgateway
`AgentgatewayPolicy`. Agentgateway filters `tools/list` and rejects calls to
tools outside that list. Disabled mutation environment flags, the Open WebUI
group grant, and the separately authenticated Agentgateway route provide
additional layers. The built-in Agentgateway policy response is not
customizable; the configurable curated-model prompt supplies the deployment's
preferred wording. Neither the authorization list nor that wording has a
Python fallback.

The compatibility path `/mcp` also targets the NOC profile. Both NOC paths
require the NOC key; unauthenticated requests are rejected.

Create two curated models:

- `qwen3.5:cloud - OSVBNG NOC Operations`: grant to `OSVBNG NOC` and attach
  only `OSVBNG NOC Operations`.
- `qwen3.5:cloud - OSVBNG Admin Operations`: leave administrator-only and
  attach `OSVBNG Admin Operations` plus `Kubernetes Operations`.

All profile inputs are under `openWebUIBootstrap` in `values.yaml`. The hook Job
reads passwords and MCP keys directly from the referenced Secrets and never
stores them in a ConfigMap or Helm values. The development helper remains
available for diagnostics but is no longer required for installation.

Add Kubernetes as a second External Tool and attach it to the same curated
model when cluster diagnostics are wanted:

```text
Type: MCP (Streamable HTTP)
URL:  http://osvbng-mcp-gateway/kubernetes/mcp
Name: Kubernetes Operations
```

Open WebUI will expose those tools with its connection prefix while the
existing `osvbng-operations_bng_running_config` name remains unchanged.

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

The lab NOC account may be stored in `osvbng-open-webui-noc`. Recover it using
the same commands with that Secret name. Add that user to the `OSVBNG NOC`
group; it can discover the NOC connection but not the admin connection.

## Verify

See [MCP-VALIDATION.md](MCP-VALIDATION.md) for the complete Agentgateway and
MCP tool validation runbook, including representative expected output.

```shell
kubectl get mcpserver osvbng-ops-noc osvbng-ops-admin kubernetes-ops -n osvbng-aiops
kubectl get mcptoolconfig osvbng-ops-noc-tools -n osvbng-aiops
kubectl get gateway,httproute,agentgatewaybackend -n osvbng-aiops
kubectl get pods -n osvbng-aiops
```

ToolHive creates a backend and proxy workload for each profile:

- `osvbng-ops-noc-0` and `osvbng-ops-noc-<hash>` serve the filtered NOC profile.
- `osvbng-ops-admin-0` and `osvbng-ops-admin-<hash>` serve the admin profile.
- `kubernetes-ops-0` is the read-only Kubernetes MCP backend.
- `kubernetes-ops-<hash>` is its ToolHive proxy runner.

Neither is an OSVBNG dataplane instance. The actual BNG pods remain `osvbng-0`
and `osvbng-1` in the BNG release namespace (for example, `osvbng-ha`).

Run unit tests:

```powershell
cd helm-chart\mcp-aiops\development
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m unittest -v test_server.py
```

Run the deterministic client through Agentgateway after setting the applicable
key in `MCP_API_KEY`:

```powershell
.\.venv\Scripts\python e2e_client.py `
  --url http://127.0.0.1:8080/mcp/noc --profile noc
```

The NOC test executes live health, HA, CGNAT, routing and UE reads and proves
that raw configuration and mutation tools are absent from discovery. The admin
profile test verifies the complete schema without invoking a mutation.

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
