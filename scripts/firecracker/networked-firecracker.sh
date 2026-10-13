#!/usr/bin/env bash
# Keep Firecracker and slirp alive together inside an unprivileged network namespace.
set -euo pipefail

run_dir="${FC_RUN_DIR:?}"
script_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
mkdir -p "$run_dir"
rm -f "$run_dir/network-namespace.ready" "$run_dir/slirp.ready" "$run_dir/slirp4netns.pid"

unshare --user --map-root-user --net "$script_dir/netns.sh" &
netns_pid=$!
slirp_pid=""
cleanup() {
  set +e
  rm -f "$run_dir/slirp.ready"
  if [ -n "$slirp_pid" ]; then
    kill "$slirp_pid" 2>/dev/null
    wait "$slirp_pid" 2>/dev/null
  fi
  kill "$netns_pid" 2>/dev/null
  wait "$netns_pid" 2>/dev/null
}
trap cleanup EXIT INT TERM

for _ in $(seq 1 100); do
  [ -e "$run_dir/network-namespace.ready" ] && break
  kill -0 "$netns_pid" 2>/dev/null || { echo "network namespace setup exited early" >&2; exit 1; }
  sleep 0.1
done
[ -s "$run_dir/network-namespace.pid" ] || { echo "network namespace did not publish its pid" >&2; exit 1; }

namespace_pid="$(<"$run_dir/network-namespace.pid")"
slirp4netns --configure --mtu=1500 --netns-type=path \
  --userns-path "/proc/$namespace_pid/ns/user" \
  "/proc/$namespace_pid/ns/net" tapslirp >"$run_dir/slirp4netns.log" 2>&1 &
slirp_pid=$!
printf '%s\n' "$slirp_pid" >"$run_dir/slirp4netns.pid"
sleep 0.3
kill -0 "$slirp_pid" 2>/dev/null || { echo "slirp4netns exited early" >&2; exit 1; }
touch "$run_dir/slirp.ready"
wait "$netns_pid"
