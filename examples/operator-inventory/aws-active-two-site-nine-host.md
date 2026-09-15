# aws-active-two-site-nine-host

`aws-active-two-site-nine-host.json` is a production-shaped, asymmetric two-site inventory. AWS is the active site: it hosts every application service, the public gateways, the primary RPC, Resolver, and enough validators to keep QBFT operating when the remote site is unavailable. Site B is a remote preservation and chain-copy site; it does not host Minter, Admin, Dashboard, Explorer, or the writer Store API, but it does host an independent read-only Resolver bundle and public proxy.

Do not install this file unchanged. The AWS and Site B addresses, SSH settings, DNS names, secret sources, artifact source, and deployment identity are examples that must be replaced before preflight.

## Shape

- Profile: `production`.
- Sites: `aws` and `site-b`, connected by `mesh-vpn`.
- AWS machines: `apps`, `resolver`, `blockchain-a`, `blockchain-b`, `storage-1`, and `storage-2`.
- Site B machines: `site-b-validator`, `site-b-observer`, and `storage-3`.
- Validators: 7 total: 3 in `blockchain-a`, 2 in `blockchain-b`, and 2 in `blockchain-c` on Site B.
- QBFT quorum: 5. AWS retains exactly 5 validators when Site B is lost, so it continues producing blocks. Site B cannot maintain consensus if AWS is lost.
- Resolver-observer bundles: AWS runs `observer01`, `resolver-api`, its local
  read-only Store API reader, and `resolver-public`; Site B runs the equivalent
  `observer02`, `resolver-api-site-b`, `store-api-reader-site-b`, and
  `resolver-public-site-b` bundle.
- Storage: three Kubo/IPFS Cluster peers, two in AWS and one in Site B, with `publish_after_replicas: 1` and `target_replicas: 3`.
- Public services: both sites publish independent Resolver gateways; AWS also
  publishes the application gateway.
- Explorer: runs with the AWS application stack and is published at
  `/explorer/` by `apps-public`.

## Failure behavior

| Event | Result |
| --- | --- |
| Site B unavailable | AWS keeps quorum with 5 validators. Applications, minting, Resolver, and the public gateways continue. Storage remains available with two AWS peers, but the target of three durable replicas is unmet until Site B returns. |
| AWS unavailable | Site B retains its observer, two validators, and one storage peer. It preserves already replicated data and can inspect its chain copy, but it cannot form QBFT quorum or provide the application stack. |
| One validator unavailable during normal operation | Consensus continues: 6 validators remain and quorum is 5. |

This is an intentionally asymmetric availability model. It is not a bidirectional active/active deployment, and it does not tolerate losing an AWS validator after Site B has already failed because AWS then has exactly quorum.

## Parameters to replace

- `deployment.id`, `deployment.label`, cluster name, and `chain_id`.
- Every `management_address`, LAN address, VPN address, SSH setting, and path.
- `aws-lan`, `site-b-lan`, and `mesh-vpn` CIDRs.
- Artifact and secret sources.
- The `apps-public`, `resolver-public`, and `resolver-public-site-b` proxy
  hostnames, origins, ports, and TLS settings.
- DNS or an upstream traffic policy decides which Resolver hostname is used;
  both Resolver instances remain running and functional.
- The Minter shoulder and application settings as required by the deployment.

Keep every machine on the mesh VPN. The resolver derives same-site P2P paths from each site LAN and cross-site P2P paths from `mesh-vpn`; it does not create or configure the VPN.

## Inspect before installation

```bash
venv/bin/python deploy.py validate \
  --inventory examples/operator-inventory/aws-active-two-site-nine-host.json
venv/bin/python deploy.py inventory-network-matrix \
  --inventory examples/operator-inventory/aws-active-two-site-nine-host.json
venv/bin/python deploy.py plan \
  --inventory examples/operator-inventory/aws-active-two-site-nine-host.json
```

After replacing all example values and provisioning the hosts, install with:

```bash
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/aws-active-two-site-nine-host.json \
  --verbose
```
