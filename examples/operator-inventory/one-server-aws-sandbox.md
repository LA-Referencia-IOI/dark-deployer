# one-server-aws-sandbox

`one-server-aws-sandbox.json` is a single-EC2 sandbox based on the `local-ha`
service layout. The deployer is expected to run directly on the EC2 instance, so
the only machine uses `execution: local` and no SSH connection is required.

Use it for an externally reachable AWS sandbox at `sandbox.dark-pid.net` while
keeping all services on one host.

## Shape

- Profile: `lab`.
- Machines: one local machine named `sandbox`.
- Site: one site named `sandbox`.
- Network: one VPC LAN named `sandbox-lan`.
- Private address: `172.31.94.30`.
- Blockchain: two validator groups, `blockchain-a` and `blockchain-b`, with 2
  validators each.
- RPC: one primary RPC named `rpc01` in the `apps` group.
- Storage: two Kubo/IPFS Cluster peers, `storage-a` and `storage-b`.
- Replication: publish after 1 replica, target 2 replicas.
- Proxy: public HTTP gateway on TCP/80 for `sandbox.dark-pid.net`.

## Parameters to change

- `networks.sandbox-lan.cidr` if the EC2 instance is in a different VPC CIDR.
- `machines.sandbox.addresses.sandbox-lan` to the EC2 private address.
- Proxy `host` and `public_origin` if the sandbox DNS name changes.
- `proxies.gateway.listener.port` if the public gateway should not use port 80.
- `defaults.paths` if `/srv/dark`, `/srv/dark/data`, or `/srv/dark/secrets`
  should live elsewhere on the EC2 instance.
- `blockchain.chain_id` when creating a fresh chain artifact.
- `overrides.settings.minter.shoulder` is intentionally `2s0` for this sandbox
  so minted ARKs can be distinguished from the maintained `200` examples.
- `storage.replication` if you intentionally reduce or expand storage behavior.

## AWS prerequisites

- Connect to the EC2 instance as `ubuntu` using the local key
  `lareferencia-dark.pem` and the hostname `sandbox.dark-pid.net`.
- DNS for `sandbox.dark-pid.net` and the AWS firewall/Security Group are already
  configured; no deployer step is needed for either of them.
- Run the deployer from the EC2 instance itself.
- Ensure Docker and the deployer prerequisites are installed on the server.
- Keep the chain artifact aligned with this exact inventory, or generate a new
  one before install.

Connect from your workstation with:

```bash
chmod 600 lareferencia-dark.pem
ssh -i lareferencia-dark.pem ubuntu@sandbox.dark-pid.net
```

## How to inspect

```bash
venv/bin/python deploy.py validate \
  --inventory examples/operator-inventory/one-server-aws-sandbox.json
venv/bin/python deploy.py plan \
  --inventory examples/operator-inventory/one-server-aws-sandbox.json
```

## How to run

```bash
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/one-server-aws-sandbox.json \
  --create-master-wallet \
  --new-chain \
  --initialize-managed-secrets \
  --clean-empty-network-conflicts \
  --verbose
```

The command is run after SSH login, from the repository root. It does not use
SSH for deployment because the inventory declares the EC2 host as local.

The expected warnings are important: all validators and storage peers share the
same EC2 instance, so this is a sandbox with logical replicas, not an HA
production deployment.
