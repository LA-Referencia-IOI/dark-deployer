# Networking in deployment V3

Deployment V3 separates controller access from application traffic.

| Field | Role |
| --- | --- |
| `machines.*.management_address` and `ssh` | Controller access only. |
| `machines.*.addresses.<network>` | Address bound by Docker on the named LAN or VPN. |
| `services.*.exposure` | API endpoint published by a service. |
| IPFS, Cluster and Besu P2P settings | Peer endpoint announced to other hosts. |

Containers on the same machine use Docker DNS and their internal port. A
connection crossing machines uses the provider's private exposure endpoint.
The `execution` setting selects the controller transport; it does not decide
whether a peer is local to Docker.

## Routed network domains

A network domain normally declares one `cidr`. Use `cidrs` when one logical
LAN or VPN contains several routed segments:

```json
"vpn": {
  "kind": "vpn",
  "cidrs": ["10.20.1.0/24", "10.20.2.0/24"]
}
```

Every machine address on `vpn` must belong to one declared segment. The
deployer treats those segments as one routed domain and still checks each
cross-host route before services start.

When a consumer has no address in the provider's selected domain, declare the
existing inter-domain route explicitly:

```json
"routes": [
  {"from": "apps-lan", "to": "storage-vpn", "via": "site-vpn"}
]
```

Routes are directional: `from` must be a network available to the consumer,
and `to` is the provider network selected by the service connection. `via` is
an operator label for the gateway or VPN path. This is an assertion of routing
that already exists; the deployer does not configure routers, VPNs, or firewall
rules. The network preflight then checks the effective route from each remote
consumer before deployment.

## Private endpoints and NAT

For normal LAN or VPN deployments, an API binds and advertises the machine's
address on its selected network:

```json
"exposure": {"mode": "private", "network": "vpn", "port": 9094}
```

When the endpoint is reached through NAT, retain the host bind address in the
machine network map and declare the reachable address and port on the service:

```json
"exposure": {
  "mode": "private",
  "network": "vpn",
  "port": 9094,
  "advertise_address": "203.0.113.20",
  "advertise_port": 19094
}
```

`advertise_address` and `advertise_port` affect remote consumers only. Docker
continues to bind `machines.*.addresses.vpn:9094`.

## P2P services

Kubo, IPFS Cluster and Besu derive cross-host peers from the network selected
by their infrastructure policy. The renderer supplies an API bootstrap route
and a P2P route separately. Entrypoints use the API only to obtain the peer
identity, then dial the rendered host/VPN endpoint; they never reuse a
Docker-private address returned by the peer API.

The default P2P ports are 4001 for Kubo, 9096 for Cluster and the configured
Besu port range. A service can use `configuration.p2p_advertise_port` when a
router translates the externally reachable P2P port.

Every host must permit the selected cross-host addresses and ports. The
installer first checks both remote API and P2P routes from each consumer host.
After startup it probes Cluster APIs from the remote storage hosts and then
verifies P2P membership. A missing peer reports the peer name and the rendered
P2P address and port expected for that route.

The route preflight proves routing, not that a firewall allows a service that
has not started yet. The post-start probes provide that protocol-level proof.

## Public proxies

`edge-proxy` is an explicit service with `configuration.sites`: each site has
an optional host name and an ordered set of public path prefixes. A rule names
a declared proxy connection and an `upstream_path`; it never embeds an IP or a
URL. Prefix routes are rendered before `/`, so `/admin/`, `/explorer/` and
`/api/` win over the Resolver catch-all.

The proxy configuration accepts `tls.mode` as `http`, `external` (TLS ends at a
trusted load balancer) or `direct` (certificate and key secret IDs supplied by
the operator). A direct TLS proxy mounts only those declared secrets. The proxy
always forwards host, scheme, client and prefix headers. Dashboard trusts those
headers so cookies, redirects and assets remain under `/admin/`; Explorer is
rendered with `/explorer/` as its SPA base.
