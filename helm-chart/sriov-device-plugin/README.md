# osvbng SR-IOV Network Device Plugin

This standalone prerequisite installs upstream SR-IOV Network Device Plugin
v3.11.0 only on explicitly labeled BNG workers. Helm ignores this directory.

The plugin advertises `qemu-virtio-dpdk.dev/osvbng_vfio`. A device must match
all three selectors: QEMU VirtIO vendor `1af4`, network device `1000`, and
driver `vfio-pci`. PCI addresses are intentionally not configured.

```shell
kubectl label node ebpf-bng-node-01 \
  osvbng.infinitydon.com/dpdk-ha=true --overwrite
kubectl label node ebpf-bng-node-02 \
  osvbng.infinitydon.com/dpdk-ha=true --overwrite
kubectl apply -k helm-chart/sriov-device-plugin
kubectl rollout status -n kube-system \
  daemonset/osvbng-sriov-device-plugin
kubectl get nodes -o custom-columns='NAME:.metadata.name,VFIO:.status.allocatable.qemu-virtio-dpdk\.dev/osvbng_vfio'
```

The plugin does not bind devices to `vfio-pci` or configure hugepages. Each
labeled worker must already expose exactly the intended two QEMU VirtIO
devices through `vfio-pci` and have sufficient hugepages.
