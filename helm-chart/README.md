# Helm charts

The Kubernetes implementation is split into independent charts:

- `bng`: osvbng, VFIO/DPDK, HA, CGNAT, subscriber testing, and the optional
  FreeRADIUS/PostgreSQL profile.
- `mcp-aiops`: Agentgateway, ToolHive, and the osvbng MCP operations server.

Install and upgrade each chart as a separate Helm release. The MCP chart reads
the osvbng northbound API and does not modify the BNG release.
