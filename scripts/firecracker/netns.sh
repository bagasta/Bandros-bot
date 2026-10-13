#!/usr/bin/env bash
# Run inside a user and network namespace. The parent starts slirp after the ready file appears.
set -euo pipefail

run_dir="${FC_RUN_DIR:?}"
mkdir -p "$run_dir"
rm -f "$run_dir/workspace.sock" "$run_dir/vsock.sock" "$run_dir/slirp.ready" "$run_dir/slirp4netns.pid"

ip tuntap add dev tapfc mode tap
ip tuntap add dev tapslirp mode tap
ip link add br0 type bridge
ip link set tapfc master br0
ip link set tapslirp master br0
ip link set tapfc up
ip link set tapslirp up
ip link set br0 up

printf '%s\n' "$$" >"$run_dir/network-namespace.pid"
touch "$run_dir/network-namespace.ready"
while [ ! -e "$run_dir/slirp.ready" ]; do
  sleep 0.1
done

exec "${FC_FIRECRACKER_BIN:-firecracker}" --api-sock "$run_dir/workspace.sock"
