param(
    [string]$Namespace = "osvbng-aiops"
)

$ErrorActionPreference = "Stop"
$gatewayApiVersion = "v1.6.0"
$agentgatewayVersion = "v1.4.0"
$toolhiveVersion = "0.40.1"

kubectl apply --server-side --force-conflicts `
  -f "https://github.com/kubernetes-sigs/gateway-api/releases/download/$gatewayApiVersion/standard-install.yaml"

helm upgrade --install agentgateway-crds `
  oci://cr.agentgateway.dev/charts/agentgateway-crds `
  --version $agentgatewayVersion --namespace $Namespace --create-namespace

helm upgrade --install toolhive-operator-crds `
  oci://ghcr.io/stacklok/toolhive/toolhive-operator-crds `
  --version $toolhiveVersion --namespace $Namespace
