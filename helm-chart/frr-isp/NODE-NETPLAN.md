# FRR node Netplan prerequisites

The FRR chart creates macvlan interfaces on existing node VirtIO interfaces.
The parent interfaces must therefore be configured and brought up by the
node operating system before Helm installs the FRR pods. The chart does not
deploy privileged helper pods to change host links.

The current lab mapping is:

| FRR | Node | Core parent | Upstream parent |
|---|---|---|---|
| A | `ebpf-bng-node-03-ubuntu-focal` | `enp6s19` | `enp7s2` |
| B | `ebpf-bng-cp-01` | `ens19` | `eth0` |

`eth0` on the control-plane node is also its DHCP-configured management
interface. Do not replace its existing Netplan definition.

## FRR-A node

The relevant parts of `/etc/netplan/99-rename-interfaces.yaml` are:

```yaml
network:
  version: 2
  renderer: networkd
  ethernets:
    enp6s19:
      dhcp4: false
      dhcp6: false
      optional: true
    enp7s2:
      dhcp4: false
      dhcp6: false
      optional: true
```

The deployed node file also declares the other unused VirtIO interfaces
without addresses. It intentionally preserves their kernel interface names;
renaming either FRR parent requires updating `members[].core.parent` or
`members[].uplink.parent` in `values.yaml`.

## FRR-B node

`/etc/netplan/interface.yaml` already contains:

```yaml
network:
  version: 2
  ethernets:
    ens19:
      dhcp4: false
```

The existing cloud-init Netplan file manages `eth0` with DHCP.

## Apply and validate

Back up any existing file before editing it. Use restrictive permissions,
generate the configuration, and reconfigure only the dedicated parent:

```shell
sudo chmod 600 /etc/netplan/*.yaml
sudo netplan generate
sudo networkctl reload
sudo networkctl reconfigure enp6s19 enp7s2  # FRR-A node
sudo networkctl reconfigure ens19           # FRR-B node
```

Validate the parents before installing the chart:

```shell
ip -br link show enp6s19
ip -br link show enp7s2
ip -br link show ens19
```

Run only the commands for interfaces that exist on the current node. Each
required parent must report `UP`.
