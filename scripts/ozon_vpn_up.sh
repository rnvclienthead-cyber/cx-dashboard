#!/bin/bash
set -euo pipefail
NS=ozonvpn
CONF=ozonbot0
VETH_HOST=veth-ozon
VETH_NS=veth-ozon-ns
HOST_IP=10.200.200.1
NS_IP=10.200.200.2

modprobe amneziawg || true
sysctl -q -w net.ipv4.ip_forward=1

# Idempotent cleanup of previous partial state
if ip netns list | grep -qw "$NS"; then
  ip netns exec "$NS" awg-quick down "$CONF" 2>/dev/null || true
  ip link del "$VETH_HOST" 2>/dev/null || true
  ip netns del "$NS" 2>/dev/null || true
fi

ip netns add "$NS"
ip link add "$VETH_HOST" type veth peer name "$VETH_NS"
ip link set "$VETH_NS" netns "$NS"
ip addr add "${HOST_IP}/24" dev "$VETH_HOST"
ip link set "$VETH_HOST" up
ip netns exec "$NS" ip addr add "${NS_IP}/24" dev "$VETH_NS"
ip netns exec "$NS" ip link set "$VETH_NS" up
ip netns exec "$NS" ip link set lo up
ip netns exec "$NS" ip route add default via "$HOST_IP"

# NAT only for this veth subnet (does not touch default host routing)
iptables -t nat -C POSTROUTING -s 10.200.200.0/24 ! -o "$VETH_HOST" -j MASQUERADE 2>/dev/null \
  || iptables -t nat -A POSTROUTING -s 10.200.200.0/24 ! -o "$VETH_HOST" -j MASQUERADE
iptables -C FORWARD -i "$VETH_HOST" -j ACCEPT 2>/dev/null || iptables -A FORWARD -i "$VETH_HOST" -j ACCEPT
iptables -C FORWARD -o "$VETH_HOST" -j ACCEPT 2>/dev/null || iptables -A FORWARD -o "$VETH_HOST" -j ACCEPT

# Bring AmneziaWG inside namespace only
ip netns exec "$NS" awg-quick up "$CONF"

echo "ozon_vpn_up OK"
ip netns exec "$NS" awg show
echo -n "host_ip="
curl -4 -s --max-time 8 ifconfig.me || true
echo
echo -n "vpn_ip="
ip netns exec "$NS" curl -4 -s --max-time 15 ifconfig.me || ip netns exec "$NS" curl -4 -s --max-time 15 https://api.ipify.org || true
echo
