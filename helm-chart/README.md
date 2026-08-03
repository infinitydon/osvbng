# Helm charts

For a complete installation—including worker preparation, secrets, required
and optional profiles, validation, upgrades, rollback, and removal—follow
[`DEPLOYMENT.md`](DEPLOYMENT.md).

The Kubernetes implementation is split into independent charts:

- `bng`: osvbng, VFIO/DPDK, HA, CGNAT, and the optional
  FreeRADIUS/PostgreSQL profile.
- `cpe-lab`: BNG Blaster subscriber sessions, per-CPE network namespaces,
  cpe-labs TR-069 clients, GenieACS, and MongoDB.
- `frr-isp`: optional routed-CGNAT ISP edge with two-router eBGP ECMP.
- `mcp-aiops`: Agentgateway, ToolHive, and the osvbng MCP operations server.

Install and upgrade each chart as a separate Helm release. The MCP chart reads
the osvbng northbound API and does not modify the BNG release.
