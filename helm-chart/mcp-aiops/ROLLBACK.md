# AIOps rollback point

The known-good state immediately before the NOC/admin MCP profile split is:

- Git tag: `aiops-rbac-rollback-20260731`
- Commit: `b64902fd80237c02e9876c8555cf098ed3573f13`
- Helm release: `osvbng-aiops`
- Namespace: `osvbng-aiops`
- Helm revision: `46`
- Chart/app: `osvbng-mcp-aiops-0.3.5` / `0.2.12`

Repository rollback inspection:

```shell
git show aiops-rbac-rollback-20260731
```

Cluster rollback without changing the working tree:

```shell
helm rollback osvbng-aiops 46 --namespace osvbng-aiops --wait --timeout 10m
```

The rollback removes the split profile resources and restores the original
single `/mcp` route. The separately generated NOC/admin Secrets and Open WebUI
connection records can be retained for a later retry or removed explicitly.
