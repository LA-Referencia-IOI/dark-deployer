# aws-active-six-host

`aws-active-six-host.json` is the single-site AWS variant of the asymmetric
two-site example. It contains the complete active application stack on AWS:
applications, primary RPC, Resolver, an observer, five validators, and two
storage peers.

Do not install it unchanged. Replace addresses, SSH settings, DNS names, secret
sources, artifact source, and deployment identity before preflight.

## Shape

- Profile: `production`.
- One site and one LAN: `aws` and `aws-lan`.
- Six SSH-managed machines: `apps`, `resolver`, `blockchain-a`,
  `blockchain-b`, `storage-1`, and `storage-2`.
- Validators: 5 total, split as 3 in `blockchain-a` and 2 in
  `blockchain-b`.
- QBFT quorum: 4. The deployment tolerates one individual validator loss.
- Resolver-observer bundle: `resolver-api`, its local read-only Store API reader, the Besu
  observer, and the Resolver proxy share the `resolver-observer` group on the
  `resolver` machine. Resolver uses the observer's local RPC.
- Applications: Minter, Admin, Dashboard, Explorer, Store and the primary RPC
  run on `apps`.
- Storage: two Kubo/IPFS Cluster peers, one on each storage machine.
- Replication: `publish_after_replicas: 1`, `target_replicas: 2`.
- Public services: separate AWS application and Resolver gateways.
- Explorer: runs with the AWS application stack and is published at
  `/explorer/` by `apps-public`.

The target is 2 because this single-site inventory has exactly two storage
peers. The three-replica target belongs to the two-site inventory, where the
third peer is remote.

## Parameters to replace

- `deployment.id`, `deployment.label`, cluster name, and `chain_id`.
- Every `management_address`, LAN address, SSH setting, and path.
- The `aws-lan` CIDR.
- Artifact and secret sources.
- The `apps-public` and `resolver-public` proxy hostnames, origins, ports, and
  TLS settings.
- The Minter shoulder and application settings as required by the deployment.

## Inspect before installation

```bash
venv/bin/python deploy.py validate \
  --inventory examples/operator-inventory/aws-active-six-host.json
venv/bin/python deploy.py inventory-network-matrix \
  --inventory examples/operator-inventory/aws-active-six-host.json
venv/bin/python deploy.py plan \
  --inventory examples/operator-inventory/aws-active-six-host.json
```

After replacing all example values and provisioning the hosts, install with:

```bash
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/aws-active-six-host.json \
  --verbose
```
