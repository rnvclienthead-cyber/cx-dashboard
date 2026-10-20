#!/bin/bash
set -euo pipefail
NS=ozonvpn
CONF=ozonbot0
VETH_HOST=veth-ozon

if ip netns list | grep -qw "$NS"; then
  ip netns exec "$NS" awg-quick down "$CONF" 2>/dev/null || true
fi
ip link del "$VETH_HOST" 2>/dev/null || true
ip netns del "$NS" 2>/dev/null || true
iptables -t nat -D POSTROUTING -s 10.200.200.0/24 ! -o "$VETH_HOST" -j MASQUERADE 2>/dev/null || true
iptables -D FORWARD -i "$VETH_HOST" -j ACCEPT 2>/dev/null || true
iptables -D FORWARD -o "$VETH_HOST" -j ACCEPT 2>/dev/null || true
echo "ozon_vpn_down OK"
