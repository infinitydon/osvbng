# Worker VirtIO interface naming

The three workers use one persistent naming convention for auxiliary
kernel-bound VirtIO NICs:

| VirtIO ordinal | Kernel name |
|---|---|
| `virtio4` | `enp8s19` |
| `virtio5` | `enp8s20` |
| `virtio6` | `enp8s21` |
| `virtio7` | `enp8s22` |
| `virtio8` | `enp8s23` |
| `virtio9` | `enp8s24` |
| `virtio10` | `enp8s25` |

Management remains `eth0` on every worker. Devices bound to `vfio-pci` do
not have kernel interface names and are not modified by this configuration.
The Kubernetes control-plane node is outside the scope of this convention.

Netplan matches each interface by its unique MAC address and then assigns the
standard name. The deployed file is:

```text
/etc/netplan/99-virtio-kernel-names.yaml
```

## Worker inventory

| Interface | node-01 MAC | node-02 MAC | node-03 MAC |
|---|---|---|---|
| `enp8s19` | `bc:24:11:7b:c7:69` | `bc:24:11:8e:ae:de` | `bc:24:11:c7:18:62` |
| `enp8s20` | `bc:24:11:8a:d7:92` | `bc:24:11:95:80:f4` | `bc:24:11:41:3d:9c` |
| `enp8s21` | `bc:24:11:f5:df:50` | `bc:24:11:da:71:96` | `bc:24:11:98:3a:3c` |
| `enp8s22` | `bc:24:11:8b:cc:81` | `bc:24:11:6e:23:c6` | `bc:24:11:ed:af:5b` |
| `enp8s23` | `bc:24:11:82:09:4a` | `bc:24:11:aa:af:fc` | `bc:24:11:07:17:0f` |
| `enp8s24` | `bc:24:11:9c:d9:e5` | `bc:24:11:bb:ac:d0` | `bc:24:11:b6:d4:d8` |
| `enp8s25` | `bc:24:11:75:42:a8` | `bc:24:11:da:b5:c7` | `bc:24:11:b5:ad:bc` |

The default FRR placement uses:

| Workload | Eligible nodes | Core parent | Upstream parent |
|---|---|---|---|
| FRR-A and FRR-B | Workers labeled `osvbng.infinitydon.com/bng-frr-ha=true` | `enp8s19` | `enp8s19` |

Both macvlan attachments use the same parent because the lab VirtIO NICs
share one L2 domain. Required node affinity selects the same worker pool as
the BNG StatefulSet, while required hostname anti-affinity spreads the two
FRR pods across different workers. The chart does not use `nodeName`.

## Netplan pattern

Each worker has the same logical structure with node-specific MAC addresses:

```yaml
network:
  version: 2
  renderer: networkd
  ethernets:
    enp8s19:
      match:
        macaddress: "NODE-SPECIFIC-MAC"
      set-name: enp8s19
      dhcp4: false
      dhcp6: false
      optional: true
```

The same structure is repeated through `enp8s25`. Existing files were backed
up before the unified configuration was installed.

## Validation

Run on each worker:

```shell
sudo netplan generate
ip -br link show | grep -E 'eth0|enp8s(19|20|21|22|23|24|25)'
```

Expected results:

- `eth0` retains the node management address.
- Exactly `enp8s19` through `enp8s25` are present and `UP`.
- Old names such as `ens19`, `enp6s19`, `enp7s2`, `enp9s1`, and `enp1s1`
  are absent.
- VFIO-bound devices remain advertised through
  `qemu-virtio-dpdk.dev/osvbng_vfio` on node-01 and node-02.
