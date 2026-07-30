# MikroTik upstream configuration

This profile connects RouterOS 7.23.1 to both ISP FRR routers using eBGP.
RouterOS configuration changes are applied immediately and saved
automatically.

## Addressing and policy

| Item | Value |
|---|---|
| MikroTik address | `192.168.88.1` |
| MikroTik ASN | `65030` |
| FRR-A address | `192.168.88.250` |
| FRR-B address | `192.168.88.251` |
| FRR ASN | `65020` |
| Routed CGNAT prefix | `100.64.100.0/24` |

The MikroTik accepts only the CGNAT prefix from the FRRs and advertises only
an installed default route. BFD is disabled because the current lab profile
does not enable it on the FRRs.

## Configuration

Create a backup export before making changes:

```routeros
/export file=before-osvbng-bgp
```

Add the strict routing filters:

```routeros
/routing/filter/rule
add chain=OSVBNG-IN rule="if (dst == 100.64.100.0/24) { accept }"
add chain=OSVBNG-IN rule="reject"
add chain=OSVBNG-OUT rule="if (dst == 0.0.0.0/0) { accept }"
add chain=OSVBNG-OUT rule="reject"
```

Create the BGP instance and both FRR connections:

```routeros
/routing/bgp/instance
add name=osvbng as=65030 router-id=192.168.88.1 multipath=2

/routing/bgp/connection
add name=frr-a instance=osvbng afi=ip local.address=192.168.88.1 \
    local.role=ebgp remote.address=192.168.88.250/32 remote.as=65020 \
    input.filter=OSVBNG-IN output.filter-chain=OSVBNG-OUT \
    output.default-originate=if-installed use-bfd=no
add name=frr-b instance=osvbng afi=ip local.address=192.168.88.1 \
    local.role=ebgp remote.address=192.168.88.251/32 remote.as=65020 \
    input.filter=OSVBNG-IN output.filter-chain=OSVBNG-OUT \
    output.default-originate=if-installed use-bfd=no
```

Permit BGP to the router. `place-before=0` ensures this rule precedes a
general input-chain drop rule:

```routeros
/ip/firewall/address-list
add list=OSVBNG-FRR address=192.168.88.250
add list=OSVBNG-FRR address=192.168.88.251

/ip/firewall/filter
add chain=input action=accept protocol=tcp dst-port=179 \
    src-address-list=OSVBNG-FRR place-before=0 \
    comment="Allow BGP from OSVBNG FRRs"
```

Masquerade the lab CGNAT pool toward the Internet:

```routeros
/ip/firewall/nat
add chain=srcnat src-address=100.64.100.0/24 out-interface-list=WAN \
    action=masquerade comment="OSVBNG lab CGNAT pool"
```

If the Internet-facing interface is not a member of the `WAN` interface
list, replace `out-interface-list=WAN` with the appropriate
`out-interface=<name>`.

## Validation

Both sessions should have the `E` (established) flag and remote ASN `65020`:

```routeros
/routing/bgp/session/print
/routing/bgp/session/print detail
```

The CGNAT prefix should appear as an active BGP route. With both paths
eligible, RouterOS may show ECMP:

```routeros
/routing/route/print detail where dst-address=100.64.100.0/24
```

Confirm that a default route is installed before expecting RouterOS to
originate it:

```routeros
/ip/route/print detail where dst-address=0.0.0.0/0
```

Inspect current advertisements:

```routeros
/routing/bgp/advertisements/print
```

## Removal

The following removes only objects created by this guide:

```routeros
/routing/bgp/connection/remove [find where name="frr-a"]
/routing/bgp/connection/remove [find where name="frr-b"]
/routing/bgp/instance/remove [find where name="osvbng"]
/routing/filter/rule/remove [find where chain="OSVBNG-IN"]
/routing/filter/rule/remove [find where chain="OSVBNG-OUT"]
/ip/firewall/filter/remove [find where comment="Allow BGP from OSVBNG FRRs"]
/ip/firewall/nat/remove [find where comment="OSVBNG lab CGNAT pool"]
/ip/firewall/address-list/remove [find where list="OSVBNG-FRR"]
```
